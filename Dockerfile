FROM python:3.12-slim

COPY --from=ghcr.io/astral-sh/uv:0.12.5 /uv /uvx /bin/

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_NO_DEV=1

WORKDIR /app

COPY pyproject.toml uv.lock README.md LICENSE ./
COPY src ./src
COPY openapi ./openapi
COPY alembic.ini ./
COPY migrations ./migrations

RUN uv sync --locked --no-editable \
    && useradd --create-home --uid 10001 shepherd

ENV PATH="/app/.venv/bin:$PATH"

USER shepherd
EXPOSE 8000

CMD ["shepherd-rm"]
