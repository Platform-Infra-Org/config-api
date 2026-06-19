"""Translate escaped coordinate-validation errors into HTTP 422.

When a Pydantic model is consumed as a ``Depends()`` parameter (the coordinate
models on the ``/config`` and ``/naming`` routes), FastAPI constructs it
directly. A ``field_validator`` that raises ``ValueError`` — e.g. a coordinate
outside its ``LIVE_ALLOWED_*`` allowlist — surfaces as a raw
``pydantic.ValidationError`` that escapes uncaught and becomes a 500. FastAPI
only auto-wraps its *own* query-param parsing (types, requiredness) into a
``RequestValidationError``/422, not validators on a depended-on model.

This module registers a handler that re-emits such errors through FastAPI's
standard request-validation path, so a rejected coordinate yields the same 422
shape as any other invalid query parameter.

This is safe to register app-wide: ``ResponseValidationError`` (server-side
response-model failures, which *should* stay 500) is not a subclass of
``pydantic.ValidationError``, so it is never caught here.
"""
from fastapi import FastAPI, Request
from fastapi.exception_handlers import request_validation_exception_handler
from fastapi.exceptions import RequestValidationError
from fastapi.responses import Response
from pydantic import ValidationError


def install_coordinate_validation_error_handler(app: FastAPI) -> None:
    """Register the ``pydantic.ValidationError`` -> 422 handler on ``app``."""

    @app.exception_handler(ValidationError)
    async def _coordinate_validation_handler(request: Request, exc: ValidationError) -> Response:
        # Prepend "query" to each location so the response body is identical to a
        # native FastAPI query-parameter validation error (which uses
        # ("query", <name>) locations rather than the bare (<name>,) pydantic emits).
        errors = []
        for err in exc.errors():
            err = dict(err)
            err["loc"] = ("query", *err.get("loc", ()))
            errors.append(err)
        return await request_validation_exception_handler(
            request, RequestValidationError(errors)
        )
