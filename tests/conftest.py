"""Fixtures: a throwaway Zotero database, and two fake plugins.

THREE THINGS ARE STUBBED, FOR THE SAME REASON
---------------------------------------------
The database and BOTH plugins default to the live ones — `~/Zotero/zotero.sqlite`,
`:23119`, `:23121` — so a test that forgot to redirect would read Adam's real library
and POST at his running Zotero.

The liveness stub is autouse specifically because calibre-core learned this the hard
way: its GUI gate read real process state, so 287 tests passed with Calibre shut and 6
failed the moment it was opened. A gate that consults a real application makes that
application a test fixture. Here the sense is INVERTED — Zotero must be RUNNING for a
write to be possible — which makes the failure mode worse, not better: without the
stub the suite passes only while Zotero happens to be up, and the tests asserting a
refusal would pass for the wrong reason.

Tests that exercise the liveness gate itself request `zotero_down` or
`cookjohn_down`, which opt out.

WHY THE FAKES APPLY THEIR WRITES
--------------------------------
`FakeLinker` and `FakeCookjohn` mutate the fixture database the way the real plugins
mutate the real one — a row in `deletedItems`, a row in `itemTags`. That is what makes
the post-write verification step real. A mock that only recorded the call would make
every verification agree by construction, which is the one thing these tests must not
do: the gate exists because a plugin can report success without having done anything.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from zotero_context.items import ZoteroItemStore
from zotero_writes.cookjohn import CookjohnClient
from zotero_writes.linker import LinkerClient

SCHEMA = Path(__file__).parent / "fixtures" / "schema.sql"

PING = {
    "status": "ok",
    "plugin": "zotero-linker",
    "version": "0.3.0",
    "zoteroVersion": "9.0.6",
}

COOKJOHN_INFO = {"name": "zotero-integrated-mcp", "version": "1.1.0"}

TYPE_IDS = {"annotation": 1, "attachment": 3, "book": 7, "journalArticle": 22, "note": 28}


class ZoteroBuilder:
    """Builds items, children, fields, tags, creators, collections and trash rows."""

    def __init__(self, root: Path):
        self.path = root / "zotero.sqlite"
        root.mkdir(parents=True, exist_ok=True)
        con = sqlite3.connect(self.path)
        con.executescript(SCHEMA.read_text())
        con.commit()
        con.close()
        self._next_item = 1
        self._next_value = 1
        self._next_collection = 1
        self._next_tag = 1
        self._next_creator = 1
        self._next_field = 900
        self.ids: dict[str, int] = {}
        self.collections: dict[str, int] = {}

    # ---- construction ---------------------------------------------------

    def add(
        self,
        key: str,
        title: str = "A Paper",
        item_type: str = "journalArticle",
        *,
        trashed: bool = False,
        parent: str | None = None,
        library_id: int = 1,
        fields: dict[str, str] | None = None,
        tags: list[str] | None = None,
        creators: list[dict[str, str]] | None = None,
    ) -> int:
        item_id = self._next_item
        self._next_item += 1
        self.ids[key] = item_id

        con = sqlite3.connect(self.path)
        con.execute(
            "INSERT INTO items (itemID, itemTypeID, libraryID, key) VALUES (?,?,?,?)",
            (item_id, TYPE_IDS[item_type], library_id, key),
        )
        parent_id = self.ids.get(parent) if parent else None
        if item_type == "attachment":
            con.execute(
                "INSERT INTO itemAttachments (itemID, parentItemID, linkMode, contentType, path) "
                "VALUES (?,?,2,'application/pdf',?)",
                (item_id, parent_id, f"attachments:{key}.pdf"),
            )
        elif item_type == "note":
            con.execute(
                "INSERT INTO itemNotes (itemID, parentItemID, note, title) VALUES (?,?,?,?)",
                (item_id, parent_id, "<p>a note</p>", title),
            )
        elif item_type == "annotation":
            con.execute(
                "INSERT INTO itemAnnotations "
                "(itemID, parentItemID, type, sortIndex, position, isExternal) "
                "VALUES (?,?,1,'00000','{}',0)",
                (item_id, parent_id),
            )
        if trashed:
            con.execute("INSERT INTO deletedItems (itemID) VALUES (?)", (item_id,))
        con.commit()
        con.close()

        # A note keeps its title on itemNotes; everything else goes through
        # itemData/itemDataValues, exactly as Zotero stores it.
        if title and item_type != "note":
            self.set_field(key, "title", title)
        for name, value in (fields or {}).items():
            self.set_field(key, name, value)
        if tags:
            self.set_tags(key, tags)
        for index, creator in enumerate(creators or []):
            self.add_creator(key, creator, index)
        return item_id

    def _field_id(self, con: sqlite3.Connection, name: str) -> int:
        row = con.execute("SELECT fieldID FROM fields WHERE fieldName = ?", (name,)).fetchone()
        if row:
            return row[0]
        # Unknown field names get a synthetic id above the real range, so the seeded
        # real ids stay real and a test can still write an arbitrary field.
        self._next_field += 1
        con.execute(
            "INSERT INTO fields (fieldID, fieldName) VALUES (?,?)", (self._next_field, name)
        )
        return self._next_field

    def set_field(self, key: str, name: str, value: str) -> None:
        con = sqlite3.connect(self.path)
        try:
            field_id = self._field_id(con, name)
            row = con.execute(
                "SELECT valueID FROM itemDataValues WHERE value = ?", (value,)
            ).fetchone()
            if row:
                value_id = row[0]
            else:
                value_id = self._next_value
                self._next_value += 1
                con.execute(
                    "INSERT INTO itemDataValues (valueID, value) VALUES (?,?)", (value_id, value)
                )
            con.execute(
                "INSERT OR REPLACE INTO itemData (itemID, fieldID, valueID) VALUES (?,?,?)",
                (self.ids[key], field_id, value_id),
            )
            con.commit()
        finally:
            con.close()

    def add_creator(self, key: str, creator: dict[str, str], order: int = 0) -> None:
        con = sqlite3.connect(self.path)
        try:
            last = creator.get("lastName") or creator.get("name") or ""
            first = creator.get("firstName") or ""
            mode = 1 if creator.get("name") else 0
            row = con.execute(
                "SELECT creatorID FROM creators WHERE lastName=? AND firstName=? AND fieldMode=?",
                (last, first, mode),
            ).fetchone()
            if row:
                creator_id = row[0]
            else:
                creator_id = self._next_creator
                self._next_creator += 1
                con.execute(
                    "INSERT INTO creators (creatorID, firstName, lastName, fieldMode) "
                    "VALUES (?,?,?,?)",
                    (creator_id, first, last, mode),
                )
            con.execute(
                "INSERT OR REPLACE INTO itemCreators "
                "(itemID, creatorID, creatorTypeID, orderIndex) VALUES (?,?,1,?)",
                (self.ids[key], creator_id, order),
            )
            con.commit()
        finally:
            con.close()

    def replace_creators(self, key: str, creators: list[dict[str, str]]) -> None:
        con = sqlite3.connect(self.path)
        con.execute("DELETE FROM itemCreators WHERE itemID = ?", (self.ids[key],))
        con.commit()
        con.close()
        for index, creator in enumerate(creators):
            self.add_creator(key, creator, index)

    def set_tags(self, key: str, tags: list[str]) -> None:
        con = sqlite3.connect(self.path)
        try:
            con.execute("DELETE FROM itemTags WHERE itemID = ?", (self.ids[key],))
            for name in tags:
                row = con.execute("SELECT tagID FROM tags WHERE name = ?", (name,)).fetchone()
                if row:
                    tag_id = row[0]
                else:
                    tag_id = self._next_tag
                    self._next_tag += 1
                    con.execute("INSERT INTO tags (tagID, name) VALUES (?,?)", (tag_id, name))
                con.execute(
                    "INSERT OR IGNORE INTO itemTags (itemID, tagID, type) VALUES (?,?,0)",
                    (self.ids[key], tag_id),
                )
            con.commit()
        finally:
            con.close()

    def tags_of(self, key: str) -> list[str]:
        con = sqlite3.connect(f"file:{self.path}?mode=ro", uri=True)
        try:
            return sorted(
                name
                for (name,) in con.execute(
                    "SELECT t.name FROM itemTags it JOIN tags t ON t.tagID = it.tagID "
                    "WHERE it.itemID = ?",
                    (self.ids[key],),
                )
            )
        finally:
            con.close()

    def fields_of(self, key: str) -> dict[str, str]:
        con = sqlite3.connect(f"file:{self.path}?mode=ro", uri=True)
        try:
            return {
                name: value
                for name, value in con.execute(
                    "SELECT f.fieldName, v.value FROM itemData d "
                    "JOIN fields f ON f.fieldID = d.fieldID "
                    "JOIN itemDataValues v ON v.valueID = d.valueID WHERE d.itemID = ?",
                    (self.ids[key],),
                )
            }
        finally:
            con.close()

    def add_collection(self, name: str, key: str | None = None, parent: str | None = None) -> str:
        cid = self._next_collection
        self._next_collection += 1
        key = key or f"COLL{cid:04d}"
        self.collections[key] = cid
        con = sqlite3.connect(self.path)
        con.execute(
            "INSERT INTO collections (collectionID, collectionName, libraryID, key, "
            "parentCollectionID) VALUES (?,?,1,?,?)",
            (cid, name, key, self.collections.get(parent) if parent else None),
        )
        con.commit()
        con.close()
        return key

    def add_to_collection(self, key: str, collection_key: str) -> None:
        con = sqlite3.connect(self.path)
        con.execute(
            "INSERT OR IGNORE INTO collectionItems (collectionID, itemID) VALUES (?,?)",
            (self.collections[collection_key], self.ids[key]),
        )
        con.commit()
        con.close()

    def collection_members(self, collection_key: str) -> list[str]:
        con = sqlite3.connect(f"file:{self.path}?mode=ro", uri=True)
        try:
            return sorted(
                k
                for (k,) in con.execute(
                    "SELECT i.key FROM collectionItems ci JOIN items i ON i.itemID = ci.itemID "
                    "WHERE ci.collectionID = ?",
                    (self.collections[collection_key],),
                )
            )
        finally:
            con.close()

    def trash(self, key: str) -> None:
        con = sqlite3.connect(self.path)
        con.execute("INSERT OR IGNORE INTO deletedItems (itemID) VALUES (?)", (self.ids[key],))
        con.commit()
        con.close()

    def untrash(self, key: str) -> None:
        con = sqlite3.connect(self.path)
        con.execute("DELETE FROM deletedItems WHERE itemID = ?", (self.ids[key],))
        con.commit()
        con.close()

    def is_trashed(self, key: str) -> bool:
        con = sqlite3.connect(f"file:{self.path}?mode=ro", uri=True)
        try:
            return (
                con.execute(
                    "SELECT COUNT(*) FROM deletedItems WHERE itemID = ?", (self.ids[key],)
                ).fetchone()[0]
                > 0
            )
        finally:
            con.close()

    def store(self) -> ZoteroItemStore:
        return ZoteroItemStore(self.path)


class FakeLinker(LinkerClient):
    """A LinkerClient that records POSTs and applies them to the fixture database.

    Subclasses the real client rather than duck-typing one, so a signature change in
    `LinkerClient` breaks these tests instead of leaving them asserting against an
    interface that no longer exists.
    """

    def __init__(self, builder: ZoteroBuilder, *, apply: bool = True, missing=()):
        super().__init__("http://127.0.0.1:23119/zotero-linker")
        self.builder = builder
        self.apply = apply
        self.missing = list(missing)
        self.pings = 0
        self.posts: list[tuple[str, dict]] = []

    def ping(self) -> dict:
        self.pings += 1
        return dict(PING)

    def post(self, path: str, payload: dict) -> dict:
        self.posts.append((path, payload))
        if path == "link-attachment":
            key = f"ATT{len(self.posts):05d}"
            if self.apply:
                self.builder.add(
                    key, payload.get("title") or "linked", "attachment",
                    parent=payload["parentItemKey"],
                )
            return {"ok": True, "attachmentKey": key}
        keys = payload["itemKeys"]
        if self.apply:
            for key in keys:
                if key in self.missing:
                    continue
                if path == "trash-items":
                    self.builder.trash(key)
                else:
                    self.builder.untrash(key)
        count = len([k for k in keys if k not in self.missing])
        field = "trashed" if path == "trash-items" else "restored"
        return {"ok": True, field: count, "missing": list(self.missing)}


class FakeCookjohn(CookjohnClient):
    """A CookjohnClient that records tool calls and applies them to the fixture.

    Its replies deliberately mimic cookjohn's real shape-inconsistency: `write_item`
    answers with a nested `data.itemKey`, `create_collection` with a flat `key`. That
    inconsistency is why `_find_key` exists, and a fake that answered uniformly would
    let a `_find_key` regression pass.
    """

    def __init__(self, builder: ZoteroBuilder, *, apply: bool = True, no_key: bool = False):
        super().__init__("http://127.0.0.1:23121/mcp")
        self.builder = builder
        self.apply = apply
        self.no_key = no_key
        self.calls: list[tuple[str, dict]] = []
        self._created = 0

    def ping(self) -> dict:
        return dict(COOKJOHN_INFO)

    def call(self, tool: str, arguments: dict):
        self.calls.append((tool, arguments))
        handler = getattr(self, f"_{tool}", None)
        if handler is None:
            raise AssertionError(f"FakeCookjohn has no handler for {tool!r}")
        return handler(arguments)

    # ---- item tools -----------------------------------------------------

    def _write_item(self, arguments: dict):
        if self.no_key:
            return {"success": True, "message": "created"}
        self._created += 1
        if arguments.get("action") == "import":
            key = f"IMP{self._created:05d}"
            if self.apply:
                self.builder.add(
                    key, arguments.get("title") or "imported", "attachment",
                    parent=arguments["parentItemKey"],
                )
        else:
            key = f"NEW{self._created:05d}"
            if self.apply:
                self.builder.add(
                    key,
                    (arguments.get("fields") or {}).get("title", ""),
                    arguments.get("itemType", "journalArticle"),
                    fields={
                        k: v for k, v in (arguments.get("fields") or {}).items() if k != "title"
                    },
                    tags=arguments.get("tags"),
                    creators=arguments.get("creators"),
                )
        return {"action": arguments.get("action"), "success": True, "data": {"itemKey": key}}

    def _write_metadata(self, arguments: dict):
        key = arguments["itemKey"]
        if self.apply:
            for name, value in (arguments.get("fields") or {}).items():
                self.builder.set_field(key, name, value)
            if arguments.get("creators") is not None:
                self.builder.replace_creators(key, arguments["creators"])
        return {"success": True, "itemKey": key}

    def _write_tag(self, arguments: dict):
        key, action = arguments["itemKey"], arguments["action"]
        before = self.builder.tags_of(key)
        if self.apply:
            if action == "add":
                self.builder.set_tags(key, sorted(set(before) | set(arguments["tags"])))
            elif action == "remove":
                self.builder.set_tags(key, sorted(set(before) - set(arguments["tags"])))
            else:
                self.builder.set_tags(key, list(arguments["tags"]))
        return {"success": True, "before": before, "after": self.builder.tags_of(key)}

    def _write_note(self, arguments: dict):
        if arguments["action"] == "create":
            self._created += 1
            key = f"NOTE{self._created:04d}"
            if self.apply:
                self.builder.add(
                    key, "note", "note", parent=arguments.get("parentKey")
                )
            return {"success": True, "data": {"noteKey": key}}
        return {"success": True, "noteKey": arguments.get("noteKey")}

    # ---- collection tools ----------------------------------------------

    def _get_collections(self, arguments: dict):
        return [{"key": k, "name": n} for k, n in self._collection_rows()]

    def _search_collections(self, arguments: dict):
        query = (arguments.get("q") or "").casefold()
        return [
            {"key": k, "name": n, "parentCollection": None}
            for k, n in self._collection_rows()
            if query in n.casefold()
        ]

    def _collection_rows(self):
        con = sqlite3.connect(f"file:{self.builder.path}?mode=ro", uri=True)
        try:
            return con.execute("SELECT key, collectionName FROM collections").fetchall()
        finally:
            con.close()

    def _get_collection_details(self, arguments: dict):
        key = arguments["collectionKey"]
        for row_key, name in self._collection_rows():
            if row_key == key:
                return {"key": key, "name": name, "parentCollection": None}
        return {}

    def _get_collection_items(self, arguments: dict):
        return [{"key": k} for k in self.builder.collection_members(arguments["collectionKey"])]

    def _create_collection(self, arguments: dict):
        key = self.builder.add_collection(arguments["name"]) if self.apply else "COLLNEW1"
        return {"success": True, "key": key, "name": arguments["name"]}

    def _update_collection(self, arguments: dict):
        return {"success": True, "collectionKey": arguments["collectionKey"]}

    def _delete_collection(self, arguments: dict):
        key = arguments["collectionKey"]
        members = self.builder.collection_members(key)
        if self.apply and arguments.get("deleteItems"):
            for item_key in members:
                self.builder.trash(item_key)
        return {"success": True, "deleted": key, "itemsTrashed": len(members)
                if arguments.get("deleteItems") else 0}

    def _add_items_to_collection(self, arguments: dict):
        if self.apply:
            for item_key in arguments["itemKeys"]:
                self.builder.add_to_collection(item_key, arguments["collectionKey"])
        return {"success": True, "added": len(arguments["itemKeys"])}

    def _remove_items_from_collection(self, arguments: dict):
        return {"success": True, "removed": len(arguments["itemKeys"])}


@pytest.fixture()
def zotero(tmp_path):
    """An empty throwaway Zotero database."""
    return ZoteroBuilder(tmp_path / "Zotero")


@pytest.fixture()
def linker(zotero):
    """A fake linker plugin wired to the throwaway database."""
    return FakeLinker(zotero)


@pytest.fixture()
def cookjohn(zotero):
    """A fake cookjohn plugin wired to the throwaway database."""
    return FakeCookjohn(zotero)


@pytest.fixture(autouse=True)
def _never_touch_the_real_zotero(monkeypatch, request):
    """Point every default at nothing, so a test that forgets to inject fails loudly.

    Belt and braces with the fixtures above: a verb called with no `store`, `linker` or
    `cookjohn` constructs the real ones. Here the database default is redirected to a
    path that does not exist, both transport defaults point at a dead port, and
    `urlopen` is made to raise — so such a call cannot reach the live library even by
    accident.
    """
    if "real_defaults" in request.fixturenames:
        return
    monkeypatch.setattr(
        "zotero_context.items.DEFAULT_ZOTERO_DB", Path("/nonexistent/not-a-zotero.sqlite")
    )
    monkeypatch.setattr(
        "zotero_writes.linker.DEFAULT_LINKER_URL", "http://127.0.0.1:1/zotero-linker"
    )
    monkeypatch.setattr("zotero_writes.cookjohn.DEFAULT_COOKJOHN_URL", "http://127.0.0.1:1/mcp")

    def _refuse(*_args, **_kwargs):
        raise AssertionError("a test tried to open a real HTTP connection")

    monkeypatch.setattr("urllib.request.urlopen", _refuse)


@pytest.fixture(autouse=True)
def _journal_to_tmp(monkeypatch, tmp_path):
    """Keep test manifests out of the shared journal directory.

    Not tidiness. The journal is the audit trail for REAL writes, and before this the
    suite had written 216 manifests into `/tmp/zotero-write-journal` — enough noise to
    make it useless for answering "what did this package actually change". Redirecting
    it only works because `journal_dir` resolves the constant at call time; as a default
    argument it was bound at import and this fixture would have done nothing.
    """
    monkeypatch.setattr(
        "zotero_writes.journal.DEFAULT_JOURNAL_DIR", str(tmp_path / "journal")
    )


@pytest.fixture()
def zotero_down(monkeypatch):
    """Opt out: nothing answers on either port, as with Zotero closed."""
    import urllib.error

    def _refused(*_args, **_kwargs):
        raise urllib.error.URLError("Connection refused")

    monkeypatch.setattr("urllib.request.urlopen", _refused)


@pytest.fixture()
def zotero_up_plugin_missing(monkeypatch):
    """Opt out: Zotero's own server answers, but the plugin path 404s."""
    import io
    import urllib.error

    def _open(url, *_args, **_kwargs):
        target = url if isinstance(url, str) else url.full_url
        if target.rstrip("/").endswith("23119"):
            raise urllib.error.HTTPError(target, 404, "Not Found", {}, io.BytesIO(b""))
        raise urllib.error.HTTPError(target, 404, "Not Found", {}, io.BytesIO(b""))

    monkeypatch.setattr("urllib.request.urlopen", _open)
