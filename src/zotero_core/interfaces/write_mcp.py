"""MCP adapter over the gated write surface: the agent-facing half of `zotero_core.application`.

WHY THIS EXISTS, GIVEN THAT COOKJOHN ALREADY SPEAKS MCP
------------------------------------------------------
cookjohn's plugin is itself an MCP server (registered as `zotero-plugin`), so it is
fair to ask what a second one buys. Two things, and they are the two that made this
package necessary in the first place:

  * **cookjohn has no delete verb at all.** Trash and restore live on `linker/`, which
    speaks plain HTTP and no MCP. An agent that wanted to remove an item had to drive
    `uv run python` one-liners -- which is how this adapter got commissioned.
  * **cookjohn's tools are ungated.** `write_tag` action='set' wipes every tag,
    `write_metadata`'s `creators` array wipes every creator, and `delete_collection`
    takes a `deleteItems` boolean. Reached directly, an agent gets all three with no
    precondition, no journal, and no read-back.

So this adapter is deliberately NOT a second transport. It is the same gates the CLI
and the Python callers get, and it adds nothing of its own: no verb here is
implemented locally, and the transport choice stays where `writes.py` put it.

WHAT IT DOES NOT EXPOSE, AND WHY
--------------------------------
**No read tools.** `zotero_core.infrastructure` owns reads, and the sibling adapter
docstring's rule is that wrapping them "would create a second answer to what is in the
library, which is the exact failure this package exists to end". The registered
`zotero-context` server already serves that half.

**No `store` / `linker` / `cookjohn` parameters.** They are dependency-injection seams
for tests. `test_crud.py` asserts that no verb "leaks a port or a plugin name into its
signature"; declaring them as tool parameters would leak both into the agent-facing
surface and would let a caller aim the write path at an arbitrary URL.

**No `journal_dir` parameter.** The journal is the audit trail of real writes, and it
is only useful as an audit trail if every write lands in the same place. The tests
redirect it because 216 stray manifests once made it useless; an agent has no such
reason, and letting it choose per call is how that directory becomes noise again.

`force` IS exposed wherever the verb has it, defaulting to False in every schema. It
is the deliberate second ask that stands between "add a tag" and "replace every tag",
so hiding it would either break the gated verbs or silently defeat them. There is a
test asserting no schema ships `force` defaulted to True.

WHY A TABLE INSTEAD OF core's IF-CHAIN
--------------------------------------
`core/mcp_server.py` lists 6 tools in `list_tools` and dispatches them in an if-chain
in `call_core`. Everything else here mirrors that file -- `run`, `main`, the
`@server.list_tools()` / `@server.call_tool()` pair, the stdio transport, the
`InitializationOptions` block, and the `{"ok": false, ...}` error envelope. The
dispatch is the one deviation: past a dozen tools the schema and the call site are two lists
that have to agree about 17 names, and a table makes them one list. That is the same
argument `find_key` won in `cookjohn.py` -- two copies of one fact is the defect this
package was built to end -- and it is also what keeps `call_tool` under ruff's
complexity ceiling.

`mcp` IS NOT A DEPENDENCY OF THE WRITE PATH
------------------------------------------
It is imported inside `main()` and pinned behind the `mcp` extra, exactly as core does
it. `cookjohn.py` states the reason: the client there is stdlib-only so it can be
VENDORED into `calibre-zotero-jump`, a Calibre plugin running inside Calibre's
embedded Python which cannot see a uv virtualenv. Making `mcp` a hard dependency would
put an async runtime in that import path. `.importlinter` enforces this -- every module
except this one is forbidden from importing `mcp`.
"""

from __future__ import annotations

import asyncio
import json
from typing import Any

from zotero_core import __version__
from zotero_core.application.services.collections import (
    add_items_to_collection,
    create_collection,
    delete_collection,
    move_items_between_collections,
    remove_items_from_collection,
    update_collection,
)
from zotero_core.application.services.liveness import ZOTERO_SERVER_URL, zotero_is_running
from zotero_core.application.services.replay import list_entries as _list_entries
from zotero_core.application.services.replay import undo as _undo
from zotero_core.application.services.verbs import (
    add_tags,
    check_keys,
    create_item,
    import_attachment,
    link_attachment,
    remove_tags,
    replace_creators,
    require_items,
    restore_items,
    set_tags,
    trash_items,
    update_metadata,
    write_note,
)
from zotero_core.domain.entities.models import to_jsonable
from zotero_core.domain.errors import WriteBlocked
from zotero_core.infrastructure.sqlite.items import ZoteroItemStore
from zotero_core.infrastructure.transports.cookjohn import CookjohnClient
from zotero_core.infrastructure.transports.linker import LinkerClient
from zotero_core.interfaces.tool_spec import WriteToolSpec as _ToolSpec
from zotero_core.interfaces.tool_spec import dispatch as _dispatch

SERVER_NAME = "zotero-writes"

# Reusable schema fragments. `force` is spelled out once so every gated verb
# describe it identically -- and so there is exactly one place to read to confirm it
# defaults to False.
_FORCE = {
    "type": "boolean",
    "default": False,
    "description": (
        "Deliberate second ask. Relaxes ONLY the gate it is passed to; never the "
        "liveness or existence gates. Read the refusal's `detail` first — it reports "
        "what is currently there and would be lost."
    ),
}
_ITEM_KEYS = {
    "type": "array",
    "items": {"type": "string"},
    "description": "Zotero item keys (8 uppercase alphanumerics). The whole batch is "
    "refused if any key does not resolve.",
}
_COPY_DB = {
    "type": "boolean",
    "default": False,
    "description": (
        "Also copy zotero.sqlite and its rollback journal aside (~330 MB per call). "
        "Off by default: a manifest with the inverse call is written regardless, and "
        "restoring a database copy requires closing Zotero and discards every "
        "unrelated change since."
    ),
}


def list_undo(limit: int = 25) -> dict:
    """The write journal, newest first, with why each entry can or cannot be replayed."""
    entries = _list_entries(limit=limit)
    return {
        "ok": True,
        "op": "list_undo",
        "count": len(entries),
        "replayable": sum(1 for e in entries if e.replayable),
        "entries": [
            {
                "manifest": e.path,
                "op": e.op,
                "written_at": e.written_at,
                "inverse": e.inverse,
                "replayable": e.replayable,
                "blocked_reason": e.blocked_reason,
            }
            for e in entries
        ],
    }


def undo_write(manifest: str | None = None, dry_run: bool = False) -> dict:
    return _undo(manifest, dry_run=dry_run)


def preflight(item_keys: list[str] | None = None) -> dict:
    """Probe both plugins and optionally resolve keys, WITHOUT writing anything.

    `require_zotero` raises on the first transport that fails, which is right for a
    write -- there is no point probing the second when the operation is already
    refused. A preflight wants the opposite: the whole picture in one answer, because
    "cookjohn is up and linker is not" means metadata writes work and trash does not,
    and an agent deciding what to attempt needs both halves.

    The key resolution reuses `check_keys` and `require_items`, which `__init__`
    exports for exactly this purpose ("gates, exposed so a caller can pre-flight
    without writing"). Nothing here mutates anything.
    """
    report: dict[str, Any] = {
        "ok": True,
        "op": "preflight",
        "zotero_running": zotero_is_running(),
        "probe": ZOTERO_SERVER_URL,
        "transports": {},
    }
    for name, client in (("linker", LinkerClient()), ("cookjohn", CookjohnClient())):
        try:
            report["transports"][name] = {"available": True, "info": client.ping()}
        except WriteBlocked as exc:
            report["transports"][name] = {"available": False, **exc.as_dict()}
            report["ok"] = False

    if item_keys is not None:
        try:
            keys = check_keys(item_keys)
            states = require_items(ZoteroItemStore(), keys)
            report["items"] = {
                "read_mode": states.read_mode,
                "resolved": [
                    {
                        "key": key,
                        "title": states[key].title,
                        "item_type": states[key].item_type,
                        "trashed": states[key].trashed,
                        "child_keys": list(states[key].child_keys),
                    }
                    for key in keys
                ],
            }
        except WriteBlocked as exc:
            report["items"] = exc.as_dict()
            report["ok"] = False
    return report


TOOLS: tuple[_ToolSpec, ...] = (
    # ---------------- CREATE ----------------
    _ToolSpec(
        name="zotero_create_item",
        verb=create_item,
        description=(
            "Create a regular Zotero item. `title` is mandatory (an untitled item is "
            "near-unfindable in the GUI). Refuses when the same DOI, ISBN or "
            "calibre_uuid is already in the library; force=True downgrades that to a "
            "warning carried in the result."
        ),
        properties={
            "item_type": {
                "type": "string",
                "description": "Zotero item type, e.g. 'book', 'journalArticle', "
                "'conferencePaper'.",
            },
            "fields": {
                "type": "object",
                "description": "Metadata fields as {fieldName: value}. `title` is "
                "required. Note that Zotero maps some base field names onto the type's "
                "own field — publicationTitle becomes proceedingsTitle on a "
                "conferencePaper — so read the type-appropriate name back.",
            },
            "creators": {
                "type": "array",
                "items": {"type": "object"},
                "description": "e.g. [{'creatorType': 'author', 'firstName': 'A', "
                "'lastName': 'B'}]. An organisation is {'creatorType': ..., 'name': ...}.",
            },
            "tags": {"type": "array", "items": {"type": "string"}},
            "collection_key": {
                "type": "string",
                "description": "File the new item into this collection.",
            },
            "calibre_uuid": {
                "type": "string",
                "description": "Stamps 'calibre-uuid: <uuid>' into `extra` (appended, "
                "never assigned over) and is ALSO the dedupe key checked before creating.",
            },
            "force": _FORCE,
        },
        required=("item_type", "fields"),
    ),
    _ToolSpec(
        name="zotero_link_attachment",
        verb=link_attachment,
        transport="linker",
        description=(
            "Attach a file BY REFERENCE — no copy into ~/Zotero/storage. The path must "
            "be ABSOLUTE and the file must exist: Zotero resolves a relative path "
            "against its own working directory, and linking never reads the file, so a "
            "bad path fails silently and looks fine until opened."
        ),
        properties={
            "parent_item_key": {"type": "string"},
            "path": {"type": "string", "description": "Absolute path to an existing file."},
            "title": {"type": "string", "description": "Defaults to the file's basename."},
        },
        required=("parent_item_key", "path"),
    ),
    _ToolSpec(
        name="zotero_import_attachment",
        verb=import_attachment,
        description=(
            "Attach a file BY COPY into Zotero's storage. Distinct from "
            "zotero_link_attachment because the consequence lasts: the bytes are "
            "duplicated into ~/Zotero/storage and then sync to the NAS over WebDAV. "
            "For a library already on disk elsewhere, link instead."
        ),
        properties={
            "parent_item_key": {"type": "string"},
            "path": {"type": "string"},
            "title": {"type": "string"},
        },
        required=("parent_item_key", "path"),
    ),
    _ToolSpec(
        name="zotero_write_note",
        verb=write_note,
        description=(
            "Create, append to, or replace a note. action='update' REPLACES the note's "
            "content and the journal records the note's key and title but NOT its "
            "previous body — use action='append' when the old text matters."
        ),
        properties={
            "content": {"type": "string", "description": "Note body (HTML)."},
            "parent_item_key": {
                "type": "string",
                "description": "Parent item for a child note. Only used by action='create'.",
            },
            "note_key": {
                "type": "string",
                "description": "Required by action='update' and action='append'.",
            },
            "action": {
                "type": "string",
                "enum": ["create", "update", "append"],
                "default": "create",
            },
            "tags": {"type": "array", "items": {"type": "string"}},
        },
        required=("content",),
    ),
    _ToolSpec(
        name="zotero_create_collection",
        verb=create_collection,
        description=(
            "Create a collection, refusing a name that already exists at the same "
            "level. The duplicate check is a real gate: two sibling collections with "
            "one name make a name-based lookup nondeterministic, and both Calibre "
            "importers look their target collection up by name."
        ),
        properties={
            "name": {"type": "string"},
            "parent_collection": {
                "type": "string",
                "description": "Parent collection key. Omit for a top-level collection.",
            },
        },
        required=("name",),
    ),
    # ---------------- UPDATE ----------------
    _ToolSpec(
        name="zotero_update_metadata",
        verb=update_metadata,
        description=(
            "Set metadata fields on ONE regular item. Fields MERGE — only the named "
            "ones change — and the previous value of each is journalled. Refuses a "
            "no-op (every field already holding the given value) and refuses notes, "
            "attachments and annotations, naming the actual type."
        ),
        properties={
            "item_key": {"type": "string"},
            "fields": {
                "type": "object",
                "description": "{fieldName: value}. The result's `verification` reports "
                "`normalized_by_zotero` when Zotero rewrote the value (date='2026' is "
                "stored as '2026-00-00 2026') or mapped a base field onto the item "
                "type's own (publicationTitle -> proceedingsTitle).",
            },
        },
        required=("item_key", "fields"),
    ),
    _ToolSpec(
        name="zotero_add_tags",
        verb=add_tags,
        description=(
            "Add tags, KEEPING the existing ones. This is the normal path — prefer it "
            "over zotero_set_tags. Refuses when the item already carries all of them."
        ),
        properties={
            "item_key": {"type": "string"},
            "tags": {"type": "array", "items": {"type": "string"}},
        },
        required=("item_key", "tags"),
    ),
    _ToolSpec(
        name="zotero_remove_tags",
        verb=remove_tags,
        description=(
            "Remove the named tags, leaving the rest. Journalled, because it destroys "
            "something. Refuses when the item carries none of them."
        ),
        properties={
            "item_key": {"type": "string"},
            "tags": {"type": "array", "items": {"type": "string"}},
        },
        required=("item_key", "tags"),
    ),
    _ToolSpec(
        name="zotero_set_tags",
        verb=set_tags,
        description=(
            "REPLACE every tag on an item. Refuses without force=True. Use "
            "zotero_add_tags to add — the tags in this library are curated knowledge no "
            "online source carries, and cookjohn's underlying action='set' deletes all "
            "of them to install a new list."
        ),
        properties={
            "item_key": {"type": "string"},
            "tags": {"type": "array", "items": {"type": "string"}},
            "force": _FORCE,
        },
        required=("item_key", "tags"),
    ),
    _ToolSpec(
        name="zotero_replace_creators",
        verb=replace_creators,
        description=(
            "REPLACE every creator on an item. Refuses without force=True. There is no "
            "add_creator: cookjohn's `creators` argument does not merge, so passing one "
            "author to add a co-author deletes the existing list. To add, read the "
            "current list out of the refusal's `detail.current_creators`, append, and "
            "send the whole list."
        ),
        properties={
            "item_key": {"type": "string"},
            "creators": {
                "type": "array",
                "items": {"type": "object"},
                "description": "The COMPLETE creator list, in order. First author is not "
                "an arbitrary member of a set.",
            },
            "force": _FORCE,
        },
        required=("item_key", "creators"),
    ),
    _ToolSpec(
        name="zotero_update_collection",
        verb=update_collection,
        description=(
            "Rename or re-parent a collection, journalling the previous name and "
            "parent. parent_collection='' moves it to the top level; omitting the "
            "parameter leaves the parent alone."
        ),
        properties={
            "collection_key": {"type": "string"},
            "name": {"type": "string"},
            "parent_collection": {"type": "string"},
        },
        required=("collection_key",),
    ),
    _ToolSpec(
        name="zotero_add_items_to_collection",
        verb=add_items_to_collection,
        description="File items into a collection. The items are not moved or copied.",
        properties={"collection_key": {"type": "string"}, "item_keys": _ITEM_KEYS},
        required=("collection_key", "item_keys"),
    ),
    _ToolSpec(
        name="zotero_remove_items_from_collection",
        verb=remove_items_from_collection,
        description=(
            "Unfile items from a collection. The items STAY in the library — this is "
            "not a delete. Journalled, because the distinction between this and "
            "zotero_trash_items is the one a caller in a hurry gets wrong."
        ),
        properties={"collection_key": {"type": "string"}, "item_keys": _ITEM_KEYS},
        required=("collection_key", "item_keys"),
    ),
    _ToolSpec(
        name="zotero_move_items_between_collections",
        verb=move_items_between_collections,
        description=(
            "MOVE items from one collection to another: one call, one journal entry, "
            "and a rollback if the second half fails. Prefer this over calling "
            "zotero_add_items_to_collection then zotero_remove_items_from_collection -- "
            "that route journals only the removal and has no rollback, so a failure "
            "between them leaves the items in BOTH collections and still returns a "
            "success envelope for the add. Refuses when the items are not in the source "
            "collection (through the two-call route that is a silent no-op that reports "
            "success); force=True files them into the target anyway and names them in "
            "`not_in_source`. Re-reads BOTH collections afterwards."
        ),
        properties={
            "from_collection_key": {"type": "string"},
            "to_collection_key": {"type": "string"},
            "item_keys": {
                "description": "Zotero item keys (8 uppercase alphanumerics).",
                "items": {"type": "string"},
                "type": "array",
            },
            "force": _FORCE,
        },
        required=("from_collection_key", "to_collection_key", "item_keys"),
    ),
    # ---------------- DELETE ----------------
    _ToolSpec(
        name="zotero_trash_items",
        verb=trash_items,
        transport="linker",
        description=(
            "Move items to the Zotero trash. RECOVERABLE — see zotero_restore_items; "
            "nothing here erases anything. Works on any item key including "
            "attachments. Every key is resolved before the request is sent, because "
            "the plugin would otherwise apply the resolvable ones and report success. "
            "force=True skips keys already trashed instead of refusing the batch."
        ),
        properties={"item_keys": _ITEM_KEYS, "force": _FORCE, "copy_db": _COPY_DB},
        required=("item_keys",),
    ),
    _ToolSpec(
        name="zotero_restore_items",
        verb=restore_items,
        transport="linker",
        description=(
            "Bring items back out of the trash — the inverse of zotero_trash_items. "
            "force=True skips keys that are not trashed instead of refusing the batch."
        ),
        properties={"item_keys": _ITEM_KEYS, "force": _FORCE, "copy_db": _COPY_DB},
        required=("item_keys",),
    ),
    _ToolSpec(
        name="zotero_delete_collection",
        verb=delete_collection,
        description=(
            "Delete a collection. Its items STAY in the library unless "
            "delete_items=True, which sends every member to the trash and requires "
            "force=True as well. Note the asymmetry with items: a trashed item can be "
            "restored, a deleted collection cannot — Zotero's trash holds items — so "
            "the manifest records the name, parent and every member key, which is the "
            "only undo there is."
        ),
        properties={
            "collection_key": {"type": "string"},
            "delete_items": {
                "type": "boolean",
                "default": False,
                "description": "Also send every member item to the trash. Requires force=True.",
            },
            "force": _FORCE,
        },
        required=("collection_key",),
    ),
    # ---------------- read-only pre-flight ----------------
    _ToolSpec(
        name="zotero_list_undo",
        verb=list_undo,
        transport="none",
        description=(
            "The write journal, newest first. Ten verbs record the call that reverses "
            "them; until now NOTHING read those manifests, so `undo_call` was advisory "
            "text you retyped. Each entry says whether it can be replayed and, if not, "
            "why -- some operations genuinely cannot express an inverse (a note update "
            "does not capture the previous body; recreating a deleted collection gives "
            "it a new key)."
        ),
        properties={"limit": {"type": "integer", "default": 25}},
    ),
    _ToolSpec(
        name="zotero_undo",
        verb=undo_write,
        transport="none",
        description=(
            "Replay a journal manifest's inverse. Defaults to the most recent REPLAYABLE "
            "entry. Use dry_run=true first: it resolves and validates the call and shows "
            "what it would do without doing it, which matters because an undo is itself "
            "a write and is journalled like any other. The inverse is parsed, never "
            "eval'd -- the callee must be a known undo verb and every argument a literal."
        ),
        properties={
            "manifest": {
                "type": "string",
                "description": "Manifest path or filename suffix. Omit for the latest.",
            },
            "dry_run": {"type": "boolean", "default": False},
        },
    ),
    _ToolSpec(
        name="zotero_write_preflight",
        verb=preflight,
        transport="both",
        description=(
            "Check whether writes are possible right now, and optionally resolve item "
            "keys, WITHOUT writing anything. Reports each plugin separately: Zotero "
            "being up with one plugin missing is a different job to fix than Zotero "
            "being closed, and it decides which verbs are available (linker serves "
            "trash, restore and linked files; cookjohn serves items, metadata, notes, "
            "tags and collections)."
        ),
        properties={
            "item_keys": {
                "type": "array",
                "items": {"type": "string"},
                "description": "Optional. Resolve these keys and report title, type and "
                "trash state for each. Read-only.",
            }
        },
    ),
)

_BY_NAME = {spec.name: spec for spec in TOOLS}


def call_writes(name: str, arguments: dict[str, Any]) -> Any:
    """Dispatch one tool call. Thin by design -- the logic is shared, see `tool_spec`."""
    return _dispatch(_BY_NAME, name, arguments)


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
            payload = call_writes(name, arguments or {})
        except WriteBlocked as exc:
            # The whole reason this adapter exists rather than a raw `uv run python`.
            # A precondition failure is DATA -- `code` is the stable field a caller
            # branches on, `detail` carries what is currently there and would have been
            # lost -- and a traceback discards all of it.
            payload = exc.as_dict()
        except Exception as exc:  # noqa: BLE001 - the envelope is the contract
            payload = {"ok": False, "error": str(exc), "error_type": type(exc).__name__}
        return [types.TextContent(type="text", text=_render(payload))]

    async with stdio_server() as (read_stream, write_stream):
        await server.run(
            read_stream,
            write_stream,
            InitializationOptions(
                server_name=SERVER_NAME,
                server_version=__version__,
                capabilities=server.get_capabilities(
                    notification_options=NotificationOptions(),
                    experimental_capabilities={},
                ),
            ),
        )


def _render(payload: Any) -> str:
    """Serialise a result. `default=str` so an unexpected type cannot kill the server.

    `to_jsonable` handles the shapes core returns -- dataclasses, tuples, nested dicts.
    A write result is already plain data, but `detail` on a `WriteBlocked` is whatever
    the gate put there, so this is the one place an un-serialisable value could reach
    the wire. Falling back to `str` degrades one field; raising here would drop the
    whole response and tell the caller nothing.
    """
    return json.dumps(to_jsonable(payload), ensure_ascii=False, indent=2, default=str)
