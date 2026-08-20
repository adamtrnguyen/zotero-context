"""Tests for collection reads -- SQL this package never had.

`grep parentCollectionID` over the read layer returned nothing before this module, while
collection WRITES were fully exposed. An agent could create a collection and file items
into it and then could not read back what existed or what was in one, which is the whole
reason a third-party plugin stayed in the tool surface.
"""

from __future__ import annotations

import sqlite3

import pytest

from zotero_core.read.collections import MAX_DEPTH, ZoteroCollectionStore


@pytest.fixture()
def store(zotero):
    return ZoteroCollectionStore(zotero.path)


# --------------------------------------------------------------------------
# the tree
# --------------------------------------------------------------------------


def test_an_empty_library_has_an_empty_tree(store):
    tree = store.tree()
    assert tree.roots == ()
    assert tree.flat() == ()
    assert tree.read_mode


def test_the_tree_nests_and_carries_breadcrumb_paths(zotero, store):
    top = zotero.add_collection("Embodied AI")
    mid = zotero.add_collection("Robotics", parent=top)
    zotero.add_collection("WAM", parent=mid)

    tree = store.tree()
    assert len(tree.roots) == 1
    root = tree.roots[0]
    assert root.name == "Embodied AI"
    assert root.path == "Embodied AI"
    assert root.depth == 0
    assert root.parent_key is None

    robotics = root.subcollections[0]
    assert robotics.path == "Embodied AI > Robotics"
    assert robotics.depth == 1
    assert robotics.parent_key == top

    wam = robotics.subcollections[0]
    assert wam.path == "Embodied AI > Robotics > WAM"
    assert wam.depth == 2
    assert wam.parent_key == mid


def test_flat_returns_every_node_once(zotero, store):
    top = zotero.add_collection("A")
    zotero.add_collection("B", parent=top)
    zotero.add_collection("C", parent=top)
    zotero.add_collection("D")

    flat = store.tree().flat()
    assert sorted(node.name for node in flat) == ["A", "B", "C", "D"]


def test_item_count_excludes_trashed_items(zotero, store):
    """A collection showing 3 when the GUI shows 2 is a bug report waiting to happen --
    trashing an item does NOT remove it from `collectionItems`."""
    coll = zotero.add_collection("Papers")
    zotero.add("AAAA1111")
    zotero.add("BBBB2222")
    zotero.add_to_collection("AAAA1111", coll)
    zotero.add_to_collection("BBBB2222", coll)
    assert store.tree().roots[0].item_count == 2

    zotero.trash("BBBB2222")
    assert store.tree().roots[0].item_count == 1


def test_the_tree_is_scoped_to_the_user_library(zotero):
    """Group libraries are REAL on this machine -- libraryIDs 1, 2, 4 and 5 exist, and
    95 collections live there against 85 in the user library. An unscoped query silently
    mixes them, and the same collection NAME can appear in several."""
    zotero.add_collection("Mine")
    con = sqlite3.connect(zotero.path)
    try:
        con.execute(
            "INSERT INTO collections (collectionID, collectionName, libraryID, key) "
            "VALUES (9001, 'Theirs', 7, 'GROUPKEY')"
        )
        con.commit()
    finally:
        con.close()

    names = [node.name for node in ZoteroCollectionStore(zotero.path).tree().flat()]
    assert names == ["Mine"]

    other = ZoteroCollectionStore(zotero.path, library_id=7)
    assert [node.name for node in other.tree().flat()] == ["Theirs"]


def test_a_parent_cycle_makes_collections_vanish_rather_than_hanging(zotero):
    """The intuitive fear here is the WRONG one, and this test exists to pin that down.

    `parentCollectionID` is a SINGLE parent pointer, so a cycle has no member with a NULL
    parent -- and the recursion is anchored on exactly that. The cycle is unreachable
    from the anchor, the query terminates normally, and the cycle's collections are
    silently ABSENT from the tree.

    So `MAX_DEPTH` is not what saves us here (it bounds pathological depth; see the next
    test). What matters is that the failure mode is "a collection disappeared", not "the
    process hung" -- and that it is a known property rather than a future mystery.
    """
    con = sqlite3.connect(zotero.path)
    try:
        # A REACHABLE cycle: a real root anchors the recursion, and the loop hangs off it.
        # Two mutually-parented nodes alone would not test the cap at all -- neither has a
        # NULL parent, so the anchor selects nothing and the recursion never starts.
        con.execute(
            "INSERT INTO collections (collectionID, collectionName, parentCollectionID, "
            "libraryID, key) VALUES (9100, 'Root', NULL, 1, 'CYCLEROO')"
        )
        con.execute(
            "INSERT INTO collections (collectionID, collectionName, parentCollectionID, "
            "libraryID, key) VALUES (9101, 'A', 9100, 1, 'CYCLEAAA')"
        )
        con.execute(
            "INSERT INTO collections (collectionID, collectionName, parentCollectionID, "
            "libraryID, key) VALUES (9102, 'B', 9101, 1, 'CYCLEBBB')"
        )
        # ...and now A's parent is its own descendant: Root -> A -> B -> A -> B -> ...
        con.execute("UPDATE collections SET parentCollectionID = 9102 WHERE collectionID = 9101")
        con.commit()
    finally:
        con.close()

    tree = ZoteroCollectionStore(zotero.path).tree()
    names = [node.name for node in tree.flat()]

    # It returned, and it did NOT trip the depth bound -- the recursion simply never
    # reached the cycle.
    assert tree.truncated is False
    # Root survives; A and B are gone, because the UPDATE detached A from Root.
    assert names == ["Root"]
    assert "A" not in names and "B" not in names


def test_deep_nesting_is_reported_as_truncated_rather_than_silently_cut(zotero):
    con = sqlite3.connect(zotero.path)
    try:
        parent = "NULL"
        for depth in range(MAX_DEPTH + 3):
            cid = 9200 + depth
            con.execute(
                "INSERT INTO collections (collectionID, collectionName, parentCollectionID,"
                f" libraryID, key) VALUES ({cid}, 'L{depth}', {parent}, 1, 'DEEP{depth:04d}')"
            )
            parent = str(cid)
        con.commit()
    finally:
        con.close()

    tree = ZoteroCollectionStore(zotero.path).tree()
    assert tree.truncated is True


# --------------------------------------------------------------------------
# membership
# --------------------------------------------------------------------------


def test_collection_items_returns_direct_members_with_type_and_title(zotero, store):
    coll = zotero.add_collection("Papers")
    zotero.add("AAAA1111", title="A Paper", item_type="journalArticle")
    zotero.add_to_collection("AAAA1111", coll)

    members = store.items(coll)
    assert len(members) == 1
    assert members.members[0].item_key == "AAAA1111"
    assert members.members[0].title == "A Paper"
    assert members.members[0].item_type == "journalArticle"
    assert members.members[0].trashed is False
    assert members.read_mode


def test_collection_items_excludes_subcollection_members(zotero, store):
    """Matches the Zotero GUI's default -- 'Show Items from Subcollections' is off."""
    top = zotero.add_collection("Top")
    child = zotero.add_collection("Child", parent=top)
    zotero.add("AAAA1111")
    zotero.add("BBBB2222")
    zotero.add_to_collection("AAAA1111", top)
    zotero.add_to_collection("BBBB2222", child)

    assert [m.item_key for m in store.items(top).members] == ["AAAA1111"]


def test_collection_items_hides_trashed_unless_asked(zotero, store):
    coll = zotero.add_collection("Papers")
    zotero.add("AAAA1111")
    zotero.add_to_collection("AAAA1111", coll)
    zotero.trash("AAAA1111")

    assert len(store.items(coll)) == 0
    with_trash = store.items(coll, include_trashed=True)
    assert len(with_trash) == 1
    assert with_trash.members[0].trashed is True


def test_collection_items_on_an_unknown_key_is_empty_not_an_error(store):
    assert len(store.items("NOSUCH00")) == 0


# --------------------------------------------------------------------------
# the inverse
# --------------------------------------------------------------------------


def test_collections_of_answers_which_collections_an_item_is_in(zotero, store):
    """New capability. Nothing answered this -- not this package, not cookjohn. Better
    BibTeX has only a citekey-keyed version, unreachable from an item key."""
    a = zotero.add_collection("Alpha")
    b = zotero.add_collection("Beta")
    zotero.add("AAAA1111")
    zotero.add_to_collection("AAAA1111", a)
    zotero.add_to_collection("AAAA1111", b)

    result = store.collections_of(["AAAA1111"])
    assert sorted(c["name"] for c in result["AAAA1111"]) == ["Alpha", "Beta"]


def test_collections_of_reports_an_unfiled_item_as_empty_not_missing(zotero, store):
    """`{} vs {'KEY': ()}` is the difference between "not filed" and "never asked"."""
    zotero.add("AAAA1111")
    result = store.collections_of(["AAAA1111"])
    assert result == {"AAAA1111": ()}


def test_collections_of_with_no_keys_is_empty(store):
    assert store.collections_of([]) == {}


# --------------------------------------------------------------------------
# find
# --------------------------------------------------------------------------


def test_find_matches_case_insensitively_and_returns_the_path(zotero, store):
    top = zotero.add_collection("Embodied AI")
    zotero.add_collection("Robotics", parent=top)

    found = store.find("robot")
    assert len(found) == 1
    assert found[0].name == "Robotics"
    assert found[0].path == "Embodied AI > Robotics"


def test_find_with_a_blank_needle_returns_nothing_rather_than_everything(zotero, store):
    zotero.add_collection("Anything")
    assert store.find("   ") == ()
