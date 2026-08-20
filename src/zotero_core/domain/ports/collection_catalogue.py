"""Reading the collection side of the catalogue, as the collection verbs need it.

Separate from `Catalogue` because the two are read by different things and the split is
already in the code: `Catalogue` is what every write gate uses (does this key exist, what
tags does it carry), while this is used only by the six collection verbs, to answer
"where does this collection sit" and "what is already in it".

⚠ The four call sites reached `ZoteroCollectionStore` through a DEFERRED import inside the
function body — `from zotero_core.infrastructure... import ZoteroCollectionStore` at line
243, 268, 319 and 527. A function-level import is still a dependency; it is a dependency
that does not show up when you read the module's head, which is worse. import-linter sees
them, which is how they were found.
"""

from __future__ import annotations

from typing import Any, Protocol, runtime_checkable


@runtime_checkable
class CollectionCatalogue(Protocol):
    """Read-only access to collections and their membership."""

    #: Which library this adapter reads. The read facade offers every collection call
    #: per-library and used to serve that by CONSTRUCTING a second store; asking the
    #: current adapter for a sibling keeps that with the code that knows how.
    library_id: int

    def for_library(self, library_id: int) -> CollectionCatalogue: ...

    def tree(self) -> Any: ...

    def items(self, collection_key: str, *, include_trashed: bool = False) -> Any: ...

    def find(self, name: str) -> tuple[Any, ...]: ...

    def collections_of(
        self, item_keys: list[str] | tuple[str, ...]
    ) -> dict[str, tuple[dict[str, str], ...]]: ...
