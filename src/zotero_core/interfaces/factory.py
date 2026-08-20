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

from zotero_core.application.services.session import WriteSession
from zotero_core.infrastructure.journal import FileJournal
from zotero_core.infrastructure.probe import HttpZoteroProbe
from zotero_core.infrastructure.service import ZoteroContext
from zotero_core.infrastructure.sqlite.collections import ZoteroCollectionStore
from zotero_core.infrastructure.sqlite.duplicates import CatalogueDuplicateFinder
from zotero_core.infrastructure.sqlite.items import ZoteroItemStore
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


def build_context(**kwargs) -> ZoteroContext:
    """The read facade, assembled. Kwargs go straight through to `ZoteroContext`.

    Thin today because `ZoteroContext` still constructs its own sqlite stores — it is the
    read side's version of the same defect, and it is not yet ported. Routing its
    construction through here first means the call sites are already correct when it is.
    """
    return ZoteroContext(**kwargs)
