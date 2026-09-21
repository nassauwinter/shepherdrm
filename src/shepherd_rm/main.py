"""Expose the production ASGI application and console-server entry point."""

import logging

import uvicorn

from shepherd_rm import __version__
from shepherd_rm.application import create_app
from shepherd_rm.config import get_settings
from shepherd_rm.observability import build_revision

app = create_app()
LOGGER = logging.getLogger(__name__)


def run() -> None:
    settings = get_settings()
    LOGGER.info(
        "service starting",
        extra={"version": __version__, "revision": build_revision()},
    )
    uvicorn.run(
        "shepherd_rm.main:app",
        host=settings.host,
        port=settings.port,
        log_config=None,
    )
