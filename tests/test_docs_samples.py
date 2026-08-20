"""Every code sample in the docs is COMPILED and its imports EXECUTED.

WHY THIS EXISTS
---------------
An audit found three samples that do not run:

    core/README.md              from zotero_core.write import create_item, …   ImportError
    core/docs/write-surface.md  the same line                                  ImportError
    core/README.md              zc = ZoteroContext()                           TypeError

They were correct when written. `write/` became `application/services/`, and `ZoteroContext`
went from constructing its own collaborators to requiring eight ports — so the samples rotted
the moment the tree moved, and nothing noticed, because a sample in a fenced block is prose
as far as every gate is concerned.

WHAT THIS CHECKS, AND WHAT IT DELIBERATELY DOES NOT
---------------------------------------------------
It compiles each block (syntax) and executes only its IMPORT statements. That is exactly the
failure mode observed: a moved module or an unpublished name.

It does NOT execute the sample bodies. `create_item(...)` and `trash_items(...)` are real
writes against the real library, and a doc test that mutates Zotero would be worse than the
rot it prevents. The `TypeError` class of failure — a published type whose constructor
changed — is covered instead by `test_public_api.py`, which asserts the surface can
CONSTRUCT and not merely resolve.
"""

from __future__ import annotations

import ast
import pathlib
import re

import pytest

DOCS = [
    pathlib.Path("README.md"),
    *sorted(pathlib.Path("docs").glob("*.md")),
]

_BLOCK = re.compile(r"```python\n(.*?)```", re.S)


def _blocks() -> list[tuple[str, int, str]]:
    found = []
    for doc in DOCS:
        if not doc.exists():
            continue
        text = doc.read_text()
        for match in _BLOCK.finditer(text):
            line = text[: match.start()].count("\n") + 1
            found.append((str(doc), line, match.group(1)))
    return found


BLOCKS = _blocks()


def test_the_docs_actually_contain_samples():
    """A guard on the guard: if the fence regex stops matching, every test below passes
    vacuously and the docs go unchecked while reporting green."""
    assert len(BLOCKS) >= 3


@pytest.mark.parametrize("doc,line,code", BLOCKS, ids=[f"{d}:{n}" for d, n, _ in BLOCKS])
def test_every_doc_sample_compiles(doc, line, code):
    try:
        ast.parse(code)
    except SyntaxError as exc:  # pragma: no cover - only on a broken sample
        pytest.fail(f"{doc}:{line} does not parse: {exc}")


@pytest.mark.parametrize("doc,line,code", BLOCKS, ids=[f"{d}:{n}" for d, n, _ in BLOCKS])
def test_every_import_in_every_doc_sample_resolves(doc, line, code):
    """The rot that actually happened: a module that moved, or a name never published."""
    tree = ast.parse(code)
    imports = [n for n in ast.walk(tree) if isinstance(n, (ast.Import, ast.ImportFrom))]
    for node in imports:
        source = ast.unparse(node)
        if isinstance(node, ast.ImportFrom) and node.level:
            continue  # a relative import in a sample is illustrative, not runnable
        try:
            exec(compile(source, f"{doc}:{line}", "exec"), {})  # noqa: S102
        except ImportError as exc:
            pytest.fail(f"{doc}:{line} — `{source}` does not resolve: {exc}")
