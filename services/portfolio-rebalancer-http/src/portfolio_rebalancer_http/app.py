from fastapi import FastAPI

app = FastAPI(title="portfolio-rebalancer-http")


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}
