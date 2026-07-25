# Zotero Context

Read-only Python API for local Zotero state and annotations.

This package is the shared core for:

- Obsidian plugin integration
- MCP tools for agents
- local automation scripts

It does not write to Zotero. It reads:

- Zotero window/tab/reader state through the local `zotero-bridge` endpoint
- annotations from `~/Zotero/zotero.sqlite` using read-only SQLite connections
- citekeys from Better BibTeX JSON-RPC when available

## CLI

Run from this directory during development:

```bash
python3 -m zotero_context.cli window-state --pretty
python3 -m zotero_context.cli active-reader --include-annotations --pretty
python3 -m zotero_context.cli open-readers --include-annotations --pretty
python3 -m zotero_context.cli annotations F6G6KC7G --pretty
python3 -m zotero_context.cli resolve-pdf U7Y49M4I --pretty
python3 -m zotero_context.cli sources --pretty
```

When installed:

```bash
zotero-context window-state --pretty
zotero-context annotations F6G6KC7G --pretty
```

## Python API

```python
from zotero_context import ZoteroContext

zc = ZoteroContext()
state = zc.get_window_state()
active = zc.get_active_reader()
annotations = zc.get_annotations(active.attachment_key) if active else []
parent_key, pdf_key = zc.resolve_pdf_attachment_key("U7Y49M4I")
open_context = zc.get_open_reader_context(include_annotations=True)
```

## MCP

The MCP adapter is intentionally thin and optional:

```bash
uv sync --extra mcp
uv run zotero-context-mcp
```

Tools exposed:

- `get_zotero_window_state`
- `get_zotero_active_reader`
- `get_zotero_open_readers`
- `get_zotero_annotations`
- `resolve_zotero_pdf`
- `get_zotero_sources`
