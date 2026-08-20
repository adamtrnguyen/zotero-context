"""Coercing the arguments a HUMAN or an AGENT typed into what a verb expects.

ONE implementation, shared by the CLI and both MCP adapters. `parse_types` existed twice —
`cli.py` took `str` only, `read_mcp.py` took `Any` and also handled `list` — with six call
sites split between them and no import linking the two, so they were free to drift and the
CLI silently could not accept the list form an agent sends.

The superset survived, which is the right direction: a CLI argument arrives as a string, an
MCP argument arrives as whatever JSON carried, and a function that accepts both is correct
for both. Narrowing to `str` would have made the shared version wrong for the adapters.
"""

from __future__ import annotations

from typing import Any


def parse_types(value: Any) -> set[str] | None:
    """A set of annotation-type names, or None for "no filter".

    Accepts a comma-separated string (`--types highlight,note`) or a list (`["highlight"]`).
    `None` and empty mean NO filter rather than an empty filter — the difference matters:
    an empty set would match nothing and read as "you have no annotations".
    """
    if not value:
        return None
    if isinstance(value, str):
        return {part.strip() for part in value.split(",") if part.strip()}
    if isinstance(value, list):
        return {str(part) for part in value if str(part).strip()}
    return None
