from __future__ import annotations

import json
import urllib.error
import urllib.request

from ..domain.entities import WindowState

DEFAULT_BRIDGE_URL = "http://127.0.0.1:23119/zotero-bridge/window-state"
DEFAULT_PING_URL = "http://127.0.0.1:23119/zotero-bridge/ping"


class ZoteroBridgeError(RuntimeError):
    pass


class ZoteroBridgeClient:
    def __init__(self, url: str = DEFAULT_BRIDGE_URL, timeout: float = 5.0):
        self.url = url
        self.timeout = timeout

    def ping(self) -> dict:
        ping_url = self.url.replace("/window-state", "/ping")
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
