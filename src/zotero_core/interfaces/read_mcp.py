"""The read tools.

ONE TABLE, NOT TWO LISTS
------------------------
Tools are declared once, in `TOOLS`, and both the schema and the dispatch derive from
it. This adapter used to be a 3-place change per tool -- a `types.Tool(...)` literal
inside `list_tools()`, an `if name == ...` branch in `call_core()`, and hand-written
`arguments.get()` coercion inside that branch -- with nothing checking that the two
lists agreed about names, and no rejection of undeclared arguments: a misspelled
`include_annotaions` was silently dropped and the caller got the default.

The sibling write adapter solved this at 17 tools and said why: "two copies of one fact
is the defect this package was built to end." The read adapter had 6 tools and never got
the refactor, which is also why it grew none.

WHAT IS NEW HERE (2026-08-19)
-----------------------------
Five tools that were implemented and unreachable. `read/items.py` and
`read/duplicates.py` had SEVEN public methods between them and not one was exposed from
CLI or MCP, so an agent could not read an item's type, fields, creators or tags, and
could not ask "is this already in the library" without attempting a create and parsing
the refusal.

Still missing, and not fixable here: collection reads and library search. Neither
exists in `read/` yet; until they do those come from cookjohn's third-party plugin.
"""

from __future__ import annotations

import asyncio
import json
import os
import sqlite3
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from ..domain.entities import to_jsonable
from ..read.annotations import ZoteroAnnotationError
from ..read.bridge import ZoteroBridgeError
from ..read.service import ZoteroContext

SERVER_NAME = "zotero-context"

# `annotations.ANNOTATION_TYPE` is the authority; repeated here as a schema `enum` so the
# vocabulary is DISCOVERABLE. The old description advertised only "['highlight', 'note']"
# as an example, which left types 3-6 undiscoverable -- a caller had to guess them.
ANNOTATION_TYPE_NAMES = ("highlight", "note", "image", "ink", "underline", "text")

_CTX: ZoteroContext | None = None


def context() -> ZoteroContext:
    """The shared context, built once, configurable by environment.

    ⚠ This used to be a bare `ZoteroContext()` constructed at server start, which
    hard-wired `~/Zotero/zotero.sqlite` and the default bridge/BBT URLs. The CLI has
    always accepted `--db`; MCP could not be pointed anywhere, so it could not be run
    against a copy or a fixture.
    """
    global _CTX
    if _CTX is None:
        kwargs: dict[str, Any] = {}
        if db := os.environ.get("ZOTERO_CORE_DB"):
            kwargs["zotero_db_path"] = db
        if url := os.environ.get("ZOTERO_CORE_BRIDGE_URL"):
            kwargs["bridge_url"] = url
        if url := os.environ.get("ZOTERO_CORE_BBT_URL"):
            kwargs["bbt_rpc_url"] = url
        _CTX = ZoteroContext(**kwargs)
    return _CTX


# --------------------------------------------------------------------------
# verbs -- plain functions, keyword arguments only, no envelope
# --------------------------------------------------------------------------


def get_zotero_window_state() -> Any:
    return context().get_window_state()


def get_zotero_active_reader(
    include_annotations: bool = False,
    annotation_types: Any = None,
    include_citekeys: bool = True,
) -> Any:
    contexts = context().get_open_reader_context(
        active_only=True,
        include_annotations=include_annotations,
        include_citekeys=include_citekeys,
        annotation_types=parse_types(annotation_types),
    )
    return contexts[0] if contexts else None


def get_zotero_open_readers(
    include_annotations: bool = False,
    annotation_types: Any = None,
    include_citekeys: bool = True,
) -> Any:
    return context().get_open_reader_context(
        include_annotations=include_annotations,
        include_citekeys=include_citekeys,
        annotation_types=parse_types(annotation_types),
    )


def get_zotero_annotations(
    attachment_key: str,
    annotation_types: Any = None,
    include_text: bool = True,
    include_comments: bool = True,
) -> Any:
    return context().get_annotations(
        attachment_key,
        types=parse_types(annotation_types),
        include_text=include_text,
        include_comments=include_comments,
    )


def resolve_zotero_pdf(identifier: str, is_attachment_key: bool = False) -> dict:
    parent_key, attachment_key = context().resolve_pdf_attachment_key(
        identifier, is_attachment_key=is_attachment_key
    )
    return {"parent_key": parent_key, "attachment_key": attachment_key}


def get_zotero_sources(include_citekeys: bool = True) -> Any:
    # `include_citekeys` was never passed by the old dispatch, so MCP always paid the
    # Better BibTeX round trip even when the caller did not want citekeys -- and BBT
    # being down is indistinguishable from no result, because its client swallows every
    # network error and returns None.
    return context().get_sources_with_annotations(include_citekeys=include_citekeys)


def get_zotero_item(item_key: str) -> dict:
    return context().get_item(item_key)


def check_zotero_duplicate(
    title: str | None = None,
    doi: str | None = None,
    isbn: str | None = None,
    calibre_uuid: str | None = None,
    creators: Any = None,
) -> dict:
    return context().check_duplicate(
        title=title,
        doi=doi,
        isbn=isbn,
        calibre_uuid=calibre_uuid,
        creators=tuple(creators or ()),
    )


def list_zotero_pdfs(limit: int | None = None) -> dict:
    return context().list_pdfs(limit=limit)


def get_zotero_trash_count() -> dict:
    return context().trash_count()


def ping_zotero() -> dict:
    # CLI-only until now, so an agent could not ask "is the bridge up?" -- it could only
    # infer it from the shape of a failure.
    return context().ping()


@dataclass(frozen=True)
class _ToolSpec:
    """One tool: its name, the verb it calls, and the arguments it accepts.

    `verb` is the real function above, not a wrapper, and every argument is passed by
    KEYWORD -- so there is no per-verb adapter that can drift from the schema.
    """

    name: str
    verb: Callable[..., Any]
    description: str
    properties: dict[str, dict] = field(default_factory=dict)
    required: tuple[str, ...] = ()

    def as_tool_schema(self) -> dict:
        return {
            "type": "object",
            "properties": dict(self.properties),
            "required": list(self.required),
        }


_ANNOTATION_TYPES_PROP = {
    "type": "array",
    "items": {"type": "string", "enum": list(ANNOTATION_TYPE_NAMES)},
    "description": "Optional filter. Omit for all types.",
}
_CITEKEYS_PROP = {
    "type": "boolean",
    "default": True,
    "description": "Join Better BibTeX citekeys. Set false to skip the BBT round trip.",
}

TOOLS: tuple[_ToolSpec, ...] = (
    _ToolSpec(
        name="get_zotero_window_state",
        verb=get_zotero_window_state,
        description=(
            "Return what Zotero is currently SHOWING: selected collection, selected "
            "items, tabs, open readers and reader positions. Live GUI state, not the "
            "catalogue -- use get_zotero_item to look something up by key."
        ),
    ),
    _ToolSpec(
        name="get_zotero_active_reader",
        verb=get_zotero_active_reader,
        description="Return the active Zotero reader tab, optionally with annotations.",
        properties={
            "include_annotations": {"type": "boolean", "default": False},
            "annotation_types": _ANNOTATION_TYPES_PROP,
            "include_citekeys": _CITEKEYS_PROP,
        },
    ),
    _ToolSpec(
        name="get_zotero_open_readers",
        verb=get_zotero_open_readers,
        description="Return all open Zotero reader tabs, optionally with annotations.",
        properties={
            "include_annotations": {"type": "boolean", "default": False},
            "annotation_types": _ANNOTATION_TYPES_PROP,
            "include_citekeys": _CITEKEYS_PROP,
        },
    ),
    _ToolSpec(
        name="get_zotero_annotations",
        verb=get_zotero_annotations,
        description=(
            "Return highlights and comments for a Zotero ATTACHMENT key (not the parent "
            "item key -- use resolve_zotero_pdf to get one from a parent or citekey)."
        ),
        properties={
            "attachment_key": {"type": "string", "description": "8-char attachment key."},
            "annotation_types": _ANNOTATION_TYPES_PROP,
            "include_text": {"type": "boolean", "default": True},
            "include_comments": {"type": "boolean", "default": True},
        },
        required=("attachment_key",),
    ),
    _ToolSpec(
        name="resolve_zotero_pdf",
        verb=resolve_zotero_pdf,
        description=(
            "Resolve a Better BibTeX citekey, parent item key, or PDF attachment key to "
            "the {parent_key, attachment_key} pair. When an item has several PDFs, the "
            "one with the most annotations wins."
        ),
        properties={
            "identifier": {"type": "string"},
            "is_attachment_key": {"type": "boolean", "default": False},
        },
        required=("identifier",),
    ),
    _ToolSpec(
        name="get_zotero_sources",
        verb=get_zotero_sources,
        description="Return Zotero PDF sources that have at least one annotation.",
        properties={"include_citekeys": _CITEKEYS_PROP},
    ),
    _ToolSpec(
        name="get_zotero_item",
        verb=get_zotero_item,
        description=(
            "Everything about ONE item by key: type, title, fields, creators (in order), "
            "tags, trash state, parent and children. Carries `read_mode` -- `immutable=1` "
            "means the answer came from a point-in-time snapshot because Zotero held the "
            "write lock, not from the live database."
        ),
        properties={"item_key": {"type": "string", "description": "8-char item key."}},
        required=("item_key",),
    ),
    _ToolSpec(
        name="check_zotero_duplicate",
        verb=check_zotero_duplicate,
        description=(
            "Is this already in the library? Returns verdict block|warn|ok WITHOUT "
            "writing. DOI, ISBN or calibre_uuid match blocks; normalised title AND first "
            "author surname warns. Trashed matches are reported, never blocking. This is "
            "the same gate create_item runs -- ask it BEFORE adding."
        ),
        properties={
            "title": {"type": "string"},
            "doi": {"type": "string"},
            "isbn": {"type": "string"},
            "calibre_uuid": {"type": "string"},
            "creators": {
                "type": "array",
                "items": {"type": "object"},
                "description": "e.g. [{'creatorType':'author','lastName':'Welling'}]. "
                "Required for the title tier to fire -- title alone never warns.",
            },
        },
    ),
    _ToolSpec(
        name="list_zotero_pdfs",
        verb=list_zotero_pdfs,
        description=(
            "Enumerate stored PDF attachments with each one's parent title and "
            "collection, and the file path on disk."
        ),
        properties={"limit": {"type": "integer", "description": "Omit for all."}},
    ),
    _ToolSpec(
        name="get_zotero_trash_count",
        verb=get_zotero_trash_count,
        description="How many items are currently in the Zotero trash.",
    ),
    _ToolSpec(
        name="ping_zotero",
        verb=ping_zotero,
        description=(
            "Is the zotero-bridge plugin answering? Use this to tell 'Zotero is closed' "
            "apart from 'that key does not exist'."
        ),
    ),
)

_BY_NAME = {spec.name: spec for spec in TOOLS}

# A locked database, a missing key and a closed Zotero used to be indistinguishable:
# every failure flattened to {"ok": false, "error": str(exc)} with no code to branch on.
_CODES: tuple[tuple[type[BaseException], str], ...] = (
    (ZoteroBridgeError, "bridge_unreachable"),
    (ZoteroAnnotationError, "annotation_read_failed"),
    (sqlite3.OperationalError, "database_unavailable"),
    (ValueError, "bad_arguments"),
)


def error_code(exc: BaseException) -> str:
    for kind, code in _CODES:
        if isinstance(exc, kind):
            return code
    return "error"


def call_read(name: str, arguments: dict[str, Any]) -> Any:
    """Dispatch one tool call to its verb. Raises rather than returning an envelope."""
    spec = _BY_NAME.get(name)
    if spec is None:
        raise ValueError(f"Unknown tool: {name}")

    unknown = sorted(set(arguments) - set(spec.properties))
    if unknown:
        # Refused, not dropped. A misspelled `include_annotaions` previously vanished
        # and the caller silently got the default.
        raise ValueError(f"{name} does not accept {unknown} (accepted: {sorted(spec.properties)})")
    missing = [key for key in spec.required if key not in arguments]
    if missing:
        raise ValueError(f"{name} requires {missing}")

    call_args = {
        key: value for key, value in arguments.items() if value is not None or key in spec.required
    }
    return spec.verb(**call_args)


def parse_types(value: Any) -> set[str] | None:
    if not value:
        return None
    if isinstance(value, str):
        return {part.strip() for part in value.split(",") if part.strip()}
    if isinstance(value, list):
        return {str(part) for part in value if str(part).strip()}
    return None


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

    from .. import __version__

    server = Server(SERVER_NAME)

    @server.list_tools()
    async def list_tools() -> list[types.Tool]:
        return [
            types.Tool(
                name=spec.name,
                description=spec.description,
                inputSchema=spec.as_tool_schema(),
            )
            for spec in TOOLS
        ]

    @server.call_tool()
    async def call_tool(name: str, arguments: dict[str, Any]) -> list[types.TextContent]:
        try:
            payload = call_read(name, arguments or {})
            text = json.dumps(to_jsonable(payload), ensure_ascii=False, indent=2)
        except Exception as exc:
            text = json.dumps(
                {"ok": False, "code": error_code(exc), "error": str(exc)},
                ensure_ascii=False,
                indent=2,
            )
        return [types.TextContent(type="text", text=text)]

    async with stdio_server() as (read_stream, write_stream):
        await server.run(
            read_stream,
            write_stream,
            InitializationOptions(
                server_name=SERVER_NAME,
                # Was hardcoded "0.1.0" and would have stayed there through every
                # release; it is the package version now.
                server_version=__version__,
                capabilities=server.get_capabilities(
                    notification_options=NotificationOptions(),
                    experimental_capabilities={},
                ),
            ),
        )
