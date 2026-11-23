"""The shared B/C provider contract, re-exported for the AI domain."""

from ips.core.ports import AnswerInput, AnswerResult, Citation, ImageInput, LLMProvider


class ProviderFailure(Exception):
    """Safe domain error that never includes provider responses or credentials."""

    def __init__(self, code: str):
        self.code = code
        super().__init__(code)


__all__ = [
    "AnswerInput",
    "AnswerResult",
    "Citation",
    "ImageInput",
    "LLMProvider",
    "ProviderFailure",
]
