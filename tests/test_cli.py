"""Tests for the JSON CLI -- which, like the read adapter, had no coverage at all.

The load-bearing test here is the last one. The read surface has already split once into
"what the CLI can do" and "what an agent can do", and the half nobody could reach is the
half that rotted: `items.py` and `duplicates.py` shipped seven public methods that
neither surface exposed, so an agent had to go to a third-party plugin instead.
"""

from __future__ import annotations

import argparse

import pytest

from zotero_core.infrastructure.service import ZoteroContext
from zotero_core.interfaces import cli, read_mcp


def _subcommands() -> set[str]:
    parser = cli.build_parser()
    actions = [a for a in parser._actions if isinstance(a, argparse._SubParsersAction)]
    assert len(actions) == 1, "expected exactly one subparser group"
    return set(actions[0].choices)


def test_every_declared_subcommand_has_a_handler():
    """A parser entry with no handler is a verb that parses and then raises."""
    missing = sorted(_subcommands() - set(cli._HANDLERS))
    assert not missing, f"subcommands with no handler: {missing}"


def test_every_handler_has_a_subcommand():
    """A handler with no parser entry is unreachable code -- the failure this whole
    refactor exists to remove."""
    orphans = sorted(set(cli._HANDLERS) - _subcommands())
    assert not orphans, f"handlers no subcommand can reach: {orphans}"


def test_unknown_command_raises():
    with pytest.raises(ValueError, match="Unknown command"):
        cli.dispatch(ZoteroContext(), argparse.Namespace(command="nope"))


@pytest.mark.parametrize(
    ("argv", "expected"),
    [
        (["item", "AAAA1111"], "item"),
        (["duplicate", "--title", "x"], "duplicate"),
        (["pdfs", "--limit", "3"], "pdfs"),
        (["trash-count"], "trash-count"),
    ],
)
def test_the_catalogue_verbs_parse(argv, expected):
    assert cli.build_parser().parse_args(argv).command == expected


def test_duplicate_accepts_repeated_authors():
    args = cli.build_parser().parse_args(
        ["duplicate", "--title", "x", "--author", "Welling", "--author", "Teh"]
    )
    assert args.author == ["Welling", "Teh"]


def test_item_reads_the_catalogue(zotero):
    ctx = ZoteroContext(zotero_db_path=zotero.path)
    zotero.add("AAAA1111", title="A Paper", item_type="book")
    result = cli.dispatch(ctx, argparse.Namespace(command="item", item_key="AAAA1111"))
    assert result["item_type"] == "book"


def test_the_cli_and_the_mcp_surface_expose_the_same_catalogue_reads():
    """PARITY GUARD.

    Every catalogue read must be reachable BOTH ways. This is the invariant that was
    violated for months: `ZoteroItemStore` had seven public methods and neither the CLI
    nor the MCP server exposed one of them, so the only way to ask "what type is this
    item" was a third-party plugin -- which returned an empty string for every item.

    Mapping is explicit rather than derived. A new catalogue read must be added here
    deliberately, which is the point: forgetting one surface is the original bug.
    """
    pairs = {
        "item": "get_zotero_item",
        "duplicate": "check_zotero_duplicate",
        "pdfs": "list_zotero_pdfs",
        "trash-count": "get_zotero_trash_count",
        "ping": "ping_zotero",
        "window-state": "get_zotero_window_state",
        "annotations": "get_zotero_annotations",
        "resolve-pdf": "resolve_zotero_pdf",
        "sources": "get_zotero_sources",
        "active-reader": "get_zotero_active_reader",
        "open-readers": "get_zotero_open_readers",
        "libraries": "list_zotero_libraries",
        "collections": "get_zotero_collections",
        "collection-items": "get_zotero_collection_items",
        "item-collections": "get_zotero_item_collections",
        "find-collections": "find_zotero_collections",
        "search": "search_zotero_items",
        "search-annotations": "search_zotero_annotations",
        "search-fulltext": "search_zotero_fulltext",
        "attachment-text": "get_zotero_attachment_text",
    }
    commands = _subcommands()
    tool_names = {spec.name for spec in read_mcp.TOOLS}

    for command, tool in pairs.items():
        assert command in commands, f"CLI lost `{command}`"
        assert tool in tool_names, f"MCP lost `{tool}`"

    # Neither surface may grow a read the other does not have.
    assert commands == set(pairs), f"CLI verbs not mirrored in MCP: {commands - set(pairs)}"
    assert tool_names == set(pairs.values()), (
        f"MCP tools not mirrored in the CLI: {tool_names - set(pairs.values())}"
    )
