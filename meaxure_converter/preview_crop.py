"""Crop missing bitmap/banner layers from the artboard preview image."""

from __future__ import annotations

import re
from pathlib import Path
from typing import List, Optional, Tuple

from .layout import LayoutNode, iter_preview_crops
from .parser import Artboard, MeaXureDocument


def _find_preview(doc: MeaXureDocument, artboard: Artboard) -> Optional[Path]:
    if artboard.image_path:
        candidate = doc.source_path.parent / artboard.image_path
        if candidate.exists():
            return candidate
    # Fallback: first @2x png under preview/
    preview_root = doc.preview_dir
    if preview_root.exists():
        matches = sorted(preview_root.rglob("*@2x.png"))
        if matches:
            return matches[0]
        matches = sorted(preview_root.rglob("*.png"))
        if matches:
            return matches[0]
    return None


def _safe_crop_name(node: LayoutNode, index: int) -> str:
    raw = re.sub(r"[^\w\u4e00-\u9fff-]+", "_", node.name or "crop", flags=re.UNICODE)
    raw = raw.strip("_") or "crop"
    return f"crop_{index}_{raw}_{int(node.rect.x)}_{int(node.rect.y)}.png"


def crop_preview_layers(
    doc: MeaXureDocument,
    artboard: Artboard,
    tree: LayoutNode,
    dest_assets: Path,
) -> List[Tuple[LayoutNode, str]]:
    """Crop preview regions for empty bitmap layers.

    Returns list of (node, relative_filename) and mutates node.meta['crop_src'].
    """
    nodes = iter_preview_crops(tree)
    if not nodes:
        return []

    preview = _find_preview(doc, artboard)
    if preview is None:
        return []

    try:
        from PIL import Image
    except ImportError:
        return []

    dest_assets.mkdir(parents=True, exist_ok=True)
    im = Image.open(preview).convert("RGBA")
    scale_x = im.width / artboard.width if artboard.width else 1.0
    scale_y = im.height / artboard.height if artboard.height else 1.0

    results: List[Tuple[LayoutNode, str]] = []
    for i, node in enumerate(nodes):
        r = node.rect
        left = max(0, int(round(r.x * scale_x)))
        top = max(0, int(round(r.y * scale_y)))
        right = min(im.width, int(round((r.x + r.width) * scale_x)))
        bottom = min(im.height, int(round((r.y + r.height) * scale_y)))
        if right <= left or bottom <= top:
            continue
        crop = im.crop((left, top, right, bottom))
        fname = _safe_crop_name(node, i)
        crop.save(dest_assets / fname, optimize=True)
        node.meta["crop_src"] = fname
        results.append((node, fname))
    return results
