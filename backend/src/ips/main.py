from uuid import uuid4

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from sqlalchemy import create_engine, text
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import sessionmaker
from starlette.concurrency import run_in_threadpool

from ips.conversations.router import public_router as conversations_public_router
from ips.conversations.router import staff_router as conversations_staff_router
from ips.core.composition import default_ports
from ips.core.config import Settings
from ips.core.errors import install_error_handlers
from ips.core.rate_limit import PostgresRateLimiter, request_category
from ips.core.usage import PostgresUsageBudget
from ips.knowledge.router import router as knowledge_router
from ips.platform.audit import router as audit_router
from ips.platform.members import router as members_router
from ips.platform.router import router as auth_router
from ips.platform.settings import router as settings_router
from ips.platform.widget_api import public_router, staff_router
from ips.platform.widget_cors import widget_cors
from ips.tickets.router import router as tickets_router
from ips.tickets.router import staff_router as tickets_staff_router


def create_app(settings: Settings | None = None, ports: object = None) -> FastAPI:
    settings = settings or Settings()
    app = FastAPI(title="IPS Support API")
    install_error_handlers(app)
    app.state.ports = ports if ports is not None else default_ports()
    app.state.settings = settings
    app.include_router(audit_router)
    app.include_router(settings_router)
    app.include_router(auth_router)
    app.include_router(members_router)
    app.include_router(staff_router)
    app.include_router(public_router)
    app.include_router(tickets_router)
    app.include_router(tickets_staff_router)
    app.include_router(conversations_public_router)
    app.include_router(conversations_staff_router)
    app.include_router(knowledge_router)
    engine = create_engine(
        settings.database_url, pool_pre_ping=True, connect_args={"connect_timeout": 2}
    )
    app.state.engine = engine
    sessions = sessionmaker(bind=engine)
    app.state.rate_limiter = PostgresRateLimiter(sessions, secret=settings.session_secret)
    app.state.usage_budget = PostgresUsageBudget(sessions)
    if ports is None:
        app.state.ports.usage_budget = app.state.usage_budget

    @app.middleware("http")
    async def limit_public_requests(request: Request, call_next):
        category = request_category(request.method, request.url.path)
        if category:
            try:
                allowed = await run_in_threadpool(
                    app.state.rate_limiter.allow,
                    category,
                    request.client.host if request.client else "unknown",
                )
            except SQLAlchemyError:
                return JSONResponse(
                    status_code=503,
                    content={
                        "error": {
                            "code": "http_503",
                            "message": "Request failed",
                            "request_id": str(uuid4()),
                        }
                    },
                )
            if not allowed:
                return JSONResponse(
                    status_code=429,
                    content={
                        "error": {
                            "code": "http_429",
                            "message": "Request failed",
                            "request_id": str(uuid4()),
                        }
                    },
                )
        return await call_next(request)

    app.add_middleware(
        CORSMiddleware,
        allow_origins=[settings.web_origin],
        allow_credentials=True,
        allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"],
        allow_headers=["Content-Type", "X-CSRF-Token"],
    )
    app.middleware("http")(widget_cors)

    @app.get("/health/live")
    def live() -> dict[str, str]:
        return {"status": "ok"}

    @app.get("/health/ready")
    def ready() -> JSONResponse:
        try:
            with engine.connect() as connection:
                connection.execute(text("SELECT 1"))
        except SQLAlchemyError:
            return JSONResponse(status_code=503, content={"status": "unavailable"})
        return JSONResponse(content={"status": "ok"})

    return app


app = create_app()
