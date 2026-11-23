"""CORS for the public widget API; never grants access by itself."""

import re
from urllib.parse import unquote

from fastapi import Request
from fastapi.responses import Response
from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError
from starlette.concurrency import run_in_threadpool

from ips.platform.widgets import normalize_origin

WIDGET_SESSION = re.compile(r"^/api/v1/public/widgets/([^/]+)/sessions$")
TICKET_PATH = re.compile(
    r"^/api/v1/public/(?:conversations/[^/]+/handoff|tickets/[^/]+(?:/confirm|/reopen)?)$"
)
CONVERSATION_PATH = re.compile(
    r"^/api/v1/public/conversations(?:/[^/]+/(?:messages|confirm-ai|resume-bot))?$"
)
OUTCOME_PATH = re.compile(r"^/api/v1/public/conversations/[^/]+/(?:confirm-ai|resume-bot)$")


def _is_allowed(request: Request, origin: str, widget_key: str | None) -> bool:
    try:
        if normalize_origin(origin) != origin:
            return False
        with request.app.state.engine.connect() as conn:
            if widget_key is not None:
                result = conn.execute(
                    text(
                        "SELECT EXISTS(SELECT 1 FROM widgets WHERE public_key=:key AND enabled AND :origin=ANY(allowed_origins))"
                    ),
                    {"key": widget_key, "origin": origin},
                ).scalar_one()
            else:
                result = conn.execute(
                    text(
                        "SELECT EXISTS(SELECT 1 FROM widgets WHERE enabled AND :origin=ANY(allowed_origins))"
                    ),
                    {"origin": origin},
                ).scalar_one()
            return bool(result) or (
                widget_key is None and origin == request.app.state.settings.widget_origin
            )
    except (ValueError, SQLAlchemyError):
        return False


async def widget_cors(request: Request, call_next):
    path = request.url.path
    match = WIDGET_SESSION.fullmatch(path)
    is_session = path == "/api/v1/public/session"
    is_ticket = bool(TICKET_PATH.fullmatch(path))
    is_conversation = bool(CONVERSATION_PATH.fullmatch(path))
    is_outcome = bool(OUTCOME_PATH.fullmatch(path))
    if not match and not is_session and not is_ticket and not is_conversation:
        return await call_next(request)
    origin = request.headers.get("origin")
    if not origin:
        return await call_next(request)
    key = unquote(match.group(1)) if match else None
    allowed = (
        origin == request.app.state.settings.widget_origin
        if is_ticket or is_conversation
        else await run_in_threadpool(_is_allowed, request, origin, key)
    )
    preflight = request.method == "OPTIONS" and "access-control-request-method" in request.headers
    if preflight:
        vary = "Origin, Access-Control-Request-Method, Access-Control-Request-Headers, Access-Control-Request-Private-Network"
        method = request.headers["access-control-request-method"].upper()
        expected_method = (
            "POST"
            if match
            or is_outcome
            or (is_ticket and path.endswith(("/handoff", "/confirm", "/reopen")))
            else "GET"
        )
        headers = {
            item.strip().lower()
            for item in request.headers.get("access-control-request-headers", "").split(",")
            if item.strip()
        }
        if (
            not allowed
            or (
                method not in {"GET", "POST"}
                if is_conversation and not is_outcome
                else method != expected_method
            )
            or not headers <= {"content-type", "authorization"}
            or "access-control-request-private-network" in request.headers
        ):
            return Response(status_code=403, headers={"Vary": vary})
        return Response(
            status_code=200,
            headers={
                "Access-Control-Allow-Origin": origin,
                "Access-Control-Allow-Methods": method,
                "Access-Control-Allow-Headers": "Content-Type, Authorization",
                "Vary": vary,
            },
        )
    response = await call_next(request)
    # The inner staff CORS middleware permits credentials. Public widget routes
    # use bearer tokens and must not inherit staff cookie permissions.
    if "Access-Control-Allow-Credentials" in response.headers:
        del response.headers["Access-Control-Allow-Credentials"]
    if allowed:
        response.headers["Access-Control-Allow-Origin"] = origin
        response.headers.add_vary_header("Origin")
    elif "Access-Control-Allow-Origin" in response.headers:
        del response.headers["Access-Control-Allow-Origin"]
    return response
