"""Shared rate limits without persisting client identifiers or request contents."""

import hashlib
import hmac

from sqlalchemy import text


class PostgresRateLimiter:
    def __init__(self, sessions, *, secret="development-only", max_requests=120, window_seconds=60):
        if max_requests < 1 or window_seconds < 1:
            raise ValueError("Invalid rate limit")
        if not secret:
            raise ValueError("Invalid rate limit secret")
        self.sessions = sessions
        self._secret = secret.encode("utf-8")
        self.max_requests = max_requests
        self.window_seconds = window_seconds

    def allow(self, category: str, client: str) -> bool:
        bucket = hmac.new(
            self._secret, f"{category}\0{client}".encode(), hashlib.sha256
        ).hexdigest()
        with self.sessions.begin() as db:
            attempts = db.execute(
                text("""INSERT INTO request_limits (bucket_key,window_start,attempts)
                VALUES (:key,now(),1) ON CONFLICT (bucket_key) DO UPDATE SET
                attempts=CASE WHEN request_limits.window_start <= now()-(:seconds * interval '1 second')
                    THEN 1 ELSE request_limits.attempts+1 END,
                window_start=CASE WHEN request_limits.window_start <= now()-(:seconds * interval '1 second')
                    THEN now() ELSE request_limits.window_start END
                RETURNING attempts"""),
                {"key": bucket, "seconds": self.window_seconds},
            ).scalar_one()
            return attempts <= self.max_requests


def request_category(method: str, path: str) -> str | None:
    if method != "POST":
        return None
    if path in ("/api/v1/auth/login", "/api/v1/auth/register", "/api/v1/auth/invitations/accept"):
        return path.rsplit("/", 1)[-1]
    if path.startswith("/api/v1/public/widgets/") and path.endswith("/sessions"):
        return "widget_session"
    if path.startswith("/api/v1/public/conversations/") and path.endswith("/messages"):
        return "customer_message"
    if path.startswith("/api/v1/public/"):
        return "public_write"
    return None
