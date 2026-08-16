from __future__ import annotations

import logging
import time
from collections.abc import Awaitable, Callable
from typing import Any
from uuid import uuid4

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from starlette.middleware.base import RequestResponseEndpoint
from starlette.responses import Response

from shepherd_rm.config import Settings, get_settings
from shepherd_rm.contract import load_openapi_contract
from shepherd_rm.database import check_database
from shepherd_rm.logging import configure_logging
from shepherd_rm.models import HealthResponse, Problem, ReadinessResponse

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

    async def default_readiness_check() -> None:
        await check_database(current_settings)

    check_readiness = readiness_check or default_readiness_check

    @app.middleware("http")
    async def correlation_id_middleware(
        request: Request,
        call_next: RequestResponseEndpoint,
    ) -> Response:
        started_at = time.perf_counter()
        supplied_correlation_id = request.headers.get("x-correlation-id")
        correlation_id = (
            supplied_correlation_id
            if supplied_correlation_id
            and len(supplied_correlation_id) <= 128
            and all(
                character.isalnum() or character in "-_." for character in supplied_correlation_id
            )
            else str(uuid4())
        )
        request.state.correlation_id = correlation_id
        response = await call_next(request)
        response.headers["x-correlation-id"] = correlation_id
        LOGGER.info(
            "request completed",
            extra={
                "correlation_id": correlation_id,
                "method": request.method,
                "path": request.url.path,
                "status_code": response.status_code,
                "duration_ms": round((time.perf_counter() - started_at) * 1000, 3),
            },
        )
        return response

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
