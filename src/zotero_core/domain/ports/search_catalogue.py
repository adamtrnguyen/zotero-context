"""Searching the library: annotations, full text, and one attachment's extracted text.

⚠ `items` was MISSING from the first draft of this port, and `ty` is what found it: the
surface was derived by grepping `_collections_for(...).X` and `_search_for(...).X` together,
which merged two different `.items` — the collection's membership listing and this one, the
fuzzy title search. A port derived from a sloppy reading is worse than none, because it
type-checks against nothing.

`library_id` and `for_library` are on the port because the read facade offers every search
per-library, and it did that by CONSTRUCTING a second store — `ZoteroSearchStore(self._db_path,
library_id=...)` — which is precisely the concrete-construction this refactor removes. Asking
the current adapter for a sibling keeps that decision with the implementation that knows how.
"""

from __future__ import annotations

from typing import Any, Protocol, runtime_checkable


@runtime_checkable
class SearchCatalogue(Protocol):
    """Full-text and annotation search, scoped to one library."""

    library_id: int

    def for_library(self, library_id: int) -> SearchCatalogue: ...

    def annotations(
        self,
        query: str = "",
        *,
        color: str | None = None,
        types: set[str] | None = None,
        limit: int = 25,
    ) -> tuple[tuple[Any, ...], str]: ...

    def items(
        self,
        query: str,
        *,
        fuzzy: bool = True,
        threshold: float = 0.72,
        limit: int = 25,
        item_type: str | None = None,
    ) -> tuple[tuple[Any, ...], str]: ...

    def attachment_text(self, attachment_key: str) -> str | None: ...

    def fulltext(
        self,
        query: str,
        *,
        limit: int = 25,
        context_chars: int = 160,
        max_snippets: int = 3,
    ) -> tuple[tuple[Any, ...], str]: ...
