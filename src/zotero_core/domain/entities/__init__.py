"""Domain entities, split by PROVENANCE — where the data actually comes from.

    models.py   rows this package reads out of zotero.sqlite
    gui.py      the zotero-bridge HTTP payload: what the app is showing right now

⚠ These were one module, and mixing them was the concrete form of "domain/ is a
shared-utilities bucket". Six of the nine types exist solely to parse a bridge response --
`WindowState` even keeps the untransformed payload in `raw: dict`, and every `from_bridge`
accepts both camelCase and snake_case, which is adapter behaviour wearing a domain label.
The two SQL-derived ones are the actual Zotero nouns, and they were sitting next to
window-state parsers.

Empty by convention: omni-rag's fifteen layer `__init__.py` files are all zero bytes and
nothing is re-exported from a layer. The package ROOT is the documented exception, because
`zotero_core.__all__` is the published API — see `tests/test_public_api.py`.
"""
