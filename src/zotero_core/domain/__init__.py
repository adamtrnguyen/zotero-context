"""Pure domain: entities, value objects and policy. No sqlite, no HTTP, no mcp.

This layer exists so the rules that were duplicated across the two former packages
have exactly one home. `_KEY_RE` lived in three files (writes.py, collections.py,
cookjohn.py) and the title normaliser was described in its own docstring as "a
degraded copy of calibre-core's". A layer that cannot import a driver is what keeps
them from drifting apart again.

`.importlinter`'s `domain-is-pure` contract enforces the "no driver" half.
"""
