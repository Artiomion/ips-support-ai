"""Claim and execute customer AI runs without holding a transaction across I/O."""

import json
from uuid import uuid4

from sqlalchemy import text
from sqlalchemy.exc import OperationalError

from ips.ai.guardrails import SafeCustomerAssistant, fallback
from ips.ai.history import CustomerContext
from ips.ai.provider import ProviderFailure
from ips.ai.rules import validated_answer
from ips.core.ports import CustomerPrincipal, Usage


class Orchestrator:
    def __init__(
        self,
        sessions,
        budget,
        search,
        provider,
        tickets=None,
        lease_seconds=90,
        tenant_id=None,
        handoff_guard=None,
        demo=False,
    ):
        self.sessions = sessions
        self.budget = budget
        self.customer_context = CustomerContext(sessions, search)
        self.guardrails = SafeCustomerAssistant(self.customer_context, provider)
        self.tickets = tickets
        self.handoff_guard = handoff_guard
        self.demo = demo
        self.lease_seconds = max(30, lease_seconds)
        self.tenant_id = tenant_id

    def _claim(self):
        with self.sessions.begin() as db:
            # A locked earlier run must still block later runs in its conversation.
            row = (
                db.execute(
                    text("""
                SELECT r.* FROM ai_runs r
                JOIN messages input ON input.tenant_id=r.tenant_id
                  AND input.conversation_id=r.conversation_id AND input.id=r.input_message_id
                WHERE (CAST(:tenant AS uuid) IS NULL OR r.tenant_id=:tenant)
                  AND (r.state='pending' OR (r.state='running' AND r.lease_until < now()))
                  AND NOT EXISTS (SELECT 1 FROM ai_runs other
                    JOIN messages previous ON previous.tenant_id=other.tenant_id
                      AND previous.conversation_id=other.conversation_id
                      AND previous.id=other.input_message_id
                    WHERE other.tenant_id=r.tenant_id AND other.conversation_id=r.conversation_id
                      AND other.id<>r.id
                      AND ((other.state='running' AND other.lease_until>=now())
                        OR (previous.seq<input.seq AND (other.state='pending'
                          OR (other.state='running' AND other.lease_until<now())))))
                ORDER BY input.created_at, r.id FOR UPDATE OF r SKIP LOCKED LIMIT 1
            """),
                    {"tenant": self.tenant_id},
                )
                .mappings()
                .first()
            )
            if row is None:
                return None
            locked = db.execute(
                text("SELECT pg_try_advisory_xact_lock(hashtextextended(:key, 0))"),
                {"key": str(row["conversation_id"])},
            ).scalar_one()
            if not locked:
                return None
            active = db.execute(
                text("""SELECT EXISTS(SELECT 1 FROM ai_runs
                WHERE tenant_id=:tenant AND conversation_id=:conversation AND id<>:id
                  AND state='running' AND lease_until>=now())"""),
                {
                    "tenant": row["tenant_id"],
                    "conversation": row["conversation_id"],
                    "id": row["id"],
                },
            ).scalar_one()
            if active:
                return None
            return dict(
                db.execute(
                    text("""UPDATE ai_runs SET state='running', attempts=attempts+1,
                lease_until=now()+(:seconds * interval '1 second'), error_code=NULL
                WHERE id=:id RETURNING *"""),
                    {"id": row["id"], "seconds": self.lease_seconds},
                )
                .mappings()
                .one()
            )

    def _input(self, run):
        with self.sessions() as db:
            row = (
                db.execute(
                    text("""SELECT c.mode,c.version,c.customer_id,c.widget_id
                FROM conversations c JOIN messages m ON m.tenant_id=c.tenant_id
                AND m.conversation_id=c.id AND m.id=:message AND m.kind='customer'
                WHERE c.tenant_id=:tenant AND c.id=:conversation"""),
                    {
                        "tenant": run["tenant_id"],
                        "conversation": run["conversation_id"],
                        "message": run["input_message_id"],
                    },
                )
                .mappings()
                .one()
            )
            return dict(row)

    def _finish(self, run, state, error=None, result=None, *, sources=()) -> bool:
        params = {
            "tenant": run["tenant_id"],
            "conversation": run["conversation_id"],
            "id": run["id"],
            "attempt": run["attempts"],
        }
        tickets = (
            self.tickets
            if state == "completed" and result is not None and result.needs_human
            else None
        )
        with self.sessions.begin() as db:
            if tickets is not None:
                if self.handoff_guard is None:
                    raise RuntimeError("handoff lock guard is not configured")
                self.handoff_guard(db, run["tenant_id"])
            conversation = (
                db.execute(
                    text("""SELECT * FROM conversations
                WHERE tenant_id=:tenant AND id=:conversation FOR UPDATE"""),
                    params,
                )
                .mappings()
                .one()
            )
            current = db.execute(
                text("""SELECT id FROM ai_runs WHERE id=:id AND tenant_id=:tenant
                AND state='running' AND attempts=:attempt AND lease_until>now() FOR UPDATE"""),
                params,
            ).first()
            if current is None:
                return False
            if (
                conversation["mode"] != "bot"
                or conversation["version"] != run["generation_version"]
            ):
                state, result, error = "discarded", None, None
            if (
                result is not None
                and sources
                and not self.customer_context.lock_current_sources(
                    db, run["tenant_id"], conversation["widget_id"], sources
                )
            ):
                state, result, error = "discarded", None, "source_unavailable"
            if result is not None:
                if tickets is not None:
                    principal = CustomerPrincipal(
                        tenant_id=run["tenant_id"],
                        customer_id=conversation["customer_id"],
                        widget_id=conversation["widget_id"],
                    )
                    tickets.escalate(
                        db,
                        principal,
                        run["conversation_id"],
                        result.reason or "AI requested support",
                        result.category,
                        result.priority,
                    )
                db.execute(
                    text("""INSERT INTO messages
                    (id,tenant_id,conversation_id,seq,kind,text,sources,client_message_id)
                    SELECT :message,:tenant,:conversation,COALESCE(MAX(seq),0)+1,
                           'assistant',:body,CAST(:sources AS jsonb),:key
                    FROM messages WHERE tenant_id=:tenant AND conversation_id=:conversation"""),
                    {
                        **params,
                        "message": uuid4(),
                        "key": run["id"],
                        "body": f"Демо-режим: {result.text}" if self.demo else result.text,
                        "sources": json.dumps(
                            [c.model_dump(mode="json") for c in result.citations]
                        ),
                    },
                )
            db.execute(
                text("""UPDATE ai_runs SET state=:state, error_code=:error, lease_until=NULL
                WHERE id=:id AND tenant_id=:tenant AND attempts=:attempt"""),
                {**params, "state": state, "error": error},
            )
        return True

    def run_once(self):
        run = self._claim()
        if run is None:
            return False
        reservation = None
        finished = False
        failed = True
        try:
            context = self._input(run)
            if context["mode"] != "bot" or context["version"] != run["generation_version"]:
                self._finish(run, "discarded")
                return True
            reservation = self.budget.reserve(run["tenant_id"], run["id"])
            principal = CustomerPrincipal(
                tenant_id=run["tenant_id"],
                customer_id=context["customer_id"],
                widget_id=context["widget_id"],
            )
            question, history, snippets = self.customer_context.prepare(
                principal, run["conversation_id"], run["input_message_id"]
            )
            answer = validated_answer(
                self.guardrails.answer_or_raise(question, history, snippets), snippets
            )
            finished = self._finish(run, "completed", result=answer, sources=snippets)
            failed = False
        except OperationalError:
            # A database interruption leaves the lease available for recovery.
            raise
        except Exception as exc:  # noqa: BLE001 - do not expose provider data or secrets.
            code = "generation_failed"
            notice = None
            if isinstance(exc, ProviderFailure) and exc.code in (
                "provider_unavailable",
                "rate_limited",
            ):
                code = exc.code
                notice = fallback(code)
            finished = self._finish(run, "failed", error=code, result=notice)
        finally:
            if finished and reservation is not None:
                self.budget.settle(reservation, Usage(input_tokens=0, output_tokens=0), failed)
        return True
