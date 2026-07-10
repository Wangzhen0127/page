"""Pixel-perfect page builders using MeaXure preview + slice assets."""

from __future__ import annotations

import html
import json
import re
import shutil
from pathlib import Path
from typing import Dict, List, Optional, Tuple
from urllib.parse import unquote

from .parser import Artboard, Layer, MeaXureDocument


def resolve_preview_path(doc: MeaXureDocument, artboard: Artboard) -> Optional[Path]:
    if not artboard.image_path:
        return None
    raw = unquote(artboard.image_path)
    candidate = doc.source_path.parent / raw
    if candidate.exists():
        return candidate
    # Try without nested oddities
    alt = doc.preview_dir / Path(raw).name
    return alt if alt.exists() else None


def slugify_page(artboard: Artboard, index: int) -> str:
    slug = (artboard.slug or artboard.name or f"page-{index}").strip()
    slug = re.sub(r"[^\w\u4e00-\u9fff-]+", "-", slug, flags=re.UNICODE)
    slug = re.sub(r"-{2,}", "-", slug).strip("-")
    return slug or f"page-{index}"


def _safe_name(path: str) -> str:
    return Path(path).name.replace("\\", "_").replace("/", "_")


def collect_artboard_slices(artboard: Artboard, doc: MeaXureDocument) -> List[Layer]:
    """Slices belonging to this artboard (layer slices + matching top-level)."""
    by_id = {s.object_id: s for s in doc.slices}
    out: List[Layer] = []
    seen = set()
    for layer in artboard.layers:
        if not layer.is_asset:
            continue
        if layer.object_id in seen:
            continue
        seen.add(layer.object_id)
        # Prefer richer exportable from top-level slices table when present
        out.append(by_id.get(layer.object_id, layer))
    return out


def copy_slice_assets(
    doc: MeaXureDocument,
    layers: List[Layer],
    dest: Path,
) -> List[str]:
    dest.mkdir(parents=True, exist_ok=True)
    copied: List[str] = []
    for layer in layers:
        for exp in layer.exportable:
            src = doc.assets_dir / exp.path
            if not src.exists():
                src = doc.assets_dir / Path(exp.path).name
            if not src.exists():
                # MeaXure sometimes stores @1x as name.png while path says @1x.png
                stem = Path(exp.path).stem.replace("@1x", "").replace("@2x", "")
                for cand in doc.assets_dir.glob(stem + "*"):
                    src = cand
                    break
            if src.exists():
                fname = _safe_name(src.name)
                target = dest / fname
                if not target.exists():
                    shutil.copy2(src, target)
                copied.append(fname)
    return copied


def copy_preview_asset(
    doc: MeaXureDocument,
    artboard: Artboard,
    dest: Path,
    page_slug: str,
) -> Optional[str]:
    preview = resolve_preview_path(doc, artboard)
    if not preview:
        return None
    dest.mkdir(parents=True, exist_ok=True)
    fname = f"preview_{page_slug}@2x{preview.suffix.lower() or '.png'}"
    shutil.copy2(preview, dest / fname)
    return fname


def build_fidelity_html(
    artboard: Artboard,
    *,
    preview_url: str,
    width: float,
    height: float,
    title: str,
    slices_html: str = "",
) -> str:
    """One artboard = one page, preview image is the visual source of truth."""
    page_title = html.escape(title)
    return f"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1, maximum-scale=1, user-scalable=no" />
  <title>{page_title}</title>
  <style>
* {{ margin: 0; padding: 0; box-sizing: border-box; }}
html, body {{
  background: #f0f0f0;
  font-family: "PingFang SC", "Hiragino Sans GB", "WenQuanYi Micro Hei",
    "Droid Sans Fallback", "Microsoft YaHei", sans-serif;
}}
.page {{
  position: relative;
  width: {width:.0f}px;
  height: {height:.0f}px;
  margin: 0 auto;
  overflow: hidden;
  background: #fff;
}}
.page-preview {{
  position: absolute;
  left: 0;
  top: 0;
  width: {width:.0f}px;
  height: {height:.0f}px;
  display: block;
  object-fit: fill;
  pointer-events: none;
  user-select: none;
}}
.slice {{
  position: absolute;
  display: block;
  object-fit: fill;
  pointer-events: none;
}}
  </style>
</head>
<body>
  <div class="page" data-artboard="{page_title}">
    <img class="page-preview" src="{html.escape(preview_url)}" alt="{page_title}" width="{width:.0f}" height="{height:.0f}" />
{slices_html}
  </div>
</body>
</html>
"""


def build_slices_overlay_html(
    layers: List[Layer],
    asset_prefix: str = "./assets",
) -> str:
    """Optional absolute slice overlays (usually unnecessary when preview is full-page)."""
    lines: List[str] = []
    for layer in layers:
        asset = layer.primary_asset
        if not asset:
            continue
        # Prefer @2x file name if present in exportable list
        path = asset.path
        for exp in layer.exportable:
            if "@2x" in exp.path:
                path = exp.path
                break
        src = f"{asset_prefix.rstrip('/')}/{_safe_name(Path(path).name)}"
        # If path was @2x.png but file saved as 编组@2x.png etc.
        r = layer.rect
        lines.append(
            f'    <img class="slice" src="{html.escape(src)}" alt="{html.escape(layer.name)}" '
            f'style="left:{r.x:.2f}px;top:{r.y:.2f}px;width:{r.width:.2f}px;height:{r.height:.2f}px;" />'
        )
    return "\n".join(lines)


def build_fidelity_miniprogram(
    artboard: Artboard,
    *,
    preview_url: str,
    page_name: str,
) -> Dict[str, str]:
    w, h = artboard.width, artboard.height
    wxml = (
        f'<view class="page">\n'
        f'  <image class="page-preview" src="{preview_url}" mode="scaleToFill" />\n'
        f"</view>\n"
    )
    wxss = (
        f"page {{ background: #f0f0f0; }}\n"
        f".page {{\n"
        f"  position: relative;\n"
        f"  width: {w:.0f}rpx;\n"
        f"  height: {h:.0f}rpx;\n"
        f"  overflow: hidden;\n"
        f"  background: #ffffff;\n"
        f"}}\n"
        f".page-preview {{\n"
        f"  position: absolute;\n"
        f"  left: 0;\n"
        f"  top: 0;\n"
        f"  width: {w:.0f}rpx;\n"
        f"  height: {h:.0f}rpx;\n"
        f"  display: block;\n"
        f"}}\n"
    )
    js = f"Page({{ data: {{ title: {json.dumps(artboard.name, ensure_ascii=False)} }} }});\n"
    cfg = json.dumps(
        {"navigationBarTitleText": artboard.name, "usingComponents": {}},
        ensure_ascii=False,
        indent=2,
    )
    return {
        f"{page_name}.wxml": wxml,
        f"{page_name}.wxss": wxss,
        f"{page_name}.js": js,
        f"{page_name}.json": cfg + "\n",
    }


def build_gallery_index(
    pages: List[Tuple[str, str, str]],
    *,
    title: str = "MeaXure Export",
) -> str:
    """pages: list of (href, name, thumb_url)."""
    items = []
    for href, name, thumb in pages:
        thumb_tag = (
            f'<img src="{html.escape(thumb)}" alt="" />'
            if thumb
            else '<div class="ph"></div>'
        )
        items.append(
            f'<a class="card" href="{html.escape(href)}">'
            f'<div class="thumb">{thumb_tag}</div>'
            f'<div class="meta"><strong>{html.escape(name)}</strong>'
            f'<span>{html.escape(href)}</span></div></a>'
        )
    body = "\n".join(items)
    return f"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1" />
  <title>{html.escape(title)}</title>
  <style>
* {{ box-sizing: border-box; }}
body {{
  margin: 0; padding: 24px;
  font-family: "PingFang SC", "WenQuanYi Micro Hei", sans-serif;
  background: #f5f7fb; color: #1a263c;
}}
h1 {{ margin: 0 0 8px; font-size: 28px; }}
p {{ margin: 0 0 24px; color: #667; }}
.grid {{
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(220px, 1fr));
  gap: 16px;
}}
.card {{
  display: flex; flex-direction: column;
  background: #fff; border-radius: 12px; overflow: hidden;
  text-decoration: none; color: inherit;
  box-shadow: 0 2px 10px rgba(0,0,0,.06);
}}
.thumb {{
  aspect-ratio: 3/5; background: #e8eef7; overflow: hidden;
}}
.thumb img {{ width: 100%; height: 100%; object-fit: cover; object-position: top; display: block; }}
.ph {{ width: 100%; height: 100%; background: linear-gradient(180deg,#d7e6ff,#f5f7fb); }}
.meta {{ padding: 12px 14px; display: flex; flex-direction: column; gap: 4px; }}
.meta span {{ font-size: 12px; color: #889; word-break: break-all; }}
  </style>
</head>
<body>
  <h1>{html.escape(title)}</h1>
  <p>共 {len(pages)} 个画板 · 每页使用 MeaXure preview 1:1 还原</p>
  <div class="grid">
{body}
  </div>
</body>
</html>
"""
