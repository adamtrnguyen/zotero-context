"""Citation keys, which Zotero itself does not have.

Better BibTeX invents and owns them, over JSON-RPC. Behind a port because it is the one
collaborator that is neither our database nor our plugin, and because a caller with BBT
uninstalled still has a working library — so an implementation that answers "no keys" is a
legitimate one, not a broken one.
"""

from __future__ import annotations

from typing import Protocol, runtime_checkable


@runtime_checkable
class CitationKeys(Protocol):
    """Citekey lookup, provided by Better BibTeX."""

    def citation_keys(self, item_keys: list[str]) -> dict[str, str]: ...

    def search_item(self, query: str) -> dict | None: ...
