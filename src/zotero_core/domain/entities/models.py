"""Zotero nouns, as read out of zotero.sqlite.

Two types, and both are joins rather than raw rows: `ZoteroSource` carries an annotation
count and the first three authors, `Annotation` carries a ready-made `zotero://` deep link.
Neither has a `from_bridge` — nothing here comes off the wire.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any


@dataclass(frozen=True)
class ZoteroSource:
    parent_key: str
    attachment_key: str
    title: str
    authors: str
    annotation_count: int
    citekey: str = ""

@dataclass(frozen=True)
class Annotation:
    key: str
    attachment_key: str
    parent_key: str | None
    type: str
    page_label: str
    color: str
    text: str
    comment: str
    sort_index: str
    zotero_url: str


def to_jsonable(value: Any) -> Any:
    if hasattr(value, "__dataclass_fields__"):
        return {k: to_jsonable(v) for k, v in asdict(value).items()}
    if isinstance(value, list):
        return [to_jsonable(v) for v in value]
    if isinstance(value, tuple):
        return [to_jsonable(v) for v in value]
    if isinstance(value, dict):
        return {str(k): to_jsonable(v) for k, v in value.items()}
    return value
