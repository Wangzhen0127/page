"""CLI for converting Sketch MeaXure HTML exports."""

from __future__ import annotations

import argparse
import json
import re
import shutil
import sys
from pathlib import Path

from . import __version__
from .fidelity import (
    build_fidelity_html,
    build_fidelity_miniprogram,
    build_gallery_index,
    collect_artboard_slices,
    copy_preview_asset,
    copy_slice_assets,
    resolve_preview_path,
    slugify_page,
)
from .html_gen import export_html
from .layout import build_layout, count_nodes
from .miniprogram_gen import export_miniprogram
from .parser import dump_document_summary, parse_meaxure_html


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="meaxure-convert",
        description=(
            "Convert Sketch MeaXure Web Export HTML into native HTML "
            "and/or WeChat Mini Program pages. "
            "Default mode `fidelity` emits ONE page per artboard using the "
            "MeaXure preview image for pixel-accurate visuals; slice assets "
            "are copied alongside for development."
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
        default=None,
        help="Artboard index to convert (default: all artboards)",
    )
    p.add_argument(
        "--all",
        action="store_true",
        default=True,
        help="Convert every artboard to its own page (default)",
    )
    p.add_argument(
        "--mode",
        choices=("fidelity", "flex"),
        default="fidelity",
        help=(
            "fidelity = preview 1:1 per artboard (recommended); "
            "flex = reconstruct layers with flex+margins"
        ),
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
        help="Dump parsed document / artboard JSON to a file",
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


def _select_indices(doc, artboard: int | None) -> list[int]:
    if artboard is None:
        return list(range(len(doc.artboards)))
    if artboard < 0 or artboard >= len(doc.artboards):
        raise IndexError(
            f"artboard index {artboard} out of range (0..{len(doc.artboards)-1})"
        )
    return [artboard]


def _unique_slugs(doc, indices: list[int]) -> dict[int, str]:
    used: dict[str, int] = {}
    out: dict[int, str] = {}
    for i in indices:
        base = slugify_page(doc.artboards[i], i)
        if base in used:
            used[base] += 1
            slug = f"{base}-{used[base]}"
        else:
            used[base] = 1
            slug = base
        out[i] = slug
    return out


def export_fidelity_all(
    doc,
    indices: list[int],
    out: Path,
    *,
    fmt: str,
    with_scaffold: bool,
) -> None:
    slugs = _unique_slugs(doc, indices)
    shared_assets = out / "assets"
    shared_assets.mkdir(parents=True, exist_ok=True)

    gallery_pages: list[tuple[str, str, str]] = []
    mp_pages: list[str] = []

    for i in indices:
        ab = doc.artboards[i]
        slug = slugs[i]
        print(f"[{i}] {ab.name} ({ab.width:.0f}x{ab.height:.0f}) → {slug}")

        preview_name = copy_preview_asset(doc, ab, shared_assets, slug)
        if not preview_name:
            print(
                f"  warning: preview missing for {ab.slug or ab.name}; "
                "page will be empty shell",
                file=sys.stderr,
            )

        slices = collect_artboard_slices(ab, doc)
        copy_slice_assets(doc, slices, shared_assets)

        if fmt in ("html", "both"):
            html_dir = out / "html"
            pages_dir = html_dir / "pages"
            pages_dir.mkdir(parents=True, exist_ok=True)
            # shared assets at html/assets
            html_assets = html_dir / "assets"
            html_assets.mkdir(parents=True, exist_ok=True)
            # sync newly added files
            for f in shared_assets.iterdir():
                target = html_assets / f.name
                if f.is_file() and not target.exists():
                    shutil.copy2(f, target)

            preview_url = f"../assets/{preview_name}" if preview_name else ""
            page_html = build_fidelity_html(
                ab,
                preview_url=preview_url,
                width=ab.width,
                height=ab.height,
                title=f"{ab.name} · {slug}",
            )
            page_file = pages_dir / f"{slug}.html"
            page_file.write_text(page_html, encoding="utf-8")
            thumb = f"./assets/{preview_name}" if preview_name else ""
            gallery_pages.append((f"./pages/{slug}.html", f"{ab.name} ({slug})", thumb))
            print(f"  HTML → {page_file}")

        if fmt in ("miniprogram", "both"):
            mp_dir = out / "miniprogram"
            page_name = re.sub(r"[^\w]+", "_", slug)[:40] or f"p{i}"
            # avoid leading digits for safety
            if page_name[0].isdigit():
                page_name = f"p_{page_name}"
            pages_dir = mp_dir / "pages" / page_name
            pages_dir.mkdir(parents=True, exist_ok=True)
            mp_assets = mp_dir / "assets"
            mp_assets.mkdir(parents=True, exist_ok=True)
            for f in shared_assets.iterdir():
                target = mp_assets / f.name
                if f.is_file() and not target.exists():
                    shutil.copy2(f, target)

            preview_url = f"/assets/{preview_name}" if preview_name else ""
            files = build_fidelity_miniprogram(
                ab, preview_url=preview_url, page_name=page_name
            )
            for fname, content in files.items():
                (pages_dir / fname).write_text(content, encoding="utf-8")
            mp_pages.append(f"pages/{page_name}/{page_name}")
            print(f"  Mini Program → {pages_dir}")

    if fmt in ("html", "both") and gallery_pages:
        gallery = build_gallery_index(
            gallery_pages,
            title=doc.source_path.parent.name or "MeaXure Export",
        )
        (out / "html" / "index.html").write_text(gallery, encoding="utf-8")
        print(f"gallery → {out / 'html' / 'index.html'}")

    if fmt in ("miniprogram", "both") and mp_pages and with_scaffold:
        mp_dir = out / "miniprogram"
        first_title = doc.artboards[indices[0]].name
        (mp_dir / "app.js").write_text("App({});\n", encoding="utf-8")
        (mp_dir / "app.json").write_text(
            json.dumps(
                {
                    "pages": mp_pages,
                    "window": {
                        "navigationBarTitleText": first_title,
                        "navigationBarBackgroundColor": "#ffffff",
                        "navigationBarTextStyle": "black",
                        "backgroundColor": "#f0f0f0",
                    },
                },
                ensure_ascii=False,
                indent=2,
            )
            + "\n",
            encoding="utf-8",
        )
        (mp_dir / "app.wxss").write_text(
            "page { background: #f0f0f0; }\n", encoding="utf-8"
        )
        (mp_dir / "project.config.json").write_text(
            json.dumps(
                {
                    "description": "MeaXure fidelity export",
                    "packOptions": {"ignore": []},
                    "setting": {
                        "urlCheck": False,
                        "es6": True,
                        "postcss": True,
                        "minified": True,
                    },
                    "compileType": "miniprogram",
                    "appid": "touristappid",
                    "projectname": "meaxure-fidelity",
                },
                ensure_ascii=False,
                indent=2,
            )
            + "\n",
            encoding="utf-8",
        )
        (mp_dir / "sitemap.json").write_text(
            json.dumps({"rules": [{"action": "allow", "page": "*"}]}, indent=2)
            + "\n",
            encoding="utf-8",
        )


def export_flex_one(doc, artboard, out: Path, fmt: str, with_scaffold: bool) -> None:
    tree = build_layout(artboard)
    print(f"layout nodes: {count_nodes(tree)}")
    if fmt in ("html", "both"):
        path = export_html(doc, tree, artboard, out / "html")
        print(f"HTML → {path}")
    if fmt in ("miniprogram", "both"):
        path = export_miniprogram(
            doc,
            tree,
            artboard,
            out / "miniprogram",
            page_name="index",
            with_app_scaffold=with_scaffold,
        )
        print(f"Mini Program → {path}")


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
        for i, ab in enumerate(doc.artboards):
            preview = resolve_preview_path(doc, ab)
            print(
                f"  preview[{i}]: "
                f"{'OK ' + str(preview.relative_to(doc.source_path.parent)) if preview else 'MISSING'}"
            )
        return 0

    if not doc.artboards:
        print("error: no artboards found in document", file=sys.stderr)
        return 1

    try:
        indices = _select_indices(doc, args.artboard)
    except IndexError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1

    print(dump_document_summary(doc))
    print(f"mode={args.mode} converting {len(indices)} artboard(s)")

    if args.dump_json:
        if len(indices) == 1:
            ab = doc.artboards[indices[0]]
            payload = {
                "name": ab.name,
                "slug": ab.slug,
                "width": ab.width,
                "height": ab.height,
                "layers": [l.raw for l in ab.layers],
            }
        else:
            payload = {
                "artboards": [
                    {
                        "index": i,
                        "name": doc.artboards[i].name,
                        "slug": doc.artboards[i].slug,
                        "width": doc.artboards[i].width,
                        "height": doc.artboards[i].height,
                        "imagePath": doc.artboards[i].image_path,
                        "layerCount": len(doc.artboards[i].layers),
                    }
                    for i in indices
                ]
            }
        args.dump_json.parent.mkdir(parents=True, exist_ok=True)
        args.dump_json.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        print(f"wrote {args.dump_json}")

    out = Path(args.output)
    out.mkdir(parents=True, exist_ok=True)

    if args.mode == "fidelity":
        export_fidelity_all(
            doc,
            indices,
            out,
            fmt=args.format,
            with_scaffold=not args.no_scaffold,
        )
    else:
        # flex mode: if multiple selected, export each into subfolders
        if len(indices) == 1:
            export_flex_one(
                doc,
                doc.artboards[indices[0]],
                out,
                args.format,
                with_scaffold=not args.no_scaffold,
            )
        else:
            slugs = _unique_slugs(doc, indices)
            for i in indices:
                sub = out / slugs[i]
                sub.mkdir(parents=True, exist_ok=True)
                print(f"--- flex {slugs[i]} ---")
                export_flex_one(
                    doc,
                    doc.artboards[i],
                    sub,
                    args.format,
                    with_scaffold=not args.no_scaffold,
                )

    assets_hint = doc.assets_dir
    if not assets_hint.exists():
        print(
            f"note: assets folder not found at {assets_hint}.",
            file=sys.stderr,
        )
    preview_hint = doc.preview_dir
    if not preview_hint.exists():
        print(
            f"note: preview folder not found at {preview_hint}. "
            "Fidelity mode needs preview/@2x.png for 1:1 visuals.",
            file=sys.stderr,
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
