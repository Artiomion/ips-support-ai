from fastapi import FastAPI
from fastapi.responses import JSONResponse
from sqlalchemy import create_engine, text
from sqlalchemy.exc import SQLAlchemyError

from ips.core.config import Settings
from ips.core.errors import install_error_handlers


def create_app(settings: Settings | None = None, ports: object = None) -> FastAPI:
    settings = settings or Settings()
    app = FastAPI(title="IPS Support API")
    install_error_handlers(app)
    app.state.ports = ports
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
