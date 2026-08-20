"""zotero-core: the ONE place that knows what a local Zotero library is.

Read and write, behind enforced internal layers:

    domain/       entities, values, policy          pure -- no driver imports
    read/         sqlite + bridge + BBT             "what is in the library"
    write/        gated verbs + two transports      "change it, recoverably"
    interfaces/   cli, read_mcp, write_mcp          the only layer that imports mcp

WHY ONE PACKAGE (merged 2026-08-19, was zotero-context + zotero-writes)
----------------------------------------------------------------------
The split was defensible and it was enforced -- `writes -> core, never the reverse`
had its own import-linter contract. What it could not survive was that the read half
was never finished. `core` shipped `items.py` and `duplicates.py` and exposed NEITHER
from its CLI or MCP surface, and never had collection or search queries at all. So an
agent asking "what is in collection X" had to go to a third-party plugin, and the
charter sentence -- "a second answer to what is in the library is the failure this
package exists to end" -- described a gap rather than a guarantee.

Two packages also meant two of everything that was really one thing: two pyprojects
agreeing on `mcp<2` and `ty==0.0.55` in duplicated comments, two lockfiles, and one
test suite that lived in `writes/` and reached `core` incidentally -- `core` had no
`tests/` directory and no `.importlinter` of its own.

The direction the split protected is now a LAYER, not a package boundary, and it is
still checked: see `.importlinter`, contract `layers`. Nothing was weakened by
merging; `domain` is a rule that could not previously be expressed at all.

WHAT THIS MODULE EXPORTS
------------------------
The READ surface only. `import zotero_core` must stay cheap and side-effect free:
the write verbs reach two HTTP transports and are an explicit opt-in --

    from zotero_core.write import create_item, trash_items

which also keeps the CRUD surface a deliberate act rather than something a caller
gets by accident.
"""

from .domain.entities import (
    Annotation,
    ReaderContext,
    ReaderPosition,
    ReaderState,
    TabState,
    WindowState,
    ZoteroCollection,
    ZoteroItem,
    ZoteroSource,
)
from .domain.services.identifiers import clean_doi, clean_isbn
from .read.annotations import ZoteroAnnotationStore
from .read.bbt import BetterBibTeXClient
from .read.bridge import ZoteroBridgeClient
from .read.duplicates import check_duplicate
from .read.items import (
    USER_LIBRARY_ID,
    ItemState,
    ItemStates,
    ZoteroAttachment,
    ZoteroAttachments,
    ZoteroItemStore,
)
from .read.service import ZoteroContext

__version__ = "0.2.0"

__all__ = [
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
]
