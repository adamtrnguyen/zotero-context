"""Recording what a write is about to change, so it can be undone.

WHY THIS IS A MANIFEST AND NOT A DATABASE COPY
----------------------------------------------
calibre-core copies metadata.db before every write because a DB rollback is the only
undo that exists there. Neither half of that holds for Zotero:

  * `zotero.sqlite` is 329 MB (measured 2026-08-13), and Zotero already keeps five
    rotating daily copies of it beside the original (`zotero.sqlite.bak` .. `.4.bak`).
    Copying it per call spends ~330 MB duplicating what already exists.
  * restoring such a copy is not an undo anyone can safely take: it requires closing
    Zotero and reverts every unrelated change since the copy was made. Taken while
    Zotero runs it also needs its `-journal` alongside to be coherent at all.

So "back up before mutating" is honoured per-operation, by recording the state the
write is about to overwrite plus the call that reverses it. `copy_database` is still
here for a caller who wants the 329 MB snapshot, with the cost stated.

WHICH OPERATIONS GET A MANIFEST, AND WHY NOT ALL OF THEM
-------------------------------------------------------
Only the ones that overwrite or remove something. A `create` has no prior state --
journalling "nothing was here" would be filing noise, and it invites the habit of
skimming manifests. What gets one:

    trash / restore          the trash flag
    metadata update          the previous value of every field being written
    tag set / creator replace  the entire previous list (these REPLACE, not merge)
    collection delete/rename   the name, parent, and membership
    remove items from collection  the membership being removed

Creates and pure additions (`add_tags`, `add_items_to_collection`) do not, because
the inverse of an addition is a removal the caller can already express.
"""

from __future__ import annotations

import json
import os
import shutil
import time

# Ephemeral on purpose. The manifest exists to undo a mistake noticed in the same
# sitting; the DURABLE undo for a trash is Zotero's own trash, which lives in the
# database until someone empties it. Same convention as calibre-core's backup dir.
DEFAULT_JOURNAL_DIR = "/tmp/zotero-write-journal"


def write_manifest(
    op: str,
    *,
    before: dict,
    inverse: str | None = None,
    journal_dir: str | None = None,
) -> str:
    """Write one manifest and return its path.

    `journal_dir=None` resolves `DEFAULT_JOURNAL_DIR` at CALL time -- the same trap as
    the transport URLs, and it bit for real: with the constant bound as a default
    ARGUMENT, the test suite could not be redirected off the shared directory and wrote
    216 manifests into it, which is exactly the noise that makes a write journal useless
    as an audit trail of real writes.

    `before` is whatever the operation is about to overwrite, in whatever shape that
    operation's undo needs -- deliberately not a fixed schema, because a tag
    replacement and a collection deletion do not have prior states of the same shape,
    and flattening them into one would lose the part that matters.

    `inverse` is a literal call string. Prose like "restore it in the GUI" is not an
    undo; something a human can paste is.
    """
    journal_dir = journal_dir or DEFAULT_JOURNAL_DIR
    os.makedirs(journal_dir, exist_ok=True)
    # Microseconds, not seconds: two writes inside the same second are ordinary in a
    # loop, and a second-resolution stamp would have the later one overwrite the
    # record of the earlier -- losing exactly the manifest most likely to be needed.
    stamp = time.strftime("%Y%m%d-%H%M%S") + f"-{time.time_ns() % 1_000_000:06d}"
    path = os.path.join(journal_dir, f"{op}-{stamp}.json")
    with open(path, "w") as handle:
        json.dump(
            {
                "op": op,
                "written_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
                "inverse": inverse,
                "before": before,
            },
            handle,
            indent=2,
        )
    return path


def copy_database(dest_dir: str, db_path) -> dict:
    """Copy zotero.sqlite AND its rollback journal aside. ~330 MB per call.

    Both files or neither: DESIGN.md's sanctioned snapshot pattern is
    "copy-then-read (db + journal)". A copy of the main file alone, taken while
    Zotero holds a transaction open, can contain pages the journal was going to roll
    back -- so on its own it is not a coherent point in time.

    Off by default everywhere. Restoring it requires closing Zotero and discards
    every unrelated change made since it was taken.
    """
    os.makedirs(dest_dir, exist_ok=True)
    stamp = time.strftime("%Y%m%d-%H%M%S")
    out: dict = {"database": None, "journal": None, "bytes": 0}
    dest = os.path.join(dest_dir, f"zotero.sqlite.backup-{stamp}")
    shutil.copy2(str(db_path), dest)
    out["database"] = dest
    out["bytes"] += os.path.getsize(dest)
    journal_file = db_path.with_name(db_path.name + "-journal")
    if journal_file.exists():
        jdest = os.path.join(dest_dir, f"zotero.sqlite-journal.backup-{stamp}")
        shutil.copy2(str(journal_file), jdest)
        out["journal"] = jdest
        out["bytes"] += os.path.getsize(jdest)
    return out
