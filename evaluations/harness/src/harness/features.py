from typing import Annotated

import httpx
import uvicorn
from fastapi import FastAPI, HTTPException, Path
from ktb_core.logging import setup_logging

from harness.information import FEATURE_BARS, technicals
from harness.market_http import parse_bar
from harness.settings import FeaturesSettings


def create_features_app(market: httpx.Client, public_url: str) -> FastAPI:
    app = FastAPI(
        title="harness-features",
        description="Technical features computed only from the point-in-time market-http.",
        servers=[{"url": public_url}],
        docs_url=None,
        redoc_url=None,
    )

    @app.get(
        "/technicals/{code}",
        operation_id="get_technicals",
        summary="Technical features for one stock at the decision time",
        description="Returns, 20-day volatility, moving-average gaps, RSI and volume ratio from"
        " daily bars whose close is at or before the decision time. Null means too few bars.",
    )
    def get_technicals(
        code: Annotated[str, Path(pattern=r"^[0-9A-Z]{6}$")],
    ) -> dict[str, float | None]:
        try:
            response = market.get(
                f"/bars/{code}", params={"timeframe": "1d", "limit": FEATURE_BARS}
            )
            response.raise_for_status()
        except httpx.HTTPError as error:
            raise HTTPException(502, f"market data unavailable: {error}") from error
        return technicals([parse_bar(item) for item in reversed(response.json()["items"])])

    @app.get("/health", include_in_schema=False)
    def health() -> dict[str, str]:
        return {"status": "ok"}

    return app


def main() -> None:
    settings = FeaturesSettings()
    setup_logging(settings.log_level, service_name="harness-features")
    with httpx.Client(base_url=settings.market_url, timeout=settings.timeout_seconds) as market:
        uvicorn.run(
            create_features_app(market, settings.public_url),
            host=settings.host,
            port=settings.port,
            log_config=None,
        )
