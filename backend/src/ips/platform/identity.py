"""Validation of short-lived site-issued customer identity assertions."""

import base64
import binascii
import hashlib
import hmac
import json
from datetime import UTC, datetime
from uuid import UUID


class IdentityError(ValueError):
    """The site assertion is invalid, expired, or not for this widget."""


def derive_widget_secret(master_secret: str, widget_id: UUID) -> bytes:
    return hmac.new(
        master_secret.encode(), b"ips:widget-identity:v1:" + widget_id.bytes, hashlib.sha256
    ).digest()


def _decode(value: str) -> bytes:
    try:
        return base64.b64decode(value + "=" * (-len(value) % 4), altchars=b"-_", validate=True)
    except (ValueError, binascii.Error) as exc:
        raise IdentityError("Invalid identity assertion") from exc


def _unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise IdentityError("Invalid identity assertion")
        result[key] = value
    return result


def decode_identity(token: str, secret: bytes, widget_key: str, now: int | None = None) -> dict:
    if len(token) > 4096:
        raise IdentityError("Invalid identity assertion")
    try:
        payload, signature = token.split(".")
        expected = hmac.new(secret, payload.encode("ascii"), hashlib.sha256).digest()
        if not hmac.compare_digest(expected, _decode(signature)):
            raise IdentityError("Invalid identity assertion")
        data = json.loads(_decode(payload), object_pairs_hook=_unique_object)
        if not isinstance(data, dict) or set(data) != {
            "widget_key",
            "external_id",
            "nonce",
            "issued_at",
            "expires_at",
        }:
            raise IdentityError("Invalid identity assertion")
        issued, expiry = data["issued_at"], data["expires_at"]
        current = int(datetime.now(UTC).timestamp()) if now is None else now
        if (
            type(issued) is not int
            or type(expiry) is not int
            or issued > current + 30
            or expiry <= current
            or expiry <= issued
            or expiry - issued > 300
        ):
            raise IdentityError("Invalid identity assertion")
        if (
            data["widget_key"] != widget_key
            or not isinstance(data["external_id"], str)
            or not 1 <= len(data["external_id"]) <= 255
            or not isinstance(data["nonce"], str)
            or not 16 <= len(data["nonce"]) <= 128
        ):
            raise IdentityError("Invalid identity assertion")
        return data
    except (UnicodeError, ValueError, TypeError, KeyError, json.JSONDecodeError) as exc:
        if isinstance(exc, IdentityError):
            raise
        raise IdentityError("Invalid identity assertion") from exc
