from mcp.server import MCPServer
from mcp.server.transport_security import TransportSecuritySettings
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import JSONResponse, Response


def build_server() -> MCPServer:
    mcp = MCPServer("market-analyzer-mcp")

    @mcp.custom_route("/health", methods=["GET"])
    async def health(request: Request) -> Response:
        return JSONResponse({"status": "ok"})

    return mcp


def build_app(host: str) -> Starlette:
    # The SDK turns DNS-rebinding protection on when host is a loopback address, which
    # would reject portfolio-builder calling market-analyzer-mcp:8000. Pin it off: the
    # server is never published outside the Docker network.
    return build_server().streamable_http_app(
        host=host,
        transport_security=TransportSecuritySettings(enable_dns_rebinding_protection=False),
    )
