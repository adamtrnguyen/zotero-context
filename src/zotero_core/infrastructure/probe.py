"""HTTP implementation of `domain.ports.zotero_probe.ZoteroProbe`.

The `urlopen` call moved here VERBATIM from `application/services/liveness.py`, including
the reason it treats an HTTP error as success -- that behaviour is subtle enough that
restating it from memory would be how it gets lost.
"""

from __future__ import annotations

import urllib.error
import urllib.request

#: Zotero's built-in HTTP server. Not the linker plugin and not cookjohn -- this is the
#: application's own port, which answers whenever Zotero is open at all.
ZOTERO_SERVER_URL = "http://127.0.0.1:23119/"


class HttpZoteroProbe:
    """Asks Zotero's own HTTP server whether anything is listening."""

    def __init__(self, url: str = ZOTERO_SERVER_URL, *, timeout: float = 5.0):
        self.url = url
        self.timeout = timeout

    def is_running(self) -> bool:
        """True if Zotero's built-in HTTP server answers at all.

        ANY reply counts, including 404 and 400. The question is whether something is
        listening on Zotero's port, not whether it likes the request -- Zotero's server
        404s an unknown path, and treating that as "not running" would make the probe
        report the opposite of the truth.
        """
        try:
            urllib.request.urlopen(self.url, timeout=self.timeout)
            return True
        except urllib.error.HTTPError:
            return True
        except OSError:
            return False
