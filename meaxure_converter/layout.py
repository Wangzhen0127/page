"""Build a flex layout tree that preserves MeaXure coordinates 1:1.

Strategy
--------
Artboard is a column flex container. Every non-asset visual layer becomes a
flex item with:

    margin-top  = y - previous_bottom   (may be negative when overlapping)
    margin-left = x

This reproduces absolute coordinates using only flex + margins.

Slice / exportable assets keep original absolute left/top relative to the
artboard (素材原始定位).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List, Optional, Sequence, Set, Tuple

from .parser import Artboard, Layer, Rect


@dataclass
class LayoutNode:
    kind: str  # artboard | shape | text | asset | spacer
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


def _css_says_invisible(layer: Layer) -> bool:
    """MeaXure marks measurement slices with `opacity: 0` in css — not real hide."""
    for line in layer.css or []:
        low = (line or "").lower().replace(" ", "")
        if low.startswith("opacity:0") or low == "opacity:0;":
            return True
    return False


def _is_measurement_hidden(layer: Layer) -> bool:
    """Skip Sketch measure helpers that are fully transparent rectangles."""
    if layer.is_asset or layer.type == "text":
        return False
    if layer.opacity is not None and layer.opacity <= 0.01:
        return True
    for line in layer.css or []:
        low = (line or "").lower().replace(" ", "")
        if low.startswith("opacity:0"):
            # Transparent hit-area / measure rect — not a real visual
            return True
    return False


def _is_complex_vector(layer: Layer) -> bool:
    """Shapes MeaXure cannot describe with CSS alone (need preview crop)."""
    if layer.type != "shape":
        return False
    name = layer.name or ""
    if any(k in name for k in ("形状", "路径", "结合", "位图", "banner", "图片")):
        # Simple ellipses / rounded rects named 椭圆形 are fine as CSS
        if "椭圆" in name and "结合" not in name and "路径" not in name:
            return False
        return True
    # No fill/border → must crop from preview
    if not layer.fills and not layer.borders:
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
    # Empty bitmap / banner placeholders — still emit so preview-crop can fill them
    name = (layer.name or "").lower()
    if any(k in name for k in ("位图", "banner", "图片", "image", "形状", "路径")):
        return True
    return False


def _covered_by_asset(layer: Layer, assets: Sequence[Layer]) -> bool:
    if layer.type == "text" or layer.is_asset:
        return False
    for asset in assets:
        if asset.rect.contains(layer.rect, pad=3.0) or _almost_same_rect(
            asset.rect, layer.rect, tol=4.0
        ):
            if _rect_area(layer.rect) <= _rect_area(asset.rect) * 1.2:
                return True
    return False


def _collect_layers(
    artboard: Artboard,
) -> Tuple[List[Tuple[int, Layer]], List[Tuple[int, Layer]]]:
    asset_layers = [l for l in artboard.layers if l.is_asset]
    assets: List[Tuple[int, Layer]] = []
    flow: List[Tuple[int, Layer]] = []

    for idx, layer in enumerate(artboard.layers):
        if layer.type == "group":
            continue
        if layer.is_asset:
            assets.append((idx, layer))
            continue
        if not _has_visual(layer):
            continue
        if _covered_by_asset(layer, asset_layers):
            continue
        flow.append((idx, layer))

    # Flex margin chain must go top→bottom
    flow.sort(key=lambda it: (it[1].rect.y, it[1].rect.x, it[0]))
    # Assets keep sketch paint order (bottom→top) via z-index
    assets.sort(key=lambda it: it[0])
    return flow, assets


def build_layout(artboard: Artboard) -> LayoutNode:
    root = LayoutNode(
        kind="artboard",
        name=artboard.name,
        rect=Rect(0, 0, artboard.width, artboard.height),
        direction="column",
        class_name="page",
        meta={"position_relative": True},
    )

    flow, assets = _collect_layers(artboard)

    prev_bottom = 0.0
    for z, layer in flow:
        kind = "text" if layer.type == "text" else "shape"
        needs_crop = kind == "shape" and _is_complex_vector(layer)
        meta: Dict[str, object] = {}
        if needs_crop:
            meta["preview_crop"] = True
            # Avoid painting solid fill under the cropped icon
            meta["suppress_fill"] = True
        node = LayoutNode(
            kind=kind,
            name=layer.name or layer.type,
            rect=layer.rect,
            layer=layer,
            class_name=kind,
            z_index=z,
            margin_top=layer.rect.y - prev_bottom,
            margin_left=layer.rect.x,
            meta=meta,
        )
        root.children.append(node)
        prev_bottom = layer.rect.y + layer.rect.height

    # Trailing spacer so flex column height matches artboard when last
    # content ends above the canvas bottom.
    if prev_bottom < artboard.height - 0.5:
        gap = artboard.height - prev_bottom
        root.children.append(
            LayoutNode(
                kind="spacer",
                name="spacer",
                rect=Rect(0, prev_bottom, 1, gap),
                margin_top=0.0,
                margin_left=0.0,
                class_name="spacer",
                meta={"spacer": True},
            )
        )

    for z, layer in assets:
        node = LayoutNode(
            kind="asset",
            name=layer.name or "asset",
            rect=layer.rect,
            layer=layer,
            class_name="asset",
            z_index=z,
            abs_left=layer.rect.x,
            abs_top=layer.rect.y,
            # Ignore MeaXure measurement `opacity: 0` on slices
            meta={"force_visible": True},
        )
        root.children.append(node)

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


# Compatibility aliases
def build_group_tree(artboard: Artboard) -> LayoutNode:
    return build_layout(artboard)


def apply_flex_flow(node: LayoutNode) -> LayoutNode:
    return node


def simplify_tree(node: LayoutNode) -> LayoutNode:
    return node
