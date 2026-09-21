"""FastAPI application for the news clusterer."""

from fastapi import FastAPI

app = FastAPI(title="news-clusterer")


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}
