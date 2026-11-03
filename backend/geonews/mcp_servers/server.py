"""Expose GeoNews's tool registry as MCP servers.

  uv run python -m geonews.mcp_servers.server news            # stdio
  uv run python -m geonews.mcp_servers.server geo --http 8101  # streamable HTTP

Add to Claude Code:  claude mcp add geonews-news -- uv run python -m geonews.mcp_servers.server news
"""

import argparse
import inspect
import json
from typing import Annotated

from mcp.server.mcpserver import MCPServer
from pydantic_core import PydanticUndefined

from geonews.tools.registry import ToolSpec, execute, load_all

INSTRUCTIONS = {
    "news": "Search and read recent news stories. Cite article ids like [A123].",
    "geo": "Geocode places and find stories near a point.",
    "memory": "Read and update the current user's profile and preferences.",
}


def _wrap(spec: ToolSpec):
    """Build a function whose signature mirrors the tool's Pydantic input model, so MCPServer
    derives the same JSON schema the in-process agent uses."""
    params, annotations = [], {}
    for name, field in spec.input_model.model_fields.items():
        default = inspect.Parameter.empty if field.default is PydanticUndefined else field.default
        # Annotated[..., FieldInfo] carries descriptions and bounds into the MCP schema.
        annotation = Annotated[field.annotation, field]
        params.append(
            inspect.Parameter(name, inspect.Parameter.KEYWORD_ONLY, default=default,
                              annotation=annotation)
        )
        annotations[name] = annotation

    async def call(**kwargs) -> str:
        result = await execute(spec.name, kwargs)
        if result.is_error:
            raise RuntimeError(result.content)
        return result.content

    call.__signature__ = inspect.Signature(params, return_annotation=str)
    call.__annotations__ = {**annotations, "return": str}
    call.__name__ = spec.name
    return call


def build_server(name: str) -> MCPServer:
    server = MCPServer(name=f"geonews-{name}", instructions=INSTRUCTIONS[name])
    for spec in load_all().values():
        if spec.server == name:
            server.add_tool(_wrap(spec), name=spec.name, description=spec.description)
    return server


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("server", choices=sorted(INSTRUCTIONS))
    ap.add_argument("--http", type=int, help="serve streamable HTTP on this port")
    ap.add_argument("--list", action="store_true", help="print tool schemas and exit")
    args = ap.parse_args()
    server = build_server(args.server)
    if args.list:
        import asyncio

        tools = asyncio.run(server.list_tools())
        print(json.dumps([t.model_dump(exclude_none=True) for t in tools], indent=2))
    elif args.http:
        server.run("streamable-http", port=args.http)
    else:
        server.run("stdio")


if __name__ == "__main__":
    main()
