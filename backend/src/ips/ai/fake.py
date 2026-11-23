"""Deterministic, offline provider for development and tests."""

from pydantic import ValidationError

from ips.ai.provider import AnswerInput, AnswerResult, Citation, ProviderFailure


class FakeLLMProvider:
    supports_vision = False

    def answer(self, data: AnswerInput, timeout_seconds: float) -> AnswerResult:
        if data.images:
            raise ProviderFailure("vision_not_supported")
        query = data.text.strip().casefold()
        for snippet in data.snippets:
            content = snippet.get("text")
            if (
                not query
                or snippet.get("visibility") != "public"
                or not isinstance(content, str)
                or query not in content.casefold()
            ):
                continue
            try:
                citation = Citation.model_validate(
                    {key: snippet[key] for key in ("article_id", "revision", "title")}
                )
            except (KeyError, ValidationError):
                continue
            return AnswerResult(
                text=content,
                citations=[citation],
                needs_human=False,
                category=None,
                priority="normal",
                reason=None,
            )
        return AnswerResult(
            text="I couldn't find an answer. Please contact a support specialist.",
            citations=[],
            needs_human=True,
            category=None,
            priority="normal",
            reason="no_matching_public_snippet",
        )
