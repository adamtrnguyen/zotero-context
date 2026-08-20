"""Write use cases: the verbs, their gates, and the undo that replays them.

Empty by convention — nothing is re-exported from a layer package. The ROOT
`zotero_core/__init__.py` is this package's one documented exception, because its
`__all__` is the published API that other repos import.

⚠ THIS FILE USED TO RE-EXPORT THIRTY-ODD NAMES, and it was not merely redundant. It
imported `infrastructure.journal`, `infrastructure.transports.cookjohn` and
`infrastructure.transports.linker` purely to republish them, which put a concrete adapter
in the application layer's import graph no matter how clean the modules below it were —
importing ANY `zotero_core.application.*` module executes this file first. The layer could
not sit below `infrastructure` while this existed.

Two of the names it published had also gone stale: `zotero_is_running` moved to
`infrastructure/probe.py` behind a port, and `write_manifest` / `copy_database` /
`DEFAULT_JOURNAL_DIR` are infrastructure. A re-export list is a second declaration of the
surface, and second declarations drift.
"""
