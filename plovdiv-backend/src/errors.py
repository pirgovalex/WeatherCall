"""Uniform error envelope: {"error": {"code": "...", "message": "..."}}."""

from __future__ import annotations

import logging

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

log = logging.getLogger("plovdiv.api")

_HTTP_CODES = {
    404: "NOT_FOUND",
    405: "METHOD_NOT_ALLOWED",
    422: "VALIDATION_ERROR",
    503: "SERVICE_UNAVAILABLE",
}


class ApiError(Exception):
    """An error that is reported to the client as-is."""

    def __init__(self, status_code: int, code: str, message: str) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.code = code
        self.message = message


def not_found(message: str) -> ApiError:
    return ApiError(404, "NOT_FOUND", message)


def invalid(message: str) -> ApiError:
    return ApiError(422, "VALIDATION_ERROR", message)


def unavailable(message: str) -> ApiError:
    return ApiError(503, "SERVICE_UNAVAILABLE", message)


def envelope(status_code: int, code: str, message: str) -> JSONResponse:
    return JSONResponse(status_code=status_code, content={"error": {"code": code, "message": message}})


def _short(text: str, limit: int = 200) -> str:
    return text if len(text) <= limit else text[: limit - 3] + "..."


def register_error_handlers(app: FastAPI) -> None:
    @app.exception_handler(ApiError)
    async def handle_api_error(_: Request, exc: ApiError) -> JSONResponse:
        return envelope(exc.status_code, exc.code, exc.message)

    @app.exception_handler(RequestValidationError)
    async def handle_validation_error(_: Request, exc: RequestValidationError) -> JSONResponse:
        problems = []
        for err in exc.errors()[:3]:
            location = ".".join(str(part) for part in err.get("loc", ()) if part != "query")
            problems.append(f"{location}: {err.get('msg', 'invalid value')}")
        return envelope(422, "VALIDATION_ERROR", _short("; ".join(problems) or "Invalid request parameters."))

    @app.exception_handler(StarletteHTTPException)
    async def handle_http_exception(_: Request, exc: StarletteHTTPException) -> JSONResponse:
        code = _HTTP_CODES.get(exc.status_code, "HTTP_ERROR")
        return envelope(exc.status_code, code, _short(str(exc.detail)))

    @app.exception_handler(Exception)
    async def handle_unexpected_error(_: Request, exc: Exception) -> JSONResponse:
        log.exception("Unhandled error while serving a request", exc_info=exc)
        return envelope(500, "INTERNAL_ERROR", "Internal server error.")
