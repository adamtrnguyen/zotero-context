"""The CRUD surface. Item verbs live here; collection verbs live in `collections.py`.

A CALLER NEVER PICKS A TRANSPORT
-------------------------------
That is the whole design constraint, and it is why this module is the surface rather
than one client per plugin. Zotero's write capability is split across two plugins on
two ports with no overlap:

    create / update / notes / tags   cookjohn zotero-mcp-plugin   :23121 (MCP JSON-RPC)
    linked attachments / trash / restore   zotero-linker           :23119 (plain HTTP)

Before this package a caller had to know that split, and two of them learned it by
copying a client. Which transport serves a verb is now an implementation detail of
that verb -- visible in the `transport` field of the result for debugging, never in
the signature.

Reads are NOT re-exported here. `zotero_core.read` owns them and is already
correct; wrapping them would create a second answer to "what is in the library",
which is the exact failure this package exists to end.

THE GATES
---------
  * Zotero must be RUNNING, with the plugin that verb needs (the inverted
    precondition -- see `liveness.py`)
  * keys are shape-checked, then RESOLVED against the library before anything is sent
  * a create is duplicate-checked first
  * an operation that REPLACES a list refuses unless asked twice
  * whatever is about to be overwritten is journalled with its inverse
  * the result is re-read rather than believed

WHAT REPLACES RATHER THAN MERGES, AND WHY IT IS GATED
----------------------------------------------------
Two of cookjohn's tools destroy data as a side effect of setting data:

  `write_tag` action='set'        replaces every tag on the item
  `write_metadata` creators=[...]  replaces every creator on the item

calibre-core carries the scar from exactly this shape: `calibredb set_metadata
--field identifiers:` REPLACES the identifier set, and using it to add an ISBN
silently deleted book 256's `zotero` identifier -- the link to its Zotero item.
Nothing in the output announced the loss. So `add_tags` and `remove_tags` are the
normal path and read-modify-write against the live tag list; `set_tags` and
`replace_creators` exist, are separately named, and refuse without `force=True`.
Naming them separately is the point: dropping a value should be a decision, not a
side effect of setting a different one.
"""

from __future__ import annotations

import os
import re

from ..read.duplicates import check_duplicate
from ..read.items import ZoteroItemStore
from .errors import Reason, WriteBlocked
from .journal import copy_database, write_manifest
from .liveness import require_zotero
from .transports.cookjohn import CookjohnClient, find_key
from .transports.linker import LinkerClient

# Eight characters, uppercase alphanumeric. Zotero's actual alphabet is narrower --
# all 3405 keys in the live library use only `23456789ABCDEFGHIJKLMNPQRSTUVWXYZ`
# (no 0, 1, or O) -- but this gate stays at [A-Z0-9] deliberately. A stricter class
# could only add a way to WRONGLY refuse a key some future Zotero mints, and it would
# buy nothing: a typo that lands inside the alphabet is caught by the existence gate,
# which is the check that actually protects the caller.
_KEY_RE = re.compile(r"^[A-Z0-9]{8}$")

# `write_metadata`'s own description: "Only works on regular items, not notes or
# attachments." Enforced here rather than left to the plugin, because this package
# can say WHICH item and what type it actually is.
_NOT_REGULAR = ("note", "attachment", "annotation")


# --------------------------------------------------------------------------
# shared gates
# --------------------------------------------------------------------------

def check_keys(item_keys) -> list[str]:
    """Normalise and shape-check a batch. Order preserved, duplicates dropped.

    Duplicates are dropped rather than refused: asking to trash the same key twice is
    a harmless caller-side accident, and passing it through would make the plugin's
    reported count disagree with the number of items affected -- which is the number
    the verification step compares against.
    """
    if isinstance(item_keys, str):
        # A string is iterable, so without this `trash_items("ABCD2345")` becomes
        # eight one-character keys and eight confusing shape failures.
        item_keys = [item_keys]
    keys = list(dict.fromkeys(item_keys or []))
    if not keys:
        raise WriteBlocked(Reason.NO_ITEM_KEYS, "no item keys given — refusing an empty write")
    bad = [k for k in keys if not isinstance(k, str) or not _KEY_RE.match(k)]
    if bad:
        raise WriteBlocked(
            Reason.MALFORMED_ITEM_KEY,
            f"not Zotero item keys (expected 8 uppercase alphanumerics): {bad}",
            {"malformed": bad, "given": keys},
        )
    return keys


def require_items(store: ZoteroItemStore, keys: list[str]):
    """Every key must resolve in the user library, or the whole batch is refused.

    `linker/bootstrap.js` resolves each key with `Zotero.Items.getByLibraryAndKey`,
    collects the ones that resolve, and 404s ONLY when none of them do (`if
    (ids.length === 0)`). Give it five keys of which two are typos and it trashes
    three, returns HTTP 200 `{"ok": true, "trashed": 3, "missing": [...]}`, and the
    caller sees success. That is the same defect class as `calibredb add` exiting 0
    having added nothing, and it is why resolution happens before the POST.

    Scoped to the user library because that is what the plugin resolves against --
    this database also holds six group libraries, and an unscoped precheck would
    confirm keys the plugin then cannot find.
    """
    states = store.item_states(keys)
    if states.missing:
        raise WriteBlocked(
            Reason.UNKNOWN_ITEM_KEYS,
            f"no item in the user library for {list(states.missing)} — refusing the whole "
            "batch, because the plugin would apply the rest and report success",
            {"missing": list(states.missing), "given": keys, "read_mode": states.read_mode},
        )
    return states


def _require_regular_item(states, key: str) -> None:
    if states[key].item_type in _NOT_REGULAR:
        raise WriteBlocked(
            Reason.WRONG_ITEM_TYPE,
            f"{key} is a {states[key].item_type}, and metadata fields only exist on "
            "regular items — notes carry their content, attachments carry their file",
            {"item_key": key, "item_type": states[key].item_type},
        )


class _Session:
    """The transports and the read store for one operation.

    Exists so every verb takes the same three optional injection points without
    repeating the construction, and so tests can hand in fakes for both plugins.
    """

    def __init__(self, linker=None, cookjohn=None, store=None):
        self.linker = linker or LinkerClient()
        self.cookjohn = cookjohn or CookjohnClient()
        self.store = store or ZoteroItemStore()

    def require(self, *needs: str) -> dict:
        return require_zotero(needs=needs, linker=self.linker, cookjohn=self.cookjohn)


# --------------------------------------------------------------------------
# CREATE
# --------------------------------------------------------------------------

def _create_preflight(
    item_type: str,
    fields: dict[str, str],
    collection_key: str | None,
    calibre_uuid: str | None,
) -> dict[str, str]:
    """The shape checks that run before Zotero is touched. Returns the fields to send.

    Split out of `create_item` because the checks and the write are separate concerns
    and the combined function tripped ruff's complexity ceiling -- which is a fair
    signal here rather than a nuisance: it is the part a reader wants to audit on its
    own.
    """
    if not item_type or not isinstance(item_type, str):
        raise WriteBlocked(Reason.UNKNOWN_ITEM_TYPE, "item_type is required")
    fields = dict(fields or {})
    if not fields.get("title", "").strip():
        raise WriteBlocked(
            Reason.MISSING_REQUIRED_FIELD,
            "title is mandatory — an untitled item is near-unfindable in the Zotero GUI",
            {"given_fields": sorted(fields)},
        )
    if collection_key and not _KEY_RE.match(collection_key):
        raise WriteBlocked(
            Reason.MALFORMED_ITEM_KEY,
            f"{collection_key!r} is not a collection key",
            {"collection_key": collection_key},
        )
    if calibre_uuid:
        stamp = f"calibre-uuid: {calibre_uuid}"
        existing = fields.get("extra", "")
        # Appended, never assigned over: `extra` is a free-text field that carries other
        # people's conventions (BBT's Citation Key lives there), and replacing it to add
        # one line would delete them.
        fields["extra"] = f"{existing}\n{stamp}".strip() if existing else stamp
    return fields


def create_item(
    item_type: str,
    fields: dict[str, str],
    *,
    creators: list[dict[str, str]] | None = None,
    tags: list[str] | None = None,
    collection_key: str | None = None,
    calibre_uuid: str | None = None,
    force: bool = False,
    linker=None,
    cookjohn=None,
    store=None,
) -> dict:
    """Create a regular item, refusing a guaranteed duplicate.

    `title` is mandatory. cookjohn will create a titleless item quite happily, and it
    is then near-unfindable in the GUI -- the analogue of calibre-core's mandatory
    title/authors rule, which exists because `calibredb add` with no metadata parses
    the FILENAME and silently inverts the record.

    `calibre_uuid` stamps `calibre-uuid: <uuid>` into `extra` and is ALSO the dedupe
    key checked before creating. That is the fix for the divergence this package was
    built to end: `importers/calibre2zotero` deduped on this stamp while
    `calibre-zotero-jump` deduped on a `zotero` identifier held on the Calibre side,
    and the two disagree precisely when a push half-succeeded.

    force=True downgrades a duplicate `block` to a warning carried in the result.
    """
    session = _Session(linker, cookjohn, store)
    fields = _create_preflight(item_type, fields, collection_key, calibre_uuid)
    info = session.require("cookjohn")
    dup = check_duplicate(
        session.store,
        title=fields.get("title"),
        doi=fields.get("DOI"),
        isbn=fields.get("ISBN"),
        calibre_uuid=calibre_uuid,
        creators=creators or (),
    )
    if dup["verdict"] == "block" and not force:
        raise WriteBlocked(
            Reason.DUPLICATE_ITEM,
            f"an item with the same identifier already exists — refusing to create "
            f"a second ({[s['signal'] for s in dup['signals']]})",
            {"duplicate_check": dup, "hint": "force=True"},
        )

    arguments: dict = {"action": "create", "itemType": item_type, "fields": fields}
    if creators:
        arguments["creators"] = list(creators)
    if tags:
        arguments["tags"] = list(tags)
    reply = session.cookjohn.call("write_item", arguments)
    item_key = find_key(reply)
    if not item_key:
        # cookjohn can answer without a key. Reporting ok=True here would make a
        # no-op indistinguishable from a create -- calibre-core's `add_book` guards
        # the same way against `calibredb` exiting 0 with no id.
        raise WriteBlocked(
            Reason.COOKJOHN_RETURNED_NO_KEY,
            "write_item returned no item key — nothing verifiable was created",
            {"reply": reply},
        )

    result = {
        "ok": True,
        "op": "create_item",
        "transport": "cookjohn",
        "item_key": item_key,
        "item_type": item_type,
        "title": fields.get("title"),
        "duplicate_check": dup,
        "cookjohn": reply,
        "undo_call": f"trash_items(['{item_key}'])",
        "versions": info,
    }
    if collection_key:
        from .collections import add_items_to_collection

        result["collection"] = add_items_to_collection(
            collection_key, [item_key], linker=session.linker,
            cookjohn=session.cookjohn, store=session.store,
        )
    # Verify by READING it back: the create is only real if the item resolves.
    after = session.store.item_states([item_key])
    result["verification"] = {
        "verified": bool(after[item_key].exists),
        "read_mode": after.read_mode,
    }
    if not after[item_key].exists and after.read_mode == "mode=ro":
        raise WriteBlocked(
            Reason.VERIFICATION_FAILED,
            f"write_item reported {item_key} but no such item is in the library",
            {"item_key": item_key, "reply": reply},
        )
    return result


def link_attachment(
    parent_item_key: str,
    path: str,
    *,
    title: str | None = None,
    linker=None,
    cookjohn=None,
    store=None,
) -> dict:
    """Attach a file BY REFERENCE — no copy into ~/Zotero/storage.

    This is the endpoint the linker plugin was written for and the reason it exists
    at all: cookjohn's `write_item` import action makes a stored COPY, and the
    Connector API only accepts raw bytes, so neither can link. For a 20 GB Calibre
    library mirrored into Zotero that distinction is the whole feature.

    The file must exist and the path must be ABSOLUTE. Zotero resolves a relative
    path against its own working directory, not the caller's, so a relative path
    produces an attachment that points at nothing -- and it fails silently, because
    linking never reads the file.
    """
    session = _Session(linker, cookjohn, store)
    check_keys([parent_item_key])
    if not os.path.isabs(path):
        raise WriteBlocked(
            Reason.FILE_NOT_FOUND,
            f"{path!r} is not absolute — Zotero resolves a relative path against its "
            "own working directory, producing an attachment that points at nothing",
            {"path": path},
        )
    if not os.path.exists(path):
        raise WriteBlocked(
            Reason.FILE_NOT_FOUND,
            f"no file at {path} — a linked attachment records the path without reading "
            "it, so this would create a broken link that looks fine until it is opened",
            {"path": path},
        )
    info = session.require("linker")
    require_items(session.store, [parent_item_key])
    reply = session.linker.post(
        "link-attachment",
        {"parentItemKey": parent_item_key, "path": path, "title": title or os.path.basename(path)},
    )
    return {
        "ok": True,
        "op": "link_attachment",
        "transport": "linker",
        "parent_item_key": parent_item_key,
        "attachment_key": reply.get("attachmentKey"),
        "path": path,
        "linker": reply,
        "undo_call": f"trash_items(['{reply.get('attachmentKey')}'])",
        "versions": info,
    }


def import_attachment(
    parent_item_key: str,
    path: str,
    *,
    title: str | None = None,
    linker=None,
    cookjohn=None,
    store=None,
) -> dict:
    """Attach a file BY COPY into Zotero's storage. Contrast `link_attachment`.

    Kept as a distinct verb rather than a flag because the choice has a lasting
    consequence, not a stylistic one: an import duplicates the bytes into
    `~/Zotero/storage` and those bytes then sync to the NAS over WebDAV. For a
    library already on disk elsewhere that is the wrong default, which is why
    ZoteroSuite built the linker at all.
    """
    session = _Session(linker, cookjohn, store)
    check_keys([parent_item_key])
    if not os.path.exists(path):
        raise WriteBlocked(
            Reason.FILE_NOT_FOUND, f"no file at {path}", {"path": path}
        )
    info = session.require("cookjohn")
    require_items(session.store, [parent_item_key])
    arguments = {"action": "import", "parentItemKey": parent_item_key, "filePath": path}
    if title:
        arguments["title"] = title
    reply = session.cookjohn.call("write_item", arguments)
    attachment_key = find_key(reply)
    if not attachment_key:
        # `create_item` raises exactly here for exactly this (COOKJOHN_RETURNED_NO_KEY);
        # this verb used to put the None straight into its envelope beside "ok": True,
        # so a caller got a success it could not act on and no way to tell whether the
        # attachment had been created.
        raise WriteBlocked(
            Reason.COOKJOHN_RETURNED_NO_KEY,
            "cookjohn accepted the import but returned no attachment key — the file may "
            "or may not have been attached; check the parent item",
            {"parent_item_key": parent_item_key, "path": path, "cookjohn": reply},
        )
    return {
        "ok": True,
        "op": "import_attachment",
        "transport": "cookjohn",
        "parent_item_key": parent_item_key,
        "attachment_key": attachment_key,
        "path": path,
        "cookjohn": reply,
        "versions": info,
    }


# --------------------------------------------------------------------------
# UPDATE
# --------------------------------------------------------------------------

def update_metadata(
    item_key: str,
    fields: dict[str, str],
    *,
    journal_dir: str | None = None,
    linker=None,
    cookjohn=None,
    store=None,
) -> dict:
    """Set metadata fields on one regular item, journalling the previous values.

    Fields MERGE -- only the named ones change -- so this is safe in the way
    `replace_creators` is not. What is journalled is the previous value of each field
    being written, which is the only record that an overwrite destroyed anything.

    Refuses on notes, attachments and annotations, naming the actual type. cookjohn's
    own description says metadata "only works on regular items"; catching it here
    turns a plugin-side failure into a precondition with a reason.
    """
    session = _Session(linker, cookjohn, store)
    check_keys([item_key])
    if not fields:
        raise WriteBlocked(
            Reason.NOTHING_TO_DO, "no fields given — refusing a no-op update",
            {"item_key": item_key},
        )
    info = session.require("cookjohn")
    states = require_items(session.store, [item_key])
    _require_regular_item(states, item_key)

    was = session.store.item_fields(item_key)
    unchanged = [k for k, v in fields.items() if was.get(k) == v]
    if len(unchanged) == len(fields):
        raise WriteBlocked(
            Reason.NOTHING_TO_DO,
            f"every field already holds the given value on {item_key} — refusing a "
            "no-op write",
            {"item_key": item_key, "fields": sorted(fields)},
        )

    # A field that DID NOT EXIST is not the same as a field holding "". Recording the
    # absent ones as `""` and handing that back as the undo would write empty strings
    # instead of removing the fields -- an inverse that leaves the item different from
    # how it started. cookjohn's `write_metadata` takes strings and has no remove, so
    # for those fields there IS no expressible inverse, and saying so is better than
    # emitting one that quietly does the wrong thing.
    overwritten = {k: was[k] for k in fields if k in was}
    added = sorted(k for k in fields if k not in was)

    manifest = write_manifest(
        "update_metadata",
        before={
            "item_key": item_key,
            "title": states[item_key].title,
            "fields": overwritten,
            "fields_absent_before": added,
            "note": (
                "fields listed in fields_absent_before did not exist; this path cannot "
                "remove a field, so the inverse only covers `fields`"
            ),
        },
        inverse=(
            f"update_metadata({item_key!r}, {overwritten!r})" if overwritten else None
        ),
        journal_dir=journal_dir,
    )
    reply = session.cookjohn.call("write_metadata", {"itemKey": item_key, "fields": fields})
    result = {
        "ok": True,
        "op": "update_metadata",
        "transport": "cookjohn",
        "item_key": item_key,
        "fields_written": fields,
        "fields_overwritten": overwritten,
        "fields_added": added,
        "cookjohn": reply,
        "undo_manifest": manifest,
        "undo_call": (
            f"update_metadata({item_key!r}, {overwritten!r})" if overwritten else None
        ),
        "versions": info,
    }
    result["verification"] = _verify_fields(
        session.store, item_key, fields, states[item_key].item_type
    )
    return result


def _verify_fields(
    store: ZoteroItemStore, item_key: str, written: dict[str, str], item_type: str = ""
) -> dict:
    """Re-read the fields and classify each one, tolerating Zotero's two rewrites.

    Strict equality here produces FALSE alarms, and neither case is hypothetical.

    THE VALUE CHANGES. Writing `date="2026"` to a real item on 2026-08-13 came back
    stored as `"2026-00-00 2026"`, because Zotero parses the date field into its own
    multipart form. An equality check reported that successful write as unverified and
    blamed the snapshot read -- an error message pointing at entirely the wrong thing.
    So a stored value that CONTAINS what was written counts as `normalized`, and the
    stored form is reported so the caller can see what Zotero made of their input.

    THE FIELD NAME CHANGES. Zotero maps a BASE field name onto the field the item type
    actually carries, and cookjohn writes through that mapping: `publicationTitle` on a
    `conferencePaper` is stored as `proceedingsTitle`, on a `bookSection` as
    `bookTitle`. `write_metadata` answers ok, the value is in the library, and a
    read-back of the name that was WRITTEN finds nothing -- so this reported a
    successful write as `disagreed` and sent the caller to check Zotero by hand
    (observed 2026-08-13 on a conferencePaper venue write). A field found under its
    mapped name is reported as `mapped_to_type_field`, which is deliberately NOT folded
    into `normalized_by_zotero`: the value is intact and it is the NAME that moved, and
    a caller re-reading the field later needs to be told which name to ask for.

    `item_type=""` skips the mapping lookup and compares names literally, which is the
    behaviour every caller had before the mapping was read.
    """
    now = store.item_fields(item_key)
    aliases = store.base_field_map(item_type) if item_type else {}
    normalized: dict[str, str] = {}
    mapped: dict[str, str] = {}
    missing: list[str] = []
    for key, value in written.items():
        stored = now.get(key)
        if stored is None and key in aliases:
            # Only when the written name resolved to nothing: a type that carries BOTH
            # names should be judged on the one that was actually asked for.
            stored = now.get(aliases[key])
            if stored is not None:
                mapped[key] = aliases[key]
        if stored == value:
            continue
        if stored and value and value in stored:
            normalized[key] = stored
        else:
            mapped.pop(key, None)
            missing.append(key)
    verdict: dict = {"verified": True if not missing else "unverified"}
    if normalized:
        verdict["normalized_by_zotero"] = normalized
    if mapped:
        verdict["mapped_to_type_field"] = mapped
    if missing:
        verdict["disagreed"] = sorted(missing)
        verdict["note"] = (
            "no trace of the written value came back; the post-write read may be an "
            "immutable snapshot lagging the commit, so check the item in Zotero"
        )
    return verdict


def replace_creators(
    item_key: str,
    creators: list[dict[str, str]],
    *,
    force: bool = False,
    journal_dir: str | None = None,
    linker=None,
    cookjohn=None,
    store=None,
) -> dict:
    """REPLACE every creator on an item. Refuses without force=True.

    Named for what it does, and gated, because cookjohn's `creators` argument does
    not merge: passing one author to add a co-author deletes the existing list. This
    is the same shape as the write that cost book 256 its `zotero` identifier in
    Calibre -- a set operation wearing an add operation's clothes.

    There is no `add_creator` here. Adding one means reading the list, appending, and
    replacing, which the caller can now do explicitly with `item_creators` -- and
    doing it for them would hide that the write is still a full replacement.
    """
    session = _Session(linker, cookjohn, store)
    check_keys([item_key])
    info = session.require("cookjohn")
    states = require_items(session.store, [item_key])
    _require_regular_item(states, item_key)

    was = session.store.item_creators(item_key)
    if not force:
        raise WriteBlocked(
            Reason.REFUSING_TO_REPLACE,
            f"this REPLACES all {len(was)} creator(s) on {item_key} rather than adding to "
            "them — pass force=True once you have read what is there",
            {
                "item_key": item_key,
                "current_creators": list(was),
                "would_become": creators,
                "hint": "force=True",
            },
        )
    manifest = write_manifest(
        "replace_creators",
        before={"item_key": item_key, "creators": list(was), "title": states[item_key].title},
        inverse=f"replace_creators({item_key!r}, {list(was)!r}, force=True)",
        journal_dir=journal_dir,
    )
    reply = session.cookjohn.call(
        "write_metadata", {"itemKey": item_key, "creators": list(creators)}
    )
    return {
        "ok": True,
        "op": "replace_creators",
        "transport": "cookjohn",
        "item_key": item_key,
        "creators_before": list(was),
        "creators_written": list(creators),
        "cookjohn": reply,
        "undo_manifest": manifest,
        "undo_call": f"replace_creators({item_key!r}, {list(was)!r}, force=True)",
        "versions": info,
    }


# --------------------------------------------------------------------------
# tags — additive by default, replacement gated
# --------------------------------------------------------------------------

def add_tags(item_key: str, tags: list[str], *, linker=None, cookjohn=None, store=None) -> dict:
    """Add tags, keeping the existing ones. No manifest: the inverse is `remove_tags`."""
    return _tag_op("add", item_key, tags, linker=linker, cookjohn=cookjohn, store=store)


def remove_tags(
    item_key: str,
    tags: list[str],
    *,
    journal_dir: str | None = None,
    linker=None,
    cookjohn=None,
    store=None,
) -> dict:
    """Remove specific tags, leaving the rest. Journalled — it destroys something."""
    return _tag_op(
        "remove", item_key, tags, journal_dir=journal_dir,
        linker=linker, cookjohn=cookjohn, store=store,
    )


def set_tags(
    item_key: str,
    tags: list[str],
    *,
    force: bool = False,
    journal_dir: str | None = None,
    linker=None,
    cookjohn=None,
    store=None,
) -> dict:
    """REPLACE every tag on an item. Refuses without force=True.

    Separate from `add_tags` on purpose. Tags in this library are curated knowledge
    that no online source carries, and `write_tag` action='set' deletes all of them
    to install a new list. A caller who wanted to add one and reached for 'set' would
    not find out until the tags were gone.
    """
    return _tag_op(
        "set", item_key, tags, force=force, journal_dir=journal_dir,
        linker=linker, cookjohn=cookjohn, store=store,
    )


def _tag_op(
    action: str,
    item_key: str,
    tags: list[str],
    *,
    force: bool = False,
    journal_dir: str | None = None,
    linker=None,
    cookjohn=None,
    store=None,
) -> dict:
    session = _Session(linker, cookjohn, store)
    check_keys([item_key])
    if not tags:
        raise WriteBlocked(
            Reason.NOTHING_TO_DO, f"no tags given — refusing a no-op {action}",
            {"item_key": item_key},
        )
    info = session.require("cookjohn")
    require_items(session.store, [item_key])
    was = session.store.item_tags([item_key])[item_key]

    if action == "set" and not force:
        raise WriteBlocked(
            Reason.REFUSING_TO_REPLACE,
            f"this REPLACES all {len(was)} tag(s) on {item_key} rather than adding to them "
            "— use add_tags to add, or pass force=True to replace deliberately",
            {"item_key": item_key, "current_tags": list(was), "would_become": tags,
             "hint": "force=True or add_tags()"},
        )
    if action == "add" and set(tags) <= set(was):
        raise WriteBlocked(
            Reason.NOTHING_TO_DO,
            f"{item_key} already carries {sorted(set(tags))} — refusing a no-op write",
            {"item_key": item_key, "current_tags": list(was)},
        )
    if action == "remove" and not (set(tags) & set(was)):
        raise WriteBlocked(
            Reason.NOTHING_TO_DO,
            f"{item_key} carries none of {sorted(set(tags))} — refusing a no-op write",
            {"item_key": item_key, "current_tags": list(was)},
        )

    manifest = None
    if action in ("set", "remove"):
        manifest = write_manifest(
            f"{action}_tags",
            before={"item_key": item_key, "tags": list(was)},
            inverse=f"set_tags({item_key!r}, {list(was)!r}, force=True)",
            journal_dir=journal_dir,
        )
    reply = session.cookjohn.call(
        "write_tag", {"action": action, "itemKey": item_key, "tags": list(tags)}
    )
    now = session.store.item_tags([item_key])[item_key]
    return {
        "ok": True,
        "op": f"{action}_tags",
        "transport": "cookjohn",
        "item_key": item_key,
        "tags_before": list(was),
        "tags_after": list(now),
        "cookjohn": reply,
        "undo_manifest": manifest,
        "undo_call": f"set_tags({item_key!r}, {list(was)!r}, force=True)",
        "versions": info,
    }


# --------------------------------------------------------------------------
# notes
# --------------------------------------------------------------------------

def _note_preflight(action, content, parent_item_key, note_key) -> None:
    """Shape checks for a note write, before Zotero is touched."""
    if action not in ("create", "update", "append"):
        raise WriteBlocked(
            Reason.MISSING_REQUIRED_FIELD,
            f"action must be create, update or append (got {action!r})",
        )
    if not (content or "").strip():
        raise WriteBlocked(Reason.MISSING_REQUIRED_FIELD, "note content is required")
    if action == "create" and parent_item_key:
        check_keys([parent_item_key])
    if action in ("update", "append"):
        if not note_key:
            raise WriteBlocked(
                Reason.MISSING_REQUIRED_FIELD, f"note_key is required to {action} a note"
            )
        check_keys([note_key])


def _note_existing_checks(session, action, parent_item_key, note_key, journal_dir) -> str | None:
    """Resolve whichever item the note write depends on, and journal a replacement.

    Returns the manifest path when one was written. Only `update` gets a manifest:
    `append` adds without destroying, and `create` has no prior state.
    """
    if action == "create":
        if parent_item_key:
            require_items(session.store, [parent_item_key])
        return None
    states = require_items(session.store, [note_key])
    if states[note_key].item_type != "note":
        raise WriteBlocked(
            Reason.WRONG_ITEM_TYPE,
            f"{note_key} is a {states[note_key].item_type}, not a note",
            {"item_key": note_key, "item_type": states[note_key].item_type},
        )
    if action != "update":
        return None
    return write_manifest(
        "write_note",
        before={
            "note_key": note_key,
            "title": states[note_key].title,
            "parent_key": states[note_key].parent_key,
            # The previous BODY is deliberately not copied -- see `write_note`'s
            # docstring. Recorded as a flag so a reader of the manifest is told what is
            # missing rather than assuming the body was captured.
            "body_recorded": False,
            "note": "content replaced; the previous body was not copied here",
        },
        inverse=None,
        journal_dir=journal_dir,
    )


def write_note(
    content: str,
    *,
    parent_item_key: str | None = None,
    note_key: str | None = None,
    action: str = "create",
    tags: list[str] | None = None,
    journal_dir: str | None = None,
    linker=None,
    cookjohn=None,
    store=None,
) -> dict:
    """Create, append to, or replace a note.

    `action='update'` REPLACES the note's content, so it is journalled -- but the
    journal records the note's key and title, NOT its previous body. That is a
    deliberate limit rather than an oversight: note bodies are read out of
    `itemNotes.note`, and copying a page of prose into /tmp on every edit turns a
    write journal into a document store. Use `append` when the old text matters.
    """
    session = _Session(linker, cookjohn, store)
    _note_preflight(action, content, parent_item_key, note_key)
    info = session.require("cookjohn")
    manifest = _note_existing_checks(
        session, action, parent_item_key, note_key, journal_dir
    )

    arguments: dict = {"action": action, "content": content}
    if parent_item_key and action == "create":
        arguments["parentKey"] = parent_item_key
    if note_key:
        arguments["noteKey"] = note_key
    if tags:
        arguments["tags"] = list(tags)
    reply = session.cookjohn.call("write_note", arguments)
    key = note_key or find_key(reply)
    return {
        "ok": True,
        "op": f"write_note:{action}",
        "transport": "cookjohn",
        "note_key": key,
        "parent_item_key": parent_item_key,
        "cookjohn": reply,
        "undo_manifest": manifest,
        "undo_call": f"trash_items(['{key}'])" if action == "create" and key else None,
        "versions": info,
    }


# --------------------------------------------------------------------------
# DELETE — trash and restore, the only removal this package can express
# --------------------------------------------------------------------------

def _trash_verdict(disagreed: list[str]) -> dict:
    """Post-write verdict for the trash flag, which Zotero does not normalise.

    A boolean either flipped or it did not, so unlike `_verify_fields` there is no
    third outcome here.

    Zotero holds the database locked while it runs, so `mode=ro` fails and the
    `immutable=1` fallback serves the row (measured 2026-08-13); that fallback is a
    point-in-time view. When such a read DISAGREES the honest report is "unverified"
    rather than "failed" -- claiming a write did not land on the authority of a snapshot
    that may simply be behind would be the mistake this module exists to avoid. Live
    evidence is that the snapshot IS fresh enough: every trash/restore round trip run
    against the real library read back immediately, all through `immutable=1`.
    """
    if not disagreed:
        return {"verified": True}
    return {
        "verified": "unverified",
        "disagreed": sorted(disagreed),
        "note": (
            "the post-write read came from an immutable snapshot, which can lag a "
            "just-committed write; check the item in Zotero rather than trusting this"
        ),
    }


def _resolve_batch(store, keys: list[str], *, want_trashed: bool, force: bool):
    """Existence + no-op gates. Returns (states, keys_to_send)."""
    states = require_items(store, keys)
    noop = [k for k in keys if states[k].trashed == want_trashed]
    if noop and not force:
        code = Reason.ALREADY_TRASHED if want_trashed else Reason.NOT_TRASHED
        verb = "already in the trash" if want_trashed else "not in the trash"
        raise WriteBlocked(
            code,
            f"{noop} {'is' if len(noop) == 1 else 'are'} {verb} — refusing a no-op write "
            "(pass force=True to skip them and proceed with the rest)",
            {
                "noop": noop,
                "titles": {k: states[k].title for k in noop},
                "read_mode": states.read_mode,
                "hint": "force=True",
            },
        )
    to_send = [k for k in keys if k not in set(noop)]
    if not to_send:
        raise WriteBlocked(
            Reason.NOTHING_TO_DO,
            "every key in the batch is already in the requested state — nothing to do",
            {"skipped": noop, "read_mode": states.read_mode},
        )
    return states, to_send


def _trash_op(
    op: str,
    endpoint: str,
    item_keys,
    *,
    want_trashed: bool,
    force: bool,
    copy_db: bool,
    journal_dir: str | None,
    linker,
    cookjohn,
    store,
) -> dict:
    """The shared body of trash_items and restore_items.

    They differ in exactly three things -- the endpoint, the state they aim for, and
    the count field the plugin names -- so the gates are written once. Two copies
    would be two places for the preconditions to drift, which is the defect that
    motivated this package.
    """
    session = _Session(linker, cookjohn, store)
    info = session.require("linker")
    keys = check_keys(item_keys)
    states, to_send = _resolve_batch(session.store, keys, want_trashed=want_trashed, force=force)
    skipped = [k for k in keys if k not in set(to_send)]

    inverse = "restore_items" if op == "trash_items" else "trash_items"
    manifest = write_manifest(
        op,
        before={
            "item_keys": to_send,
            "read_mode": states.read_mode,
            "items": [
                {
                    "key": k,
                    "title": states[k].title,
                    "item_type": states[k].item_type,
                    "trashed_before": states[k].trashed,
                    "parent_key": states[k].parent_key,
                    "child_keys": list(states[k].child_keys),
                    "collection_count": states[k].collection_count,
                }
                for k in to_send
            ],
        },
        inverse=f"{inverse}({to_send})",
        journal_dir=journal_dir,
    )
    db_backup = copy_database(journal_dir, session.store.db_path) if copy_db else None

    reply = session.linker.post(endpoint, {"itemKeys": to_send})
    # The plugin reports keys it could not resolve. The existence gate just confirmed
    # all of them, so anything here means the database read and the running Zotero
    # disagree -- a race. Either way the write was partial and saying so is the point.
    if reply.get("missing"):
        raise WriteBlocked(
            Reason.PARTIAL_APPLY,
            f"the plugin could not resolve {reply['missing']} even though they resolved in "
            "the database moments earlier — the write was partial",
            {"reply": reply, "sent": to_send, "undo_manifest": manifest},
        )

    after = session.store.item_states(to_send)
    disagreed = [k for k in to_send if after[k].exists and after[k].trashed != want_trashed]
    if disagreed and after.read_mode == "mode=ro":
        raise WriteBlocked(
            Reason.VERIFICATION_FAILED,
            f"the plugin reported success but {disagreed} did not change state",
            {"disagreed": disagreed, "read_mode": after.read_mode},
        )
    verification = _trash_verdict(disagreed)
    verification["read_mode"] = after.read_mode

    result = {
        "ok": True,
        "op": op,
        "transport": "linker",
        "item_keys": to_send,
        "skipped": skipped,
        "affected": [
            {
                "key": k,
                "title": states[k].title,
                "item_type": states[k].item_type,
                "child_keys": list(states[k].child_keys),
            }
            for k in to_send
        ],
        "linker": reply,
        "undo_manifest": manifest,
        "undo_call": f"{inverse}({to_send})",
        "verification": verification,
        "versions": info,
    }
    if db_backup:
        result["database_backup"] = db_backup
    return result


def trash_items(
    item_keys,
    *,
    force: bool = False,
    copy_db: bool = False,
    journal_dir: str | None = None,
    linker=None,
    cookjohn=None,
    store=None,
) -> dict:
    """Move items to the Zotero trash. Recoverable — see `restore_items`.

    Recoverable is a property of the endpoint, not a hope: `bootstrap.js` calls
    `Zotero.Items.trashTx`, which sets the trash flag inside a transaction. Nothing
    is erased, and the library already holds 277 trashed items, so this is a state
    the application is entirely used to.

    Works on any item key the running Zotero can resolve, INCLUDING attachments.
    That was the one thing nobody had ever run -- `bootstrap.js` uses
    `getByLibraryAndKey`, which accepts any item key, so it looked likely but was
    unverified. Now measured: linked-file attachment MAQ3PAG9 trashed and restored
    cleanly on 2026-08-13 (see README).

    force=True skips keys that are already trashed instead of refusing the batch. It
    never relaxes the liveness or existence gates.
    """
    return _trash_op(
        "trash_items", "trash-items", item_keys, want_trashed=True, force=force,
        copy_db=copy_db, journal_dir=journal_dir,
        linker=linker, cookjohn=cookjohn, store=store,
    )


def restore_items(
    item_keys,
    *,
    force: bool = False,
    copy_db: bool = False,
    journal_dir: str | None = None,
    linker=None,
    cookjohn=None,
    store=None,
) -> dict:
    """Bring items back out of the trash — the inverse of `trash_items`.

    Exists in the same pass as `trash_items` on purpose: an undo that has to be
    performed by hand in the GUI is not an undo the write path can promise, and
    without it the trash gate's "this is recoverable" claim would be someone else's
    problem.
    """
    return _trash_op(
        "restore_items", "restore-items", item_keys, want_trashed=False, force=force,
        copy_db=copy_db, journal_dir=journal_dir,
        linker=linker, cookjohn=cookjohn, store=store,
    )
