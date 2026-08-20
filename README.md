# zotero-core

**The one place that knows what a local Zotero library is.** Reads and gated writes,
in one versioned package.

Zotero 9, local only — no Web API, no Zotero cloud. Attachments sync to the NAS via
WebDAV; everything here talks to the desktop app on this machine.

```
src/zotero_core/
  domain/       entities, values, policy         pure — no sqlite, no network, no mcp
  read/         items, duplicates, annotations   sqlite (read-only)
                bridge, bbt                      http: live window state, citekeys
  write/        verbs, collections, liveness     the gated CRUD surface
                journal, errors
                transports/ cookjohn, linker     stdlib urllib only
  interfaces/   cli, read_mcp, write_mcp         the only layer that may import mcp
```

`.importlinter` enforces `interfaces > write > read > domain`, plus four narrower rules.
**Five contracts, checked in CI-equivalent `just qa`.**

## Why one package

`zotero-context` (read) and `zotero-writes` (write) merged on 2026-08-19. The split was
defensible and was enforced — but the read half was never finished. `core` shipped
`items.py` and `duplicates.py` and exposed **neither** from its CLI or MCP surface, and
never had collection or search queries at all. So an agent asking *"what is in
collection X"* had to go to a third-party plugin, and the charter sentence — *"a second
answer to what is in the library is the failure this package exists to end"* — described
a gap rather than a guarantee.

Two packages also meant two of everything that was really one thing: two pyprojects
agreeing on `mcp<2` and `ty==0.0.55` in duplicated comments, two lockfiles, and one test
suite that lived in `writes/` and reached the read half only incidentally — `core` had no
`tests/` and no `.importlinter` of its own.

**Nothing was weakened.** The direction the split protected is now the `layers` contract.
`domain-is-pure` is new, and could not be expressed before: there was no domain layer for
the 8-char key regex (which lived in **three** files) or the title normaliser (described
in its own docstring as *"a degraded copy of calibre-core's"*) to live in.

## Install

```bash
uv sync --all-extras          # `mcp` extra: needed only by the two adapters
```

Zero runtime dependencies. The read layer is stdlib `sqlite3`; both write transports are
stdlib `urllib` + `json`. That is load-bearing, not incidental —
`calibre-zotero-jump/ui.py` runs inside Calibre's embedded Python, which cannot see a uv
virtualenv, and vendors `write/transports/cookjohn.py` verbatim.

## Read

```python
from zotero_core import ZoteroContext, ZoteroItemStore, check_duplicate

zc = ZoteroContext()
state       = zc.get_window_state()
active      = zc.get_active_reader()
annotations = zc.get_annotations(active.attachment_key) if active else []
_, pdf_key  = zc.resolve_pdf_attachment_key("U7Y49M4I")

store = ZoteroItemStore()
store.item_states(["ARTINWQZ"])      # exists / trashed / type / parent / children
store.item_fields("ARTINWQZ")        # {fieldName: value}
store.item_creators("ARTINWQZ")      # in orderIndex order
```

Every sqlite read reports **which mode served it**. With Zotero running, `mode=ro`
intermittently loses to the rollback journal and `immutable=1` answers from a
point-in-time snapshot instead — a caller that cannot tell those apart cannot know
whether it read the present or the recent past.

```bash
zotero-core window-state --pretty          # what Zotero is showing right now
zotero-core item NUQP6L46 --pretty         # type, fields, creators, tags, trash state
zotero-core duplicate --title "..." --author Welling
zotero-core pdfs --limit 5 --pretty
zotero-core trash-count
zotero-core annotations F6G6KC7G --pretty
zotero-core sources --pretty
```

The CLI and the MCP tools are held at **parity by a test** — every catalogue read is
reachable both ways. That invariant was violated for months: `ZoteroItemStore` had seven
public methods and *neither* surface exposed one of them.

## Write

Explicit import: `import zotero_core` stays cheap and read-only.

```python
from zotero_core.write import create_item, add_tags, trash_items, restore_items, WriteBlocked
```

Zotero must be **running** with the plugin that verb needs. Keys are shape-checked then
resolved before anything is sent. Creates are duplicate-checked. Verbs that REPLACE a
list rather than adding to it refuse unless asked twice. Whatever is about to be
overwritten is journalled with the call that reverses it. Results are re-read.

**No hard erase, ever** — the strongest thing here is a move to the trash, and
`restore_items` ships alongside. A test greps `dir()` for `purge|destroy|erase` to keep it
that way.

Full rationale, transport split and the incident that motivated the gates:
[`docs/write-surface.md`](docs/write-surface.md).

## MCP

```bash
uv run zotero-core-read-mcp     # the read tools
uv run zotero-core-write-mcp    # the gated write tools; call zotero_write_preflight first
```

Both adapters declare their tools in **one `TOOLS` table** from which the schema and the
dispatch are derived, so adding a tool is one entry rather than a three-place change
across two lists that must agree. **Undeclared arguments are refused, not dropped** — a
misspelled `include_annotaions` used to vanish silently and hand back the default.

Read tools, generated from the table rather than retyped -- the previous list said
19, enumerated 19, and omitted one:

`get_zotero_window_state`, `get_zotero_active_reader`, `get_zotero_open_readers`, `get_zotero_annotations`, `resolve_zotero_pdf`, `get_zotero_sources`, `get_zotero_item`, `check_zotero_duplicate`, `list_zotero_pdfs`, `get_zotero_trash_count`, `get_zotero_collections`, `get_zotero_collection_items`, `get_zotero_item_collections`, `find_zotero_collections`, `search_zotero_items`, `search_zotero_annotations`, `search_zotero_fulltext`, `get_zotero_attachment_text`, `list_zotero_libraries`, `ping_zotero`.

Point either adapter somewhere else with `ZOTERO_CORE_DB`, `ZOTERO_CORE_BRIDGE_URL`,
`ZOTERO_CORE_BBT_URL` — MCP used to hard-wire `~/Zotero/zotero.sqlite` while the CLI had
`--db`, so it could not be run against a copy or a fixture.

**Search** (`read/search.py`) is **fuzzy by default**, which nothing in the stack had.
Zotero's own search is substring-only: `Langevan Dynmaics` returns **zero** results there
and scores **0.875** here against the paper it means. Scoring is token-wise — a
whole-string ratio gives 0.395 on that pair, because the candidate title is three times
longer than the query. Every hit reports `matched_on`, so a surprising result explains
itself.

Fulltext search runs in **two stages**: Zotero's word index has no positions and no
counts, so it narrows candidates, and only the survivors' `.zotero-ft-cache` files are
opened for the phrase and its context. A query touches a handful of files rather than
587 MB.

**Collections** (`read/collections.py`) landed 2026-08-19 — the tree with breadcrumb
paths, membership, and the inverse (which collections an item is in) that nothing
answered before, in this package or in cookjohn. Two properties worth knowing: item
counts EXCLUDE trashed items so they match the GUI, and everything is scoped to the user
library — this machine has group libraries, where 10 of its 95 collections live.

## QA

```bash
just qa      # from the suite root: ruff + ty + 5 contracts + vulture + codespell + tests
```

`ruff format` is **deliberately absent** — `__all__` is grouped by CRUD concern rather
than sorted (`# noqa: RUF022`, "the grouping IS the documentation") and verb call sites
are hand-aligned to keep the injection seams readable. Use `just format`, which is
`ruff check --fix` only.
