"""Sample accurate CSS gradients from the MeaXure artboard preview."""

from __future__ import annotations

from pathlib import Path
from typing import Dict, List, Optional, Sequence, Tuple

from .parser import Artboard, Layer, MeaXureDocument, Rect


def find_preview_path(doc: MeaXureDocument, artboard: Artboard) -> Optional[Path]:
    if artboard.image_path:
        candidate = doc.source_path.parent / artboard.image_path
        if candidate.exists():
            return candidate
    preview_root = doc.preview_dir
    if preview_root.exists():
        matches = sorted(preview_root.rglob("*@2x.png"))
        if matches:
            return matches[0]
        matches = sorted(preview_root.rglob("*.png"))
        if matches:
            return matches[0]
    return None


def _rgba(c: Tuple[int, int, int] | Tuple[int, int, int, int]) -> str:
    if len(c) == 4:
        r, g, b, a = c
        return f"rgba({r},{g},{b},{round(a / 255.0, 4)})"
    r, g, b = c[:3]
    return f"rgb({r},{g},{b})"


def _gradient_axis(layer: Layer) -> Tuple[float, float, float, float, float]:
    """Return (angle_deg, x0, y0, x1, y1) in layer-local unit coords 0..1."""
    fills = layer.fills or []
    grad = {}
    for f in fills:
        if f.get("fillType") == "Gradient":
            grad = f.get("gradient") or {}
            break
    frm = grad.get("from") or {"x": 0.5, "y": 0.0}
    to = grad.get("to") or {"x": 0.5, "y": 1.0}
    x0, y0 = float(frm.get("x", 0.5)), float(frm.get("y", 0.0))
    x1, y1 = float(to.get("x", 0.5)), float(to.get("y", 1.0))
    import math

    dx, dy = x1 - x0, y1 - y0
    angle = (math.degrees(math.atan2(dx, -dy)) + 360) % 360
    return angle, x0, y0, x1, y1


def sample_linear_gradient_css(
    preview_img,
    artboard: Artboard,
    layer: Layer,
    *,
    stops: int = 12,
    inset: float = 0.02,
) -> Optional[str]:
    """Build a multi-stop linear-gradient by sampling the preview along the axis.

    Samples near the layer edge parallel to the gradient to reduce overlay noise.
    """
    try:
        from PIL import Image
    except ImportError:
        return None

    if preview_img is None:
        return None

    r = layer.rect
    if r.width < 2 or r.height < 2:
        return None

    angle, x0, y0, x1, y1 = _gradient_axis(layer)
    scale_x = preview_img.width / artboard.width
    scale_y = preview_img.height / artboard.height

    # Prefer sampling along a side with less UI chrome:
    # vertical gradients → sample left inset column; horizontal → top inset row.
    import math

    vertical = abs(math.cos(math.radians(angle))) > abs(math.sin(math.radians(angle)))

    colors: List[Tuple[float, Tuple[int, int, int]]] = []
    arr = None
    try:
        import numpy as np

        arr = np.asarray(preview_img.convert("RGB"))
    except Exception:
        arr = None

    for i in range(stops):
        t = i / (stops - 1) if stops > 1 else 0.0
        # Point along gradient axis in layer-local 0..1
        lx = x0 + (x1 - x0) * t
        ly = y0 + (y1 - y0) * t
        if vertical:
            # sample near left edge at this y
            lx = inset
        else:
            ly = inset
        px = int(round((r.x + lx * r.width) * scale_x))
        py = int(round((r.y + ly * r.height) * scale_y))
        px = max(0, min(preview_img.width - 1, px))
        py = max(0, min(preview_img.height - 1, py))

        if arr is not None:
            # median of a small window to reject text/icon noise
            x0b, x1b = max(0, px - 2), min(preview_img.width, px + 3)
            y0b, y1b = max(0, py - 2), min(preview_img.height, py + 3)
            patch = arr[y0b:y1b, x0b:x1b].reshape(-1, 3)
            color = tuple(int(v) for v in np.median(patch, axis=0))
        else:
            color = preview_img.convert("RGB").getpixel((px, py))
        colors.append((t * 100.0, color))  # type: ignore[arg-type]

    # Deduplicate consecutive identical stops
    compact: List[Tuple[float, Tuple[int, int, int]]] = []
    for pos, color in colors:
        if compact and compact[-1][1] == color and abs(compact[-1][0] - pos) < 8:
            compact[-1] = (pos, color)
            continue
        compact.append((pos, color))  # type: ignore[arg-type]
    if len(compact) < 2:
        return None

    stop_css = ", ".join(f"{_rgba(c)} {p:.2f}%" for p, c in compact)
    return f"linear-gradient({angle:.2f}deg, {stop_css})"


def layer_has_gradient(layer: Layer) -> bool:
    for f in layer.fills or []:
        if f.get("fillType") == "Gradient":
            return True
    for b in layer.borders or []:
        if b.get("fillType") == "Gradient":
            return True
    return False


def gradient_is_irregular(layer: Layer) -> bool:
    """True when CSS 2-stop approximation is likely wrong."""
    for f in layer.fills or []:
        if f.get("fillType") != "Gradient":
            continue
        grad = f.get("gradient") or {}
        stops = grad.get("colorStops") or []
        if len(stops) != 2:
            return True
        # Alpha / translucent glass gradients are hard to composite exactly
        for s in stops:
            alpha = (s.get("color") or {}).get("alpha", 255)
            try:
                if float(alpha) < 250:
                    return True
            except (TypeError, ValueError):
                return True
        # Non-axis-aligned
        frm = grad.get("from") or {}
        to = grad.get("to") or {}
        dx = abs(float(to.get("x", 0.5)) - float(frm.get("x", 0.5)))
        dy = abs(float(to.get("y", 1)) - float(frm.get("y", 0)))
        if dx > 0.05 and dy > 0.05:
            return True
    for b in layer.borders or []:
        if b.get("fillType") == "Gradient":
            return True
    return False


def apply_sampled_gradients(
    doc: MeaXureDocument,
    artboard: Artboard,
    layers: Sequence[Layer],
) -> Dict[str, str]:
    """Return map objectID -> sampled background-image CSS for opaque gradients."""
    preview_path = find_preview_path(doc, artboard)
    if not preview_path:
        return {}
    try:
        from PIL import Image
    except ImportError:
        return {}

    img = Image.open(preview_path)
    out: Dict[str, str] = {}
    for layer in layers:
        if not layer_has_gradient(layer):
            continue
        # Only opaque fills — translucent glass must keep rgba compositing
        opaque = True
        for f in layer.fills or []:
            if f.get("fillType") != "Gradient":
                continue
            for s in f.get("gradient", {}).get("colorStops") or []:
                try:
                    if float((s.get("color") or {}).get("alpha", 255)) < 250:
                        opaque = False
                except (TypeError, ValueError):
                    opaque = False
        if not opaque:
            continue
        if min(layer.rect.width, layer.rect.height) < 40:
            continue
        css = sample_linear_gradient_css(img, artboard, layer, stops=24, inset=0.02)
        if css:
            out[layer.object_id] = css
    return out
