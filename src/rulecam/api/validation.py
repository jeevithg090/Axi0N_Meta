"""422 answers that never crash on the request's own bytes.

FastAPI's default `RequestValidationError` handler echoes each failing input back, and encodes a
raw `bytes` body with `bytes.decode()`. A JSON route that receives a non-UTF-8 body under a
non-JSON content type (any client can send one) then raised `UnicodeDecodeError` inside the error
handler and answered 500 instead of 422 (QA, tests/e2e/test_api_surface.py). This handler is the
default one with bytes decoded leniently.
"""

from __future__ import annotations

from fastapi import FastAPI, Request, status
from fastapi.encoders import jsonable_encoder
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse


def _text(data: bytes) -> str:
    return data.decode("utf-8", errors="replace")


async def request_validation_error(_: Request, exc: Exception) -> JSONResponse:
    """FastAPI's 422 body (`{"detail": [errors]}`), with any bytes input decoded leniently."""
    errors = exc.errors() if isinstance(exc, RequestValidationError) else []
    return JSONResponse(
        status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
        content={"detail": jsonable_encoder(errors, custom_encoder={bytes: _text})},
    )


def install_validation_handler(app: FastAPI) -> None:
    """Answer every request validation failure with `request_validation_error`."""
    app.add_exception_handler(RequestValidationError, request_validation_error)
