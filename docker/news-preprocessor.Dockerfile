FROM ghcr.io/astral-sh/uv:python3.13-bookworm-slim AS builder

ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    VIRTUAL_ENV=/app/.venv

WORKDIR /app
RUN uv venv "$VIRTUAL_ENV"

COPY docker/requirements/news-preprocessor.txt ./requirements.txt
RUN uv pip install --require-hashes --requirement requirements.txt

COPY pyproject.toml ./
COPY packages/core packages/core
COPY services/news-preprocessor services/news-preprocessor
RUN uv pip install --no-deps ./packages/core ./services/news-preprocessor

FROM public.ecr.aws/lambda/python:3.13 AS lambda

COPY --from=ghcr.io/astral-sh/uv:0.9.9 /uv /usr/local/bin/uv

COPY docker/requirements/news-preprocessor.txt ./requirements.txt
RUN uv pip install --system --target "${LAMBDA_TASK_ROOT}" \
    --require-hashes --requirement requirements.txt

COPY pyproject.toml ./
COPY packages/core packages/core
COPY services/news-preprocessor services/news-preprocessor
RUN uv pip install --system --target "${LAMBDA_TASK_ROOT}" --no-deps \
    ./packages/core ./services/news-preprocessor \
    && rm -rf pyproject.toml packages services requirements.txt

CMD ["news_preprocessor.handler.handler"]

FROM python:3.13-slim-bookworm AS runtime

RUN useradd --create-home --uid 10001 app
WORKDIR /app

COPY --from=builder --chown=app:app /app/.venv /app/.venv
ENV PATH="/app/.venv/bin:$PATH"

USER app
CMD ["news-preprocessor"]
