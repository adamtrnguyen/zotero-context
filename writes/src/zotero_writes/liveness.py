"""The liveness gate. One probe, three distinguishable failures.

THE PRECONDITION THAT INVERTS
-----------------------------
calibre-core's first gate is "the Calibre GUI must be CLOSED" -- `calibredb`
corrupts state if it writes underneath a running GUI. Zotero is the exact opposite:
every write channel is code executing INSIDE the application, so a closed Zotero is
not a safe state, it is no channel at all.

WHY THREE FAILURES AND NOT ONE BOOLEAN
--------------------------------------
The surface spans two plugins on two ports, and they fail independently. Collapsing
that into "Zotero is not available" would send someone to start an application that
is already running. So the probe walks a hierarchy:

    :23119/            -- Zotero's own built-in server. Present whenever Zotero runs,
                          with no plugin involved, which makes it the authoritative
                          "is the application up" signal.
    :23119/zotero-linker/ping   -- the linker plugin (trash, restore, linked files)
    :23121/mcp                  -- the cookjohn plugin (items, metadata, collections)

Connection refused at :23119 means the app is down (`zotero_not_running`). :23119
answering while a plugin path does not means the app is up and that ONE plugin is
missing (`linker_not_installed` / `cookjohn_not_installed`), which is a different
job to fix.

Each operation declares the transports it actually needs, so a metadata write is
not blocked by a missing linker and a trash is not blocked by a missing cookjohn.
"""

from __future__ import annotations

import urllib.error
import urllib.request

from .cookjohn import CookjohnClient
from .errors import Reason, WriteBlocked
from .linker import LinkerClient

ZOTERO_SERVER_URL = "http://127.0.0.1:23119/"


def zotero_is_running(url: str = ZOTERO_SERVER_URL, *, timeout: float = 5.0) -> bool:
    """True if Zotero's built-in HTTP server answers at all.

    ANY reply counts, including 404 and 400. The question is whether something is
    listening on Zotero's port, not whether it likes the request -- Zotero's server
    404s an unknown path, and treating that as "not running" would make the probe
    report the opposite of the truth.
    """
    try:
        urllib.request.urlopen(url, timeout=timeout)
        return True
    except urllib.error.HTTPError:
        return True
    except OSError:
        return False


def require_zotero(
    *,
    needs: tuple[str, ...] = ("linker",),
    linker: LinkerClient | None = None,
    cookjohn: CookjohnClient | None = None,
) -> dict:
    """Refuse unless Zotero is running with the plugins this operation needs.

    Returns {"zotero": ..., "linker": ..., "cookjohn": ...} for whatever was probed,
    so the result of a write can record which versions performed it.
    """
    info: dict = {}
    for transport in needs:
        try:
            if transport == "linker":
                info["linker"] = (linker or LinkerClient()).ping()
            elif transport == "cookjohn":
                info["cookjohn"] = (cookjohn or CookjohnClient()).ping()
            else:
                raise ValueError(f"unknown transport {transport!r}")
        except WriteBlocked as exc:
            # A plugin did not answer. Which of the two failures it is depends on
            # whether the APPLICATION is up, so that is checked only now -- on the
            # failure path, where the extra request costs nothing anybody waits for.
            if exc.code in (
                Reason.ZOTERO_NOT_RUNNING,
                Reason.COOKJOHN_NOT_INSTALLED,
            ) and not zotero_is_running():
                raise WriteBlocked(
                    Reason.ZOTERO_NOT_RUNNING,
                    "Zotero is not running — every write channel is code executing "
                    "inside the application, so there is no way in while it is closed",
                    {"needed": list(needs), "probe": ZOTERO_SERVER_URL},
                ) from exc
            raise
    return info
