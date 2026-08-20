"""Search over the fixture library: metadata, annotations, fulltext.

There was no search of any kind in this package before, so every search an agent ran went
to a third-party plugin -- and got Zotero's substring matching, which returns nothing for
a query with one typo.
"""

from __future__ import annotations

import pytest

from zotero_core.infrastructure.sqlite.search import ZoteroSearchStore


@pytest.fixture()
def search(zotero):
    return ZoteroSearchStore(zotero.path)


# --------------------------------------------------------------------------
# metadata
# --------------------------------------------------------------------------


def test_fuzzy_search_finds_a_title_the_query_misspells(zotero, search):
    """The whole point. Zotero's own search returns ZERO for this."""
    zotero.add("AAAA1111", title="Bayesian Learning via Stochastic Gradient Langevin Dynamics")
    hits, _ = search.items("Langevan Dynmaics")
    assert [h.item_key for h in hits] == ["AAAA1111"]
    assert hits[0].matched_on == "title"
    assert hits[0].score > 0.8


def test_exact_mode_does_not_find_it(zotero, search):
    """Contrast, so the fuzzy test cannot pass for the wrong reason."""
    zotero.add("AAAA1111", title="Bayesian Learning via Stochastic Gradient Langevin Dynamics")
    hits, _ = search.items("Langevan Dynmaics", fuzzy=False)
    assert hits == ()


def test_exact_mode_still_matches_a_real_substring(zotero, search):
    zotero.add("AAAA1111", title="Bayesian Learning via Stochastic Gradient Langevin Dynamics")
    hits, _ = search.items("stochastic gradient", fuzzy=False)
    assert [h.item_key for h in hits] == ["AAAA1111"]


def test_search_reports_which_field_matched(zotero, search):
    """A fuzzy hit is otherwise unexplainable -- "why did this come back" has no answer."""
    zotero.add(
        "AAAA1111",
        title="Some Paper",
        creators=[{"creatorType": "author", "lastName": "Welling"}],
    )
    zotero.add("BBBB2222", title="Welling Water Systems")
    hits = {h.item_key: h.matched_on for h in search.items("welling")[0]}
    assert hits["AAAA1111"] == "creator"
    assert hits["BBBB2222"] == "title"


def test_search_matches_tags_and_doi(zotero, search):
    zotero.add("AAAA1111", title="Untagged", tags=["mechanistic-interp"])
    zotero.add("BBBB2222", title="Other", fields={"DOI": "10.1234/xyz"})
    assert [h.item_key for h in search.items("mechanistic-interp")[0]] == ["AAAA1111"]
    assert [h.item_key for h in search.items("10.1234/xyz")[0]] == ["BBBB2222"]


def test_search_excludes_trashed_items(zotero, search):
    zotero.add("AAAA1111", title="Findable Paper")
    zotero.trash("AAAA1111")
    assert search.items("Findable Paper")[0] == ()


def test_search_excludes_attachments_and_notes(zotero, search):
    """A collection of PDF filenames is noise on top of every result."""
    zotero.add("PARENT01", title="Real Paper")
    zotero.add("ATTACH01", item_type="attachment", parent="PARENT01", title="Real Paper.pdf")
    hits, _ = search.items("Real Paper")
    assert [h.item_key for h in hits] == ["PARENT01"]


def test_search_is_scoped_to_the_user_library(zotero):
    zotero.add("AAAA1111", title="Shared Title")
    assert [h.item_key for h in ZoteroSearchStore(zotero.path).items("Shared Title")[0]]
    other = ZoteroSearchStore(zotero.path, library_id=7)
    assert other.items("Shared Title")[0] == ()


def test_item_type_filter(zotero, search):
    zotero.add("AAAA1111", title="A Study", item_type="book")
    zotero.add("BBBB2222", title="A Study", item_type="journalArticle")
    hits, _ = search.items("A Study", item_type="book")
    assert [h.item_key for h in hits] == ["AAAA1111"]


def test_results_are_ordered_by_score(zotero, search):
    zotero.add("AAAA1111", title="Langevin Dynamics")
    zotero.add("BBBB2222", title="Stochastic Gradient Langevin Dynamics In Practice")
    hits, _ = search.items("Langevin Dynamics")
    assert hits[0].item_key == "AAAA1111"
    assert hits[0].score >= hits[1].score


def test_limit_is_respected(zotero, search):
    for n in range(5):
        zotero.add(f"KEY{n:05d}", title=f"Paper About Models {n}")
    assert len(search.items("Paper About Models", limit=2)[0]) == 2


def test_a_blank_query_returns_nothing_rather_than_everything(zotero, search):
    zotero.add("AAAA1111", title="Anything")
    assert search.items("   ")[0] == ()


def test_search_reports_its_read_mode(zotero, search):
    zotero.add("AAAA1111", title="A Paper")
    assert search.items("A Paper")[1]


# --------------------------------------------------------------------------
# annotations
# --------------------------------------------------------------------------


def _annotate(zotero, key, parent, *, text="", comment="", color="#ffd400", type_id=1):
    """Add an annotation via the builder, then fill in the fields it does not model.

    `ZoteroBuilder.add(item_type="annotation")` already inserts the itemAnnotations row
    (and requires a parent), so this UPDATEs rather than inserting a second one.
    """
    import sqlite3

    zotero.add(key, item_type="annotation", parent=parent, title="")
    con = sqlite3.connect(zotero.path)
    try:
        con.execute(
            "UPDATE itemAnnotations SET type=?, text=?, comment=?, color=?, pageLabel=?"
            " WHERE itemID=?",
            (type_id, text, comment, color, "12", zotero.ids[key]),
        )
        con.commit()
    finally:
        con.close()


def test_annotation_search_matches_highlight_text(zotero, search):
    zotero.add("PARENT01", title="A Paper")
    zotero.add("ATTACH01", item_type="attachment", parent="PARENT01", title="")
    _annotate(zotero, "ANNO0001", "ATTACH01", text="predictive world models")
    hits, _ = search.annotations("world model")
    assert len(hits) == 1
    assert hits[0].parent_title == "A Paper"
    assert hits[0].page_label == "12"


def test_annotation_search_matches_the_comment_too(zotero, search):
    zotero.add("PARENT01", title="A Paper")
    zotero.add("ATTACH01", item_type="attachment", parent="PARENT01", title="")
    _annotate(zotero, "ANNO0001", "ATTACH01", text="something", comment="check this later")
    assert len(search.annotations("check this")[0]) == 1


def test_annotation_search_filters_by_colour(zotero, search):
    """Colour is how people encode meaning in Zotero -- "the yellow ones" is a category."""
    zotero.add("PARENT01", title="A Paper")
    zotero.add("ATTACH01", item_type="attachment", parent="PARENT01", title="")
    _annotate(zotero, "ANNO0001", "ATTACH01", text="yellow note", color="#ffd400")
    _annotate(zotero, "ANNO0002", "ATTACH01", text="red note", color="#ff6666")

    assert len(search.annotations(color="#ffd400")[0]) == 1
    assert search.annotations(color="#ffd400")[0][0].text == "yellow note"


def test_annotation_search_filters_by_type(zotero, search):
    zotero.add("PARENT01", title="A Paper")
    zotero.add("ATTACH01", item_type="attachment", parent="PARENT01", title="")
    _annotate(zotero, "ANNO0001", "ATTACH01", text="a highlight", type_id=1)
    _annotate(zotero, "ANNO0002", "ATTACH01", text="a note", type_id=2)
    hits, _ = search.annotations(types={"note"})
    assert [h.annotation_type for h in hits] == ["note"]


def test_annotation_search_with_no_query_browses(zotero, search):
    """Omitting the query is how you ask "show me everything yellow"."""
    zotero.add("PARENT01", title="A Paper")
    zotero.add("ATTACH01", item_type="attachment", parent="PARENT01", title="")
    _annotate(zotero, "ANNO0001", "ATTACH01", text="anything")
    assert len(search.annotations()[0]) == 1


def test_annotation_search_excludes_trashed_annotations(zotero, search):
    zotero.add("PARENT01", title="A Paper")
    zotero.add("ATTACH01", item_type="attachment", parent="PARENT01", title="")
    _annotate(zotero, "ANNO0001", "ATTACH01", text="gone")
    zotero.trash("ANNO0001")
    assert search.annotations("gone")[0] == ()


# --------------------------------------------------------------------------
# fulltext
# --------------------------------------------------------------------------


def test_fulltext_finds_a_phrase_and_returns_a_snippet(zotero, search):
    zotero.add("PARENT01", title="A Paper")
    zotero.add("ATTACH01", item_type="attachment", parent="PARENT01", title="")
    zotero.index_fulltext(
        "ATTACH01", "Intro text. We propose stochastic gradient Langevin dynamics. The end."
    )
    hits, _ = search.fulltext("stochastic gradient langevin")
    assert len(hits) == 1
    assert hits[0].match_count == 1
    assert "stochastic gradient Langevin" in hits[0].snippets[0]


def test_fulltext_matches_a_phrase_that_spans_a_line_break(zotero, search):
    """Cache files separate paragraphs with '\\n\\n', so a phrase crossing one has TWO
    whitespace characters where the query has a space. An exact substring never matches
    it -- this is the bug the regex rewrite fixed."""
    zotero.add("PARENT01", title="A Paper")
    zotero.add("ATTACH01", item_type="attachment", parent="PARENT01", title="")
    zotero.index_fulltext("ATTACH01", "we use stochastic\n\ngradient descent here")
    assert len(search.fulltext("stochastic gradient")[0]) == 1


def test_fulltext_is_case_insensitive_but_snippets_keep_the_original_text(zotero, search):
    zotero.add("PARENT01", title="A Paper")
    zotero.add("ATTACH01", item_type="attachment", parent="PARENT01", title="")
    zotero.index_fulltext("ATTACH01", "Café Society and the Bayesian Method")
    hits, _ = search.fulltext("bayesian method")
    assert "Bayesian Method" in hits[0].snippets[0]


def test_fulltext_requires_every_word_to_be_present(zotero, search):
    """Stage 1 intersects the word index, so a query word absent from a document
    eliminates it before any file is opened."""
    zotero.add("PARENT01", title="A Paper")
    zotero.add("ATTACH01", item_type="attachment", parent="PARENT01", title="")
    zotero.index_fulltext("ATTACH01", "only stochastic here")
    assert search.fulltext("stochastic gradient")[0] == ()


def test_fulltext_counts_every_occurrence_but_caps_the_snippets(zotero, search):
    zotero.add("PARENT01", title="A Paper")
    zotero.add("ATTACH01", item_type="attachment", parent="PARENT01", title="")
    zotero.index_fulltext("ATTACH01", " ".join(["langevin dynamics"] * 10))
    hits, _ = search.fulltext("langevin dynamics", max_snippets=3)
    assert hits[0].match_count == 10
    assert len(hits[0].snippets) == 3


def test_fulltext_ranks_by_match_count(zotero, search):
    for key, reps in (("ATTACH01", 1), ("ATTACH02", 5)):
        parent = f"P{key[-2:]}00000"[:8]
        zotero.add(parent, title=f"Paper {key}")
        zotero.add(key, item_type="attachment", parent=parent, title="")
        zotero.index_fulltext(key, " ".join(["langevin dynamics"] * reps))
    hits, _ = search.fulltext("langevin dynamics")
    assert hits[0].attachment_key == "ATTACH02"


def test_fulltext_skips_a_document_whose_cache_file_is_missing(zotero, search):
    """The index can outlive the cache -- the honest answer is no hit, not a crash."""
    zotero.add("PARENT01", title="A Paper")
    zotero.add("ATTACH01", item_type="attachment", parent="PARENT01", title="")
    zotero.index_fulltext("ATTACH01", "langevin dynamics")
    (zotero.path.parent / "storage" / "ATTACH01" / ".zotero-ft-cache").unlink()
    assert search.fulltext("langevin dynamics")[0] == ()


def test_fulltext_with_a_blank_query_returns_nothing(search):
    assert search.fulltext("   ")[0] == ()


def test_attachment_text_returns_the_cache(zotero, search):
    zotero.add("PARENT01", title="A Paper")
    zotero.add("ATTACH01", item_type="attachment", parent="PARENT01", title="")
    zotero.index_fulltext("ATTACH01", "the whole document text")
    assert search.attachment_text("ATTACH01") == "the whole document text"


def test_attachment_text_is_none_when_never_indexed(search):
    """A scan with no text layer is not an error; it is a fact about the file."""
    assert search.attachment_text("NOSUCH00") is None
