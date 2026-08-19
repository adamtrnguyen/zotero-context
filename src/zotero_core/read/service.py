from __future__ import annotations

from pathlib import Path

from ..domain.entities import Annotation, ReaderContext, ReaderState, WindowState, ZoteroSource
from .annotations import DEFAULT_ZOTERO_DB, ZoteroAnnotationStore
from .bbt import DEFAULT_BBT_RPC_URL, BetterBibTeXClient
from .bridge import DEFAULT_BRIDGE_URL, ZoteroBridgeClient
from .duplicates import check_duplicate as _check_duplicate
from .items import ZoteroItemStore


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
        # ⚠ ADDED 2026-08-19, and its absence was the whole problem. This class is what
        # both the CLI and the MCP adapter wrap, so for as long as it held no item store,
        # `items.py` (493 lines, 7 public methods) and `duplicates.py` were unreachable
        # from every agent-facing surface -- and the gap got filled by a third-party
        # plugin, which is exactly what this package exists to prevent.
        self.items = ZoteroItemStore(zotero_db_path)

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

    def get_item(self, item_key: str) -> dict:
        """Everything about one item in a single read: state, fields, creators, tags.

        Four queries rather than one because they fan out differently (fields and tags
        are one-to-many, creators are ordered), and a caller almost always wants all
        four -- an agent deciding what to do next needs the type, and reading creators
        used to require deliberately triggering a `refusing_to_replace` refusal and
        parsing `detail.current_creators` out of it.

        `read_mode` travels with the answer. With Zotero running, `mode=ro` loses to the
        rollback journal and `immutable=1` serves a point-in-time snapshot instead; a
        caller that cannot tell those apart cannot know whether it read the present.
        """
        states = self.items.item_states([item_key])
        state = states.get(item_key)
        # `item_states` returns an entry for EVERY requested key and marks absence with
        # `exists=False` -- it does not omit them -- so `.get() is None` never fires.
        if state is None or not state.exists:
            return {
                "ok": False,
                "error": "not_found",
                "item_key": item_key,
                "read_mode": states.read_mode,
            }
        return {
            "ok": True,
            "item_key": item_key,
            "item_type": state.item_type,
            "title": state.title,
            "trashed": state.trashed,
            "parent_key": state.parent_key,
            "child_keys": list(state.child_keys),
            "collection_count": state.collection_count,
            "fields": self.items.item_fields(item_key),
            "creators": [dict(creator) for creator in self.items.item_creators(item_key)],
            "tags": list(self.items.item_tags([item_key]).get(item_key, ())),
            "read_mode": states.read_mode,
        }

    def check_duplicate(
        self,
        *,
        title: str | None = None,
        doi: str | None = None,
        isbn: str | None = None,
        calibre_uuid: str | None = None,
        creators: tuple = (),
    ) -> dict:
        """Is this already in the library? Answerable WITHOUT attempting a write.

        The logic is unchanged -- it is the same gate `write.verbs.create_item` runs.
        What is new is that it can be asked. Until now the only way to reach a verdict
        was to attempt a create and read it out of the refusal, so "check before you
        add" cost a write attempt.
        """
        return _check_duplicate(
            self.items,
            title=title,
            doi=doi,
            isbn=isbn,
            calibre_uuid=calibre_uuid,
            creators=creators,
        )

    def list_pdfs(self, *, limit: int | None = None) -> dict:
        """Enumerate stored PDF attachments with parent title and collection."""
        attachments = self.items.pdf_attachments(limit=limit)
        return {
            "count": len(attachments),
            "read_mode": attachments.read_mode,
            "attachments": [
                {
                    "attachment_key": att.attachment_key,
                    "path": str(att.path),
                    "title": att.title,
                    "collection": att.collection,
                }
                for att in attachments
            ],
        }

    def trash_count(self) -> dict:
        """How many items are in the trash. Had ZERO call sites anywhere before now."""
        return {"trashed": self.items.trashed_count()}

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
