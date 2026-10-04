FROM ghcr.io/astral-sh/uv:python3.13-bookworm-slim AS builder

ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    VIRTUAL_ENV=/app/.venv

WORKDIR /app
RUN uv venv "$VIRTUAL_ENV"

COPY docker/requirements/app.txt ./requirements.txt
RUN uv pip install --require-hashes --requirement requirements.txt

COPY pyproject.toml ./
COPY packages packages
COPY services services
RUN uv pip install --no-deps \
    ./packages/core \
    ./packages/market-analyzer \
    ./services/market-collector \
    ./services/market-syncer \
    ./services/news-clusterer \
    ./services/news-graph-builder \
    ./services/news-preprocessor \
    ./services/portfolio-builder \
    ./services/portfolio-rebalancer

FROM python:3.13-slim-bookworm AS runtime

RUN useradd --create-home --uid 10001 app
WORKDIR /app

COPY --from=builder --chown=app:app /app/.venv /app/.venv
COPY --chown=app:app alembic.ini ./
COPY --chown=app:app infrastructure infrastructure
ENV PATH="/app/.venv/bin:$PATH"

USER app
EXPOSE 8000
CMD ["portfolio-rebalancer"]
