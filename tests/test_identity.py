"""One rule for what a Zotero key is — and the drift detector for the copy that must stay.

Five copies, four implementations, before this. `domain/` was created to hold the rule and
never received it: the layer docstring, the `.importlinter` rationale and the README all
claimed the consolidation had happened while every copy stayed put.
"""

from __future__ import annotations

import pytest

from zotero_core.domain.services.identity import (
    find_embedded_key,
    is_key,
)


@pytest.mark.parametrize("key", ["ARTINWQZ", "NUQP6L46", "AAAA1111", "00000000"])
def test_a_real_key_is_a_key(key):
    assert is_key(key)


@pytest.mark.parametrize(
    ("value", "why"),
    [
        ("abcd1234", "lowercase"),
        ("ABCD123", "seven characters"),
        ("ABCD12345", "nine characters"),
        ("ABCD-123", "punctuation"),
        ("", "empty"),
        ("ＡＲＴＩＮＷＱＺ", "full-width Latin"),
        ("ⅠⅠⅠⅠⅠⅠⅠⅠ", "Roman-numeral characters"),
        ("ABCDΕΦΓΗ", "Greek capitals"),
        ("ABCD１２３４", "half ASCII, half full-width digits"),
    ],
)
def test_what_is_not_a_key(value, why):
    """The last four are the REGRESSION. `str.isalnum()` is Unicode-aware, so the old
    read-side check accepted every one of them while the write-side regex rejected them --
    two layers disagreeing about what a key is."""
    assert not is_key(value), why


def test_the_read_layer_now_asks_the_same_question_as_the_write_layer():
    """The specific bug: a string could pass the read-side check, be treated as an item key
    rather than sent to Better BibTeX, and then be refused downstream as malformed."""
    from zotero_core.application.services.context import _looks_like_item_key

    for value in ("ＡＲＴＩＮＷＱＺ", "ⅠⅠⅠⅠⅠⅠⅠⅠ", "ABCDΕΦΓΗ"):
        assert _looks_like_item_key(value) == is_key(value) is False


def test_a_citekey_shaped_string_still_goes_to_bbt():
    """Not every 8-char string is a key, and the ones that are not must reach BBT."""
    assert not is_key("welling2011")
    assert not is_key("houWorldModelRobot")


def test_find_embedded_key_pulls_a_key_out_of_prose():
    """cookjohn sometimes answers with a sentence rather than a field."""
    assert find_embedded_key("Item created (key: ARTINWQZ, type: book)") == "ARTINWQZ"
    assert find_embedded_key("nothing key-shaped here") is None


def test_the_transport_has_no_private_copy_of_the_rule():
    """WAS a drift detector; is now an absence check, because the copy is gone.

    This test used to assert `cookjohn._KEY_RE.pattern == KEY_PATTERN` -- drift detection
    for a copy kept "on purpose", because the module was "vendored verbatim into
    `calibre-zotero-jump`... so it must stay import-standalone". Both halves were false:

      * `calibre-zotero-jump/build.sh` zips three files and none is `cookjohn.py`; its
        `ui.py` contains "cookjohn" zero times and reimplements the client (2026-08-19).
      * the module imports `zotero_core.domain.errors` at module scope, so it was never
        import-standalone regardless.

    A checked copy is still a copy. The transport now calls the domain rule directly, so
    what there is to assert is that the copy did not come back.
    """
    from zotero_core.infrastructure.transports import cookjohn

    assert not hasattr(cookjohn, "_KEY_RE")
    assert not hasattr(cookjohn, "_EMBEDDED_KEY_RE")
    assert cookjohn.is_key is is_key
    assert cookjohn.find_embedded_key is find_embedded_key


def test_no_module_anywhere_defines_its_own_key_regex():
    """Structural guard: the consolidation stays consolidated.

    Greps the source rather than trusting it, because the previous claim that this had
    been done was written in three places and true in none.

    ⚠ `cookjohn.py` USED TO BE EXEMPT here and no longer is -- that exemption is what let
    a sixth copy live in the tree with a test blessing it. COMMENT LINES are skipped
    instead, so a module may still *discuss* the pattern (`cookjohn.py` explains why its
    copy was removed, and quoting it is the clearest way to say which one) while a real
    `re.compile` is caught. Stripping comments is the narrower exemption: it is scoped to
    prose rather than to a file.
    """
    import pathlib

    import zotero_core

    root = pathlib.Path(zotero_core.__file__).parent
    offenders = []
    for path in root.rglob("*.py"):
        if path.parts[-2:] == ("services", "identity.py"):
            continue
        code = "\n".join(
            line for line in path.read_text().splitlines() if not line.lstrip().startswith("#")
        )
        if "[A-Z0-9]{8}" in code:
            offenders.append(str(path.relative_to(root)))
    assert not offenders, f"key regex re-introduced in: {offenders}"
