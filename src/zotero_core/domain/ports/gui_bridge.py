"""Zotero's own window: what is selected, which tabs are open, where each reader sits.

The one collaborator that answers about the RUNNING APPLICATION rather than the database.
`zotero.sqlite` cannot answer "what is selected right now" — that is GUI state, it never
touches disk, and it is why the `bridge` plugin exists at all.

Named `GuiBridge` and not `Bridge`: the plugin is called zotero-bridge, but a port is named
for the capability, so that a second implementation (a stub, or a different plugin) does not
have to be called "bridge" to make sense.
"""

from __future__ import annotations

from typing import Any, Protocol, runtime_checkable


@runtime_checkable
class GuiBridge(Protocol):
    """Live GUI state from the running Zotero."""

    def get_window_state(self) -> Any: ...

    def ping(self) -> dict: ...
