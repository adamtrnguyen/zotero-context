from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from ..domain.entities import to_jsonable
from ..read.annotations import DEFAULT_ZOTERO_DB
from ..read.bbt import DEFAULT_BBT_RPC_URL
from ..read.bridge import DEFAULT_BRIDGE_URL
from ..read.service import ZoteroContext


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    ctx = ZoteroContext(
        bridge_url=args.bridge_url,
        zotero_db_path=args.db,
        bbt_rpc_url=args.bbt_url,
    )

    try:
        payload = dispatch(ctx, args)
    except Exception as exc:
        print_json({"ok": False, "error": str(exc)}, pretty=args.pretty)
        return 1

    print_json(payload, pretty=args.pretty)
    return 0


def build_parser() -> argparse.ArgumentParser:
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--bridge-url", default=DEFAULT_BRIDGE_URL)
    common.add_argument("--db", default=str(DEFAULT_ZOTERO_DB), type=Path)
    common.add_argument("--bbt-url", default=DEFAULT_BBT_RPC_URL)
    common.add_argument("--pretty", action="store_true")

    parser = argparse.ArgumentParser(
        prog="zotero-context",
        description="Read-only Zotero window state and annotation context",
        parents=[common],
    )

    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("ping", parents=[common])
    sub.add_parser("window-state", parents=[common])
    add_reader_parser(sub.add_parser("active-reader", parents=[common]))
    add_reader_parser(sub.add_parser("open-readers", parents=[common]))
    annotations = sub.add_parser("annotations", parents=[common])
    annotations.add_argument("attachment_key")
    add_annotation_filters(annotations)
    resolve = sub.add_parser("resolve-pdf", parents=[common])
    resolve.add_argument("identifier")
    resolve.add_argument("--pdf-key", action="store_true")
    sources = sub.add_parser("sources", parents=[common])
    sources.add_argument("--no-citekeys", action="store_true")
    return parser


def add_reader_parser(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--include-annotations", action="store_true")
    parser.add_argument("--annotation-types", default="")
    parser.add_argument("--no-citekeys", action="store_true")


def add_annotation_filters(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--annotation-types", default="")
    parser.add_argument("--no-text", action="store_true")
    parser.add_argument("--no-comments", action="store_true")


def dispatch(ctx: ZoteroContext, args: argparse.Namespace) -> Any:
    if args.command == "ping":
        return {"ok": True, "bridge": ctx.ping()}
    if args.command == "window-state":
        return ctx.get_window_state()
    if args.command == "active-reader":
        contexts = ctx.get_open_reader_context(
            active_only=True,
            include_annotations=args.include_annotations,
            include_citekeys=not args.no_citekeys,
            annotation_types=parse_types(args.annotation_types),
        )
        return contexts[0] if contexts else None
    if args.command == "open-readers":
        return ctx.get_open_reader_context(
            include_annotations=args.include_annotations,
            include_citekeys=not args.no_citekeys,
            annotation_types=parse_types(args.annotation_types),
        )
    if args.command == "annotations":
        return ctx.get_annotations(
            args.attachment_key,
            types=parse_types(args.annotation_types),
            include_text=not args.no_text,
            include_comments=not args.no_comments,
        )
    if args.command == "resolve-pdf":
        parent_key, attachment_key = ctx.resolve_pdf_attachment_key(
            args.identifier,
            is_attachment_key=args.pdf_key,
        )
        return {"parent_key": parent_key, "attachment_key": attachment_key}
    if args.command == "sources":
        return ctx.get_sources_with_annotations(include_citekeys=not args.no_citekeys)
    raise ValueError(f"Unknown command: {args.command}")


def parse_types(value: str) -> set[str] | None:
    if not value.strip():
        return None
    return {part.strip() for part in value.split(",") if part.strip()}


def print_json(payload: Any, *, pretty: bool = False) -> None:
    print(
        json.dumps(
            to_jsonable(payload),
            indent=2 if pretty else None,
            ensure_ascii=False,
        )
    )


if __name__ == "__main__":
    raise SystemExit(main())
