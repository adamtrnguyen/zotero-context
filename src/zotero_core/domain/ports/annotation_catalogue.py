"""Annotations, and the PDF attachment they hang off.

Separate from `Catalogue` because these are a different noun read from different tables, and
because the write path never touches them: nothing in `application/services/` annotates. A
port earns its place by having a consumer, and this one's consumer is the read facade.
"""

from __future__ import annotations

from typing import Any, Protocol, runtime_checkable


@runtime_checkable
class AnnotationCatalogue(Protocol):
    """Read-only access to stored annotations."""

    def get_annotations(
        self,
        attachment_key: str,
        *,
        types: set[str] | None = None,
        include_text: bool = True,
        include_comments: bool = True,
    ) -> list[Any]: ...

    def get_pdf_attachment_key(self, parent_key: str) -> str | None: ...

    def get_sources_with_annotations(self) -> list[Any]: ...
