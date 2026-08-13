"""Read-only item-state lookups: existence, trash state, type, parents, children.

Exists to serve the WRITE gate in `../../../../writes` without putting any SQL
there. Every precondition a write needs is a read -- does this key resolve, is it
already in the trash, what type is it, what hangs off it -- and ZoteroSuite's rule
is that consumers do not duplicate Zotero SQL (README, "Rules of the road"). So
the reads live here, in the canonical read layer, and `zotero_writes` reaches
Zotero through exactly one channel: the linker plugin's HTTP endpoint.

core stays READ-ONLY. Nothing here opens the database read-write.

WHICH READ MODE SERVED THE ANSWER
---------------------------------
It is reported rather than hidden, because the two modes do not answer the same
question. `mode=ro` is the honest read: it respects locking and plays back a hot
rollback journal. But Zotero holds the database locked while it runs, and the
write path REQUIRES Zotero to be running (that precondition inverts relative to
Calibre, which requires its GUI closed) -- so in normal operation `mode=ro` raises
and the `immutable=1` fallback is what actually serves the row. Measured
2026-08-13 against the live 329 MB database with Zotero 9.0.6 up:

    mode=ro        FAIL OperationalError: database is locked
    immutable=1    OK  deletedItems=277 items=3405

`immutable=1` tells SQLite the file cannot change, so it skips locking and skips
hot-journal playback: a point-in-time view of the main file's pages. That is fine
for a precondition -- a stale read blocks a write that would have been fine, which
is the safe direction -- but it is NOT authority for "the write did not land".
`ItemStates.read_mode` travels with the answer so the write path can tell a
verified result from a snapshot that may simply be behind.

LIBRARY SCOPE
-------------
Every lookup is scoped to ONE library, default the user library (libraryID 1,
confirmed `type='user'` in `libraries`). This matches
`Zotero.Items.getByLibraryAndKey(Zotero.Libraries.userLibraryID, key)`, which is
how `linker/bootstrap.js` resolves the same keys. Unscoped lookups would make the
precheck and the plugin disagree about which keys exist -- this database has six
group libraries as well.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from pathlib import Path

from .annotations import DEFAULT_ZOTERO_DB, ZoteroAnnotationError

USER_LIBRARY_ID = 1


@dataclass(frozen=True)
class ItemState:
    """One item as the write gate needs to see it."""

    key: str
    exists: bool
    trashed: bool
    item_type: str = ""
    title: str = ""
    parent_key: str | None = None
    child_keys: tuple[str, ...] = ()
    # Trashing does not change collection membership, so this is recorded before a
    # write purely so a restore can be checked against it rather than assumed.
    collection_count: int = 0


@dataclass(frozen=True)
class ItemStates:
    """A batch of lookups, plus which read mode produced them."""

    states: dict[str, ItemState]
    read_mode: str

    def __getitem__(self, key: str) -> ItemState:
        return self.states[key]

    def get(self, key: str) -> ItemState | None:
        return self.states.get(key)

    @property
    def missing(self) -> tuple[str, ...]:
        return tuple(k for k, s in self.states.items() if not s.exists)

    @property
    def trashed(self) -> tuple[str, ...]:
        return tuple(k for k, s in self.states.items() if s.exists and s.trashed)

    @property
    def live(self) -> tuple[str, ...]:
        return tuple(k for k, s in self.states.items() if s.exists and not s.trashed)


class ZoteroItemStore:
    def __init__(
        self,
        db_path: str | Path | None = None,
        *,
        library_id: int = USER_LIBRARY_ID,
        busy_timeout_ms: int = 5000,
    ):
        # Resolved at CALL time rather than as a default argument. `db_path=
        # DEFAULT_ZOTERO_DB` in the signature binds the constant when the `def`
        # executes, so a test redirecting the constant to keep itself off the REAL
        # library would silently still open the real library.
        self.db_path = Path(db_path or DEFAULT_ZOTERO_DB).expanduser()
        self.library_id = library_id
        self.busy_timeout_ms = busy_timeout_ms

    def item_states(self, keys: list[str] | tuple[str, ...]) -> ItemStates:
        """Look up every key at once. Keys that do not resolve come back exists=False.

        A key absent from this library is reported, not omitted: the caller has to
        be able to tell "you asked about 5 keys and 2 are not here" from "you asked
        about 3 keys".
        """
        wanted = list(dict.fromkeys(keys))
        if not wanted:
            return ItemStates(states={}, read_mode="none")

        conn, mode = self._connect()
        try:
            placeholders = ",".join("?" * len(wanted))
            rows = conn.execute(
                f"""
                SELECT i.key,
                       it.typeName,
                       COALESCE(idv.value, n.title, ''),
                       parent.key,
                       (SELECT COUNT(*) FROM collectionItems ci WHERE ci.itemID = i.itemID),
                       (SELECT COUNT(*) FROM deletedItems d WHERE d.itemID = i.itemID)
                  FROM items i
                  JOIN itemTypes it ON it.itemTypeID = i.itemTypeID
             LEFT JOIN itemAttachments a ON a.itemID = i.itemID
             LEFT JOIN itemNotes n ON n.itemID = i.itemID
             LEFT JOIN itemAnnotations ann ON ann.itemID = i.itemID
             LEFT JOIN items parent
                    ON parent.itemID = COALESCE(a.parentItemID, n.parentItemID, ann.parentItemID)
             LEFT JOIN fields f ON f.fieldName = 'title'
             LEFT JOIN itemData idt ON idt.itemID = i.itemID AND idt.fieldID = f.fieldID
             LEFT JOIN itemDataValues idv ON idv.valueID = idt.valueID
                 WHERE i.libraryID = ? AND i.key IN ({placeholders})
                """,
                (self.library_id, *wanted),
            ).fetchall()
            children = self._children(conn, wanted)
        finally:
            conn.close()

        found = {
            row[0]: ItemState(
                key=row[0],
                exists=True,
                trashed=bool(row[5]),
                item_type=row[1] or "",
                title=row[2] or "",
                parent_key=row[3],
                child_keys=tuple(children.get(row[0], ())),
                collection_count=int(row[4] or 0),
            )
            for row in rows
        }
        return ItemStates(
            states={k: found.get(k, ItemState(key=k, exists=False, trashed=False)) for k in wanted},
            read_mode=mode,
        )

    def _children(self, conn: sqlite3.Connection, keys: list[str]) -> dict[str, list[str]]:
        """Attachments and notes filed under each key.

        Two SELECTs UNIONed rather than one join: `itemAttachments` and `itemNotes`
        are separate tables with separate parentItemID columns, and joining both to
        `items` in one pass fans the rows out.
        """
        placeholders = ",".join("?" * len(keys))
        rows = conn.execute(
            f"""
            SELECT p.key, c.key
              FROM itemAttachments x
              JOIN items c ON c.itemID = x.itemID
              JOIN items p ON p.itemID = x.parentItemID
             WHERE p.libraryID = ? AND p.key IN ({placeholders})
            UNION ALL
            SELECT p.key, c.key
              FROM itemNotes n
              JOIN items c ON c.itemID = n.itemID
              JOIN items p ON p.itemID = n.parentItemID
             WHERE p.libraryID = ? AND p.key IN ({placeholders})
            """,
            (self.library_id, *keys, self.library_id, *keys),
        ).fetchall()
        out: dict[str, list[str]] = {}
        for parent_key, child_key in rows:
            out.setdefault(parent_key, []).append(child_key)
        return out

    def trashed_count(self) -> int:
        """How many items are in the trash. Cheap sanity signal around a write."""
        conn, _ = self._connect()
        try:
            return int(conn.execute("SELECT COUNT(*) FROM deletedItems").fetchone()[0])
        finally:
            conn.close()

    # ----------------------------------------------------------------------
    # prior state, for writes that overwrite rather than add
    #
    # Three of cookjohn's write tools REPLACE a whole collection of values rather
    # than merging: `write_tag` action='set', and `write_metadata`'s `creators`
    # array. calibre-core has the scar from exactly this shape -- a plain
    # identifier write replaced the whole set and silently deleted book 256's
    # `zotero` identifier, which was the link to its Zotero item. Nothing in the
    # output announced the loss. So a caller about to replace has to be able to see
    # what is there first, and the write path has to be able to record it.
    # ----------------------------------------------------------------------

    def item_tags(self, keys: list[str] | tuple[str, ...]) -> dict[str, tuple[str, ...]]:
        """Tags per item key, sorted. Keys with no tags map to an empty tuple."""
        wanted = list(dict.fromkeys(keys))
        if not wanted:
            return {}
        conn, _ = self._connect()
        try:
            placeholders = ",".join("?" * len(wanted))
            rows = conn.execute(
                f"""
                SELECT i.key, t.name
                  FROM items i
                  JOIN itemTags it ON it.itemID = i.itemID
                  JOIN tags t ON t.tagID = it.tagID
                 WHERE i.libraryID = ? AND i.key IN ({placeholders})
                """,
                (self.library_id, *wanted),
            ).fetchall()
        finally:
            conn.close()
        out: dict[str, list[str]] = {k: [] for k in wanted}
        for key, name in rows:
            out[key].append(name)
        return {k: tuple(sorted(v)) for k, v in out.items()}

    def item_fields(self, key: str) -> dict[str, str]:
        """Every populated metadata field on one item, as {fieldName: value}."""
        conn, _ = self._connect()
        try:
            return {
                name: value
                for name, value in conn.execute(
                    """
                    SELECT f.fieldName, v.value
                      FROM items i
                      JOIN itemData d ON d.itemID = i.itemID
                      JOIN fields f ON f.fieldID = d.fieldID
                      JOIN itemDataValues v ON v.valueID = d.valueID
                     WHERE i.libraryID = ? AND i.key = ?
                    """,
                    (self.library_id, key),
                )
            }
        finally:
            conn.close()

    def base_field_map(self, item_type: str) -> dict[str, str]:
        """{baseFieldName: actualFieldName} for one item type. Empty when it maps nothing.

        Zotero stores a handful of fields under a name that depends on the item type,
        with a shared BASE name used to address them generically. `publicationTitle` is
        the one that bites: on a `conferencePaper` the value lives in
        `proceedingsTitle`, on a `bookSection` in `bookTitle`, on a `webpage` in
        `websiteTitle` -- ten item types in the live library map that base alone
        (measured 2026-08-13). A write addressed to the base name lands in the mapped
        field, so reading the base name back finds nothing and a verifier comparing
        written names against stored names calls a successful write unverified.

        Read out of `baseFieldMappingsCombined` rather than hard-coded, for the reason
        the annotation reads already follow: the mapping is the database's own data and
        it moves with Zotero's schema version. A table this method invents would be a
        second answer that drifts.
        """
        conn, _ = self._connect()
        try:
            rows = conn.execute(
                """
                SELECT base.fieldName, actual.fieldName
                  FROM baseFieldMappingsCombined m
                  JOIN itemTypes t ON t.itemTypeID = m.itemTypeID
                  JOIN fields base ON base.fieldID = m.baseFieldID
                  JOIN fields actual ON actual.fieldID = m.fieldID
                 WHERE t.typeName = ?
                """,
                (item_type,),
            ).fetchall()
        except sqlite3.OperationalError:
            # The table is absent. Returning {} degrades to comparing names literally,
            # which is what this method exists to improve on -- so the caller loses the
            # improvement and nothing else. Raising would turn a SUCCESSFUL metadata
            # write into an exception on its verification step, which is the one outcome
            # worse than an unhelpful verdict.
            return {}
        finally:
            conn.close()
        return {base: actual for base, actual in rows}

    def item_creators(self, key: str) -> tuple[dict[str, str], ...]:
        """Creators on one item, IN ORDER.

        Order is part of the data -- first author is not an arbitrary member of a
        set -- so this returns a tuple ordered by `itemCreators.orderIndex` rather
        than a dict. `fieldMode = 1` is Zotero's single-field name (organisations),
        which is why an institutional author comes back as `name` rather than a
        split first/last.
        """
        conn, _ = self._connect()
        try:
            rows = conn.execute(
                """
                SELECT ct.creatorType, c.firstName, c.lastName, c.fieldMode
                  FROM items i
                  JOIN itemCreators ic ON ic.itemID = i.itemID
                  JOIN creators c ON c.creatorID = ic.creatorID
                  JOIN creatorTypes ct ON ct.creatorTypeID = ic.creatorTypeID
                 WHERE i.libraryID = ? AND i.key = ?
                 ORDER BY ic.orderIndex
                """,
                (self.library_id, key),
            ).fetchall()
        finally:
            conn.close()
        creators = []
        for creator_type, first, last, field_mode in rows:
            if field_mode == 1:
                creators.append({"creatorType": creator_type, "name": last or ""})
            else:
                creators.append(
                    {
                        "creatorType": creator_type,
                        "firstName": first or "",
                        "lastName": last or "",
                    }
                )
        return tuple(creators)

    def _connect(self) -> tuple[sqlite3.Connection, str]:
        """Open read-only, honest mode first. Returns the connection AND the mode.

        The fallback order is the one `importers/calibre2zotero/sync.py` already
        uses and DESIGN.md sanctions. What is different here is that the mode is
        RETURNED: a caller deciding whether a write landed needs to know it was
        told by a snapshot.
        """
        if not self.db_path.exists():
            raise ZoteroAnnotationError(f"Zotero database not found: {self.db_path}")
        try:
            conn = sqlite3.connect(f"file:{self.db_path}?mode=ro", uri=True, timeout=5.0)
            conn.execute(f"PRAGMA busy_timeout={int(self.busy_timeout_ms)}")
            # Touch a table: `connect` succeeds lazily, so the lock only surfaces on
            # the first real read. Without this the fallback never fires and the
            # caller gets "database is locked" from the middle of a query instead.
            conn.execute("SELECT 1 FROM items LIMIT 1").fetchone()
            return conn, "mode=ro"
        except sqlite3.Error:
            try:
                conn = sqlite3.connect(
                    f"file:{self.db_path}?mode=ro&immutable=1", uri=True, timeout=5.0
                )
                conn.execute("SELECT 1 FROM items LIMIT 1").fetchone()
                return conn, "immutable=1"
            except sqlite3.Error as exc:
                raise ZoteroAnnotationError(
                    f"Could not open Zotero database read-only: {exc}"
                ) from exc
