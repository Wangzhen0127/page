"""Hybrid layout reconstruction from MeaXure flat layers.

Goals
-----
* Prefer editable HTML/CSS (text, colors, shapes, flex/grid).
* Recover spatial containers from containment (cards / sections).
* Use row / column / grid when siblings do not overlap.
* Use local absolute overlay only inside overlapping modules.
* Crop from preview only for tiny, hard vectors with no foreground overlap.
* Never emit a full-page preview image as page content.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List, Optional, Sequence, Set, Tuple

from .parser import Artboard, Layer, Rect

# Crop only when both sides stay under this (px). Large surfaces stay CSS.
MAX_CROP_SIDE = 120.0
# Containment / alignment tolerances
CONTAIN_PAD = 2.0
ALIGN_TOL = 10.0
GAP_CLUSTER = 8.0


@dataclass
class LayoutNode:
    kind: str  # artboard | group | row | grid | overlay | shape | text | asset | spacer
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


@dataclass
class _Item:
    z: int
    layer: Layer
    role: str  # text | shape | asset | container


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


def _is_css_shape(layer: Layer) -> bool:
    """Shapes we can express with CSS fills / radius / borders."""
    if layer.type != "shape":
        return False
    if _is_complex_vector(layer):
        # Large complex surfaces still stay CSS (sampled gradient / solid)
        if layer.rect.width >= 200 or layer.rect.height >= 200:
            name = layer.name or ""
            if any(k in name for k in ("位图", "banner", "图片")) and not layer.fills:
                return False
            return bool(layer.fills or layer.borders)
        return False
    return bool(layer.fills or layer.borders or layer.shadows or layer.radius)


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
    # Keep shapes that still contribute border / shadow / distinct fill
    if layer.borders or layer.shadows:
        return False
    for asset in assets:
        if asset.rect.contains(layer.rect, pad=3.0) or _almost_same_rect(
            asset.rect, layer.rect, tol=4.0
        ):
            if _rect_area(layer.rect) <= _rect_area(asset.rect) * 1.1:
                return True
    return False


def _can_be_container(layer: Layer) -> bool:
    """Background-like shapes that may wrap children."""
    if layer.type != "shape" or layer.is_asset:
        return False
    if not (layer.fills or layer.borders or layer.shadows):
        return False
    # Tiny icons / dots / complex vectors are not containers
    if layer.rect.width < 48 or layer.rect.height < 28:
        return False
    name = layer.name or ""
    if any(k in name for k in ("路径", "形状结合", "位图", "banner")):
        return False
    if "形状" in name and "矩形" not in name and "椭圆" not in name and "蒙版" not in name:
        # Generic vector icon — not a card shell
        if layer.rect.width < 200 and layer.rect.height < 200:
            return False
    return True


def _enrich_layer_exportable(layer: Layer, slice_by_id: Dict[str, Layer]) -> Layer:
    """Attach top-level MeaXure slice exportable metadata when objectIDs match."""
    if layer.exportable:
        return layer
    hit = slice_by_id.get(layer.object_id)
    if hit and hit.exportable:
        layer.exportable = list(hit.exportable)
        if layer.type == "shape":
            # Treat as asset for rendering while keeping rect/styles
            layer.type = "slice"
    return layer


def _collect_items(
    artboard: Artboard,
    slice_by_id: Optional[Dict[str, Layer]] = None,
) -> List[_Item]:
    slice_by_id = slice_by_id or {}
    layers = [_enrich_layer_exportable(l, slice_by_id) for l in artboard.layers]
    asset_layers = [l for l in layers if l.is_asset]
    items: List[_Item] = []
    seen_asset_rects: List[Rect] = []

    for idx, layer in enumerate(layers):
        if layer.type == "group":
            continue
        if layer.is_asset:
            dup = any(_almost_same_rect(prev, layer.rect, tol=2.0) for prev in seen_asset_rects)
            if dup:
                continue
            seen_asset_rects.append(layer.rect)
            items.append(_Item(idx, layer, "asset"))
            continue
        if not _has_visual(layer):
            continue
        if _covered_by_asset(layer, asset_layers):
            continue
        if layer.type == "text":
            items.append(_Item(idx, layer, "text"))
        else:
            items.append(_Item(idx, layer, "shape"))
    return items


def _foreground_overlap(target: Layer, others: Sequence[_Item], target_z: int) -> bool:
    """True if a later (higher-z) text/asset overlaps the crop candidate."""
    t = target.rect
    for it in others:
        if it.z <= target_z:
            continue
        if it.role not in ("text", "asset"):
            continue
        if _overlap_area(t, it.layer.rect) > 4.0:
            return True
    return False


def _needs_preview_crop(layer: Layer, items: Sequence[_Item], z: int) -> bool:
    """Strict crop gate: only tiny hard vectors without foreground overlap.

    Prefer CSS / original assets. Crop is a last resort for path-like icons
    that MeaXure cannot describe with fills alone.
    """
    if layer.type != "shape" or layer.is_asset:
        return False
    # Never crop large surfaces — bake risk is too high.
    if layer.rect.width > MAX_CROP_SIDE or layer.rect.height > MAX_CROP_SIDE:
        return False
    if _foreground_overlap(layer, items, z):
        return False

    name = layer.name or ""
    hard_name = any(k in name for k in ("路径", "形状结合", "蒙版", "位图", "banner", "图片"))
    generic_shape = "形状" in name and not hard_name and "椭圆" not in name

    # Ellipses / rounded rects with solid fills → CSS
    if "椭圆" in name and layer.fills and not hard_name:
        return False
    if "矩形" in name and layer.fills and not hard_name:
        return False

    # Solid-filled generic shapes: keep as CSS color blocks (editable)
    if generic_shape and layer.fills and not any(
        (f.get("fillType") == "Gradient") for f in layer.fills
    ):
        return False

    # Hard vectors / empty bitmaps / gradient borders
    if hard_name:
        return True
    if not layer.fills and not layer.borders:
        return True
    if _is_complex_vector(layer) and not layer.fills:
        return True
    # Gradient border cannot be CSS-accurate on tiny icons
    for b in layer.borders or []:
        if b.get("fillType") == "Gradient":
            return True
    return False


def _infer_artboard_background(artboard: Artboard, items: Sequence[_Item]) -> Optional[str]:
    """Pick a solid page background from a near-full-bleed opaque fill."""
    best: Optional[Tuple[float, str]] = None
    for it in items:
        layer = it.layer
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
        # Prefer layers covering most of the artboard
        cover = _rect_area(layer.rect) / max(1.0, artboard.width * artboard.height)
        if cover < 0.55:
            continue
        css = color.get("css-rgba") or color.get("color-hex")
        if not css and color.get("rgb"):
            rgb = color["rgb"]
            css = f"rgb({int(rgb.get('r', 0))},{int(rgb.get('g', 0))},{int(rgb.get('b', 0))})"
        if not css:
            continue
        score = cover
        # Prefer bottom-most large fills as page bg
        if it.z < 5:
            score += 0.1
        if best is None or score > best[0]:
            best = (score, str(css))
    return best[1] if best else None


# ---------------------------------------------------------------------------
# Spatial tree
# ---------------------------------------------------------------------------


@dataclass
class _TreeNode:
    item: Optional[_Item]
    rect: Rect
    children: List["_TreeNode"] = field(default_factory=list)
    z: int = 0


def _find_parent(node: _TreeNode, candidates: List[_TreeNode]) -> Optional[_TreeNode]:
    """Nearest (smallest-area) container that fully contains node and paints under it."""
    best: Optional[_TreeNode] = None
    best_area = float("inf")
    for cand in candidates:
        if cand is node or cand.item is None:
            continue
        if cand.z >= node.z:
            continue
        layer = cand.item.layer
        if not _can_be_container(layer):
            continue
        if not cand.rect.contains(node.rect, pad=CONTAIN_PAD):
            continue
        # Require meaningful size difference
        if _rect_area(cand.rect) < _rect_area(node.rect) * 1.08:
            continue
        area = _rect_area(cand.rect)
        if area < best_area:
            best_area = area
            best = cand
    return best


def _build_spatial_forest(items: List[_Item], artboard: Artboard) -> List[_TreeNode]:
    nodes = [_TreeNode(item=it, rect=it.layer.rect, z=it.z) for it in items]
    # Larger containers first helps stable parent picking
    by_area = sorted(nodes, key=lambda n: (-_rect_area(n.rect), n.z))

    roots: List[_TreeNode] = []
    assigned: Set[int] = set()

    for node in sorted(nodes, key=lambda n: n.z):
        parent = _find_parent(node, by_area)
        if parent is None:
            roots.append(node)
        else:
            parent.children.append(node)
            assigned.add(id(node))

    # Sort children by paint / reading order
    def sort_tree(n: _TreeNode) -> None:
        n.children.sort(key=lambda c: (c.rect.y, c.rect.x, c.z))
        for c in n.children:
            sort_tree(c)

    for r in roots:
        sort_tree(r)
    roots.sort(key=lambda n: (n.rect.y, n.rect.x, n.z))
    return roots


# ---------------------------------------------------------------------------
# Layout mode inference
# ---------------------------------------------------------------------------


def _sibling_overlap_ratio(nodes: Sequence[_TreeNode]) -> float:
    n = len(nodes)
    if n < 2:
        return 0.0
    pairs = 0
    hits = 0
    for i in range(n):
        for j in range(i + 1, n):
            pairs += 1
            if _overlap_area(nodes[i].rect, nodes[j].rect) > 8.0:
                hits += 1
    return hits / pairs if pairs else 0.0


def _y_centers(nodes: Sequence[_TreeNode]) -> List[float]:
    return [n.rect.y + n.rect.height * 0.5 for n in nodes]


def _x_centers(nodes: Sequence[_TreeNode]) -> List[float]:
    return [n.rect.x + n.rect.width * 0.5 for n in nodes]


def _median(vals: Sequence[float]) -> float:
    if not vals:
        return 0.0
    s = sorted(vals)
    m = len(s) // 2
    if len(s) % 2:
        return s[m]
    return 0.5 * (s[m - 1] + s[m])


def _looks_like_row(nodes: Sequence[_TreeNode]) -> bool:
    if len(nodes) < 2:
        return False
    ys = _y_centers(nodes)
    med = _median(ys)
    if max(abs(y - med) for y in ys) > ALIGN_TOL:
        return False
    # x must be strictly increasing enough
    ordered = sorted(nodes, key=lambda n: n.rect.x)
    for a, b in zip(ordered, ordered[1:]):
        if b.rect.x + 2 < a.rect.x:
            return False
    return True


def _looks_like_grid(nodes: Sequence[_TreeNode]) -> bool:
    if len(nodes) < 4:
        return False
    xs = sorted({round(n.rect.x / GAP_CLUSTER) * GAP_CLUSTER for n in nodes})
    ys = sorted({round(n.rect.y / GAP_CLUSTER) * GAP_CLUSTER for n in nodes})
    if len(xs) < 2 or len(ys) < 2:
        return False
    # Cell sizes roughly similar
    widths = [n.rect.width for n in nodes]
    heights = [n.rect.height for n in nodes]
    if max(widths) - min(widths) > 24 or max(heights) - min(heights) > 24:
        return False
    return len(nodes) >= len(xs) * len(ys) * 0.6


def _median_gap_column(nodes: Sequence[_TreeNode]) -> float:
    ordered = sorted(nodes, key=lambda n: n.rect.y)
    gaps = []
    for a, b in zip(ordered, ordered[1:]):
        gaps.append(b.rect.y - a.rect.bottom())
    gaps = [g for g in gaps if g >= -1]
    return max(0.0, _median(gaps)) if gaps else 0.0


def _median_gap_row(nodes: Sequence[_TreeNode]) -> float:
    ordered = sorted(nodes, key=lambda n: n.rect.x)
    gaps = []
    for a, b in zip(ordered, ordered[1:]):
        gaps.append(b.rect.x - a.rect.right())
    gaps = [g for g in gaps if g >= -1]
    return max(0.0, _median(gaps)) if gaps else 0.0


def _infer_mode(nodes: Sequence[_TreeNode]) -> str:
    if len(nodes) <= 1:
        return "column"
    if _sibling_overlap_ratio(nodes) > 0.12:
        return "overlay"
    if _looks_like_grid(nodes):
        return "grid"
    if _looks_like_row(nodes):
        return "row"
    return "column"


# ---------------------------------------------------------------------------
# Materialize LayoutNode tree
# ---------------------------------------------------------------------------


def _leaf_node(item: _Item, parent_rect: Rect, *, absolute: bool, items: Sequence[_Item]) -> LayoutNode:
    layer = item.layer
    kind = item.role if item.role in ("text", "asset", "shape") else "shape"
    meta: Dict[str, object] = {}
    if kind == "shape" and _needs_preview_crop(layer, items, item.z):
        meta["preview_crop"] = True
        meta["suppress_fill"] = True
    if kind == "asset":
        meta["force_visible"] = True

    rel_x = layer.rect.x - parent_rect.x
    rel_y = layer.rect.y - parent_rect.y

    node = LayoutNode(
        kind=kind,
        name=layer.name or layer.type,
        rect=layer.rect,
        layer=layer,
        class_name=kind,
        z_index=item.z,
        meta=meta,
    )
    if absolute or kind == "asset":
        # Assets always absolute inside their positioning context
        node.abs_left = rel_x
        node.abs_top = rel_y
        node.margin_top = 0.0
        node.margin_left = 0.0
    else:
        node.margin_left = rel_x
        node.margin_top = rel_y
    return node


def _materialize_children(
    children: List[_TreeNode],
    parent_rect: Rect,
    items: Sequence[_Item],
) -> Tuple[List[LayoutNode], str, float]:
    if not children:
        return [], "column", 0.0

    mode = _infer_mode(children)

    if mode == "overlay":
        out = [_materialize_node(c, parent_rect, items, force_absolute=True) for c in children]
        # Assets / leaves already absolute; ensure all absolute
        for n in out:
            if not n.is_absolute:
                n.abs_left = n.rect.x - parent_rect.x
                n.abs_top = n.rect.y - parent_rect.y
                n.margin_top = 0.0
                n.margin_left = 0.0
        return out, "overlay", 0.0

    if mode == "row":
        ordered = sorted(children, key=lambda n: n.rect.x)
        gap = _median_gap_row(ordered)
        out: List[LayoutNode] = []
        prev_right: Optional[float] = None
        first = True
        for c in ordered:
            node = _materialize_node(c, parent_rect, items, force_absolute=False)
            # Convert to row flow: margin-left = gap from previous, margin-top = y offset
            if node.is_absolute:
                # Nested overlay kept absolute — wrap not needed; keep as-is relative parent
                out.append(node)
                continue
            y_off = c.rect.y - parent_rect.y
            if first:
                node.margin_left = c.rect.x - parent_rect.x
                node.margin_top = y_off
                first = False
            else:
                assert prev_right is not None
                node.margin_left = max(0.0, c.rect.x - prev_right)
                node.margin_top = y_off
            prev_right = c.rect.right()
            out.append(node)
        return out, "row", gap

    if mode == "grid":
        ordered = sorted(children, key=lambda n: (n.rect.y, n.rect.x))
        # Estimate column count from unique x clusters
        xs = sorted({round(n.rect.x / GAP_CLUSTER) * GAP_CLUSTER for n in ordered})
        cols = max(1, len(xs))
        gap_x = _median_gap_row(ordered) if len(ordered) > 1 else 0.0
        gap_y = _median_gap_column(ordered) if len(ordered) > 1 else 0.0
        out = []
        for c in ordered:
            node = _materialize_node(c, parent_rect, items, force_absolute=False)
            if not node.is_absolute:
                node.margin_left = 0.0
                node.margin_top = 0.0
            out.append(node)
        # Stash grid meta on a synthetic wrapper via gap average
        return out, "grid", max(gap_x, gap_y)

    # column
    ordered = sorted(children, key=lambda n: (n.rect.y, n.rect.x))
    gap = _median_gap_column(ordered)
    out = []
    prev_bottom: Optional[float] = None
    first = True
    for c in ordered:
        node = _materialize_node(c, parent_rect, items, force_absolute=False)
        if node.is_absolute:
            out.append(node)
            continue
        x_off = c.rect.x - parent_rect.x
        if first:
            node.margin_top = c.rect.y - parent_rect.y
            node.margin_left = x_off
            first = False
        else:
            assert prev_bottom is not None
            # Prefer non-negative spacing; overlapping siblings should have been overlay
            node.margin_top = max(0.0, c.rect.y - prev_bottom)
            node.margin_left = x_off
        prev_bottom = c.rect.bottom()
        out.append(node)
    return out, "column", gap


def _materialize_node(
    node: _TreeNode,
    parent_rect: Rect,
    items: Sequence[_Item],
    *,
    force_absolute: bool,
) -> LayoutNode:
    assert node.item is not None
    item = node.item

    # Leaf
    if not node.children:
        return _leaf_node(item, parent_rect, absolute=force_absolute or item.role == "asset", items=items)

    # Container with children: background shape + nested content
    child_nodes, mode, gap = _materialize_children(node.children, node.rect, items)

    kind = {
        "row": "row",
        "grid": "grid",
        "overlay": "overlay",
        "column": "group",
    }.get(mode, "group")

    container = LayoutNode(
        kind=kind,
        name=item.layer.name or kind,
        rect=node.rect,
        layer=item.layer,
        class_name=kind,
        z_index=item.z,
        direction="row" if mode == "row" else "column",
        gap=gap,
        children=child_nodes,
        meta={
            "layout_mode": mode,
            "position_relative": True,
        },
    )
    if mode == "grid":
        xs = sorted({round(c.rect.x / GAP_CLUSTER) * GAP_CLUSTER for c in node.children})
        container.meta["grid_columns"] = max(1, len(xs))
        container.meta["grid_gap"] = gap

    rel_x = node.rect.x - parent_rect.x
    rel_y = node.rect.y - parent_rect.y
    if force_absolute:
        container.abs_left = rel_x
        container.abs_top = rel_y
    else:
        container.margin_left = rel_x
        container.margin_top = rel_y
    return container


def _top_level_bands(roots: List[_TreeNode]) -> List[List[_TreeNode]]:
    """Group non-overlapping root nodes into horizontal bands."""
    if not roots:
        return []
    ordered = sorted(roots, key=lambda n: (n.rect.y, n.rect.x, n.z))
    bands: List[List[_TreeNode]] = []
    cur: List[_TreeNode] = []
    cur_bottom = -1e9
    for n in ordered:
        if not cur:
            cur = [n]
            cur_bottom = n.rect.bottom()
            continue
        # New band if clearly below current band with little vertical overlap
        overlap = min(cur_bottom, n.rect.bottom()) - max(
            min(c.rect.y for c in cur), n.rect.y
        )
        if n.rect.y >= cur_bottom - 4 or overlap < 8:
            # still allow if heavily overlapping current band vertically
            band_top = min(c.rect.y for c in cur)
            if n.rect.y >= cur_bottom - 2:
                bands.append(cur)
                cur = [n]
                cur_bottom = n.rect.bottom()
                continue
            if n.rect.y > band_top + 40 and overlap < n.rect.height * 0.25:
                bands.append(cur)
                cur = [n]
                cur_bottom = n.rect.bottom()
                continue
        cur.append(n)
        cur_bottom = max(cur_bottom, n.rect.bottom())
    if cur:
        bands.append(cur)
    return bands


def build_layout(
    artboard: Artboard,
    slices: Optional[Sequence[Layer]] = None,
) -> LayoutNode:
    slice_by_id = {s.object_id: s for s in (slices or []) if s.object_id}
    items = _collect_items(artboard, slice_by_id)
    bg = _infer_artboard_background(artboard, items)

    root = LayoutNode(
        kind="artboard",
        name=artboard.name,
        rect=Rect(0, 0, artboard.width, artboard.height),
        direction="column",
        class_name="page",
        meta={
            "position_relative": True,
            "background": bg or "#EFF4FB",
            "stats": {},
        },
    )

    forest = _build_spatial_forest(items, artboard)
    bands = _top_level_bands(forest)

    prev_bottom = 0.0
    for band in bands:
        mode = _infer_mode(band)
        if len(band) == 1 and mode != "overlay":
            node = _materialize_node(band[0], root.rect, items, force_absolute=False)
            if not node.is_absolute:
                # column flow on artboard
                node.margin_top = max(0.0, band[0].rect.y - prev_bottom)
                node.margin_left = band[0].rect.x
                prev_bottom = max(prev_bottom, band[0].rect.bottom())
            else:
                prev_bottom = max(prev_bottom, band[0].rect.bottom())
            root.children.append(node)
            continue

        # Multi-node band → section container
        band_x = min(n.rect.x for n in band)
        band_y = min(n.rect.y for n in band)
        band_r = max(n.rect.right() for n in band)
        band_b = max(n.rect.bottom() for n in band)
        band_rect = Rect(band_x, band_y, band_r - band_x, band_b - band_y)

        child_nodes, child_mode, gap = _materialize_children(band, band_rect, items)
        # If band itself overlaps heavily, force overlay section
        if child_mode == "overlay" or mode == "overlay":
            for n in child_nodes:
                if not n.is_absolute:
                    n.abs_left = n.rect.x - band_rect.x
                    n.abs_top = n.rect.y - band_rect.y
                    n.margin_top = 0.0
                    n.margin_left = 0.0
            kind = "overlay"
            direction = "column"
        else:
            kind = {"row": "row", "grid": "grid"}.get(child_mode, "group")
            direction = "row" if child_mode == "row" else "column"

        section = LayoutNode(
            kind=kind,
            name="section",
            rect=band_rect,
            direction=direction,
            gap=gap,
            children=child_nodes,
            class_name=kind,
            margin_top=max(0.0, band_y - prev_bottom),
            margin_left=band_x,
            meta={"layout_mode": child_mode, "position_relative": True},
        )
        if child_mode == "grid":
            xs = sorted({round(n.rect.x / GAP_CLUSTER) * GAP_CLUSTER for n in band})
            section.meta["grid_columns"] = max(1, len(xs))
            section.meta["grid_gap"] = gap
        root.children.append(section)
        prev_bottom = max(prev_bottom, band_b)

    if prev_bottom < artboard.height - 0.5:
        gap = artboard.height - prev_bottom
        root.children.append(
            LayoutNode(
                kind="spacer",
                name="spacer",
                rect=Rect(0, prev_bottom, 1, gap),
                class_name="spacer",
                meta={"spacer": True},
            )
        )

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
