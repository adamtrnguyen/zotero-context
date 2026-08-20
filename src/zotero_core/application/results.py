"""The success envelope every write verb returns.

WHY A BUILDER
-------------
The shape was spelled out by hand at 15 return sites across `verbs.py`, `collections.py`
and `replay.py`. Every one repeated `"ok": True`, the `op` name, the `transport` string
and `"versions": info`, and then added its own fields -- so renaming a field, or adding
one that every verb should carry, was a 15-place edit with no way to notice a site that
was missed. `errors.py` already made the opposite case for the failure path: the reason
is a CODE "because a consumer that wants to react differently... has to match substrings
of an English sentence". A success envelope assembled 15 independent times has the same
weakness one level up -- the consumer is matching a shape nobody guarantees.

WHAT STAYS FLEXIBLE
-------------------
Only the frame is fixed. The verb-specific payload is passed through unchanged and is
deliberately NOT schematised, for the same reason `journal.write_manifest`'s `before` is
not: a tag replacement and a collection deletion do not have results of the same shape,
and flattening them into one would lose the part that matters.

OMITTED vs EXPLICITLY NULL -- the distinction is load-bearing
------------------------------------------------------------
A key the caller never mentions is left OUT; a key the caller passes as None is emitted
AS null. That is not pedantry, and getting it backwards would have been a silent
behaviour change dressed as a refactor: `update_metadata` sets `undo_call` to None on
purpose when nothing was overwritten (cookjohn has no field-remove, so no inverse is
expressible) and a test asserts exactly `result["undo_call"] is None`.

So `undo_call: null` means "this verb has an undo concept and this call has none", while
an absent key means "this verb does not journal at all" -- e.g. `add_items_to_collection`,
whose inverse is a plain removal. Collapsing the two would lose which is which.
"""

from __future__ import annotations

from typing import Any

# Distinguishes "caller did not mention this key" from "caller passed None". See the
# module docstring -- the two mean different things and both occur.
_UNSET = object()

# The `transport` values a verb may report. `both` is preflight, which probes each plugin
# separately on purpose; `none` is a verb that touches no plugin at all.
TRANSPORTS = frozenset({"cookjohn", "linker", "both", "none"})


def ok(
    op: str,
    *,
    transport: str,
    versions: dict | None = None,
    undo_manifest: Any = _UNSET,
    undo_call: Any = _UNSET,
    **payload: Any,
) -> dict:
    """Build a success envelope. `payload` is the verb's own fields, passed through."""
    if transport not in TRANSPORTS:
        # A typo here would otherwise travel silently into the result and be believed --
        # `transport` is what someone debugging a write reads first.
        raise ValueError(f"unknown transport {transport!r}; expected one of {sorted(TRANSPORTS)}")
    envelope: dict[str, Any] = {"ok": True, "op": op, "transport": transport}
    envelope.update(payload)
    if undo_manifest is not _UNSET:
        envelope["undo_manifest"] = undo_manifest
    if undo_call is not _UNSET:
        envelope["undo_call"] = undo_call
    if versions is not None:
        envelope["versions"] = versions
    return envelope


def rows(key: str, items: Any, *, read_mode: Any, **extra: Any) -> dict:
    """The success envelope every READ returns. The counterpart to `ok()` above.

    WHY THIS EXISTS, WHICH IS THE SAME REASON `ok()` DOES
    -----------------------------------------------------
    `ok()` was written because the write frame had been spelled out by hand at 15 sites. The
    read side had no equivalent and had accumulated the same defect: `count` and `read_mode`
    were assembled by hand at ELEVEN returns in `context.py`, each with a different payload
    key -- `libraries`, `attachments`, `collections`, `items`, `hits`, `annotations`,
    `documents`, `sources`. Adding a field every read should carry was an eleven-place edit
    with no way to notice a site that was missed, and two sites HAD been missed:
    `get_zotero_trash_count` returned `{"trashed": N}` and `get_zotero_sources` returned a
    bare list that could not carry a `read_mode` at all.

    `count` is derived from the payload rather than passed, because the one thing a caller
    must be able to trust is that `count` describes the rows it was handed. Passing it
    separately is how those two drift.

    `read_mode` is REQUIRED, not defaulted. Every read is served either live or from an
    `immutable=1` snapshot, and "which" is the difference between "there is no such thing"
    and "there is no such thing YET" -- a default would let a new read quietly omit it,
    which is exactly how the two outliers happened.

    `extra` is the read's own fields, passed through unschematised for the same reason `ok`'s
    payload is: `query` and `fuzzy` mean nothing to a collection listing, and flattening them
    into one shape would lose the part a caller reads.
    """
    payload = list(items)
    return {"count": len(payload), "read_mode": read_mode, key: payload, **extra}
