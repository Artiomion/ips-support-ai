from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from sqlalchemy import create_engine, text
from sqlalchemy.exc import SQLAlchemyError

from ips.core.config import Settings
from ips.core.errors import install_error_handlers
from ips.platform.members import router as members_router
from ips.platform.router import router as auth_router
from ips.platform.widget_api import public_router, staff_router
from ips.platform.widget_cors import widget_cors
from ips.tickets.router import router as tickets_router
from ips.tickets.router import staff_router as tickets_staff_router


def create_app(settings: Settings | None = None, ports: object = None) -> FastAPI:
    settings = settings or Settings()
    app = FastAPI(title="IPS Support API")
    app.add_middleware(
        CORSMiddleware,
        allow_origins=[settings.web_origin],
        allow_credentials=True,
        allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"],
        allow_headers=["Content-Type", "X-CSRF-Token"],
    )
    install_error_handlers(app)
    app.state.ports = ports
    app.state.settings = settings
    app.include_router(auth_router)
    app.include_router(members_router)
    app.include_router(staff_router)
    app.include_router(public_router)
    app.include_router(tickets_router)
    app.include_router(tickets_staff_router)
    app.middleware("http")(widget_cors)
    engine = create_engine(
        settings.database_url, pool_pre_ping=True, connect_args={"connect_timeout": 2}
    )
    app.state.engine = engine

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
