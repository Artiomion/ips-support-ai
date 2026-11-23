"""Conservative contact redaction and exact normalized deduplication."""

import hashlib
import re
import unicodedata

_EMAIL = re.compile(r"(?<![\w.+-])[\w.+-]+@[\w.-]+\.[a-zA-Zа-яА-Я]{2,}(?![\w.-])")
_PHONE = re.compile(r"(?<!\w)(?:\+?\d[\d ()-]{5,}\d)(?!\w)")


def redact(value: str) -> tuple[str, list[str]]:
    flags = []
    value, count = _EMAIL.subn("[EMAIL REDACTED]", value)
    if count:
        flags.append("email")

    def phone(match):
        digits = sum(char.isdigit() for char in match.group())
        if 7 <= digits <= 15:
            if "phone" not in flags:
                flags.append("phone")
            return "[PHONE REDACTED]"
        return match.group()

    return _PHONE.sub(phone, value), flags


def fingerprint(value: str) -> str:
    normalized = " ".join(unicodedata.normalize("NFKC", value).casefold().split())
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()
