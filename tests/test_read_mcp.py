"""Tests for the read adapter -- the surface that had ZERO coverage before the merge.

`writes/`'s suite reached `read/items.py` only incidentally, through write verbs that
happened to call it for pre/post state. Nothing tested `cli.py`, `read_mcp.py`,
`service.py`, `bridge.py`, `bbt.py` or `annotations.py` at all: the entire agent-facing
read surface was untested, which is part of why five implemented methods stayed
unreachable long enough for a third-party plugin to fill the gap.

Most of these are PROPERTIES OVER `TOOLS` rather than per-tool assertions, mirroring
`test_mcp_server.py`. A property holds for the eleventh tool as well as the first, which
is the point of moving to a table.
"""

from __future__ import annotations

import ast
import inspect
import pathlib
import sqlite3

import pytest

from zotero_core.domain.annotation_type import ANNOTATION_TYPE
from zotero_core.infrastructure.http.bridge import ZoteroBridgeError
from zotero_core.infrastructure.sqlite.connect import ZoteroReadError
from zotero_core.interfaces import read_mcp
from zotero_core.interfaces.factory import build_context


@pytest.fixture()
def ctx(zotero):
    """A read facade aimed at the throwaway database, assembled by the real factory.

    ⚠ REPLACES `monkeypatch.setattr(read_mcp, "_CTX", ctx)`. The adapter kept its context in
    a module global built lazily on first use, so pointing it anywhere meant rewriting that
    global -- five sites did. `call_read` takes `ctx=` now, so the substitution is an
    argument and a wrong one is a TypeError rather than a silent no-op.
    """
    return build_context(zotero_db_path=zotero.path)


@pytest.fixture()
def wired(zotero):
    """The throwaway database itself, for tests that add rows to it."""
    return zotero


# --------------------------------------------------------------------------
# properties over TOOLS
# --------------------------------------------------------------------------


def test_every_declared_property_is_a_real_parameter_of_the_verb():
    """The schema cannot promise an argument the verb would reject.

    This is the failure the table exists to prevent: the schema and the dispatch used to
    be two hand-maintained lists with nothing checking they agreed.
    """
    for spec in read_mcp.TOOLS:
        params = set(inspect.signature(spec.verb).parameters)
        undeclared = sorted(set(spec.properties) - params)
        assert not undeclared, f"{spec.name} declares {undeclared}, not accepted by {spec.verb}"


def test_every_required_argument_is_also_declared_as_a_property():
    for spec in read_mcp.TOOLS:
        missing = sorted(set(spec.required) - set(spec.properties))
        assert not missing, f"{spec.name} requires {missing} but does not declare them"


def test_every_required_argument_has_no_default_in_the_verb():
    """A required argument with a default is a contradiction -- one of them is a lie."""
    for spec in read_mcp.TOOLS:
        params = inspect.signature(spec.verb).parameters
        for name in spec.required:
            assert params[name].default is inspect.Parameter.empty, (
                f"{spec.name}.{name} is declared required but defaults to "
                f"{params[name].default!r}"
            )


def test_tool_names_are_unique_and_the_index_agrees_with_the_table():
    names = [spec.name for spec in read_mcp.TOOLS]
    assert len(names) == len(set(names)), "duplicate tool name"
    assert set(read_mcp._BY_NAME) == set(names)
    assert len(read_mcp._BY_NAME) == len(read_mcp.TOOLS)


def test_every_tool_has_a_description():
    for spec in read_mcp.TOOLS:
        assert spec.description.strip(), f"{spec.name} has no description"


def test_no_schema_exposes_an_injection_seam():
    """`store`, `ctx` and friends are for tests and composition, never for a caller."""
    forbidden = {"store", "ctx", "context", "conn", "db", "bridge", "bbt"}
    for spec in read_mcp.TOOLS:
        leaked = forbidden & set(spec.properties)
        assert not leaked, f"{spec.name} exposes {sorted(leaked)}"


def test_schema_shape_is_valid_json_schema_object():
    for spec in read_mcp.TOOLS:
        schema = spec.as_tool_schema()
        assert schema["type"] == "object"
        assert isinstance(schema["properties"], dict)
        assert isinstance(schema["required"], list)
        assert set(schema["required"]) <= set(schema["properties"])


def test_the_annotation_type_vocabulary_is_discoverable():
    """Types 3-6 used to be unguessable: the description advertised two by example.

    Pinned against `ANNOTATION_TYPE` so adding a type to the store fails here rather
    than silently leaving the schema behind.
    """
    assert set(read_mcp.ANNOTATION_TYPE_NAMES) == set(ANNOTATION_TYPE.values())
    for spec in read_mcp.TOOLS:
        prop = spec.properties.get("annotation_types")
        if prop is not None:
            assert set(prop["items"]["enum"]) == set(ANNOTATION_TYPE.values())


# --------------------------------------------------------------------------
# dispatch
# --------------------------------------------------------------------------


def test_unknown_tool_raises(ctx):
    with pytest.raises(ValueError, match="Unknown tool"):
        read_mcp.call_read("get_zotero_nothing", {}, ctx=ctx)


def test_an_undeclared_argument_is_refused_not_dropped(ctx):
    """The old dispatch read `arguments.get(...)` per branch, so a misspelling vanished
    and the caller silently got the default."""
    with pytest.raises(ValueError, match="does not accept"):
        read_mcp.call_read(
            "get_zotero_item",
            {"item_key": "AAAA1111", "include_annotaions": True},
            ctx=ctx,
        )


def test_a_missing_required_argument_is_refused(ctx):
    with pytest.raises(ValueError, match="requires"):
        read_mcp.call_read("get_zotero_item", {}, ctx=ctx)


def test_an_explicit_null_on_an_optional_argument_falls_back_to_the_verb_default(wired, ctx):
    wired.add("AAAA1111", title="A Paper")
    result = read_mcp.call_read("list_zotero_pdfs", {"limit": None}, ctx=ctx)
    assert result["count"] == 0


# --------------------------------------------------------------------------
# the reads themselves
# --------------------------------------------------------------------------


def test_get_zotero_item_reports_a_non_empty_item_type(wired, ctx):
    """REGRESSION GUARD for the tool this one replaces.

    cookjohn's `get_item_details` returns `itemType: ""` for EVERY item -- verified
    2026-08-19 against a live zotero.sqlite that reported `journalArticle` for the same
    keys. An agent reading an item to decide what to do next got a blank type.
    """
    wired.add("AAAA1111", title="A Paper", item_type="book")
    result = read_mcp.call_read("get_zotero_item", {"item_key": "AAAA1111"}, ctx=ctx)
    assert result["ok"] is True
    assert result["item_type"] == "book"
    assert result["item_type"] != ""


def test_get_zotero_item_returns_fields_creators_and_tags(wired, ctx):
    wired.add(
        "AAAA1111",
        title="A Paper",
        fields={"DOI": "10.1/xyz"},
        tags=["read", "ml"],
        creators=[{"creatorType": "author", "firstName": "Max", "lastName": "Welling"}],
    )
    result = read_mcp.call_read("get_zotero_item", {"item_key": "AAAA1111"}, ctx=ctx)
    assert result["fields"]["DOI"] == "10.1/xyz"
    assert result["tags"] == ["ml", "read"]
    assert result["creators"][0]["lastName"] == "Welling"


def test_get_zotero_item_creators_keep_their_order(wired, ctx):
    """Author order is meaning, not presentation -- first author drives the dedupe tier."""
    wired.add(
        "AAAA1111",
        creators=[
            {"creatorType": "author", "lastName": "First"},
            {"creatorType": "author", "lastName": "Second"},
            {"creatorType": "author", "lastName": "Third"},
        ],
    )
    result = read_mcp.call_read("get_zotero_item", {"item_key": "AAAA1111"}, ctx=ctx)
    assert [c["lastName"] for c in result["creators"]] == ["First", "Second", "Third"]


def test_get_zotero_item_on_a_missing_key_says_so_rather_than_raising(wired, ctx):
    result = read_mcp.call_read("get_zotero_item", {"item_key": "NOPE0000"}, ctx=ctx)
    assert result["ok"] is False
    assert result["error"] == "not_found"


def test_get_zotero_item_reports_trash_state(wired, ctx):
    wired.add("AAAA1111")
    wired.trash("AAAA1111")
    result = read_mcp.call_read("get_zotero_item", {"item_key": "AAAA1111"}, ctx=ctx)
    assert result["trashed"] is True


def test_every_read_carries_the_mode_that_served_it(wired, ctx):
    """The honesty mechanism `items.py` was built around, finally visible from outside.

    Every agent-facing read used to go through `annotations._connect`, which is
    `immutable=1`-only and reports nothing -- so a snapshot and a live read were
    indistinguishable to a caller.
    """
    wired.add("AAAA1111")
    assert read_mcp.call_read("get_zotero_item", {"item_key": "AAAA1111"}, ctx=ctx)["read_mode"]
    assert read_mcp.call_read("list_zotero_pdfs", {}, ctx=ctx)["read_mode"]


def test_check_zotero_duplicate_answers_without_writing(wired, ctx):
    """The verdict used to be reachable only by ATTEMPTING a create and reading the
    refusal, so "check before you add" cost a write attempt."""
    wired.add(
        "AAAA1111",
        title="Bayesian Learning",
        creators=[{"creatorType": "author", "lastName": "Welling"}],
    )
    before = wired.store().item_states(["AAAA1111"])

    ok = read_mcp.call_read("check_zotero_duplicate", {"title": "Something Absent"}, ctx=ctx)
    assert ok["verdict"] == "ok"

    warn = read_mcp.call_read(
        "check_zotero_duplicate",
        {
            "title": "bayesian   learning",  # normalisation: case and whitespace
            "creators": [{"creatorType": "author", "lastName": "Welling"}],
        },
        ctx=ctx,
    )
    assert warn["verdict"] == "warn"

    # nothing moved
    assert wired.store().item_states(["AAAA1111"]).live == before.live


def test_check_zotero_duplicate_blocks_on_doi(wired, ctx):
    wired.add("AAAA1111", fields={"DOI": "10.1/xyz"})
    result = read_mcp.call_read(
        "check_zotero_duplicate", {"doi": "https://doi.org/10.1/xyz"}, ctx=ctx
    )
    assert result["verdict"] == "block"


def test_get_zotero_trash_count(wired, ctx):
    wired.add("AAAA1111")
    wired.add("BBBB2222")
    wired.trash("BBBB2222")
    assert read_mcp.call_read("get_zotero_trash_count", {}, ctx=ctx)["trashed"] == 1


# --------------------------------------------------------------------------
# error codes
# --------------------------------------------------------------------------


def test_annotation_reads_go_through_the_one_opener_and_record_the_mode(zotero):
    """REGRESSION. `ZoteroAnnotationStore` was the only sqlite reader in the package not
    using `connect.open_readonly` -- it hardcoded `immutable=1` and probed nothing, while
    `read/__init__.py` claimed every read reports the mode that served it. Two real
    consequences: an annotation read could never be a live read even with Zotero closed,
    and a caller could not tell it had been handed a snapshot."""
    from zotero_core.infrastructure.sqlite.annotations import ZoteroAnnotationStore

    store = ZoteroAnnotationStore(zotero.path)
    assert store.last_read_mode == ""
    store.get_sources_with_annotations()
    assert store.last_read_mode in {"mode=ro", "immutable=1"}


def test_the_annotation_error_alias_is_gone(zotero):
    """WAS `test_the_annotation_error_name_still_resolves`, asserting the alias held.

    The alias is removed. It was not a harmless name: `_CODES` matched on it and read as
    annotation-scoped while resolving to the class EVERY store raises, so a missing
    database via `collection_tree` came back as `annotation_read_failed`. The test below
    pins the corrected behaviour; this one stops the name coming back.
    """
    from zotero_core.infrastructure.sqlite import annotations

    assert not hasattr(annotations, "ZoteroAnnotationError")
    assert not hasattr(annotations, "ANNOTATION_TYPE")


def test_failures_carry_a_code_a_caller_can_branch_on():
    """A locked database, a missing key and a closed Zotero used to be one string."""
    assert read_mcp.error_code(ZoteroBridgeError("down")) == "bridge_unreachable"
    assert read_mcp.error_code(ZoteroReadError("no db")) == "database_unavailable"
    assert read_mcp.error_code(sqlite3.OperationalError("locked")) == "database_unavailable"
    assert read_mcp.error_code(ValueError("nope")) == "bad_arguments"
    assert read_mcp.error_code(RuntimeError("?")) == "error"


def test_a_non_annotation_read_failure_is_not_labelled_an_annotation_failure(tmp_path):
    """THE REGRESSION. Measured before the fix:

        collection_tree -> ZoteroReadError -> 'annotation_read_failed'
        list_libraries  -> ZoteroReadError -> 'annotation_read_failed'

    Every store calls `open_readonly`, so the one class covers all of them; the alias is
    what made an annotation-specific code look correct at the call site.
    """
    from zotero_core.infrastructure.sqlite.collections import ZoteroCollectionStore
    from zotero_core.infrastructure.sqlite.libraries import list_libraries

    absent = str(tmp_path / "absent.sqlite")
    for call in (lambda: ZoteroCollectionStore(absent).tree(), lambda: list_libraries(absent)):
        with pytest.raises(ZoteroReadError) as caught:
            call()
        assert read_mcp.error_code(caught.value) == "database_unavailable"


# --------------------------------------------------------------------------
# structure
# --------------------------------------------------------------------------


def test_mcp_is_imported_only_inside_a_function():
    """AST, not grep. A module-scope `import mcp` would force every consumer of a read
    verb to install an async runtime, and would break the `zotero-core` CLI on a machine
    that never opted into the extra."""
    source = pathlib.Path(read_mcp.__file__).read_text()
    tree = ast.parse(source)
    for node in tree.body:  # module scope only
        if isinstance(node, ast.Import):
            for alias in node.names:
                assert not alias.name.startswith("mcp"), "module-scope `import mcp`"
        if isinstance(node, ast.ImportFrom):
            assert not (node.module or "").startswith("mcp"), "module-scope `from mcp import`"


def test_the_adapter_imports_without_the_mcp_extra():
    """Importing the module must not require the extra; only `main()` does."""
    import importlib

    importlib.reload(read_mcp)
    assert read_mcp.TOOLS


def test_the_context_is_configurable_by_environment(monkeypatch, tmp_path):
    """MCP used to hard-wire ~/Zotero/zotero.sqlite -- the CLI had --db, MCP had nothing,
    so it could not be pointed at a copy or a fixture."""
    monkeypatch.setenv("ZOTERO_CORE_DB", str(tmp_path / "elsewhere.sqlite"))
    ctx = read_mcp._context_from_env()
    assert str(tmp_path / "elsewhere.sqlite") in str(ctx.items.db_path)


def test_both_adapters_share_one_tool_declaration_and_one_dispatch():
    """`_ToolSpec` and the dispatch existed TWICE, byte-identical apart from comments.

    Both adapters exist because of the `TOOLS` table -- each replaced a three-place-per-tool
    change with one entry. Declaring the table's row type twice, and its dispatch twice,
    reintroduced exactly that duplication one level up.
    """
    from zotero_core.interfaces import read_mcp, tool_spec, write_mcp

    # one row type, with the write side adding only `transport`
    assert issubclass(tool_spec.WriteToolSpec, tool_spec.ToolSpec)
    assert all(isinstance(s, tool_spec.ToolSpec) for s in read_mcp.TOOLS)
    assert all(isinstance(s, tool_spec.WriteToolSpec) for s in write_mcp.TOOLS)

    # a read tool must NOT claim a transport it does not have
    assert not any(hasattr(s, "transport") for s in read_mcp.TOOLS)

    # and one dispatch, reached from both
    import inspect

    for module in (read_mcp, write_mcp):
        src = inspect.getsource(module)
        assert "_dispatch(" in src, f"{module.__name__} hand-rolls its dispatch again"
