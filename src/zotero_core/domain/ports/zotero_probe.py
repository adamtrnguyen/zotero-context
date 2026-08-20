"""Is the Zotero application running at all?

Distinct from the two transport ports, because it answers a different question and it
answers it about the APPLICATION rather than about a plugin. `require_zotero` needs both:
when a plugin fails to answer, whether that is "the plugin is missing" or "Zotero is
closed" is decided by this probe, and reporting the wrong one sends you looking for a
plugin bug when the app simply is not open.

WHY THIS IS A PORT AND NOT A FUNCTION
-------------------------------------
It was a function -- `liveness.zotero_is_running`, calling `urllib.request.urlopen`
directly -- which put network I/O in the application layer and gave the test suite no way
in. Three tests patched the module global by name, and a fourth patched `urlopen` itself;
the fixture's own comment conceded "it is the only way in, because the MCP surface has no
injection parameter by design". A dependency that can only be replaced by rewriting the
module's globals is a dependency that was never declared.
"""

from __future__ import annotations

from typing import Protocol, runtime_checkable


@runtime_checkable
class ZoteroProbe(Protocol):
    """Whether Zotero itself is answering."""

    #: Reported in refusals so the message names what was probed.
    url: str

    def is_running(self) -> bool: ...
