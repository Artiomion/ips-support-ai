"""Prepare two synthetic demo tenants through the supported HTTP API."""

import json
import os
from http.cookiejar import CookieJar
from urllib.error import HTTPError
from urllib.request import HTTPCookieProcessor, ProxyHandler, Request, build_opener


def seed_demo(api_url, web_origin, password):
    if not password:
        raise SystemExit("Set IPS_DEMO_PASSWORD for both demo accounts")
    seeded = []
    for name, slug, color, policy in (
        ("Альфа", "alpha", "#2457b8", "Альфа принимает возврат в течение 14 дней."),
        ("Бета", "beta", "#923348", "Бета принимает возврат в течение 30 дней."),
    ):
        seeded.append(seed_company(api_url, web_origin, password, name, slug, color, policy))
    return seeded


def seed_company(api_url, web_origin, password, name, slug, color, policy):
    opener = build_opener(ProxyHandler({}), HTTPCookieProcessor(CookieJar()))
    headers = {"Origin": web_origin, "Content-Type": "application/json"}
    email = f"admin-{slug}@example.org"

    def call(endpoint, data=None):
        request = Request(
            f"{api_url.rstrip('/')}/api/v1/{endpoint}",
            headers=headers,
            data=json.dumps(data).encode() if data is not None else None,
        )
        with opener.open(request, timeout=10) as response:
            return json.load(response)

    def ensure(endpoint, field, value, data):
        page = call(f"{endpoint}?limit=100")
        existing = next((item for item in page["items"] if item[field] == value), None)
        return existing if existing is not None else call(endpoint, {field: value, **data})

    credentials = {"email": email, "password": password}
    try:
        session = call("auth/register", {**credentials, "organization_name": name})
    except HTTPError as error:
        if error.code != 409:
            raise
        session = call("auth/login", credentials)
    headers["X-CSRF-Token"] = session["csrf_token"]
    section = ensure("knowledge/sections", "name", f"{name} · Справка", {})
    for title, visibility, body in (
        (f"{name} · Возврат", "public", policy),
        (f"{name} · Инструкция поддержки", "internal", "Учебная внутренняя инструкция."),
    ):
        ensure(
            "knowledge/articles",
            "title",
            title,
            {
                "section_id": section["id"],
                "visibility": visibility,
                "body": body,
                "status": "published",
            },
        )
    widget = ensure(
        "widgets",
        "name",
        f"{name} · Поддержка",
        {
            "color": color,
            "greeting": f"Здравствуйте! Это поддержка компании «{name}».",
            "allowed_origins": [web_origin],
            "section_ids": [section["id"]],
        },
    )
    return {"company": name, "email": email, "public_key": widget["public_key"]}


if __name__ == "__main__":
    try:
        print(
            json.dumps(
                seed_demo(
                    os.getenv("IPS_DEMO_API_URL", "http://api:8000"),
                    os.getenv("WEB_ORIGIN", "http://localhost:5173"),
                    os.getenv("IPS_DEMO_PASSWORD", ""),
                ),
                ensure_ascii=False,
                indent=2,
            )
        )
    except HTTPError as error:
        raise SystemExit(f"Demo seed failed: HTTP {error.code}") from None
