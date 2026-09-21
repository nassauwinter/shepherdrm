FROM python:3.12-slim@sha256:78387bc3881b8273120a12ebe6c1ab22b018ccc2c9adf565ae1ac9b536e184ea AS builder

COPY --from=ghcr.io/astral-sh/uv:0.12.5@sha256:e85be844203885286c60ffad8a858d48afb6c5a5c237ca0e67f12e74b8f174b1 /uv /uvx /bin/

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

RUN uv sync --locked --no-editable

FROM python:3.12-slim@sha256:78387bc3881b8273120a12ebe6c1ab22b018ccc2c9adf565ae1ac9b536e184ea AS runtime

ARG DEBIAN_FRONTEND=noninteractive
ARG SHEPHERD_BUILD_VERSION=0.1.0
ARG SHEPHERD_BUILD_REVISION=unknown

LABEL org.opencontainers.image.title="Shepherd RM" \
    org.opencontainers.image.source="https://github.com/nassauwinter/shepherdrm" \
    org.opencontainers.image.version="${SHEPHERD_BUILD_VERSION}" \
    org.opencontainers.image.revision="${SHEPHERD_BUILD_REVISION}"

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    SHEPHERD_BUILD_REVISION="${SHEPHERD_BUILD_REVISION}"

WORKDIR /app

COPY --from=builder /app/.venv /app/.venv
COPY openapi ./openapi
COPY alembic.ini ./
COPY migrations ./migrations

RUN apt-get update \
    && apt-get upgrade --yes \
    && rm -rf /var/lib/apt/lists/* \
    && useradd --create-home --uid 10001 shepherd \
    && install --directory --owner shepherd --group shepherd /var/lib/shepherd-rm

ENV PATH="/app/.venv/bin:$PATH"

USER shepherd
EXPOSE 8000

CMD ["shepherd-rm"]
