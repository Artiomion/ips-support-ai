"""Consistent public errors without internal details."""

from uuid import uuid4

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException


def install_error_handlers(app: FastAPI) -> None:
    def error(status: int, code: str, message: str) -> JSONResponse:
        return JSONResponse(
            status_code=status,
            content={"error": {"code": code, "message": message, "request_id": str(uuid4())}},
        )

    @app.exception_handler(RequestValidationError)
    async def validation_error(request: Request, exc: RequestValidationError) -> JSONResponse:
        return error(422, "validation_error", "Invalid request")

    @app.exception_handler(HTTPException)
    async def http_error(request: Request, exc: HTTPException) -> JSONResponse:
        return error(exc.status_code, f"http_{exc.status_code}", "Request failed")

    @app.exception_handler(Exception)
    async def internal_error(request: Request, exc: Exception) -> JSONResponse:
        return error(500, "internal_error", "Internal server error")
