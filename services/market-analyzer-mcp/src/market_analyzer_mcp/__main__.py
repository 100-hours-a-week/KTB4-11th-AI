import logging

from ktb_core.logging import setup_logging
from mcp.server.transport_security import TransportSecuritySettings

from market_analyzer_mcp.server import build_server
from market_analyzer_mcp.settings import Settings


def main() -> None:
    settings = Settings()
    setup_logging(settings.log_level)
    logging.getLogger(__name__).info("market-analyzer-mcp started")
    build_server().run(
        transport="streamable-http",
        host=settings.host,
        port=settings.port,
        # The default allowlist only accepts Host: localhost, which would reject
        # portfolio-builder calling market-analyzer-mcp:8000. The server is never
        # published outside the Docker network.
        transport_security=TransportSecuritySettings(enable_dns_rebinding_protection=False),
    )


if __name__ == "__main__":
    main()
