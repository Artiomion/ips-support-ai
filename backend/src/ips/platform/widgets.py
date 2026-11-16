"""Validation shared by widget configuration and its public sessions."""

import ipaddress
import re
from urllib.parse import urlsplit

_HOSTNAME = re.compile(
    r"(?=.{1,253}$)(?:[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)*[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?$",
    re.ASCII,
)


def normalize_origin(origin: str) -> str:
    try:
        parsed = urlsplit(origin)
        host = parsed.hostname
        port = parsed.port
        if (
            parsed.scheme not in ("http", "https")
            or not host
            or parsed.username
            or parsed.password
            or parsed.path
            or parsed.query
            or parsed.fragment
            or any(c.isspace() for c in origin)
        ):
            raise ValueError("Invalid origin")
        host = host.lower()
        try:
            address = ipaddress.ip_address(host)
            host = f"[{address.compressed}]" if address.version == 6 else address.compressed
        except ValueError:
            if not _HOSTNAME.fullmatch(host):
                raise ValueError("Invalid origin") from None
        supplied = f"{parsed.scheme}://{host}" + (f":{port}" if port is not None else "")
        if supplied != origin:
            raise ValueError("Origin must be canonical")
        default_port = (parsed.scheme == "https" and port == 443) or (
            parsed.scheme == "http" and port == 80
        )
        normalized = f"{parsed.scheme}://{host}" + (f":{port}" if port and not default_port else "")
        return normalized
    except (ValueError, UnicodeError) as exc:
        raise ValueError("Invalid origin") from exc
