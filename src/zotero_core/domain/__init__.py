"""Pure domain: entities, values and policy. No sqlite, no HTTP, no mcp.

    read_mode.py        ReadMode -- which mode served a sqlite read
    entities.py         the types (being split by provenance; see the plan)
    services/           pure functions: identity, policy

WHAT THIS LAYER IS FOR, AND WHAT IT TOOK TO MEAN IT
---------------------------------------------------
Rules that were duplicated because there was nowhere to put them.

⚠ This docstring previously said `_KEY_RE` "lived in three files ... a layer that cannot
import a driver is what keeps them from drifting apart again" -- in the present tense,
describing work that had not been done. There were in fact FIVE copies and FOUR distinct
implementations, and `domain/` contained none of them. The `.importlinter` rationale and
the README repeated the same claim.

It is true now: `services/identity.py` holds the one rule, and
`tests/test_identity.py` greps the package to keep it that way. The fourth
implementation -- `str.isalnum()`, which accepted full-width Unicode the regexes reject --
was a real divergence between the read and write layers, not just untidiness.

One copy remains ON PURPOSE, in `write/transports/cookjohn.py`: that module is vendored
verbatim into Calibre's embedded Python and must stay import-standalone. It cannot share
the code; a test asserts it has not drifted from it.

`.importlinter`'s `domain-is-pure` contract enforces the "no driver" half.
"""
