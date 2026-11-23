"""Safety boundary for customer AI calls; no provider can access tools here."""

import logging
import re

from ips.ai.provider import ProviderFailure
from ips.core.ports import AnswerInput, AnswerResult

logger = logging.getLogger(__name__)
_SAFE_CODES = {"provider_unavailable", "rate_limited", "invalid_response", "invalid_timeout"}
_SECRET_PATTERN = re.compile(r"(?i)(?:\bBearer\s+[A-Za-z0-9._~+/-]{16,}|\bsk-[A-Za-z0-9_-]{16,})")


def fallback(reason):
    return AnswerResult(
        text="Не удалось подготовить ответ. Обратитесь к специалисту.",
        citations=[],
        needs_human=True,
        category=None,
        priority="normal",
        reason=reason,
    )


class SafeCustomerAssistant:
    def __init__(self, context, provider):
        self.context = context
        self.provider = provider

    def answer(self, principal, conversation_id, message_id):
        try:
            question, history, snippets = self.context.prepare(
                principal, conversation_id, message_id
            )
        except Exception:  # noqa: BLE001 - preserve tenant privacy on failed context retrieval.
            logger.warning("AI customer context unavailable")
            return fallback("context_unavailable")
        return self.answer_prepared(question, history, snippets)

    def answer_prepared(self, question, history, snippets):
        try:
            return self.answer_or_raise(question, history, snippets)
        except ProviderFailure as exc:
            return fallback(exc.code)

    def answer_or_raise(self, question, history, snippets):
        if not snippets:
            return fallback("no_public_evidence")
        inputs = [question]
        inputs.extend(row.get("text", "") for row in history if isinstance(row, dict))
        inputs.extend(value for item in snippets for value in (item.title, item.text))
        if any(isinstance(value, str) and _SECRET_PATTERN.search(value) for value in inputs):
            return fallback("sensitive_input")
        data = AnswerInput(
            text=question,
            history=history,
            snippets=[snippet.model_dump(mode="json") for snippet in snippets],
        )
        try:
            answer = self.provider.answer(data, timeout_seconds=20)
            if not isinstance(answer, AnswerResult):
                raise TypeError("invalid_response")
            answer = AnswerResult.model_validate(answer.model_dump())
            allowed = {(s.article_id, s.revision, s.title) for s in snippets}
            if (
                not answer.text.strip()
                or len(answer.text) > 8000
                or _SECRET_PATTERN.search(answer.text)
                or _SECRET_PATTERN.search(answer.reason or "")
                or _SECRET_PATTERN.search(answer.category or "")
                or any((c.article_id, c.revision, c.title) not in allowed for c in answer.citations)
                or (not answer.needs_human and not answer.citations)
            ):
                raise ValueError("invalid_response")
            return answer
        except Exception as exc:  # noqa: BLE001 - don't leak provider responses or keys.
            code = (
                exc.code
                if isinstance(exc, ProviderFailure) and exc.code in _SAFE_CODES
                else "invalid_response"
            )
            logger.warning("AI provider failure: %s", code)
            raise ProviderFailure(code) from None
