"""Zotero's annotation types: the int the database stores, the name a caller wants.

THREE COPIES, TWO OF THEM DISAGREEING
-------------------------------------
The mapping existed as a dict in `read/annotations.py`, was read again in `read/search.py`,
and was transcribed by hand a third time into the MCP schema:

    read/annotations.py   ANNOTATION_TYPE.get(row[3], f"type{row[3]}")   -> "type7"
    read/search.py        ANNOTATION_TYPE.get(type_id, str(type_id))     -> "7"
    interfaces/read_mcp   ANNOTATION_TYPE_NAMES = ("highlight", ...)     hand-maintained

Two different names for the SAME unknown type, from the same table, depending on which
query found it -- so a caller filtering on the name would match one and miss the other. The
third copy's own comment admitted it duplicated the authority and asked to be kept in step
by hand, which is a promise no comment can keep.

WHY AN ENUM AND NOT A DICT
--------------------------
The values are a closed vocabulary Zotero owns, and the MCP schema needs the names as a
list to make them DISCOVERABLE -- a caller was previously expected to guess types 3-6 from
a description advertising two by example. An enum gives the lookup, the reverse lookup and
that list from one declaration.

`int, Enum` for the same reason `ReadMode` is `str, Enum`: `enum.IntEnum` would work, but
the mixin form keeps `== 1` true for anything comparing against a raw database value, and
this package targets 3.10 where `StrEnum` does not exist (see `read_mode.py`).
"""

from __future__ import annotations

from enum import Enum


class AnnotationType(int, Enum):
    """The `itemAnnotations.type` column. Values are Zotero's, not ours."""

    HIGHLIGHT = 1
    NOTE = 2
    IMAGE = 3
    INK = 4
    UNDERLINE = 5
    TEXT = 6

    @property
    def label(self) -> str:
        """The name callers filter on: `"highlight"`, `"note"`, …"""
        return self.name.lower()


#: Every name, in database order. The MCP schema's `enum` list, derived rather than typed
#: out — the third copy was a hand-maintained tuple that nothing kept in step.
ANNOTATION_TYPE_NAMES: tuple[str, ...] = tuple(t.label for t in AnnotationType)

#: Back-compat: the shape `read/annotations.py` exported and tests import by name.
ANNOTATION_TYPE: dict[int, str] = {int(t): t.label for t in AnnotationType}


def label_for(type_id: int) -> str:
    """Name for a stored type id, with ONE fallback for a type Zotero adds later.

    The fallback shape is `type<N>`, chosen over the bare `str(N)` the search path used
    because a caller filtering on `"7"` cannot tell a type name from a page label, while
    `"type7"` is self-describing. What matters more than which was chosen is that there is
    now one of them: the two paths previously answered `"type7"` and `"7"` for the same row.
    """
    try:
        return AnnotationType(type_id).label
    except ValueError:
        return f"type{type_id}"
