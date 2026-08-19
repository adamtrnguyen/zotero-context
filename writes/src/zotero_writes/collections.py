"""Collection CRUD. All of it rides cookjohn's MCP; the caller never sees that.

Collections are the one part of the surface where the plugin exposes a genuinely
dangerous option, and it is a keyword argument on an innocuous-sounding verb:

    delete_collection(collectionKey, deleteItems=True)

cookjohn's own description says "WARNING: This is a destructive operation... Set
deleteItems=true to also send items in the collection to trash." So one boolean turns
"delete this folder" into "trash everything filed in it". It is gated here, refused
without `force=True`, and the membership is journalled first -- because the inverse of
that cascade is a list of item keys nobody would otherwise have.

Deleting a collection is NOT deleting its items by default, which is worth stating
plainly: the items stay in the library, they just stop being filed there. That makes
the default form recoverable only in part -- the collection itself is gone from the
`collections` table and cannot be restored from the trash the way an item can, since
Zotero's trash holds items. So the manifest records the name, the parent, and every
member key, which together are enough to rebuild it by hand.
"""

from __future__ import annotations

import re

from zotero_context.items import ZoteroItemStore

from .cookjohn import CookjohnClient, find_key
from .errors import Reason, WriteBlocked
from .journal import write_manifest
from .linker import LinkerClient
from .liveness import require_zotero

_KEY_RE = re.compile(r"^[A-Z0-9]{8}$")


def _session(linker, cookjohn, store):
    return (
        linker or LinkerClient(),
        cookjohn or CookjohnClient(),
        store or ZoteroItemStore(),
    )


def _check_collection_key(collection_key: str) -> str:
    if not isinstance(collection_key, str) or not _KEY_RE.match(collection_key):
        raise WriteBlocked(
            Reason.MALFORMED_ITEM_KEY,
            f"{collection_key!r} is not a collection key (8 uppercase alphanumerics)",
            {"collection_key": collection_key},
        )
    return collection_key


def _find_collection(cookjohn, collection_key: str) -> dict:
    """Resolve a collection through cookjohn, refusing if it does not exist.

    Collections are resolved through the PLUGIN rather than through SQL, unlike items.
    That asymmetry is deliberate: the item path needs a bulk existence check over
    dozens of keys, where a query beats dozens of round trips, while a collection
    operation touches exactly one and the plugin's answer carries the name and parent
    that the manifest needs anyway.
    """
    reply = cookjohn.call("get_collection_details", {"collectionKey": collection_key})
    if not isinstance(reply, dict) or not reply:
        raise WriteBlocked(
            Reason.UNKNOWN_COLLECTION_KEY,
            f"no collection with key {collection_key}",
            {"collection_key": collection_key, "reply": reply},
        )
    return reply


def _member_keys(cookjohn, collection_key: str) -> list[str]:
    """Item keys filed in a collection, for the manifest. Best effort by design.

    A failure to enumerate must not block the operation: the enumeration exists to
    make an undo possible, and refusing to delete an empty-looking collection because
    a read hiccuped would be the gate protecting itself rather than the library. What
    it must not do is report an empty list as if it were confirmed -- so a failure
    surfaces as the string marker below, which reads as unknown in the manifest.
    """
    try:
        reply = cookjohn.call(
            "get_collection_items", {"collectionKey": collection_key, "limit": 1000}
        )
    except WriteBlocked:
        return ["<enumeration failed>"]
    keys: list[str] = []

    def walk(node):
        if isinstance(node, dict):
            for field in ("key", "itemKey"):
                value = node.get(field)
                if isinstance(value, str) and _KEY_RE.match(value):
                    keys.append(value)
                    return
            for value in node.values():
                walk(value)
        elif isinstance(node, list):
            for value in node:
                walk(value)

    walk(reply)
    return list(dict.fromkeys(keys))


def create_collection(
    name: str,
    *,
    parent_collection: str | None = None,
    linker=None,
    cookjohn=None,
    store=None,
) -> dict:
    """Create a collection, refusing a name that already exists at the same level.

    The duplicate check is a real gate rather than politeness: Zotero permits two
    sibling collections with identical names, and `calibre2zotero` and
    `calibre-zotero-jump` BOTH look up their target collection by NAME ("calibre").
    A second collection with that name makes which one they find depend on iteration
    order, and half the imports quietly land in the wrong folder.
    """
    linker, cookjohn, store = _session(linker, cookjohn, store)
    if not (name or "").strip():
        raise WriteBlocked(Reason.MISSING_REQUIRED_FIELD, "collection name is required")
    if parent_collection:
        _check_collection_key(parent_collection)
    info = require_zotero(needs=("cookjohn",), linker=linker, cookjohn=cookjohn)

    existing = cookjohn.call("search_collections", {"q": name, "limit": 50})
    clashes = _same_name_siblings(existing, name, parent_collection)
    if clashes:
        raise WriteBlocked(
            Reason.DUPLICATE_ITEM,
            f"a collection named {name!r} already exists at this level ({clashes}) — two "
            "siblings with one name make a name-based lookup nondeterministic",
            {"name": name, "existing": clashes},
        )

    arguments: dict = {"name": name}
    if parent_collection:
        arguments["parentCollection"] = parent_collection
    reply = cookjohn.call("create_collection", arguments)
    key = find_key(reply, prefer=("collectionKey", "key"))
    if not key:
        raise WriteBlocked(
            Reason.COOKJOHN_RETURNED_NO_KEY,
            "create_collection returned no collection key — nothing verifiable was created",
            {"reply": reply},
        )
    return {
        "ok": True,
        "op": "create_collection",
        "transport": "cookjohn",
        "collection_key": key,
        "name": name,
        "parent_collection": parent_collection,
        "cookjohn": reply,
        "undo_call": f"delete_collection({key!r})",
        "versions": info,
    }


def _same_name_siblings(reply, name: str, parent_collection: str | None) -> list[dict]:
    """Collections matching `name` exactly, at the same nesting level.

    Case-insensitive, because "Calibre" and "calibre" are the same folder to a human
    doing a name lookup and Zotero will happily hold both.
    """
    found: list[dict] = []
    wanted = name.strip().casefold()

    def walk(node):
        if isinstance(node, dict):
            node_name = node.get("name") or node.get("collectionName")
            if isinstance(node_name, str) and node_name.strip().casefold() == wanted:
                parent = node.get("parentCollection") or node.get("parentKey") or None
                if (parent or None) == (parent_collection or None):
                    found.append({"key": node.get("key"), "name": node_name})
            for value in node.values():
                walk(value)
        elif isinstance(node, list):
            for value in node:
                walk(value)

    walk(reply)
    return found


def update_collection(
    collection_key: str,
    *,
    name: str | None = None,
    parent_collection: str | None = None,
    journal_dir: str | None = None,
    linker=None,
    cookjohn=None,
    store=None,
) -> dict:
    """Rename or re-parent a collection, journalling the previous name and parent.

    `parent_collection=""` moves it to the top level, which is cookjohn's own
    convention. That is why the parameter defaults to None rather than "": the two
    mean different things here, and collapsing them would make every rename also
    move the collection to the root.
    """
    linker, cookjohn, store = _session(linker, cookjohn, store)
    _check_collection_key(collection_key)
    if name is None and parent_collection is None:
        raise WriteBlocked(
            Reason.NOTHING_TO_DO,
            "neither name nor parent_collection given — refusing a no-op update",
            {"collection_key": collection_key},
        )
    info = require_zotero(needs=("cookjohn",), linker=linker, cookjohn=cookjohn)
    before = _find_collection(cookjohn, collection_key)

    manifest = write_manifest(
        "update_collection",
        before={
            "collection_key": collection_key,
            "name": before.get("name") or before.get("collectionName"),
            "parent_collection": before.get("parentCollection") or before.get("parentKey"),
        },
        inverse=(
            f"update_collection({collection_key!r}, "
            f"name={(before.get('name') or before.get('collectionName'))!r})"
        ),
        journal_dir=journal_dir,
    )
    arguments: dict = {"collectionKey": collection_key}
    if name is not None:
        arguments["name"] = name
    if parent_collection is not None:
        arguments["parentCollection"] = parent_collection
    reply = cookjohn.call("update_collection", arguments)
    return {
        "ok": True,
        "op": "update_collection",
        "transport": "cookjohn",
        "collection_key": collection_key,
        "before": {"name": before.get("name") or before.get("collectionName")},
        "name": name,
        "parent_collection": parent_collection,
        "cookjohn": reply,
        "undo_manifest": manifest,
        "versions": info,
    }


def delete_collection(
    collection_key: str,
    *,
    delete_items: bool = False,
    force: bool = False,
    journal_dir: str | None = None,
    linker=None,
    cookjohn=None,
    store=None,
) -> dict:
    """Delete a collection. Items stay in the library unless `delete_items=True`.

    `delete_items=True` sends every member to the trash and REQUIRES force=True. One
    boolean turning "delete this folder" into "trash everything in it" is precisely
    the kind of option that should not be reachable by a default, and the membership
    is journalled first so the cascade has an inverse.

    Note the asymmetry with items, which is real and not hidden: a trashed item can
    be restored, a deleted collection cannot. Zotero's trash holds items. So the
    manifest records name, parent and every member key -- enough to rebuild the
    collection by hand, which is the only undo there is.
    """
    linker, cookjohn, store = _session(linker, cookjohn, store)
    _check_collection_key(collection_key)
    info = require_zotero(needs=("cookjohn",), linker=linker, cookjohn=cookjohn)
    before = _find_collection(cookjohn, collection_key)
    members = _member_keys(cookjohn, collection_key)

    if delete_items and not force:
        raise WriteBlocked(
            Reason.REFUSING_CASCADE_DELETE,
            f"delete_items=True would send {len(members)} item(s) to the trash as well as "
            "deleting the collection — pass force=True to mean it",
            {
                "collection_key": collection_key,
                "name": before.get("name") or before.get("collectionName"),
                "members": members,
                "hint": "force=True, or delete_items=False to keep the items",
            },
        )

    name = before.get("name") or before.get("collectionName")
    manifest = write_manifest(
        "delete_collection",
        before={
            "collection_key": collection_key,
            "name": name,
            "parent_collection": before.get("parentCollection") or before.get("parentKey"),
            "members": members,
            "delete_items": delete_items,
        },
        # A collection cannot be restored from the trash, so the inverse is a rebuild
        # rather than an undo, and the manifest is the only record of what to rebuild.
        inverse=(
            f"create_collection({name!r}) then add_items_to_collection(<new key>, {members!r})"
        ),
        journal_dir=journal_dir,
    )
    arguments: dict = {"collectionKey": collection_key}
    if delete_items:
        arguments["deleteItems"] = True
    reply = cookjohn.call("delete_collection", arguments)
    return {
        "ok": True,
        "op": "delete_collection",
        "transport": "cookjohn",
        "collection_key": collection_key,
        "name": name,
        "members_at_deletion": members,
        "items_trashed": bool(delete_items),
        "cookjohn": reply,
        "undo_manifest": manifest,
        "versions": info,
    }


def add_items_to_collection(
    collection_key: str,
    item_keys,
    *,
    linker=None,
    cookjohn=None,
    store=None,
) -> dict:
    """File items into a collection. No manifest — the inverse is a removal."""
    from .writes import check_keys, require_items

    linker, cookjohn, store = _session(linker, cookjohn, store)
    _check_collection_key(collection_key)
    keys = check_keys(item_keys)
    info = require_zotero(needs=("cookjohn",), linker=linker, cookjohn=cookjohn)
    require_items(store, keys)
    _find_collection(cookjohn, collection_key)
    reply = cookjohn.call(
        "add_items_to_collection", {"collectionKey": collection_key, "itemKeys": keys}
    )
    return {
        "ok": True,
        "op": "add_items_to_collection",
        "transport": "cookjohn",
        "collection_key": collection_key,
        "item_keys": keys,
        "cookjohn": reply,
        "undo_call": f"remove_items_from_collection({collection_key!r}, {keys!r})",
        "versions": info,
    }


def remove_items_from_collection(
    collection_key: str,
    item_keys,
    *,
    journal_dir: str | None = None,
    linker=None,
    cookjohn=None,
    store=None,
) -> dict:
    """Unfile items from a collection. The items stay in the library.

    Journalled, because it removes something -- and because the distinction between
    this and `trash_items` is exactly the one a caller in a hurry gets wrong. Nothing
    here deletes an item.
    """
    from .writes import check_keys, require_items

    linker, cookjohn, store = _session(linker, cookjohn, store)
    _check_collection_key(collection_key)
    keys = check_keys(item_keys)
    info = require_zotero(needs=("cookjohn",), linker=linker, cookjohn=cookjohn)
    require_items(store, keys)
    _find_collection(cookjohn, collection_key)

    manifest = write_manifest(
        "remove_items_from_collection",
        before={"collection_key": collection_key, "item_keys": keys},
        inverse=f"add_items_to_collection({collection_key!r}, {keys!r})",
        journal_dir=journal_dir,
    )
    reply = cookjohn.call(
        "remove_items_from_collection", {"collectionKey": collection_key, "itemKeys": keys}
    )
    return {
        "ok": True,
        "op": "remove_items_from_collection",
        "transport": "cookjohn",
        "collection_key": collection_key,
        "item_keys": keys,
        "cookjohn": reply,
        "undo_manifest": manifest,
        "undo_call": f"add_items_to_collection({collection_key!r}, {keys!r})",
        "versions": info,
    }
