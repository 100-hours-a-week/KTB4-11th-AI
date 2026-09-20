"""FastAPI application for the news clusterer.

Deliberately thin at initialization: one health endpoint. The real clustering
endpoint arrives in the news-clusterer spec — and it matters, because
portfolio-builder blocks on it (see the spec's "one synchronous edge").
"""

from fastapi import FastAPI

app = FastAPI(title="news-clusterer")


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}
