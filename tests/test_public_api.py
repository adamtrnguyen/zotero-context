"""`zotero_core.__all__` is the PUBLISHED API. This is the gate that makes moving cheap.

`zotero-core` is a distributed library, not an application, so it deviates from the
convention the rest of the estate follows: omni-rag's fifteen layer `__init__.py` files are
all zero bytes and nothing is ever re-exported. Here the top-level `__all__` is what
`omni-rag` and `youtube2zotero` import, and keeping it stable is precisely what turns "every
internal move is a breaking change" into "internal moves are free".

That trade only holds if something checks it, which nothing did.
"""

from __future__ import annotations

import importlib

import pytest

import zotero_core

# Frozen 2026-08-19, before the DDD restructure. A name may be ADDED; removing or failing to
# resolve one is a breaking change for a consumer that pins this package by tag.
PUBLISHED = (
    "USER_LIBRARY_ID",
    "Annotation",
    "BetterBibTeXClient",
    "ItemState",
    "ItemStates",
    "ReaderContext",
    "ReaderPosition",
    "ReaderState",
    "TabState",
    "WindowState",
    "ZoteroAnnotationStore",
    "ZoteroAttachment",
    "ZoteroAttachments",
    "ZoteroBridgeClient",
    "ZoteroCollection",
    "ZoteroContext",
    "ZoteroItem",
    "ZoteroItemStore",
    "ZoteroSource",
    "__version__",
    "check_duplicate",
    "clean_doi",
    "clean_isbn",
)


@pytest.mark.parametrize("name", PUBLISHED)
def test_every_published_name_still_resolves_from_the_top(name):
    """Wherever the type moves internally, `from zotero_core import X` must keep working."""
    assert hasattr(zotero_core, name), f"{name} no longer resolves from zotero_core"


def test_all_lists_exactly_the_published_names():
    """A name added to `__all__` without being added here is an undeclared API expansion."""
    missing = sorted(set(PUBLISHED) - set(zotero_core.__all__))
    assert not missing, f"dropped from __all__: {missing}"
    added = sorted(set(zotero_core.__all__) - set(PUBLISHED))
    assert not added, f"added to __all__ without updating this test: {added}"


def test_the_known_external_consumers_import_paths_still_work():
    """The three submodule paths `arxiv-bulk` reaches for directly.

    It bypasses `__init__` and broke once already on 2026-08-19 for exactly that reason.
    Pinned here so a move fails in THIS suite rather than in a sibling repo nobody runs.
    """
    for module, name in (
        ("zotero_core", "ZoteroContext"),
        ("zotero_core.read.annotations", "DEFAULT_ZOTERO_DB"),
        ("zotero_core.read.bbt", "DEFAULT_BBT_RPC_URL"),
        ("zotero_core.domain.entities", "Annotation"),
    ):
        mod = importlib.import_module(module)
        assert hasattr(mod, name), f"{module}.{name} no longer resolves"
