"""Build a flex-oriented layout tree from flat MeaXure layers.

Strategy
--------
1. Reconstruct nesting from Sketch ``group`` bounding boxes.
2. Among siblings, treat large containing shapes as absolute backgrounds.
3. Flow remaining content with flex rows/columns + margin gaps.
4. Keep slice / exportable assets absolute using original rects
   relative to their parent container.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List, Optional, Sequence, Set

from .parser import Artboard, Layer, Rect


@dataclass
class LayoutNode:
    kind: str  # artboard | group | row | shape | text | asset | spacer
    name: str
    rect: Rect
    layer: Optional[Layer] = None
    children: List["LayoutNode"] = field(default_factory=list)
    # Flex flow hints relative to previous sibling in the same parent
    margin_top: float = 0.0
    margin_left: float = 0.0
    # Absolute placement (relative to parent) for assets / backgrounds
    abs_left: Optional[float] = None
    abs_top: Optional[float] = None
    # Row/column direction for containers
    direction: str = "column"  # column | row
    class_name: str = ""
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


def _almost_same_rect(a: Rect, b: Rect, tol: float = 2.0) -> bool:
    return (
        abs(a.x - b.x) <= tol
        and abs(a.y - b.y) <= tol
        and abs(a.width - b.width) <= tol
        and abs(a.height - b.height) <= tol
    )


def _overlap_ratio(a: Rect, b: Rect) -> float:
    """Intersection area / min(area(a), area(b))."""
    ox = min(a.right(), b.right()) - max(a.x, b.x)
    oy = min(a.bottom(), b.bottom()) - max(a.y, b.y)
    if ox <= 0 or oy <= 0:
        return 0.0
    inter = ox * oy
    smaller = min(_rect_area(a), _rect_area(b))
    if smaller <= 0:
        return 0.0
    return inter / smaller


def _x_overlap_ratio(a: Rect, b: Rect) -> float:
    ox = min(a.right(), b.right()) - max(a.x, b.x)
    if ox <= 0:
        return 0.0
    return ox / min(a.width, b.width)


def _y_overlap_ratio(a: Rect, b: Rect) -> float:
    oy = min(a.bottom(), b.bottom()) - max(a.y, b.y)
    if oy <= 0:
        return 0.0
    return oy / min(a.height, b.height)


def build_group_tree(artboard: Artboard) -> LayoutNode:
    """Assign layers into nested groups by geometric containment."""
    layers = list(artboard.layers)
    groups = [l for l in layers if l.type == "group"]
    groups_sorted = sorted(
        groups,
        key=lambda g: (-_rect_area(g.rect), g.rect.y, g.rect.x),
    )

    root = LayoutNode(
        kind="artboard",
        name=artboard.name,
        rect=Rect(0, 0, artboard.width, artboard.height),
        direction="column",
        class_name="page",
    )

    group_nodes: Dict[str, LayoutNode] = {}
    for g in groups_sorted:
        node = LayoutNode(
            kind="group",
            name=g.name or "group",
            rect=g.rect,
            layer=g,
            direction="column",
            class_name="group",
        )
        group_nodes[g.object_id] = node

    for g in groups_sorted:
        node = group_nodes[g.object_id]
        parent: LayoutNode = root
        best_area = _rect_area(root.rect)
        for other in groups_sorted:
            if other.object_id == g.object_id:
                continue
            if other.rect.contains(g.rect) and not _almost_same_rect(other.rect, g.rect):
                area = _rect_area(other.rect)
                if area < best_area:
                    best_area = area
                    parent = group_nodes[other.object_id]
        parent.children.append(node)

    content_layers = [l for l in layers if l.type != "group"]
    assets = [l for l in content_layers if l.is_asset]
    non_assets = [l for l in content_layers if not l.is_asset]

    # Suppress vector shapes fully covered by an exported slice
    suppressed: Set[str] = set()
    for asset in assets:
        for other in non_assets:
            if other.type == "text":
                continue
            if asset.rect.contains(other.rect, pad=3.0) or _almost_same_rect(
                asset.rect, other.rect, tol=4.0
            ):
                suppressed.add(other.object_id)

    def find_parent(layer: Layer) -> LayoutNode:
        parent = root
        best_area = _rect_area(root.rect)
        for g in groups_sorted:
            node = group_nodes[g.object_id]
            if g.rect.contains(layer.rect, pad=2.0):
                area = _rect_area(g.rect)
                if area < best_area:
                    best_area = area
                    parent = node
        return parent

    for layer in content_layers:
        if layer.object_id in suppressed:
            continue
        parent = find_parent(layer)
        if layer.is_asset:
            child = LayoutNode(
                kind="asset",
                name=layer.name or "asset",
                rect=layer.rect,
                layer=layer,
                class_name="asset",
                abs_left=layer.rect.x - parent.rect.x,
                abs_top=layer.rect.y - parent.rect.y,
            )
        elif layer.type == "text":
            child = LayoutNode(
                kind="text",
                name=layer.name or "text",
                rect=layer.rect,
                layer=layer,
                class_name="text",
            )
        else:
            child = LayoutNode(
                kind="shape",
                name=layer.name or "shape",
                rect=layer.rect,
                layer=layer,
                class_name="shape",
            )
        parent.children.append(child)

    def prune(node: LayoutNode) -> Optional[LayoutNode]:
        node.children = [c for c in (prune(c) for c in node.children) if c]
        if node.kind == "group" and not node.children:
            layer = node.layer
            has_visual = bool(
                layer
                and (
                    layer.fills
                    or layer.borders
                    or layer.shadows
                    or any(
                        (line or "").lower().startswith("background")
                        for line in (layer.css or [])
                    )
                )
            )
            if not has_visual:
                return None
        return node

    pruned = prune(root)
    assert pruned is not None
    return pruned


def _extract_backgrounds(
    parent: LayoutNode, children: List[LayoutNode]
) -> tuple[List[LayoutNode], List[LayoutNode]]:
    """Split children into absolute backgrounds vs flow content.

    A node is a background when it contains (or heavily overlaps) other
    siblings and is a shape/group — typical Sketch card / banner fills.
    """
    if len(children) <= 1:
        return [], children

    backgrounds: List[LayoutNode] = []
    remaining = list(children)

    # Largest first so outer cards become backgrounds before inner ones
    candidates = sorted(
        [c for c in remaining if c.kind in ("shape", "group")],
        key=lambda n: -_rect_area(n.rect),
    )

    bg_ids: Set[int] = set()
    for cand in candidates:
        others = [o for o in remaining if o is not cand and id(o) not in bg_ids]
        if not others:
            continue
        contained = [
            o
            for o in others
            if cand.rect.contains(o.rect, pad=4.0) or _overlap_ratio(cand.rect, o.rect) > 0.7
        ]
        # Must contain at least one other sibling, and cover a meaningful share
        if not contained:
            continue
        # Avoid treating a small icon shape as background of a nearby text
        if _rect_area(cand.rect) < _rect_area(parent.rect) * 0.02 and len(contained) < 2:
            # still allow if it clearly contains multiple items
            if len(contained) < 2:
                continue
        cand.abs_left = cand.rect.x - parent.rect.x
        cand.abs_top = cand.rect.y - parent.rect.y
        cand.meta["is_background"] = True
        backgrounds.append(cand)
        bg_ids.add(id(cand))

    flow = [c for c in remaining if id(c) not in bg_ids]
    return backgrounds, flow


def _are_side_by_side(a: LayoutNode, b: LayoutNode, y_tol: float = 12.0) -> bool:
    """True when two nodes sit on one horizontal line without heavy overlap."""
    y_overlap = _y_overlap_ratio(a.rect, b.rect)
    x_overlap = _x_overlap_ratio(a.rect, b.rect)
    # Similar vertical band
    centers_close = abs((a.rect.y + a.rect.height / 2) - (b.rect.y + b.rect.height / 2)) <= max(
        y_tol, min(a.rect.height, b.rect.height) * 0.5
    )
    if x_overlap > 0.35:
        return False  # stacked / overlapping, not side-by-side
    if y_overlap < 0.25 and not centers_close:
        return False
    return True


def _cluster_rows(nodes: Sequence[LayoutNode], y_tol: float = 12.0) -> List[List[LayoutNode]]:
    """Cluster nodes into horizontal rows; overlapping stacks stay separate."""
    if not nodes:
        return []
    ordered = sorted(nodes, key=lambda n: (n.rect.y, n.rect.x))
    rows: List[List[LayoutNode]] = []
    for node in ordered:
        placed = False
        for row in rows:
            if all(_are_side_by_side(node, existing, y_tol=y_tol) for existing in row):
                row.append(node)
                placed = True
                break
        if not placed:
            rows.append([node])
    for row in rows:
        row.sort(key=lambda n: n.rect.x)
    rows.sort(key=lambda row: min(n.rect.y for n in row))
    return rows


def apply_flex_flow(node: LayoutNode) -> LayoutNode:
    """Rewrite children into flex rows/columns with margin gaps."""
    if not node.children:
        return node

    assets = [c for c in node.children if c.kind == "asset"]
    flow_children = [c for c in node.children if c.kind != "asset"]
    flow_children = [apply_flex_flow(c) for c in flow_children]

    backgrounds, flow_children = _extract_backgrounds(node, flow_children)

    if not flow_children and not backgrounds and not assets:
        node.children = []
        return node

    new_children: List[LayoutNode] = []

    # Absolute backgrounds first (painted under content)
    if backgrounds or assets:
        node.meta["position_relative"] = True
    for bg in backgrounds:
        bg.abs_left = bg.rect.x - node.rect.x
        bg.abs_top = bg.rect.y - node.rect.y
        new_children.append(bg)

    if flow_children:
        rows = _cluster_rows(flow_children)
        prev_bottom = node.rect.y
        for row_nodes in rows:
            row_top = min(n.rect.y for n in row_nodes)
            row_left = min(n.rect.x for n in row_nodes)
            row_right = max(n.rect.right() for n in row_nodes)
            row_bottom = max(n.rect.bottom() for n in row_nodes)
            row_rect = Rect(row_left, row_top, row_right - row_left, row_bottom - row_top)

            # Overlaps previous flow content.
            overlaps_previous = row_top + 1 < prev_bottom and prev_bottom > node.rect.y
            if overlaps_previous:
                overlap_amount = prev_bottom - row_top
                row_area = (row_right - row_left) * (row_bottom - row_top)
                # Large section blocks: keep in flex flow with negative margin
                # so overall page height stays correct.
                is_large_section = (
                    (row_right - row_left) >= node.rect.width * 0.5
                    or row_area >= _rect_area(node.rect) * 0.08
                )
                if is_large_section:
                    margin_top = -overlap_amount
                    if len(row_nodes) == 1:
                        child = row_nodes[0]
                        child.margin_top = margin_top
                        child.margin_left = max(0.0, child.rect.x - node.rect.x)
                        child.abs_left = None
                        child.abs_top = None
                        new_children.append(child)
                    else:
                        row_node = LayoutNode(
                            kind="row",
                            name="row",
                            rect=row_rect,
                            direction="row",
                            class_name="row",
                            margin_top=margin_top,
                            margin_left=max(0.0, row_left - node.rect.x),
                        )
                        prev_x = row_left
                        for i, child in enumerate(row_nodes):
                            gap = max(0.0, child.rect.x - prev_x)
                            child.margin_left = gap if i > 0 else 0.0
                            child.margin_top = max(0.0, child.rect.y - row_top)
                            child.abs_left = None
                            child.abs_top = None
                            row_node.children.append(child)
                            prev_x = child.rect.right()
                        new_children.append(row_node)
                    prev_bottom = max(prev_bottom, row_bottom)
                    continue

                # Small overlays (titles on hero, badges): original absolute coords
                for child in row_nodes:
                    child.abs_left = child.rect.x - node.rect.x
                    child.abs_top = child.rect.y - node.rect.y
                    child.margin_top = 0.0
                    child.margin_left = 0.0
                    child.meta["overlay"] = True
                    new_children.append(child)
                continue

            margin_top = max(0.0, row_top - prev_bottom)

            if len(row_nodes) == 1:
                child = row_nodes[0]
                child.margin_top = margin_top
                child.margin_left = max(0.0, child.rect.x - node.rect.x)
                if not child.meta.get("is_background"):
                    child.abs_left = None
                    child.abs_top = None
                new_children.append(child)
            else:
                row_node = LayoutNode(
                    kind="row",
                    name="row",
                    rect=row_rect,
                    direction="row",
                    class_name="row",
                    margin_top=margin_top,
                    margin_left=max(0.0, row_left - node.rect.x),
                )
                prev_x = row_left
                for i, child in enumerate(row_nodes):
                    gap = max(0.0, child.rect.x - prev_x)
                    child.margin_left = gap if i > 0 else 0.0
                    child.margin_top = max(0.0, child.rect.y - row_top)
                    child.abs_left = None
                    child.abs_top = None
                    row_node.children.append(child)
                    prev_x = child.rect.right()
                new_children.append(row_node)

            prev_bottom = max(prev_bottom, row_bottom)

    for asset in assets:
        asset.abs_left = asset.rect.x - node.rect.x
        asset.abs_top = asset.rect.y - node.rect.y
        new_children.append(asset)

    node.children = new_children
    node.direction = "column"
    return node


def simplify_tree(node: LayoutNode) -> LayoutNode:
    """Collapse useless single-child group wrappers without visuals."""
    node.children = [simplify_tree(c) for c in node.children]
    if (
        node.kind == "group"
        and len(node.children) == 1
        and node.children[0].kind in ("group", "row")
        and not node.is_absolute
        and node.layer
        and not node.layer.fills
        and not node.layer.borders
        and not node.layer.shadows
    ):
        child = node.children[0]
        child.margin_top += node.margin_top
        child.margin_left += node.margin_left
        return child
    return node


def build_layout(artboard: Artboard) -> LayoutNode:
    tree = build_group_tree(artboard)
    tree = apply_flex_flow(tree)
    tree = simplify_tree(tree)
    return tree


def iter_assets(node: LayoutNode) -> List[LayoutNode]:
    out: List[LayoutNode] = []
    if node.kind == "asset":
        out.append(node)
    for c in node.children:
        out.extend(iter_assets(c))
    return out


def count_nodes(node: LayoutNode) -> Dict[str, int]:
    counts: Dict[str, int] = {}

    def walk(n: LayoutNode) -> None:
        counts[n.kind] = counts.get(n.kind, 0) + 1
        for c in n.children:
            walk(c)

    walk(node)
    return counts
