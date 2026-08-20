from __future__ import annotations

import json
import urllib.error
import urllib.request

from zotero_core.domain.entities.gui import WindowState

DEFAULT_BRIDGE_URL = "http://127.0.0.1:23119/zotero-bridge/window-state"
DEFAULT_PING_URL = "http://127.0.0.1:23119/zotero-bridge/ping"


class ZoteroBridgeError(RuntimeError):
    pass


class ZoteroBridgeClient:
    def __init__(self, url: str = DEFAULT_BRIDGE_URL, timeout: float = 5.0):
        self.url = url
        self.timeout = timeout

    def ping(self) -> dict:
        # ⚠ WAS `self.url.replace("/window-state", "/ping")` while `DEFAULT_PING_URL`
        # sat three lines above, defined and never read — the constant was dead AND its
        # value hand-derived. String surgery on a caller-supplied URL also silently
        # produces the wrong endpoint for any `url` not containing "/window-state".
        ping_url = (
            DEFAULT_PING_URL
            if self.url == DEFAULT_BRIDGE_URL
            else self.url.replace("/window-state", "/ping")
        )
        try:
            with urllib.request.urlopen(ping_url, timeout=self.timeout) as response:
                return json.load(response)
        except (urllib.error.HTTPError, urllib.error.URLError, TimeoutError) as exc:
            raise ZoteroBridgeError(f"Zotero bridge is not reachable at {ping_url}: {exc}") from exc

    def get_window_state_raw(self) -> dict:
        try:
            with urllib.request.urlopen(self.url, timeout=self.timeout) as response:
                payload = json.load(response)
        except (urllib.error.HTTPError, urllib.error.URLError, TimeoutError) as exc:
            raise ZoteroBridgeError(f"Zotero bridge is not reachable at {self.url}: {exc}") from exc
        if not isinstance(payload, dict):
            raise ZoteroBridgeError("Zotero bridge returned a non-object payload")
        return payload

    def get_window_state(self) -> WindowState:
        return WindowState.from_bridge(self.get_window_state_raw())
