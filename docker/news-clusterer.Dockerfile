FROM ghcr.io/astral-sh/uv:python3.13-bookworm-slim AS builder

ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy

WORKDIR /app

COPY pyproject.toml uv.lock ./
COPY packages/core/pyproject.toml packages/core/
COPY packages/market-analyzer/pyproject.toml packages/market-analyzer/
COPY services/news-preprocessor/pyproject.toml services/news-preprocessor/
COPY services/news-clusterer/pyproject.toml services/news-clusterer/
COPY services/portfolio-builder/pyproject.toml services/portfolio-builder/
RUN uv sync --package news-clusterer --locked --no-dev --no-install-workspace

COPY . .
RUN uv sync --package news-clusterer --locked --no-dev --no-editable

FROM python:3.13-slim-bookworm AS runtime

RUN useradd --create-home --uid 10001 app
WORKDIR /app

COPY --from=builder --chown=app:app /app/.venv /app/.venv
ENV PATH="/app/.venv/bin:$PATH"
EXPOSE 8000

USER app
CMD ["news-clusterer"]
