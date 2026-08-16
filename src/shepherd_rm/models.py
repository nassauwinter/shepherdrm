from typing import Literal

from pydantic import BaseModel, ConfigDict


class HealthResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    status: Literal["ok"] = "ok"


class ReadinessResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    status: Literal["ready"] = "ready"


class Problem(BaseModel):
    type: str = "about:blank"
    title: str
    status: int
    detail: str
    instance: str | None = None
    correlation_id: str
