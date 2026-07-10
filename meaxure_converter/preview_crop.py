"""Crop hard layers and upgrade assets from the artboard preview image."""

from __future__ import annotations

import re
from pathlib import Path
from typing import List, Optional, Tuple

from .layout import LayoutNode, iter_assets, iter_preview_crops
from .parser import Artboard, MeaXureDocument


def find_preview_path(doc: MeaXureDocument, artboard: Artboard) -> Optional[Path]:
    from .fidelity import resolve_preview_path

    exact = resolve_preview_path(doc, artboard)
    if exact is not None:
        return exact
    if artboard.image_path:
        candidate = doc.source_path.parent / artboard.image_path
        if candidate.exists():
            return candidate
    preview_root = doc.preview_dir
    if preview_root.exists():
        # Prefer filename match against artboard imagePath
        if artboard.image_path:
            want = Path(artboard.image_path).name
            for match in preview_root.rglob(want):
                return match
        matches = sorted(preview_root.rglob("*@2x.png"))
        if matches:
            return matches[0]
        matches = sorted(preview_root.rglob("*.png"))
        if matches:
            return matches[0]
    return None


def _safe_crop_name(
    node: LayoutNode,
    index: int,
    page_token: str,
    prefix: str = "crop",
) -> str:
    page = re.sub(
        r"[^\w\u4e00-\u9fff-]+",
        "_",
        page_token or "page",
        flags=re.UNICODE,
    ).strip("_")
    raw = re.sub(r"[^\w\u4e00-\u9fff-]+", "_", node.name or prefix, flags=re.UNICODE)
    raw = raw.strip("_") or prefix
    return (
        f"{prefix}_{page}_{index}_{raw}_"
        f"{int(node.rect.x)}_{int(node.rect.y)}.png"
    )


def _crop_box(im, artboard: Artboard, rect) -> Optional[Tuple[int, int, int, int]]:
    scale_x = im.width / artboard.width if artboard.width else 1.0
    scale_y = im.height / artboard.height if artboard.height else 1.0
    left = max(0, int(round(rect.x * scale_x)))
    top = max(0, int(round(rect.y * scale_y)))
    right = min(im.width, int(round((rect.x + rect.width) * scale_x)))
    bottom = min(im.height, int(round((rect.y + rect.height) * scale_y)))
    if right <= left or bottom <= top:
        return None
    return left, top, right, bottom


def crop_preview_layers(
    doc: MeaXureDocument,
    artboard: Artboard,
    tree: LayoutNode,
    dest_assets: Path,
) -> List[Tuple[LayoutNode, str]]:
    """Crop preview regions for hard layers. Mutates node.meta['crop_src']."""
    nodes = iter_preview_crops(tree)
    if not nodes:
        return []

    preview = find_preview_path(doc, artboard)
    if preview is None:
        return []

    try:
        from PIL import Image
    except ImportError:
        return []

    dest_assets.mkdir(parents=True, exist_ok=True)
    im = Image.open(preview).convert("RGBA")

    results: List[Tuple[LayoutNode, str]] = []
    for i, node in enumerate(nodes):
        box = _crop_box(im, artboard, node.rect)
        if not box:
            continue
        crop = im.crop(box)
        # Composite card crops keep text editable. Replace preview text with a
        # locally blurred background patch, then HTML text renders above it.
        # Transparency exposed a mismatched CSS gradient as white rectangles.
        text_rects = node.meta.get("text_rects") or []
        if node.meta.get("component_crop") and text_rects:
            try:
                from PIL import ImageFilter

                sx = crop.width / max(1.0, node.rect.width)
                sy = crop.height / max(1.0, node.rect.height)
                # Heavy blur removes glyph silhouettes while retaining the
                # card's broad local gradient.
                blur_radius = max(28, int(round(28 * max(sx, sy))))
                blurred = crop.filter(ImageFilter.GaussianBlur(radius=blur_radius))
                for text_rect in text_rects:
                    pad = 2.0
                    left = max(
                        0,
                        int(round((text_rect.x - node.rect.x - pad) * sx)),
                    )
                    top = max(
                        0,
                        int(round((text_rect.y - node.rect.y - pad) * sy)),
                    )
                    right = min(
                        crop.width,
                        int(
                            round(
                                (text_rect.x + text_rect.width - node.rect.x + pad)
                                * sx
                            )
                        ),
                    )
                    bottom = min(
                        crop.height,
                        int(
                            round(
                                (text_rect.y + text_rect.height - node.rect.y + pad)
                                * sy
                            )
                        ),
                    )
                    if right > left and bottom > top:
                        patch = blurred.crop((left, top, right, bottom))
                        crop.paste(patch, (left, top))
            except (AttributeError, TypeError, ValueError):
                pass
        # Downscale to 1x display size for crisp CSS background-size:100% 100%
        target = (
            max(1, int(round(node.rect.width))),
            max(1, int(round(node.rect.height))),
        )
        # Keep @2x pixels when possible for sharper rendering on retina
        if crop.size[0] >= target[0] * 2 and crop.size[1] >= target[1] * 2:
            target2 = (target[0] * 2, target[1] * 2)
            if crop.size != target2:
                crop = crop.resize(target2, Image.Resampling.LANCZOS)
        elif crop.size != target:
            crop = crop.resize(target, Image.Resampling.LANCZOS)

        fname = _safe_crop_name(node, i, artboard.slug or artboard.object_id or artboard.name)
        crop.save(dest_assets / fname, optimize=True)
        node.meta["crop_src"] = fname
        results.append((node, fname))
    return results


def upgrade_assets_from_preview(
    doc: MeaXureDocument,
    artboard: Artboard,
    tree: LayoutNode,
    dest_assets: Path,
    *,
    min_scale: float = 1.5,
    max_icon_side: float = 120.0,
) -> List[str]:
    """Upscale low-res transparent slice icons to @2x PNG (keep original alpha).

    Avoid cropping icons from the flattened preview — that bakes the grey
    circle/card behind them. Lanczos-upscale the exportable instead.
    """
    try:
        from PIL import Image
    except ImportError:
        return []

    dest_assets.mkdir(parents=True, exist_ok=True)
    upgraded: List[str] = []

    for node in iter_assets(tree):
        layer = node.layer
        if not layer or not layer.primary_asset:
            continue
        if max(node.rect.width, node.rect.height) > max_icon_side:
            continue

        src = doc.assets_dir / layer.primary_asset.path
        if not src.exists():
            alt = doc.assets_dir / Path(layer.primary_asset.path).name
            src = alt if alt.exists() else src
        if not src.exists():
            continue

        def _safe_asset_filename(path: str) -> str:
            base = Path(path).name
            return base.replace("\\", "_").replace("/", "_")

        fname = _safe_asset_filename(layer.primary_asset.path)
        stem = Path(fname).stem
        out_name = (stem if stem.endswith("@2x") else stem + "@2x") + ".png"

        with Image.open(src) as orig:
            im = orig.convert("RGBA")
            tw = max(1, int(round(node.rect.width * 2)))
            th = max(1, int(round(node.rect.height * 2)))
            if im.size != (tw, th):
                im = im.resize((tw, th), Image.Resampling.LANCZOS)
            im.save(dest_assets / out_name, optimize=True)

        node.meta["asset_src"] = out_name
        upgraded.append(out_name)

    return upgraded
