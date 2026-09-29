FROM ghcr.io/astral-sh/uv:python3.13-bookworm-slim AS builder

ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    VIRTUAL_ENV=/app/.venv

WORKDIR /app
RUN uv venv "$VIRTUAL_ENV"

COPY docker/requirements/market-syncer.txt ./requirements.txt
RUN uv pip install --require-hashes --requirement requirements.txt

COPY pyproject.toml ./
COPY packages/core packages/core
COPY services/market-syncer services/market-syncer
RUN uv pip install --no-deps ./packages/core ./services/market-syncer

FROM python:3.13-slim-bookworm AS runtime

RUN useradd --create-home --uid 10001 app
# OpenDartReader writes docs_cache/ into the working directory, so it must be writable by app.
WORKDIR /tmp

COPY --from=builder --chown=app:app /app/.venv /app/.venv
ENV PATH="/app/.venv/bin:$PATH"

USER app
CMD ["market-syncer"]
