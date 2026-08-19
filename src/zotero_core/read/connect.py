"""Opening zotero.sqlite read-only, and saying which mode answered.

ONE opener for the whole read layer. This logic lived as `ZoteroItemStore._connect`,
and `duplicates.check_duplicate` already reached across to call it -- the package's only
cross-module private access. `collections` and `search` would have been the third and
fourth, so it is a module now rather than a private that everyone pretends not to touch.

WHY THE MODE IS RETURNED
------------------------
Zotero uses a ROLLBACK JOURNAL, not WAL. With the app running, a plain `mode=ro` read
intermittently loses to the writer and fails with "database is locked (5)" -- observed
live, not theorised. `immutable=1` always answers, but it answers from a POINT-IN-TIME
snapshot: it tells sqlite the file cannot change, so the journal is ignored.

Those two are not interchangeable, and the difference is invisible in the rows. A caller
checking whether a write landed needs to know it was told by a snapshot, so every read in
this layer carries `read_mode` out to its caller rather than quietly picking one.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

from .annotations import ZoteroAnnotationError

# Zotero's own user library. Group libraries get their own ids; every query in this layer
# is scoped, because an unscoped one silently mixes a group library into a personal count.
USER_LIBRARY_ID = 1

DEFAULT_BUSY_TIMEOUT_MS = 5000


def open_readonly(
    db_path: str | Path,
    *,
    busy_timeout_ms: int = DEFAULT_BUSY_TIMEOUT_MS,
) -> tuple[sqlite3.Connection, str]:
    """Open read-only, honest mode first. Returns the connection AND the mode that won."""
    path = Path(db_path)
    if not path.exists():
        raise ZoteroAnnotationError(f"Zotero database not found: {path}")
    try:
        conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True, timeout=5.0)
        conn.execute(f"PRAGMA busy_timeout={int(busy_timeout_ms)}")
        # Touch a table: `connect` succeeds lazily, so the lock only surfaces on the
        # first real read. Without this the fallback never fires and the caller gets
        # "database is locked" from the middle of a query instead.
        conn.execute("SELECT 1 FROM items LIMIT 1").fetchone()
        return conn, "mode=ro"
    except sqlite3.Error:
        try:
            conn = sqlite3.connect(f"file:{path}?mode=ro&immutable=1", uri=True, timeout=5.0)
            conn.execute("SELECT 1 FROM items LIMIT 1").fetchone()
            return conn, "immutable=1"
        except sqlite3.Error as exc:
            raise ZoteroAnnotationError(
                f"Could not open Zotero database read-only: {exc}"
            ) from exc
