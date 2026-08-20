from __future__ import annotations

from pathlib import Path

from ..domain.entities import Annotation, ReaderContext, ReaderState, WindowState, ZoteroSource
from .annotations import DEFAULT_ZOTERO_DB, ZoteroAnnotationStore
from .bbt import DEFAULT_BBT_RPC_URL, BetterBibTeXClient
from .bridge import DEFAULT_BRIDGE_URL, ZoteroBridgeClient
from .collections import ZoteroCollectionStore
from .duplicates import check_duplicate as _check_duplicate
from .items import ZoteroItemStore
from .libraries import list_libraries
from .search import ZoteroSearchStore


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
        self.collections = ZoteroCollectionStore(zotero_db_path)
        self.search = ZoteroSearchStore(zotero_db_path)
        self._db_path = zotero_db_path

    def _collections_for(self, library_id: int | None) -> ZoteroCollectionStore:
        """The default store, or a fresh one aimed at another library.

        Constructing one is cheap -- it holds paths and an int, opening nothing -- so
        this stays a per-call choice rather than server state.
        """
        if library_id is None or library_id == self.collections.library_id:
            return self.collections
        return ZoteroCollectionStore(self._db_path, library_id=library_id)

    def _search_for(self, library_id: int | None) -> ZoteroSearchStore:
        if library_id is None or library_id == self.search.library_id:
            return self.search
        return ZoteroSearchStore(self._db_path, library_id=library_id)

    def list_libraries(self) -> dict:
        """Every library with a live item count -- the user's, plus any groups.

        Every other read here is scoped to ONE library and defaults to the user's. That
        default is right and it is also invisible: without this, a caller cannot tell
        "you have nothing" from "you are looking in the wrong library".
        """
        libraries, read_mode = list_libraries(self._db_path)
        return {
            "count": len(libraries),
            "read_mode": read_mode,
            "libraries": [
                {
                    "library_id": lib.library_id,
                    "type": lib.library_type,
                    "name": lib.name,
                    "editable": lib.editable,
                    "item_count": lib.item_count,
                    "collection_count": lib.collection_count,
                }
                for lib in libraries
            ],
        }

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

        Several queries rather than one because they fan out differently (fields and tags
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

    def collection_tree(self, *, library_id: int | None = None) -> dict:
        """The whole collection tree, nested, with breadcrumb paths and item counts."""
        tree = self._collections_for(library_id).tree()
        return {
            "count": len(tree.flat()),
            "read_mode": tree.read_mode,
            "truncated": tree.truncated,
            "collections": [_node_to_dict(node) for node in tree.roots],
        }

    def collection_items(
        self, collection_key: str, *, include_trashed: bool = False,
        library_id: int | None = None,
    ) -> dict:
        """Direct members of one collection -- the read nothing else could answer."""
        members = self._collections_for(library_id).items(
            collection_key, include_trashed=include_trashed
        )
        return {
            "collection_key": collection_key,
            "count": len(members),
            "read_mode": members.read_mode,
            "items": [
                {
                    "item_key": m.item_key,
                    "title": m.title,
                    "item_type": m.item_type,
                    "trashed": m.trashed,
                }
                for m in members.members
            ],
        }

    def item_collections(self, item_keys: list[str], *, library_id: int | None = None) -> dict:
        """Which collections each item is filed in. The inverse; new capability."""
        return {"items": self._collections_for(library_id).collections_of(item_keys)}

    def find_collections(self, name: str, *, library_id: int | None = None) -> dict:
        found = self._collections_for(library_id).find(name)
        return {
            "count": len(found),
            "collections": [
                {"key": n.key, "name": n.name, "path": n.path, "item_count": n.item_count}
                for n in found
            ],
        }

    def search_items(
        self,
        query: str,
        *,
        fuzzy: bool = True,
        limit: int = 25,
        item_type: str | None = None,
        library_id: int | None = None,
    ) -> dict:
        hits, read_mode = self._search_for(library_id).items(
            query, fuzzy=fuzzy, limit=limit, item_type=item_type
        )
        return {
            "query": query,
            "fuzzy": fuzzy,
            "count": len(hits),
            "read_mode": read_mode,
            "hits": [
                {
                    "item_key": h.item_key,
                    "title": h.title,
                    "item_type": h.item_type,
                    "creators": h.creators,
                    "score": h.score,
                    "matched_on": h.matched_on,
                }
                for h in hits
            ],
        }

    def search_annotations(
        self,
        query: str = "",
        *,
        color: str | None = None,
        annotation_types: set[str] | None = None,
        limit: int = 25,
    ) -> dict:
        hits, read_mode = self.search.annotations(
            query, color=color, types=annotation_types, limit=limit
        )
        return {
            "query": query,
            "count": len(hits),
            "read_mode": read_mode,
            "annotations": [
                {
                    "annotation_key": h.annotation_key,
                    "attachment_key": h.attachment_key,
                    "parent_key": h.parent_key,
                    "parent_title": h.parent_title,
                    "type": h.annotation_type,
                    "text": h.text,
                    "comment": h.comment,
                    "color": h.color,
                    "page_label": h.page_label,
                }
                for h in hits
            ],
        }

    def search_fulltext(self, query: str, *, limit: int = 25) -> dict:
        hits, read_mode = self.search.fulltext(query, limit=limit)
        return {
            "query": query,
            "count": len(hits),
            "read_mode": read_mode,
            "documents": [
                {
                    "attachment_key": h.attachment_key,
                    "parent_key": h.parent_key,
                    "title": h.title,
                    "match_count": h.match_count,
                    "snippets": list(h.snippets),
                }
                for h in hits
            ],
        }

    def attachment_text(self, attachment_key: str, *, max_chars: int = 20000) -> dict:
        text = self.search.attachment_text(attachment_key)
        if text is None:
            return {"ok": False, "error": "not_indexed", "attachment_key": attachment_key}
        return {
            "ok": True,
            "attachment_key": attachment_key,
            "chars": len(text),
            "truncated": len(text) > max_chars,
            "text": text[:max_chars],
        }

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


def _node_to_dict(node) -> dict:
    return {
        "key": node.key,
        "name": node.name,
        "path": node.path,
        "depth": node.depth,
        "parent_key": node.parent_key,
        "item_count": node.item_count,
        "subcollections": [_node_to_dict(child) for child in node.subcollections],
    }
