FROM ghcr.io/astral-sh/uv:python3.13-bookworm-slim AS builder

ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    VIRTUAL_ENV=/app/.venv

WORKDIR /app
RUN uv venv "$VIRTUAL_ENV"

COPY docker/requirements/market-analyzer-mcp.txt ./requirements.txt
RUN uv pip install --require-hashes --requirement requirements.txt

COPY pyproject.toml ./
COPY packages/core packages/core
COPY packages/market-analyzer packages/market-analyzer
COPY services/market-analyzer-mcp services/market-analyzer-mcp
RUN uv pip install --no-deps ./packages/core ./packages/market-analyzer \
    ./services/market-analyzer-mcp

FROM python:3.13-slim-bookworm AS runtime

RUN useradd --create-home --uid 10001 app
WORKDIR /app

COPY --from=builder --chown=app:app /app/.venv /app/.venv
ENV PATH="/app/.venv/bin:$PATH"

USER app
EXPOSE 8010
CMD ["market-analyzer-mcp"]
