"""Absolute-position layout reconstruction from MeaXure flat layers.

Phase-1 baseline (current default)
----------------------------------
Artboard is `position: relative`. Every visual layer is an absolute child
using MeaXure's original rect + paint order (array index = z-index):

    left / top / width / height = layer.rect
    z-index = layer index in artboard.layers

No flex/grid inference. No spatial parent recovery. No margin-chain.
Background shapes stay leaf nodes — never become containers.

Render priority
---------------
1. text          → editable HTML text
2. CSS shapes    → solid / radius / ellipse / simple gradient
3. slice assets  → original exportable images
4. tiny hard vectors without foreground overlap → local preview crop
5. full-page preview → never used as page content
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List, Optional, Sequence

from .parser import Artboard, Layer, Rect

# Crop only when both sides stay under this (px). Large surfaces stay CSS.
MAX_CROP_SIDE = 120.0
# Crop area must stay under this fraction of the artboard.
MAX_CROP_AREA_RATIO = 0.01


@dataclass
class LayoutNode:
    kind: str  # artboard | shape | text | asset | spacer | group | row | grid | overlay
    name: str
    rect: Rect
    layer: Optional[Layer] = None
    children: List["LayoutNode"] = field(default_factory=list)
    margin_top: float = 0.0
    margin_left: float = 0.0
    abs_left: Optional[float] = None
    abs_top: Optional[float] = None
    direction: str = "column"
    class_name: str = ""
    z_index: int = 0
    gap: float = 0.0
    meta: Dict[str, object] = field(default_factory=dict)

    @property
    def width(self) -> float:
        return self.rect.width

    @property
    def height(self) -> float:
        return self.rect.height

    @property
    def is_absolute(self) -> bool:
        return self.abs_left is not None and self.abs_top is not None


def _rect_area(r: Rect) -> float:
    return max(0.0, r.width) * max(0.0, r.height)


def _almost_same_rect(a: Rect, b: Rect, tol: float = 4.0) -> bool:
    return (
        abs(a.x - b.x) <= tol
        and abs(a.y - b.y) <= tol
        and abs(a.width - b.width) <= tol
        and abs(a.height - b.height) <= tol
    )


def _overlap_area(a: Rect, b: Rect) -> float:
    ox = min(a.right(), b.right()) - max(a.x, b.x)
    oy = min(a.bottom(), b.bottom()) - max(a.y, b.y)
    if ox <= 0 or oy <= 0:
        return 0.0
    return ox * oy


def _is_measurement_hidden(layer: Layer) -> bool:
    if layer.is_asset or layer.type == "text":
        return False
    if layer.opacity is not None and layer.opacity <= 0.01:
        return True
    for line in layer.css or []:
        low = (line or "").lower().replace(" ", "")
        if low.startswith("opacity:0"):
            return True
    return False


def _is_complex_vector(layer: Layer) -> bool:
    if layer.type != "shape":
        return False
    name = layer.name or ""
    if any(k in name for k in ("形状", "路径", "结合", "位图", "banner", "图片", "蒙版")):
        if "椭圆" in name and "结合" not in name and "路径" not in name and "蒙版" not in name:
            return False
        return True
    if not layer.fills and not layer.borders:
        return True
    for b in layer.borders or []:
        if b.get("fillType") == "Gradient":
            return True
    for f in layer.fills or []:
        if f.get("fillType") != "Gradient":
            continue
        grad = f.get("gradient") or {}
        stops = grad.get("colorStops") or []
        if len(stops) != 2:
            return True
        for s in stops:
            try:
                if float((s.get("color") or {}).get("alpha", 255)) < 250:
                    return True
            except (TypeError, ValueError):
                return True
        frm = grad.get("from") or {}
        to = grad.get("to") or {}
        dx = abs(float(to.get("x", 0.5)) - float(frm.get("x", 0.5)))
        dy = abs(float(to.get("y", 1)) - float(frm.get("y", 0)))
        if dx > 0.08 and dy > 0.08:
            return True
    return False


def _has_visual(layer: Layer) -> bool:
    if layer.type == "text":
        return bool((layer.content or "").strip())
    if layer.is_asset:
        return True
    if layer.type == "group":
        return False
    if _is_measurement_hidden(layer):
        return False
    if layer.fills or layer.borders or layer.shadows:
        return True
    for line in layer.css or []:
        low = (line or "").lower().strip()
        if low.startswith("background") or low.startswith("border") or low.startswith(
            "box-shadow"
        ):
            return True
    name = (layer.name or "").lower()
    if any(k in name for k in ("位图", "banner", "图片", "image", "形状", "路径")):
        return True
    return False


def _covered_by_asset(layer: Layer, assets: Sequence[Layer]) -> bool:
    """Drop redundant shapes that only duplicate a slice bitmap."""
    if layer.type == "text" or layer.is_asset:
        return False
    if layer.borders or layer.shadows:
        return False
    for asset in assets:
        if asset.rect.contains(layer.rect, pad=3.0) or _almost_same_rect(
            asset.rect, layer.rect, tol=4.0
        ):
            if _rect_area(layer.rect) <= _rect_area(asset.rect) * 1.1:
                return True
    return False


def _enrich_layer_exportable(layer: Layer, slice_by_id: Dict[str, Layer]) -> Layer:
    """Attach top-level MeaXure slice exportable metadata when objectIDs match."""
    if layer.exportable:
        return layer
    hit = slice_by_id.get(layer.object_id)
    if hit and hit.exportable:
        layer.exportable = list(hit.exportable)
        if layer.type == "shape":
            layer.type = "slice"
    return layer


def _foreground_overlap(target: Layer, layers: Sequence[tuple[int, Layer]], target_z: int) -> bool:
    """True if a later text/asset overlaps the crop candidate."""
    t = target.rect
    for z, layer in layers:
        if z <= target_z:
            continue
        if layer.type == "text" or layer.is_asset:
            if _overlap_area(t, layer.rect) > 4.0:
                return True
    return False


def _needs_preview_crop(
    layer: Layer,
    layers: Sequence[tuple[int, Layer]],
    z: int,
    artboard: Artboard,
) -> bool:
    """Strict crop gate: tiny hard vectors only, never large surfaces."""
    if layer.type != "shape" or layer.is_asset:
        return False
    board_area = max(1.0, artboard.width * artboard.height)

    name = layer.name or ""
    bitmap_placeholder = any(k in name.lower() for k in ("位图", "banner", "图片", "image"))
    area_ratio = _rect_area(layer.rect) / board_area

    # MeaXure often exposes raster artwork only as a shape called 位图/banner.
    # These are not CSS shapes: crop their local rectangle even when larger
    # than icon limits. This restores hero banners and card illustrations
    # without falling back to a full-page screenshot.
    if bitmap_placeholder:
        return area_ratio <= 0.10 and not _foreground_overlap(layer, layers, z)

    if layer.rect.width > MAX_CROP_SIDE or layer.rect.height > MAX_CROP_SIDE:
        return False
    if area_ratio > MAX_CROP_AREA_RATIO:
        return False
    if _foreground_overlap(layer, layers, z):
        return False

    hard_name = any(k in name for k in ("路径", "形状结合", "蒙版"))
    generic_shape = "形状" in name and not hard_name and "椭圆" not in name

    if "椭圆" in name and layer.fills and not hard_name:
        return False
    if "矩形" in name and layer.fills and not hard_name:
        return False
    if generic_shape and layer.fills and not any(
        (f.get("fillType") == "Gradient") for f in layer.fills
    ):
        return False

    if hard_name:
        return True
    if not layer.fills and not layer.borders:
        return True
    if _is_complex_vector(layer) and not layer.fills:
        return True
    for b in layer.borders or []:
        if b.get("fillType") == "Gradient":
            return True
    return False


def _infer_artboard_background(artboard: Artboard, layers: Sequence[tuple[int, Layer]]) -> Optional[str]:
    """Pick a solid page background from a near-full-bleed opaque fill."""
    best: Optional[tuple[float, str]] = None
    for z, layer in layers:
        if layer.type != "shape" or not layer.fills:
            continue
        fill = layer.fills[0]
        if fill.get("fillType") == "Gradient":
            continue
        color = fill.get("color") or {}
        try:
            alpha = float(color.get("alpha", 255))
        except (TypeError, ValueError):
            alpha = 255
        if alpha < 250:
            continue
        cover = _rect_area(layer.rect) / max(1.0, artboard.width * artboard.height)
        if cover < 0.55:
            continue
        css = color.get("css-rgba") or color.get("color-hex")
        if not css and color.get("rgb"):
            rgb = color["rgb"]
            css = f"rgb({int(rgb.get('r', 0))},{int(rgb.get('g', 0))},{int(rgb.get('b', 0))})"
        if not css:
            continue
        score = cover + (0.1 if z < 5 else 0.0)
        if best is None or score > best[0]:
            best = (score, str(css))
    return best[1] if best else None


def _collect_layers(
    artboard: Artboard,
    slice_by_id: Optional[Dict[str, Layer]] = None,
) -> List[tuple[int, Layer]]:
    """Return visual layers in original paint order (index = z)."""
    slice_by_id = slice_by_id or {}
    enriched = [_enrich_layer_exportable(l, slice_by_id) for l in artboard.layers]
    asset_layers = [l for l in enriched if l.is_asset]
    out: List[tuple[int, Layer]] = []
    seen_asset_rects: List[Rect] = []

    for idx, layer in enumerate(enriched):
        if layer.type == "group":
            continue
        if layer.is_asset:
            if any(_almost_same_rect(prev, layer.rect, tol=2.0) for prev in seen_asset_rects):
                continue
            seen_asset_rects.append(layer.rect)
            out.append((idx, layer))
            continue
        if not _has_visual(layer):
            continue
        if _covered_by_asset(layer, asset_layers):
            continue
        out.append((idx, layer))
    return out


def _component_crop_candidates(
    artboard: Artboard,
    layers: Sequence[tuple[int, Layer]],
) -> Dict[int, Dict[str, object]]:
    """Find card-like raster composites.

    MeaXure flattens card artwork into a background mask, decorative gradient
    shapes and one or more bitmap placeholders. Rendering those independently
    creates the large blue blocks seen in service cards. Crop only that card
    rectangle, remove text pixels later, and keep editable HTML text above it.
    """
    candidates: Dict[int, Dict[str, object]] = {}
    used_rects: List[Rect] = []
    board_area = max(1.0, artboard.width * artboard.height)

    for z, layer in layers:
        r = layer.rect
        name = layer.name or ""
        if layer.type != "shape" or layer.is_asset:
            continue
        if not any(k in name for k in ("蒙版", "矩形")):
            continue
        if not (250 <= r.width <= 420 and 120 <= r.height <= 420):
            continue
        if _rect_area(r) / board_area > 0.10:
            continue
        if any(_almost_same_rect(r, prev, tol=2.0) for prev in used_rects):
            continue

        text_layers: List[Layer] = []
        bitmap_layers: List[tuple[int, Layer]] = []
        covered_visuals: List[int] = []
        for child_z, child in layers:
            if child_z == z:
                continue
            overlap = _overlap_area(r, child.rect)
            if overlap <= 0:
                continue
            child_area = max(1.0, _rect_area(child.rect))
            mostly_inside = overlap / child_area >= 0.60
            if child.type == "text" and r.contains(child.rect, pad=3.0):
                text_layers.append(child)
                continue
            child_name = (child.name or "").lower()
            if (
                child.type == "shape"
                and any(k in child_name for k in ("位图", "banner", "图片", "image"))
                and mostly_inside
            ):
                bitmap_layers.append((child_z, child))
            if mostly_inside and child.type != "text":
                covered_visuals.append(child_z)

        if not text_layers or not bitmap_layers:
            continue

        used_rects.append(r)
        candidates[z] = {
            # Composite pixels include later decorations, but the crop itself
            # stays at the original card-background z so editable text layers
            # retain their original order and paint above it.
            "paint_z": z,
            "text_rects": [text.rect for text in text_layers],
            "suppress_z": set(covered_visuals),
            "component_crop": True,
        }
    return candidates


def build_layout(
    artboard: Artboard,
    slices: Optional[Sequence[Layer]] = None,
) -> LayoutNode:
    """Build an absolute-position artboard tree from MeaXure layers."""
    slice_by_id = {s.object_id: s for s in (slices or []) if s.object_id}
    layers = _collect_layers(artboard, slice_by_id)
    bg = _infer_artboard_background(artboard, layers)
    component_crops = _component_crop_candidates(artboard, layers)
    suppressed_z: set[int] = set()
    for info in component_crops.values():
        suppressed_z.update(info["suppress_z"])  # type: ignore[arg-type]

    root = LayoutNode(
        kind="artboard",
        name=artboard.name,
        rect=Rect(0, 0, artboard.width, artboard.height),
        direction="column",
        class_name="page",
        meta={
            "position_relative": True,
            "layout_mode": "absolute",
            "background": bg or "#EFF4FB",
            "stats": {},
        },
    )

    for z, layer in layers:
        if z in suppressed_z and z not in component_crops:
            continue
        if layer.type == "text":
            kind = "text"
        elif layer.is_asset:
            kind = "asset"
        else:
            kind = "shape"

        meta: Dict[str, object] = {"layout_mode": "absolute"}
        component = component_crops.get(z)
        node_z = z
        if component:
            meta.update(component)
            meta["preview_crop"] = True
            meta["crop_reason"] = "complex-card"
            # Keep CSS fill beneath the crop; transparent text holes reveal it.
            meta["suppress_fill"] = False
            node_z = int(component["paint_z"])
        elif kind == "shape" and _needs_preview_crop(layer, layers, z, artboard):
            meta["preview_crop"] = True
            meta["suppress_fill"] = True
            meta["crop_reason"] = layer.name or "complex-vector"
        if kind == "asset":
            meta["force_visible"] = True

        node = LayoutNode(
            kind=kind,
            name=layer.name or layer.type,
            rect=layer.rect,
            layer=layer,
            class_name=kind,
            z_index=node_z,
            abs_left=layer.rect.x,
            abs_top=layer.rect.y,
            meta=meta,
        )
        root.children.append(node)

    # Preserve MeaXure paint order in DOM (bottom → top).
    root.children.sort(key=lambda n: n.z_index)
    root.meta["stats"] = summarize_render_stats(root)
    return root


def iter_assets(node: LayoutNode) -> List[LayoutNode]:
    out: List[LayoutNode] = []
    if node.kind == "asset":
        out.append(node)
    for child in node.children:
        out.extend(iter_assets(child))
    return out


def iter_preview_crops(node: LayoutNode) -> List[LayoutNode]:
    out: List[LayoutNode] = []
    if node.meta.get("preview_crop"):
        out.append(node)
    for child in node.children:
        out.extend(iter_preview_crops(child))
    return out


def count_nodes(node: LayoutNode) -> Dict[str, int]:
    counts: Dict[str, int] = {}

    def walk(n: LayoutNode) -> None:
        counts[n.kind] = counts.get(n.kind, 0) + 1
        for c in n.children:
            walk(c)

    walk(node)
    return counts


def summarize_render_stats(node: LayoutNode) -> Dict[str, int]:
    """Count how layers will be rendered (code / asset / crop)."""
    stats = {
        "text": 0,
        "css_shape": 0,
        "asset": 0,
        "preview_crop": 0,
        "containers": 0,
    }

    def walk(n: LayoutNode) -> None:
        if n.kind == "text":
            stats["text"] += 1
        elif n.kind == "asset":
            stats["asset"] += 1
        elif n.kind == "shape":
            if n.meta.get("preview_crop"):
                stats["preview_crop"] += 1
            else:
                stats["css_shape"] += 1
        elif n.kind in ("group", "row", "grid", "overlay"):
            stats["containers"] += 1
        for c in n.children:
            walk(c)

    walk(node)
    return stats


# Compatibility aliases
def build_group_tree(artboard: Artboard) -> LayoutNode:
    return build_layout(artboard)


def apply_flex_flow(node: LayoutNode) -> LayoutNode:
    return node


def simplify_tree(node: LayoutNode) -> LayoutNode:
    return node
