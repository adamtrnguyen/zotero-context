"""One rule for what a Zotero key is — and the drift detector for the copy that must stay.

Five copies, four implementations, before this. `domain/` was created to hold the rule and
never received it: the layer docstring, the `.importlinter` rationale and the README all
claimed the consolidation had happened while every copy stayed put.
"""

from __future__ import annotations

import pytest

from zotero_core.domain.services.identity import (
    EMBEDDED_KEY_PATTERN,
    KEY_PATTERN,
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
    from zotero_core.read.service import _looks_like_item_key

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


def test_the_vendored_copy_has_not_drifted():
    """THE DRIFT DETECTOR.

    `write/transports/cookjohn.py` keeps its own copy on purpose: it is vendored verbatim
    into `calibre-zotero-jump`, which runs inside Calibre's embedded Python and cannot see
    a uv virtualenv, so it must stay import-standalone. It cannot share the code — but it
    can be checked against it, which is the whole point of this test.
    """
    from zotero_core.write.transports import cookjohn

    assert cookjohn._KEY_RE.pattern == KEY_PATTERN
    assert cookjohn._EMBEDDED_KEY_RE.pattern == EMBEDDED_KEY_PATTERN


def test_no_module_outside_the_transport_defines_its_own_key_regex():
    """Structural guard: the consolidation stays consolidated.

    Greps the source rather than trusting it, because the previous claim that this had
    been done was written in three places and true in none.
    """
    import pathlib

    import zotero_core

    root = pathlib.Path(zotero_core.__file__).parent
    offenders = []
    for path in root.rglob("*.py"):
        if path.name == "cookjohn.py" or path.parts[-2:] == ("services", "identity.py"):
            continue
        if "[A-Z0-9]{8}" in path.read_text():
            offenders.append(str(path.relative_to(root)))
    assert not offenders, f"key regex re-introduced in: {offenders}"
