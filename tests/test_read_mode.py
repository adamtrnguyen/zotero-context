"""`ReadMode` — the one primitive-wrapper type in a package that otherwise has none.

Neither omni-rag nor arete wraps a primitive in a class. This one earns the exception on
evidence: ~45 bare-`str` sites, and two comparisons against a magic literal that decide
whether a failed verification is a real failure or a stale snapshot.
"""

from __future__ import annotations

import json

import pytest

from zotero_core.domain.read_mode import ReadMode


@pytest.mark.parametrize(
    ("mode", "literal"),
    [(ReadMode.LIVE, "mode=ro"), (ReadMode.SNAPSHOT, "immutable=1"), (ReadMode.NONE, "none")],
)
def test_it_still_compares_equal_to_the_literal(mode, literal):
    """Fourteen existing assertions compare read_mode to a string. `str, Enum` keeps that
    working; a plain frozen dataclass would have broken every one of them."""
    assert mode == literal
    assert mode in {literal}


def test_it_serialises_to_the_value_not_the_member_name():
    """Everything leaves this package through `json.dumps`."""
    assert json.dumps({"read_mode": ReadMode.SNAPSHOT}) == '{"read_mode": "immutable=1"}'


def test_str_gives_the_value_not_the_repr():
    """REGRESSION GUARD. `enum.StrEnum` fixes `__str__`; the `str, Enum` mixin does NOT --
    without the explicit `__str__ = str.__str__`, `f"{ReadMode.LIVE}"` renders
    "ReadMode.LIVE". Nothing interpolates it today; this is so nothing has to notice when
    something does.

    `StrEnum` is not an option: it is 3.11+ and this package declares >=3.10.
    """
    assert str(ReadMode.LIVE) == "mode=ro"
    assert f"{ReadMode.SNAPSHOT}" == "immutable=1"


def test_is_snapshot_is_the_question_callers_actually_ask():
    """`after.read_mode == "mode=ro"` was load-bearing logic keyed on a magic string: a
    typo there does not raise, it silently flips a verification verdict."""
    assert ReadMode.SNAPSHOT.is_snapshot is True
    assert ReadMode.LIVE.is_snapshot is False
    assert ReadMode.NONE.is_snapshot is False


def test_the_real_opener_returns_the_type(zotero):
    from zotero_core.infrastructure.sqlite.connect import open_readonly

    zotero.add("AAAA1111")
    conn, mode = open_readonly(zotero.path)
    try:
        assert isinstance(mode, ReadMode)
    finally:
        conn.close()


def test_an_empty_batch_reports_NONE_rather_than_a_mode_it_did_not_use(zotero):
    """No query ran, so claiming "live" or "snapshot" would assert something about the
    database that was never read."""
    assert zotero.store().item_states([]).read_mode is ReadMode.NONE
