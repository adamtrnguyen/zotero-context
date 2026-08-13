from __future__ import annotations

import asyncio
import json
from typing import Any

from .models import to_jsonable
from .service import ZoteroContext


def run() -> None:
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass


async def main() -> None:
    try:
        import mcp.types as types
        from mcp.server import NotificationOptions, Server
        from mcp.server.models import InitializationOptions
        from mcp.server.stdio import stdio_server
    except ImportError as exc:
        raise SystemExit(
            "The MCP adapter requires the optional dependency: uv sync --extra mcp"
        ) from exc

    server = Server("zotero-context")
    ctx = ZoteroContext()

    @server.list_tools()
    async def list_tools() -> list[types.Tool]:
        return [
            types.Tool(
                name="get_zotero_window_state",
                description="Return current Zotero selected collection, selected items, "
                "tabs, readers, and reader positions.",
                inputSchema={"type": "object", "properties": {}, "required": []},
            ),
            types.Tool(
                name="get_zotero_active_reader",
                description="Return the active Zotero reader tab, optionally with annotations.",
                inputSchema={
                    "type": "object",
                    "properties": {
                        "include_annotations": {"type": "boolean", "default": False},
                        "annotation_types": {
                            "type": "array",
                            "items": {"type": "string"},
                            "description": "Optional filter, e.g. ['highlight', 'note']",
                        },
                    },
                    "required": [],
                },
            ),
            types.Tool(
                name="get_zotero_open_readers",
                description="Return all open Zotero reader tabs, optionally with annotations.",
                inputSchema={
                    "type": "object",
                    "properties": {
                        "include_annotations": {"type": "boolean", "default": False},
                        "annotation_types": {
                            "type": "array",
                            "items": {"type": "string"},
                        },
                    },
                    "required": [],
                },
            ),
            types.Tool(
                name="get_zotero_annotations",
                description="Return text highlights and comments for a Zotero attachment key.",
                inputSchema={
                    "type": "object",
                    "properties": {
                        "attachment_key": {"type": "string"},
                        "annotation_types": {
                            "type": "array",
                            "items": {"type": "string"},
                        },
                        "include_text": {"type": "boolean", "default": True},
                        "include_comments": {"type": "boolean", "default": True},
                    },
                    "required": ["attachment_key"],
                },
            ),
            types.Tool(
                name="resolve_zotero_pdf",
                description="Resolve a Better BibTeX citekey, parent item key, or PDF "
                "attachment key to Zotero parent/PDF keys.",
                inputSchema={
                    "type": "object",
                    "properties": {
                        "identifier": {"type": "string"},
                        "is_attachment_key": {"type": "boolean", "default": False},
                    },
                    "required": ["identifier"],
                },
            ),
            types.Tool(
                name="get_zotero_sources",
                description="Return Zotero PDF sources that have annotations.",
                inputSchema={"type": "object", "properties": {}, "required": []},
            ),
        ]

    @server.call_tool()
    async def call_tool(name: str, arguments: dict[str, Any]) -> list[types.TextContent]:
        try:
            payload = call_core(ctx, name, arguments or {})
            text = json.dumps(to_jsonable(payload), ensure_ascii=False, indent=2)
        except Exception as exc:
            text = json.dumps({"ok": False, "error": str(exc)}, ensure_ascii=False, indent=2)
        return [types.TextContent(type="text", text=text)]

    async with stdio_server() as (read_stream, write_stream):
        await server.run(
            read_stream,
            write_stream,
            InitializationOptions(
                server_name="zotero-context",
                server_version="0.1.0",
                capabilities=server.get_capabilities(
                    notification_options=NotificationOptions(),
                    experimental_capabilities={},
                ),
            ),
        )


def call_core(ctx: ZoteroContext, name: str, arguments: dict[str, Any]) -> Any:
    if name == "get_zotero_window_state":
        return ctx.get_window_state()
    if name == "get_zotero_active_reader":
        contexts = ctx.get_open_reader_context(
            active_only=True,
            include_annotations=bool(arguments.get("include_annotations", False)),
            annotation_types=parse_types(arguments.get("annotation_types")),
        )
        return contexts[0] if contexts else None
    if name == "get_zotero_open_readers":
        return ctx.get_open_reader_context(
            include_annotations=bool(arguments.get("include_annotations", False)),
            annotation_types=parse_types(arguments.get("annotation_types")),
        )
    if name == "get_zotero_annotations":
        attachment_key = str(arguments.get("attachment_key") or "")
        if not attachment_key:
            raise ValueError("attachment_key is required")
        return ctx.get_annotations(
            attachment_key,
            types=parse_types(arguments.get("annotation_types")),
            include_text=bool(arguments.get("include_text", True)),
            include_comments=bool(arguments.get("include_comments", True)),
        )
    if name == "resolve_zotero_pdf":
        identifier = str(arguments.get("identifier") or "")
        if not identifier:
            raise ValueError("identifier is required")
        parent_key, attachment_key = ctx.resolve_pdf_attachment_key(
            identifier,
            is_attachment_key=bool(arguments.get("is_attachment_key", False)),
        )
        return {"parent_key": parent_key, "attachment_key": attachment_key}
    if name == "get_zotero_sources":
        return ctx.get_sources_with_annotations()
    raise ValueError(f"Unknown tool: {name}")


def parse_types(value: Any) -> set[str] | None:
    if not value:
        return None
    if isinstance(value, str):
        return {part.strip() for part in value.split(",") if part.strip()}
    if isinstance(value, list):
        return {str(part) for part in value if str(part).strip()}
    return None
