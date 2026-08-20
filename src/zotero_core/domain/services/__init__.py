"""Pure functions over the domain. No sqlite, no network, no mcp.

Modules of functions rather than classes, matching `omnirag/domain/services/` -- the
distinction that convention draws is that a DOMAIN service takes entities and returns
entities, while an APPLICATION service takes ports and sequences them.
"""
