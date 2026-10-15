import uvicorn
from ktb_core.logging import setup_logging

from market_http.app import create_app
from market_http.settings import Settings
from market_http.valkey import connect_valkey


def main() -> None:
    settings = Settings()
    setup_logging(settings.log_level, service_name="market-http")
    app = create_app(
        connect_valkey(settings),
        settings.questdb_conf,
        settings.docs_enabled,
        settings.docs_path_prefix,
    )
    uvicorn.run(app, host=settings.host, port=settings.port, log_config=None)
