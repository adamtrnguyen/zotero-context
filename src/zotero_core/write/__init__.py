"""Zotero writes, as ONE CRUD surface. The single place ZoteroSuite mutates a library.

THE PROBLEM THIS ENDS
---------------------
Zotero's write capability was scattered across two plugins on two ports, and a caller
had to know which one served which verb:

    create / update / notes / tags / collections   cookjohn   :23121 (MCP JSON-RPC)
    linked attachments / trash / restore           linker     :23119 (plain HTTP)

Two consumers learned that split by copying a client -- `importers/calibre2zotero/
sync.py` and `calibre-zotero-jump/ui.py` carry near-identical MCP clients -- and they
drifted where it mattered most: they disagree about how to tell whether a book is
already in Zotero. One scans Zotero's `extra` for `calibre-uuid:`, the other trusts a
`zotero` identifier on the Calibre side. Meanwhile `linker/` v0.3.0 had been running
with `trash-items` and `restore-items` registered and ZERO consumers.

So the fix is not a delete client. A module owning only Delete would make it four
scattered places instead of three. This is the whole CRUD surface, and which transport
serves a verb is an implementation detail -- visible in the result's `transport` field
for debugging, never in a signature.

    create      create_item, link_attachment, import_attachment, write_note,
                create_collection
    read        NOT HERE -- `zotero_core.read` owns reads
    update      update_metadata, replace_creators, add_tags, remove_tags, set_tags,
                write_note(action="update"|"append"), update_collection,
                add_items_to_collection, remove_items_from_collection,
                move_items_between_collections
    delete      trash_items / restore_items, delete_collection

A LAYER NOW, NOT A PACKAGE (merged 2026-08-19)
----------------------------------------------
This docstring used to argue at length that the write half belonged in its OWN
distribution, against folding it into core. Three reasons were given; the merge
answers each rather than ignoring them:

  1. "core/ stays read-only forever" -- preserved exactly, as the `layers` contract
     `domain < read < write < interfaces`. The rule was about DIRECTION, and direction
     is what a layer expresses. Nothing here is importable from `zotero_core.read`.
  2. Import surface -- preserved. `zotero_core/__init__.py` exports the READ surface
     only; the two HTTP transports arrive solely via an explicit
     `from zotero_core.write import ...`. (The original form of this argument was
     already shaky: core's own `__init__` imported `bbt` and `bridge`, so a plain
     import dragged in `urllib` regardless.)
  3. The dependency direction is naturally acyclic -- still true, still the reason
     this works: every precondition a write needs is a read.

What forced the merge was the other half of the story. The read package never shipped
the reads: `items.py` and `duplicates.py` were exposed from neither its CLI nor its
MCP server, and collection and search queries were never written at all. Two
distributions could not fix that; they only guaranteed two pyprojects, two lockfiles,
and a test suite that lived here and reached core by accident.

WHAT IS ENFORCED
----------------
Zotero must be RUNNING with the plugin that verb needs -- the precondition that
INVERTS relative to calibre-core's closed-GUI rule, because every Zotero write channel
is code executing inside the application. Keys are shape-checked then resolved before
anything is sent. Creates are duplicate-checked. Operations that REPLACE a list rather
than adding to it refuse unless asked twice. Whatever is about to be overwritten is
journalled with the call that reverses it. Results are re-read rather than believed.

NOT HERE, DELIBERATELY: hard erase and empty-trash. docs/DESIGN.md excludes them and no
plugin exposes an endpoint for them. Nothing in this package can destroy an item -- the
strongest thing it can do is move one to the trash.
"""

from .collections import (
    add_items_to_collection,
    create_collection,
    delete_collection,
    move_items_between_collections,
    remove_items_from_collection,
    update_collection,
)
from .errors import ALL_REASONS, Reason, WriteBlocked
from .journal import DEFAULT_JOURNAL_DIR, copy_database, write_manifest
from .liveness import require_zotero, zotero_is_running
from .transports.cookjohn import DEFAULT_COOKJOHN_URL, CookjohnClient
from .transports.linker import DEFAULT_LINKER_URL, LinkerClient
from .verbs import (
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

# Grouped by CRUD concern rather than sorted -- the grouping IS the documentation of
# what this package is for, and the whole point is that the surface reads as one
# coherent thing rather than as two transports. RUF022 wants alphabetical, which
# would scatter exactly the structure being asserted.
__all__ = [  # noqa: RUF022
    # errors -- `code` is the machine-readable field an MCP layer branches on
    "WriteBlocked", "Reason", "ALL_REASONS",
    # CREATE
    "create_item", "link_attachment", "import_attachment", "write_note",
    "create_collection",
    # UPDATE -- additive verbs are plain; the two that REPLACE are named for it
    "update_metadata", "add_tags", "remove_tags",
    "set_tags", "replace_creators",
    "update_collection", "add_items_to_collection", "remove_items_from_collection",
    "move_items_between_collections",
    # DELETE -- trash is recoverable and restore ships alongside it. No hard erase.
    "trash_items", "restore_items", "delete_collection",
    # gates, exposed so a caller can pre-flight without writing
    "require_zotero", "zotero_is_running", "check_keys", "require_items",
    # undo -- a manifest by default; the 329 MB database copy is opt-in
    "write_manifest", "copy_database", "DEFAULT_JOURNAL_DIR",
    # transports. Here for injection and debugging; no verb requires naming one.
    "LinkerClient", "CookjohnClient", "DEFAULT_LINKER_URL", "DEFAULT_COOKJOHN_URL",
]
