"""The two write transports. Neither knows the other exists.

  cookjohn   :23121  items, metadata, notes, tags, collections   (MCP JSON-RPC)
  linker     :23119  linked attachments, trash, restore          (plain HTTP)

That mutual ignorance is the property that lets a verb need one and not the other,
and it is why `liveness` -- which must distinguish "Zotero is down" from "one plugin
is missing" -- is the only module that probes both.

Both are stdlib-only (urllib + json) ON PURPOSE: `calibre-zotero-jump/ui.py` runs
inside Calibre's embedded Python and vendors `cookjohn.py` verbatim rather than
reimplementing it. An `mcp` or `requests` import here would end that.
"""
