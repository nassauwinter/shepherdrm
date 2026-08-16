import uvicorn

from shepherd_rm.application import create_app
from shepherd_rm.config import get_settings

app = create_app()


def run() -> None:
    settings = get_settings()
    uvicorn.run(
        "shepherd_rm.main:app",
        host=settings.host,
        port=settings.port,
        log_config=None,
    )
