from mcp.server import MCPServer
from starlette.requests import Request
from starlette.responses import JSONResponse, Response


def build_server() -> MCPServer:
    mcp = MCPServer("market-analyzer-mcp")

    @mcp.custom_route("/health", methods=["GET"])
    async def health(request: Request) -> Response:
        return JSONResponse({"status": "ok"})

    return mcp
