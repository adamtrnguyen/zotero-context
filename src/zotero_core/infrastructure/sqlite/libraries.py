"""Which libraries exist. The user library, and any groups.

WHY THIS EXISTS AT ALL
----------------------
Every other query in this layer is SCOPED to one library, defaulting to the user's --
`USER_LIBRARY_ID = 1` -- because an unscoped query silently mixes a group's collections
and items into personal counts. That default is right, and it is also invisible: with no
way to list libraries, a caller cannot tell "you have nothing" from "you are looking in
the wrong library".

This is not hypothetical here. Measured 2026-08-19: seven libraries, six of them groups,
four of those non-empty -- 109 items and 10 collections outside the user library, all old
coursework (ENGE1215_A4, D7 resources, A4_U2 Project, companionbot3000).

`item_count` counts LIVE items, matching every other count in this layer, and counts
every item type rather than only top-level ones -- it is a "is there anything in here"
signal for choosing a library, not a catalogue figure.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from zotero_core.infrastructure.sqlite.annotations import DEFAULT_ZOTERO_DB
from zotero_core.infrastructure.sqlite.connect import DEFAULT_BUSY_TIMEOUT_MS, open_readonly

_LIBRARIES_SQL = """
SELECT l.libraryID,
       l.type,
       COALESCE(g.name, ''),
       l.editable,
       (SELECT COUNT(*) FROM items i
         WHERE i.libraryID = l.libraryID
           AND i.itemID NOT IN (SELECT itemID FROM deletedItems)) AS item_count,
       (SELECT COUNT(*) FROM collections c WHERE c.libraryID = l.libraryID) AS collection_count
  FROM libraries l
  LEFT JOIN groups g ON g.libraryID = l.libraryID
 ORDER BY l.libraryID
"""


@dataclass(frozen=True)
class Library:
    library_id: int
    library_type: str
    name: str
    editable: bool
    item_count: int
    collection_count: int


def list_libraries(
    db_path: str | Path | None = None,
    *,
    busy_timeout_ms: int = DEFAULT_BUSY_TIMEOUT_MS,
) -> tuple[tuple[Library, ...], str]:
    """Every library, with a live item count. Returns the rows AND the read mode."""
    path = Path(db_path or DEFAULT_ZOTERO_DB).expanduser()
    conn, read_mode = open_readonly(path, busy_timeout_ms=busy_timeout_ms)
    try:
        rows = conn.execute(_LIBRARIES_SQL).fetchall()
    finally:
        conn.close()
    return (
        tuple(
            Library(
                library_id=row[0],
                library_type=row[1],
                # The user library has no `groups` row and therefore no name of its own.
                name=row[2] or ("My Library" if row[1] == "user" else f"Group {row[0]}"),
                editable=bool(row[3]),
                item_count=row[4],
                collection_count=row[5],
            )
            for row in rows
        ),
        read_mode,
    )
