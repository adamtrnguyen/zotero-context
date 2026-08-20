"""The audit trail a write leaves behind, as the application sees it.

Every mutating verb records what it is about to change BEFORE it changes it, so an undo
has something to replay from. That is an application obligation — the verb decides a
manifest is owed — but writing it is filesystem I/O, which is not. This port is the seam.

A PORT FOR WHAT WERE FREE FUNCTIONS, and the shape is the point: `write_manifest` and
`copy_database` were module-level functions in `infrastructure/journal.py`, called
directly at nine sites. Calling them by import is what put a filesystem dependency in
every verb, so the functions become methods on something injected. The implementation
is still those functions; only who reaches them changed.

`default_dir` is on the port because `replay` needs to know where to look, and "where
manifests live" is the same fact as "where they are written" — splitting it would let the
two drift, which is exactly the failure where a manifest is written somewhere the lister
never globs.
"""

from __future__ import annotations

from typing import Protocol, runtime_checkable


@runtime_checkable
class Journal(Protocol):
    """Records what a write is about to do, before it does it."""

    #: Where manifests are written when a caller names no directory.
    default_dir: str

    def write_manifest(
        self,
        op: str,
        *,
        before: dict,
        inverse: str | None = None,
        journal_dir: str | None = None,
    ) -> str: ...

    def copy_database(self, dest_dir: str | None, db_path) -> dict: ...
