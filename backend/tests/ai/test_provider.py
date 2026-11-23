import json
from uuid import uuid4

import pytest

from ips.ai.http_provider import OpenAIProvider
from ips.ai.provider import AnswerInput, ProviderFailure
from ips.knowledge.retrieval import PostgresKnowledgeSearch


def response(text, citations=None):
    return {
        "status": "completed",
        "output": [
            {
                "type": "message",
                "content": [
                    {
                        "type": "output_text",
                        "text": json.dumps(
                            {
                                "text": text,
                                "citations": citations or [],
                                "needs_human": False,
                                "category": None,
                                "priority": "normal",
                                "reason": None,
                            }
                        ),
                    }
                ],
            }
        ],
    }


def test_provider_filters_internal_and_unauthorized_citations():
    article_id, invented_id = uuid4(), uuid4()
    captured = {}

    def transport(url, headers, payload, timeout):
        captured.update(payload=payload, timeout=timeout)
        return response(
            "answer",
            [
                {"article_id": str(article_id), "revision": 1, "title": "modified"},
                {"article_id": str(invented_id), "revision": 1, "title": "invented"},
            ],
        )

    provider = OpenAIProvider(api_key="test-secret", model="test-model", transport=transport)
    data = AnswerInput(
        text="refund",
        history=[{"kind": "internal_note", "text": "NOTE_SECRET"}],
        snippets=[
            {
                "article_id": str(article_id),
                "revision": 1,
                "title": "Public title",
                "text": "Refund policy",
                "visibility": "public",
            },
            {
                "article_id": str(uuid4()),
                "revision": 1,
                "title": "INTERNAL_SECRET",
                "text": "INTERNAL_SECRET",
                "visibility": "internal",
            },
        ],
    )
    result = provider.answer(data, timeout_seconds=20)
    prompt = json.dumps(captured["payload"])
    assert "INTERNAL_SECRET" not in prompt and "NOTE_SECRET" not in prompt
    assert captured["timeout"] == 20
    assert [citation.article_id for citation in result.citations] == [article_id]
    assert result.citations[0].title == "Public title"
    assert "test-secret" not in repr(provider)


@pytest.mark.parametrize("failure", [429, 500, 503, "timeout", "malformed", "unexpected"])
def test_provider_failures_are_safe(failure, caplog):
    def transport(url, headers, payload, timeout):
        assert timeout == 20
        if failure == "timeout":
            raise TimeoutError("test-secret")
        if failure == "unexpected":
            raise RuntimeError("test-secret")
        if failure == "malformed":
            return {"status": "completed", "output": []}
        return (failure, {"error": "test-secret"})

    provider = OpenAIProvider(api_key="test-secret", model="test-model", transport=transport)
    snippet = {
        "article_id": str(uuid4()),
        "revision": 1,
        "title": "Info",
        "text": "question answer",
        "visibility": "public",
    }
    with pytest.raises(ProviderFailure) as error:
        provider.answer(
            AnswerInput(text="question", history=[], snippets=[snippet]), timeout_seconds=50
        )
    assert "test-secret" not in str(error.value)
    assert "test-secret" not in caplog.text
    if failure == 429:
        assert error.value.code == "rate_limited"


def test_empty_or_missing_widget_binding_fails_closed():
    class NoDatabase:
        def __call__(self):
            raise AssertionError("empty search must not access database")

    search = PostgresKnowledgeSearch(NoDatabase())
    assert search.search(uuid4(), uuid4(), "   ", "customer") == []


def test_provider_without_evidence_never_calls_transport():
    def fail(*args):
        raise AssertionError("must not contact a provider without public evidence")

    result = OpenAIProvider(api_key="key", model="test", transport=fail).answer(
        AnswerInput(text="unknown", history=[], snippets=[]), timeout_seconds=20
    )
    assert result.needs_human and result.citations == []


def test_real_provider_is_not_enabled_in_fake_mode(monkeypatch):
    monkeypatch.setenv("AI_PROVIDER", "fake")
    monkeypatch.delenv("AI_API_KEY", raising=False)
    with pytest.raises(ProviderFailure) as error:
        OpenAIProvider.from_env()
    assert error.value.code == "not_configured"


def test_missing_widget_binding_returns_no_articles():
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker

    from ips.core.config import Settings
    from ips.core.ports import KnowledgeSearch

    engine = create_engine(Settings().database_url)
    try:
        search = PostgresKnowledgeSearch(sessionmaker(bind=engine))
        assert isinstance(search, KnowledgeSearch)
        assert search.search(uuid4(), uuid4(), "refund", "customer") == []
    finally:
        engine.dispose()


def test_answer_with_only_invented_sources_escalates():
    snippet = {
        "article_id": str(uuid4()),
        "revision": 1,
        "title": "Info",
        "text": "question answer",
        "visibility": "public",
    }

    def transport(url, headers, payload, timeout):
        return response(
            "unsupported", [{"article_id": str(uuid4()), "revision": 1, "title": "made up"}]
        )

    result = OpenAIProvider(api_key="key", model="test", transport=transport).answer(
        AnswerInput(text="question", history=[], snippets=[snippet]), timeout_seconds=20
    )
    assert result.needs_human and result.citations == [] and result.text != "unsupported"


def test_postgres_fts_is_tenant_widget_and_visibility_scoped():
    from contextlib import contextmanager

    from sqlalchemy import create_engine, text
    from sqlalchemy.orm import Session

    from ips.core.config import Settings
    from ips.knowledge.repository import KnowledgeRepository
    from ips.knowledge.schemas import ArticleInput

    engine = create_engine(Settings().database_url)
    tenant, other, widget, other_widget, section, unbound, other_section, actor = (
        uuid4() for _ in range(8)
    )
    with engine.connect() as connection:
        transaction = connection.begin()
        try:
            db = Session(bind=connection)
            db.execute(
                text("""CREATE TEMP TABLE widget_sections
                (tenant_id uuid NOT NULL, widget_id uuid NOT NULL, section_id uuid NOT NULL)
                ON COMMIT DROP""")
            )
            for tid in (tenant, other):
                db.execute(
                    text("INSERT INTO organizations (id,name) VALUES (:id,'fts test')"), {"id": tid}
                )
            for wid, tid in ((widget, tenant), (other_widget, other)):
                db.execute(
                    text("""INSERT INTO widgets (id,tenant_id,name,public_key,color,greeting,allowed_origins)
                    VALUES (:id,:tenant,'test',:key,'#ffffff','hello',ARRAY['https://example.test'])"""),
                    {"id": wid, "tenant": tid, "key": str(wid)},
                )
            for sid, tid in ((section, tenant), (unbound, tenant), (other_section, other)):
                db.execute(
                    text(
                        "INSERT INTO article_sections (id,tenant_id,name) VALUES (:id,:tenant,:name)"
                    ),
                    {"id": sid, "tenant": tid, "name": str(sid)},
                )
            for tid, wid, sid in (
                (tenant, widget, section),
                (other, other_widget, other_section),
                (tenant, widget, other_section),
            ):
                db.execute(
                    text(
                        "INSERT INTO widget_sections (tenant_id,widget_id,section_id) VALUES (:tenant,:widget,:section)"
                    ),
                    {"tenant": tid, "widget": wid, "section": sid},
                )
            repo = KnowledgeRepository()
            for tid, sid, visibility, status, title in (
                (tenant, section, "public", "published", "allowed"),
                (tenant, section, "internal", "published", "internal"),
                (tenant, section, "public", "disabled", "disabled"),
                (tenant, section, "public", "draft", "draft"),
                (tenant, unbound, "public", "published", "unbound"),
                (other, other_section, "public", "published", "foreign"),
            ):
                repo.create_article(
                    db,
                    tid,
                    actor,
                    ArticleInput(
                        section_id=sid,
                        title=title,
                        body="refund details here",
                        visibility=visibility,
                        status=status,
                    ),
                )

            @contextmanager
            def factory():
                yield db

            search = PostgresKnowledgeSearch(factory)
            assert [item.title for item in search.search(tenant, widget, "refund", "customer")] == [
                "allowed"
            ]
            assert search.search(tenant, other_widget, "refund", "customer") == []
            assert [
                item.title for item in search.search(other, other_widget, "refund", "customer")
            ] == ["foreign"]
            assert search.search(tenant, widget, "nonsense", "customer") == []
            found = search.search(tenant, widget, "refund", "customer")
            captured = {}

            def transport(url, headers, payload, timeout):
                captured.update(payload=payload)
                item = found[0]
                return response(
                    "ok",
                    [
                        {
                            "article_id": str(item.article_id),
                            "revision": item.revision,
                            "title": item.title,
                        }
                    ],
                )

            result = OpenAIProvider(api_key="key", model="test", transport=transport).answer(
                AnswerInput(
                    text="refund",
                    history=[],
                    snippets=[item.model_dump(mode="json") for item in found],
                ),
                timeout_seconds=20,
            )
            prompt = json.dumps(captured["payload"])
            assert not result.needs_human
            assert all(
                value not in prompt for value in ("foreign", "internal", "disabled", "unbound")
            )
            repo.create_article(
                db,
                tenant,
                actor,
                ArticleInput(
                    section_id=section,
                    title="Long document",
                    body=("filler " * 500) + "needleword details",
                    visibility="public",
                    status="published",
                ),
            )
            long_result = search.search(tenant, widget, "needleword", "customer")
            assert len(long_result) == 1 and "needleword" in long_result[0].text
            for number in range(6):
                repo.create_article(
                    db,
                    tenant,
                    actor,
                    ArticleInput(
                        section_id=section,
                        title=f"extra {number}",
                        body="refund",
                        visibility="public",
                        status="published",
                    ),
                )
            assert len(search.search(tenant, widget, "refund", "customer", limit=100)) == 5
            db.close()
        finally:
            transaction.rollback()
    engine.dispose()
