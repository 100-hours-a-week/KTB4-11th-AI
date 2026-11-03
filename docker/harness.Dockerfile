FROM ghcr.io/astral-sh/uv:python3.14-bookworm-slim AS builder

ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    VIRTUAL_ENV=/app/.venv

WORKDIR /app
RUN uv venv "$VIRTUAL_ENV"

COPY docker/requirements/harness.txt ./requirements.txt
RUN uv pip install --require-hashes --requirement requirements.txt

COPY pyproject.toml ./
COPY packages/core packages/core
COPY evaluations/harness evaluations/harness
RUN uv pip install --no-deps ./packages/core ./evaluations/harness

FROM python:3.14-slim-bookworm AS runtime

RUN useradd --create-home --uid 10001 app
WORKDIR /app

COPY --from=builder --chown=app:app /app/.venv /app/.venv
ENV PATH="/app/.venv/bin:$PATH"

FROM runtime AS agent

ARG DOCKER_AGENT_URL
ARG DOCKER_AGENT_SHA256
ADD --checksum=sha256:${DOCKER_AGENT_SHA256} --chmod=755 ${DOCKER_AGENT_URL} /usr/local/bin/docker-agent

USER app
EXPOSE 8200
CMD ["harness-agent-worker"]

FROM runtime AS harness

USER app
EXPOSE 8000 8100
CMD ["harness", "run"]
