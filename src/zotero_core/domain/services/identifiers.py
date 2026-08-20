"""Normalising the identifiers two records are matched on.

Pure `re`, no database -- they were in `read/duplicates.py` only because that is the one
place that used them, and they are publicly re-exported from the package root, so they were
already part of the API while living in the SQL layer.

`clean_doi` matters most: translators emit the same DOI three ways (bare,
`https://doi.org/...`, `doi:...`), and a duplicate check that compared them literally would
miss two thirds of real matches.
"""

from __future__ import annotations

import re


def clean_doi(doi: str | None) -> str:
    """Bare lowercase DOI. Accepts the URL and `doi:` forms Zotero records mix.

    Zotero's DOI field holds whatever the translator put there, and translators
    disagree: `10.1145/3592433`, `https://doi.org/10.1145/3592433`, and
    `doi:10.1145/3592433` all appear. Comparing them raw makes the same DOI look
    like three different ones, which turns the strongest signal available into a
    miss.
    """
    if not doi:
        return ""
    value = doi.strip().casefold()
    value = re.sub(r"^https?://(dx\.)?doi\.org/", "", value)
    value = re.sub(r"^doi:\s*", "", value)
    return value.strip()


def clean_isbn(isbn: str | None) -> str:
    """Digits and X only. Deliberately does NOT validate the checksum.

    calibre-core runs isbnlib.canonical here, which rejects malformed lengths --
    better, and unavailable without adding a dependency to a package that has none.
    The consequence: a 12-digit typo can match another identical 12-digit typo. That
    is a `block` on a garbage value, so it is worth knowing about; it needs the same
    typo recorded twice to fire.
    """
    if not isbn:
        return ""
    return re.sub(r"[^0-9Xx]", "", isbn).upper()


def _calibre_uuids(extra: str) -> set[str]:
    return set(re.findall(r"calibre-uuid:\s*([0-9a-f-]{36})", extra or "", re.I))
