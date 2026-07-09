"""CLI for converting Sketch MeaXure HTML exports."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from . import __version__
from .layout import build_layout, count_nodes
from .parser import dump_document_summary, parse_meaxure_html
from .html_gen import export_html
from .miniprogram_gen import export_miniprogram


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="meaxure-convert",
        description=(
            "Convert Sketch MeaXure Web Export HTML into native HTML "
            "and/or WeChat Mini Program pages. Layout uses flex + margins; "
            "slice assets keep original absolute positioning."
        ),
    )
    p.add_argument(
        "input",
        type=Path,
        help="Path to MeaXure index.html (contains `let data = {...}`)",
    )
    p.add_argument(
        "-o",
        "--output",
        type=Path,
        default=Path("output"),
        help="Output directory (default: ./output)",
    )
    p.add_argument(
        "-f",
        "--format",
        choices=("html", "miniprogram", "both"),
        default="both",
        help="Target format (default: both)",
    )
    p.add_argument(
        "-a",
        "--artboard",
        type=int,
        default=0,
        help="Artboard index to convert (default: 0)",
    )
    p.add_argument(
        "--list",
        action="store_true",
        help="List artboards and exit",
    )
    p.add_argument(
        "--dump-json",
        type=Path,
        default=None,
        help="Dump parsed artboard JSON to a file",
    )
    p.add_argument(
        "--no-scaffold",
        action="store_true",
        help="For miniprogram: only emit page files, skip app.* scaffold",
    )
    p.add_argument(
        "-V",
        "--version",
        action="version",
        version=f"%(prog)s {__version__}",
    )
    return p


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    input_path = args.input
    if not input_path.exists():
        print(f"error: input not found: {input_path}", file=sys.stderr)
        return 1

    try:
        doc = parse_meaxure_html(input_path)
    except Exception as exc:  # noqa: BLE001
        print(f"error: failed to parse MeaXure HTML: {exc}", file=sys.stderr)
        return 1

    if args.list:
        print(dump_document_summary(doc))
        return 0

    if not doc.artboards:
        print("error: no artboards found in document", file=sys.stderr)
        return 1

    if args.artboard < 0 or args.artboard >= len(doc.artboards):
        print(
            f"error: artboard index {args.artboard} out of range "
            f"(0..{len(doc.artboards)-1})",
            file=sys.stderr,
        )
        return 1

    artboard = doc.artboards[args.artboard]
    tree = build_layout(artboard)
    print(dump_document_summary(doc))
    print(f"layout nodes: {count_nodes(tree)}")

    if args.dump_json:
        payload = {
            "name": artboard.name,
            "width": artboard.width,
            "height": artboard.height,
            "layers": [l.raw for l in artboard.layers],
        }
        args.dump_json.parent.mkdir(parents=True, exist_ok=True)
        args.dump_json.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        print(f"wrote {args.dump_json}")

    out = Path(args.output)
    out.mkdir(parents=True, exist_ok=True)

    if args.format in ("html", "both"):
        html_dir = out / "html"
        path = export_html(doc, tree, artboard, html_dir)
        print(f"HTML → {path}")

    if args.format in ("miniprogram", "both"):
        mp_dir = out / "miniprogram"
        path = export_miniprogram(
            doc,
            tree,
            artboard,
            mp_dir,
            page_name="index",
            with_app_scaffold=not args.no_scaffold,
        )
        print(f"Mini Program → {path}")

    assets_hint = doc.assets_dir
    if not assets_hint.exists():
        print(
            f"note: assets folder not found at {assets_hint}. "
            "Place MeaXure `assets/` next to index.html and re-run to copy images.",
            file=sys.stderr,
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
