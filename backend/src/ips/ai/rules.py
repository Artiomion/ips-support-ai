"""Validate provider output before it can affect customer-visible state."""

from ips.core.ports import AnswerResult


def validated_answer(value: object, snippets: list) -> AnswerResult:
    # A provider must honor the port; arbitrary dictionaries are not trusted.
    if not isinstance(value, AnswerResult):
        raise TypeError("invalid_provider_result")
    result = AnswerResult.model_validate(value.model_dump())
    if not result.text.strip() or len(result.text) > 8000:
        raise ValueError("invalid_provider_result")
    allowed = {(s.article_id, s.revision, s.title) for s in snippets if s.visibility == "public"}
    if any((c.article_id, c.revision, c.title) not in allowed for c in result.citations):
        raise ValueError("invalid_provider_result")
    return result
