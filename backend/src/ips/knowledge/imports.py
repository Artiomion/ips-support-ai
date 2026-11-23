"""Bounds and decoding for synchronous TXT imports."""

MAX_TXT_BYTES = 2 * 1024 * 1024


class ImportFailure(ValueError):
    def __init__(self, code: str):
        self.code = code
        super().__init__(code)


def read_text(data: bytes) -> str:
    if len(data) > MAX_TXT_BYTES:
        raise ImportFailure("too_large")
    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise ImportFailure("invalid_utf8") from exc
    if not text or len(text) > 100_000 or "\x00" in text:
        raise ImportFailure("invalid_article_length")
    return text
