"""What a Zotero key looks like. One rule, one place.

THE DUPLICATION THIS ENDS
-------------------------
Five copies, four distinct implementations:

    write/verbs.py                     re.compile(r"^[A-Z0-9]{8}$")
    write/collections.py               re.compile(r"^[A-Z0-9]{8}$")
    write/transports/cookjohn.py       re.compile(r"^[A-Z0-9]{8}$")
    write/transports/cookjohn.py       re.search(r"\\b([A-Z0-9]{8})\\b", ...)   inline
    read/service.py                    len(v) == 8 and v.isalnum() and v.upper() == v

`domain/` was created to hold this and never received it -- the layer docstring, the
`.importlinter` rationale and the README all claimed the consolidation had happened while
all five copies stayed where they were. This module is that claim becoming true.

⚠ THE FOURTH ONE DISAGREED WITH THE OTHER THREE. `str.isalnum()` is Unicode-aware, so the
`read/service.py` form accepted input the regexes reject:

    ARTINWQZ    regex True    isalnum True      a real key
    ＡＲＴＩＮＷＱＺ   regex False   isalnum True      full-width Latin
    ⅠⅠⅠⅠⅠⅠⅠⅠ   regex False   isalnum True      Roman-numeral characters
    ABCDΕΦΓΗ    regex False   isalnum True      Greek capitals

The read layer used it to decide "is this an item key, or a citekey to look up in Better
BibTeX?" -- so a string could pass there, be treated as a key, and then be refused by the
write layer as malformed. Two layers, two answers, one string.

WHY [A-Z0-9] AND NOT ZOTERO'S REAL ALPHABET
-------------------------------------------
Zotero's actual alphabet is narrower -- all 3405 keys in the live library use only
`23456789ABCDEFGHIJKLMNPQRSTUVWXYZ` (no 0, 1, or O) -- but this gate stays at [A-Z0-9]
deliberately. A stricter class could only add a way to WRONGLY refuse a key some future
Zotero mints, and it would buy nothing: a typo that lands inside the alphabet is caught by
the existence gate, which is the check that actually protects the caller.

(That paragraph existed in exactly one of the five copies. It is the spec, so it travels
with the rule.)

⚠ THERE IS NO LONGER A SIXTH COPY. This paragraph used to read: "`write/transports/
cookjohn.py` KEEPS ITS OWN COPY, on purpose. It is vendored verbatim into
`calibre-zotero-jump`... so it must stay import-standalone. A test asserts the two
patterns are identical -- drift detection without coupling."

Both premises were false, checked 2026-08-19:

  * `calibre-zotero-jump` does not vendor that file. `build.sh` zips `__init__.py`,
    `ui.py` and the import-name marker; `ui.py` contains "cookjohn" zero times and
    reimplements the JSON-RPC client, carrying its own `\b([A-Z0-9]{8})\b`. The plugin
    truly cannot import this package -- Calibre's embedded Python cannot see a uv
    virtualenv -- but it copies the IDEA, not the file, so its copy is unreachable from
    here and no test ever checked it.
  * `cookjohn.py` was never import-standalone: it imports `zotero_core.domain.errors` at
    module scope.

The transport now calls `is_key` / `find_embedded_key` from here, and the old drift test
became an absence check. Copies inside this package are down from five to zero; the
plugin's copy is outside it and always was.
"""

from __future__ import annotations

import re

#: The shape of a Zotero item or collection key. Anchored: a key IS this, whole.
KEY_PATTERN = r"^[A-Z0-9]{8}$"

#: The same shape unanchored, for finding a key embedded in prose (cookjohn sometimes
#: answers with a sentence rather than a field). Separate constant because the anchoring
#: is the difference between "this string is a key" and "this string contains one".
EMBEDDED_KEY_PATTERN = r"\b([A-Z0-9]{8})\b"

_KEY_RE = re.compile(KEY_PATTERN)
_EMBEDDED_KEY_RE = re.compile(EMBEDDED_KEY_PATTERN)


def is_key(value: str) -> bool:
    """Is this string a Zotero key? The one answer, for both layers."""
    return bool(_KEY_RE.match(value))


def find_embedded_key(text: str) -> str | None:
    """The first key-shaped token in some text, or None. Last resort, not a validator."""
    match = _EMBEDDED_KEY_RE.search(text)
    return match.group(1) if match else None
