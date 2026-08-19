"""Zotero writes, as ONE CRUD surface. The single place ZoteroSuite mutates a library.

THE PROBLEM THIS ENDS
---------------------
Zotero's write capability was scattered across two plugins on two ports, and a caller
had to know which one served which verb:

    create / update / notes / tags / collections   cookjohn   :23121 (MCP JSON-RPC)
    linked attachments / trash / restore           linker     :23119 (plain HTTP)
    reads                                          core       zotero.sqlite

Two consumers learned that split by copying a client — `importers/calibre2zotero/
sync.py:139` and `calibre-zotero-jump/ui.py:24` carry near-identical MCP clients —
and they drifted where it mattered most: they disagree about how to tell whether a
book is already in Zotero. One scans Zotero's `extra` for `calibre-uuid:`, the other
trusts a `zotero` identifier on the Calibre side. Meanwhile `linker/` v0.3.0 had been
running with `trash-items` and `restore-items` registered and ZERO consumers.

So the fix is not a delete client. A module owning only Delete would make it four
scattered places instead of three. This is the whole CRUD surface, and which
transport serves a verb is an implementation detail — visible in the result's
`transport` field for debugging, never in a signature.

    create      create_item, link_attachment, import_attachment, write_note,
                create_collection
    read        NOT HERE — `zotero_context` owns reads and is already correct
    update      update_metadata, replace_creators, add_tags, remove_tags, set_tags,
                write_note(action="update"|"append"), update_collection,
                add_items_to_collection, remove_items_from_collection
    delete      trash_items / restore_items, delete_collection

WHERE THIS LIVES: option (b), and core/ is the READ HALF
-------------------------------------------------------
Answering the question directly, because it decides what `core/` is: **`core/` is not
the single Zotero owner. It is the read half, and this package is the write half.**
The dependency runs `zotero_writes` → `zotero_context`, never the reverse.

The alternative was amending "core/ stays read-only forever" so one package owned
everything, with calibre-core as precedent — its docstring says an earlier version
"over-read its own reason: the argument is against raw SQL, not against owning the
gate." That reasoning is sound there and does not transfer, for three reasons:

  1. ZoteroSuite never conflated the two rules. "core/ stays read-only" and "never
     open zotero.sqlite read-write" are SEPARATE bullets in the README. calibre-core
     had to amend because ONE sentence carried both arguments and only one held;
     there is no such tangle here, so the core rule still stands on its own stated
     reason — it is the canonical shared READ layer and its consumers are read-only.
  2. Import surface, which calibre-core itself treats as load-bearing: it keeps
     `audit` and `openlibrary` out of its `__init__` specifically so a plain import
     does not drag in `urllib.request`. `zotero_context` has `dependencies = []` and
     is imported by an Obsidian-facing JSON CLI. A CRUD surface needs two HTTP
     clients; folding them into core would do exactly what that precedent avoids.
  3. The dependency direction is naturally acyclic. Every precondition a write needs
     is a read — does the key resolve, is it already trashed, what tags are there to
     be replaced — so writes depend on reads and reads never depend on writes.

What the calibre-core lesson DOES transfer is the part that matters: "keeping the
gate outside meant every caller re-implemented its preconditions, and they
disagreed." The gate had to be owned SOMEWHERE. This is that somewhere.

The suite-level ROUTING rule was amended, because it was already stale: the README
said all mutation goes through cookjohn's MCP, untrue since `linker/` shipped write
endpoints in 2026-08 — and never true for linked attachments, which cookjohn cannot
create at all. That bullet now names this package. The rules about core and about raw
SQL are untouched, and both remain literally true: nothing here executes SQL.

WHAT IS ENFORCED
----------------
Zotero must be RUNNING with the plugin that verb needs — the precondition that
INVERTS relative to calibre-core's closed-GUI rule, because every Zotero write
channel is code executing inside the application. Keys are shape-checked then
resolved before anything is sent. Creates are duplicate-checked. Operations that
REPLACE a list rather than adding to it refuse unless asked twice. Whatever is about
to be overwritten is journalled with the call that reverses it. Results are re-read
rather than believed.

NOT HERE, DELIBERATELY: hard erase and empty-trash. docs/DESIGN.md excludes them and
no plugin exposes an endpoint for them. Nothing in this package can destroy an item —
the strongest thing it can do is move one to a trash that already holds 277.
"""

from .collections import (
    add_items_to_collection,
    create_collection,
    delete_collection,
    remove_items_from_collection,
    update_collection,
)
from .cookjohn import DEFAULT_COOKJOHN_URL, CookjohnClient
from .errors import ALL_REASONS, Reason, WriteBlocked
from .journal import DEFAULT_JOURNAL_DIR, copy_database, write_manifest
from .linker import DEFAULT_LINKER_URL, LinkerClient
from .liveness import require_zotero, zotero_is_running
from .writes import (
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

__version__ = "0.2.0"

# Grouped by CRUD concern rather than sorted -- the grouping IS the documentation of
# what this package is for, and the whole point is that the surface reads as one
# coherent thing rather than as two transports. RUF022 wants alphabetical, which
# would scatter exactly the structure being asserted.
__all__ = [  # noqa: RUF022
    "__version__",
    # errors -- `code` is the machine-readable field an MCP layer branches on
    "WriteBlocked", "Reason", "ALL_REASONS",
    # CREATE
    "create_item", "link_attachment", "import_attachment", "write_note",
    "create_collection",
    # UPDATE -- additive verbs are plain; the two that REPLACE are named for it
    "update_metadata", "add_tags", "remove_tags",
    "set_tags", "replace_creators",
    "update_collection", "add_items_to_collection", "remove_items_from_collection",
    # DELETE -- trash is recoverable and restore ships alongside it. No hard erase.
    "trash_items", "restore_items", "delete_collection",
    # gates, exposed so a caller can pre-flight without writing
    "require_zotero", "zotero_is_running", "check_keys", "require_items",
    # undo -- a manifest by default; the 329 MB database copy is opt-in
    "write_manifest", "copy_database", "DEFAULT_JOURNAL_DIR",
    # transports. Here for injection and debugging; no verb requires naming one.
    "LinkerClient", "CookjohnClient", "DEFAULT_LINKER_URL", "DEFAULT_COOKJOHN_URL",
]
