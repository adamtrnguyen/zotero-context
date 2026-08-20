"""Which libraries exist — the user's own, plus any groups.

The smallest port here, and the one whose absence was most visible: every other read is scoped
to ONE library and silently defaults to the user's, so without this a caller cannot tell "you
have nothing" from "you are looking in the wrong library".

Wraps what was a module-level FUNCTION taking a db path, for the same reason `FileJournal` does:
a port needs something to inject, and a module is not injectable.
"""

from __future__ import annotations

from typing import Any, Protocol, runtime_checkable


@runtime_checkable
class LibraryCatalogue(Protocol):
    """Enumerates libraries with a live item count."""

    def list_libraries(self) -> tuple[tuple[Any, ...], str]: ...
