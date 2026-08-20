"""The composition root: the ONE place that names concrete adapters.

Free `build_*` functions rather than a container class, matching `omni-rag`'s
`interfaces/factory.py`. It lives in `interfaces` and not in `application` for the reason
the whole layering exists: something has to choose the real implementations, and whatever
does is the thing that cannot be reused with different ones.

WHAT MOVED HERE, AND WHY IT WAS A PROBLEM WHERE IT WAS
------------------------------------------------------
`verbs._Session.__init__` did `self.linker = linker or LinkerClient()`, and
`collections._session()` did the same three defaults again. That `or Concrete()` idiom
looks like injection and is the opposite of it: the parameter is optional, so the module
MUST import the adapter to have a default, and the application layer therefore named
infrastructure at three sites plus a re-export list.

It also left the test suite no way in. Substituting a fake meant rewriting the module
global — `monkeypatch.setattr(f"{module}.CookjohnClient", ...)` for each consuming module,
with `raising=False`, so a renamed module would silently stop being patched. The fixture's
own comment conceded it: "it is the only way in, because the MCP surface has no injection
parameter by design."

Now there is one. Every default lives in this file; everything above takes ports.
"""

from __future__ import annotations

from pathlib import Path

from zotero_core.application.services.context import ZoteroContext
from zotero_core.application.services.session import WriteSession
from zotero_core.infrastructure.http.bbt import DEFAULT_BBT_RPC_URL, BetterBibTeXClient
from zotero_core.infrastructure.http.bridge import DEFAULT_BRIDGE_URL, ZoteroBridgeClient
from zotero_core.infrastructure.journal import FileJournal
from zotero_core.infrastructure.probe import HttpZoteroProbe
from zotero_core.infrastructure.sqlite.annotations import (
    DEFAULT_ZOTERO_DB,
    ZoteroAnnotationStore,
)
from zotero_core.infrastructure.sqlite.collections import ZoteroCollectionStore
from zotero_core.infrastructure.sqlite.duplicates import CatalogueDuplicateFinder
from zotero_core.infrastructure.sqlite.items import ZoteroItemStore
from zotero_core.infrastructure.sqlite.libraries import SqliteLibraryCatalogue
from zotero_core.infrastructure.sqlite.search import ZoteroSearchStore
from zotero_core.infrastructure.transports.cookjohn import CookjohnClient
from zotero_core.infrastructure.transports.linker import LinkerClient


def build_write_session(
    *,
    linker=None,
    cookjohn=None,
    store=None,
    collections=None,
    journal=None,
    probe=None,
    duplicates=None,
) -> WriteSession:
    """Assemble one write session, defaulting each port to its real adapter.

    Every argument is an override, and they exist for callers that already hold a
    collaborator — a test with fakes, or a caller pointing at a different database. The
    DEFAULTS are the point: they are here, in one function, instead of spread across the
    verbs as `or Concrete()`.

    `duplicates` defaults to a finder over the SAME store, not a second one: a duplicate
    check that read a different catalogue than the existence gates would answer about a
    library the write is not going to touch.
    """
    store = store if store is not None else ZoteroItemStore()
    return WriteSession(
        linker=linker if linker is not None else LinkerClient(),
        cookjohn=cookjohn if cookjohn is not None else CookjohnClient(),
        store=store,
        collections=(
            collections
            if collections is not None
            else ZoteroCollectionStore(store.db_path, busy_timeout_ms=store.busy_timeout_ms)
        ),
        journal=journal if journal is not None else FileJournal(),
        probe=probe if probe is not None else HttpZoteroProbe(),
        duplicates=(
            duplicates if duplicates is not None else CatalogueDuplicateFinder(store)
        ),
    )


def build_context(
    *,
    bridge_url: str = DEFAULT_BRIDGE_URL,
    zotero_db_path: str | Path = DEFAULT_ZOTERO_DB,
    bbt_rpc_url: str = DEFAULT_BBT_RPC_URL,
    **overrides,
) -> ZoteroContext:
    """Assemble the read facade, defaulting every port to its real adapter.

    The three keyword arguments are the ones a CALLER actually varies -- point it at a copy
    of the database, or at a bridge on another port -- and they are kept because the CLI's
    `--db` and the MCP adapter's `ZOTERO_CORE_DB` both mean exactly this. `overrides` takes
    a ready-made port by name, which is how a test substitutes one.

    ⚠ Was a one-line passthrough while `ZoteroContext` built its own stores. That is the
    defect this replaces: seven concrete adapters constructed inside an application-layer
    facade, none of them replaceable.

    Every store reads the SAME database. Handing them different paths would let a duplicate
    check answer about one library while the existence gate read another.
    """
    db = zotero_db_path
    items = overrides.pop("items", None) or ZoteroItemStore(db)
    collections = overrides.pop("collections", None) or ZoteroCollectionStore(db)
    return ZoteroContext(
        bridge=overrides.pop("bridge", None) or ZoteroBridgeClient(bridge_url),
        annotations=overrides.pop("annotations", None) or ZoteroAnnotationStore(db),
        bbt=overrides.pop("bbt", None) or BetterBibTeXClient(bbt_rpc_url),
        items=items,
        collections=collections,
        search=overrides.pop("search", None) or ZoteroSearchStore(db),
        libraries=overrides.pop("libraries", None) or SqliteLibraryCatalogue(db),
        duplicates=overrides.pop("duplicates", None) or CatalogueDuplicateFinder(items),
    )
