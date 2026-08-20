"""Does an item like this already exist?

The check `create_item` runs before it creates anything. It is a READ over the catalogue
plus a policy about what counts as the same work — DOI and ISBN equality, a Calibre uuid,
a normalised title paired with an author surname — and the policy half already lives in
`domain/services/`. What is left is the reading, which is what this port names.

Separate from `Catalogue` on purpose. `Catalogue` is six mechanical lookups the write
gates use; this is one question with an opinion in it, asked by exactly one verb. Folding
it in would make every implementation of `Catalogue` owe an answer to a question most of
them have no business having.
"""

from __future__ import annotations

from typing import Protocol, runtime_checkable


@runtime_checkable
class DuplicateFinder(Protocol):
    """Answers whether a proposed new item already exists."""

    def check(
        self,
        *,
        title: str | None = None,
        doi: str | None = None,
        isbn: str | None = None,
        calibre_uuid: str | None = None,
        creators: list[dict[str, str]] | tuple[dict[str, str], ...] = (),
    ) -> dict: ...
