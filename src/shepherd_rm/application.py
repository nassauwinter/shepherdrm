"""Assemble the FastAPI application, middleware, and operational endpoints."""

from __future__ import annotations

import logging
import time
from collections.abc import Awaitable, Callable
from typing import Any
from uuid import uuid4

from fastapi import FastAPI, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from fastapi.routing import APIRoute
from starlette.middleware.base import RequestResponseEndpoint
from starlette.responses import Response

from shepherd_rm.catalog import build_catalog_router
from shepherd_rm.config import Settings, get_settings
from shepherd_rm.contract import load_openapi_contract
from shepherd_rm.database import check_database
from shepherd_rm.identity.api import build_identity_router
from shepherd_rm.leasing import build_leasing_router
from shepherd_rm.logging import configure_logging
from shepherd_rm.models import HealthResponse, Problem, ReadinessResponse
from shepherd_rm.request_context import (
    reset_correlation_id,
    select_correlation_id,
    set_correlation_id,
)
from shepherd_rm.request_limits import RequestBodyLimitMiddleware
from shepherd_rm.resource_secrets import build_resource_secrets_router

ReadinessCheck = Callable[[], Awaitable[None]]
LOGGER = logging.getLogger(__name__)


class ContractFastAPI(FastAPI):
    def __init__(self, contract: dict[str, Any]) -> None:
        super().__init__(
            title="Shepherd RM API",
            version="0.1.0",
            docs_url="/docs",
            openapi_url="/openapi.json",
        )
        self.contract = contract

    def openapi(self) -> dict[str, Any]:
        return self.contract


def create_app(
    settings: Settings | None = None,
    readiness_check: ReadinessCheck | None = None,
) -> FastAPI:
    current_settings = settings or get_settings()
    configure_logging(current_settings.log_level)

    contract = load_openapi_contract(current_settings.openapi_path)
    app = ContractFastAPI(contract)
    app.include_router(build_identity_router(current_settings))
    app.include_router(build_catalog_router(current_settings))
    app.include_router(build_leasing_router(current_settings))
    app.include_router(build_resource_secrets_router(current_settings))

    async def default_readiness_check() -> None:
        await check_database(current_settings)

    check_readiness = readiness_check or default_readiness_check

    @app.exception_handler(HTTPException)
    async def http_problem(request: Request, error: HTTPException) -> JSONResponse:
        correlation_id = getattr(request.state, "correlation_id", str(uuid4()))
        problem = Problem(
            title="Request failed",
            status=error.status_code,
            detail=str(error.detail),
            correlation_id=correlation_id,
        )
        headers = error.headers or {}
        return JSONResponse(
            status_code=error.status_code,
            content=problem.model_dump(exclude_none=True),
            headers=headers,
            media_type="application/problem+json",
        )

    @app.exception_handler(RequestValidationError)
    async def validation_problem(request: Request, _error: RequestValidationError) -> JSONResponse:
        """Return validation failures without reflecting potentially secret input."""
        correlation_id = getattr(request.state, "correlation_id", str(uuid4()))
        problem = Problem(
            title="Request validation failed",
            status=422,
            detail="The request did not satisfy the API contract",
            correlation_id=correlation_id,
        )
        return JSONResponse(
            status_code=422,
            content=problem.model_dump(exclude_none=True),
            media_type="application/problem+json",
        )

    @app.middleware("http")
    async def correlation_id_middleware(
        request: Request,
        call_next: RequestResponseEndpoint,
    ) -> Response:
        started_at = time.perf_counter()
        correlation_id = select_correlation_id(request.headers.get("x-correlation-id"))
        request.state.correlation_id = correlation_id
        context_token = set_correlation_id(correlation_id)
        try:
            response = await call_next(request)
        finally:
            reset_correlation_id(context_token)
        response.headers["x-correlation-id"] = correlation_id
        if request.url.path.endswith("/access") and "/secrets/" in request.url.path:
            response.headers["Cache-Control"] = "no-store"
            response.headers["Pragma"] = "no-cache"
        route = request.scope.get("route")
        logged_path = route.path_format if isinstance(route, APIRoute) else "<unmatched>"
        LOGGER.info(
            "request completed",
            extra={
                "correlation_id": correlation_id,
                "method": request.method,
                "path": logged_path,
                "status_code": response.status_code,
                "duration_ms": round((time.perf_counter() - started_at) * 1000, 3),
            },
        )
        return response

    app.add_middleware(
        RequestBodyLimitMiddleware,
        max_body_bytes=current_settings.max_request_body_bytes,
    )

    @app.get(
        "/health",
        operation_id="getHealth",
        response_model=HealthResponse,
        tags=["Operations"],
    )
    async def get_health() -> HealthResponse:
        return HealthResponse()

    @app.get(
        "/ready",
        operation_id="getReadiness",
        response_model=ReadinessResponse,
        responses={503: {"model": Problem}},
        tags=["Operations"],
    )
    async def get_readiness(request: Request) -> ReadinessResponse | JSONResponse:
        try:
            await check_readiness()
        except Exception:
            correlation_id = request.state.correlation_id
            LOGGER.warning(
                "readiness check failed",
                extra={"correlation_id": correlation_id},
            )
            problem = Problem(
                title="Service unavailable",
                status=503,
                detail="PostgreSQL is unavailable or its schema is incompatible",
                correlation_id=correlation_id,
            )
            return JSONResponse(
                status_code=503,
                content=problem.model_dump(exclude_none=True),
                media_type="application/problem+json",
            )
        return ReadinessResponse()

    return app
