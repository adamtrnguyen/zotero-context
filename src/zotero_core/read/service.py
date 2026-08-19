from __future__ import annotations

from pathlib import Path

from ..domain.entities import Annotation, ReaderContext, ReaderState, WindowState, ZoteroSource
from .annotations import DEFAULT_ZOTERO_DB, ZoteroAnnotationStore
from .bbt import DEFAULT_BBT_RPC_URL, BetterBibTeXClient
from .bridge import DEFAULT_BRIDGE_URL, ZoteroBridgeClient


class ZoteroContext:
    def __init__(
        self,
        *,
        bridge_url: str = DEFAULT_BRIDGE_URL,
        zotero_db_path: str | Path = DEFAULT_ZOTERO_DB,
        bbt_rpc_url: str = DEFAULT_BBT_RPC_URL,
    ):
        self.bridge = ZoteroBridgeClient(bridge_url)
        self.annotations = ZoteroAnnotationStore(zotero_db_path)
        self.bbt = BetterBibTeXClient(bbt_rpc_url)

    def ping(self) -> dict:
        return self.bridge.ping()

    def get_window_state(self) -> WindowState:
        return self.bridge.get_window_state()

    def get_selected_items(self):
        return self.get_window_state().selected_items

    def get_open_readers(self) -> list[ReaderState]:
        return self.get_window_state().readers

    def get_active_reader(self) -> ReaderState | None:
        for reader in self.get_open_readers():
            if reader.is_active_tab:
                return reader
        return None

    def get_annotations(
        self,
        attachment_key: str,
        *,
        types: set[str] | None = None,
        include_text: bool = True,
        include_comments: bool = True,
    ) -> list[Annotation]:
        return self.annotations.get_annotations(
            attachment_key,
            types=types,
            include_text=include_text,
            include_comments=include_comments,
        )

    def resolve_pdf_attachment_key(
        self,
        identifier: str,
        *,
        is_attachment_key: bool = False,
    ) -> tuple[str | None, str | None]:
        if is_attachment_key:
            return None, identifier

        parent_key = identifier
        if not _looks_like_item_key(identifier):
            item = self.bbt.search_item(identifier)
            if not item:
                return None, None
            parent_key = (item.get("id") or "").split("/")[-1]

        if not parent_key:
            return None, None
        return parent_key, self.annotations.get_pdf_attachment_key(parent_key)

    def get_sources_with_annotations(self, *, include_citekeys: bool = True) -> list[ZoteroSource]:
        sources = self.annotations.get_sources_with_annotations()
        if not include_citekeys:
            return sources
        citekeys = self.bbt.citation_keys([source.parent_key for source in sources])
        return [
            ZoteroSource(
                parent_key=source.parent_key,
                attachment_key=source.attachment_key,
                title=source.title,
                authors=source.authors,
                annotation_count=source.annotation_count,
                citekey=citekeys.get(source.parent_key, ""),
            )
            for source in sources
        ]

    def get_open_reader_context(
        self,
        *,
        active_only: bool = False,
        include_annotations: bool = True,
        include_citekeys: bool = True,
        annotation_types: set[str] | None = None,
    ) -> list[ReaderContext]:
        readers = self.get_open_readers()
        if active_only:
            readers = [reader for reader in readers if reader.is_active_tab]

        citekeys = {}
        if include_citekeys:
            parent_keys = [reader.parent_key for reader in readers if reader.parent_key]
            citekeys = self.bbt.citation_keys(parent_keys)

        contexts: list[ReaderContext] = []
        for reader in readers:
            anns = None
            if include_annotations and reader.attachment_key:
                anns = self.get_annotations(reader.attachment_key, types=annotation_types)
            contexts.append(
                ReaderContext(
                    reader=reader,
                    citekey=citekeys.get(reader.parent_key or "", ""),
                    annotations=anns,
                )
            )
        return contexts


def _looks_like_item_key(value: str) -> bool:
    return len(value) == 8 and value.isalnum() and value.upper() == value
