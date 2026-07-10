"""Convert MeaXure layer style fields into CSS / WXSS declarations."""

from __future__ import annotations

from typing import Any, Dict, List, Optional, Tuple

from .parser import Layer


def color_to_css(color: Optional[Dict[str, Any]], fallback: str = "transparent") -> str:
    if not color:
        return fallback
    if color.get("css-rgba"):
        return str(color["css-rgba"])
    rgb = color.get("rgb") or {}
    alpha = color.get("alpha", 255)
    try:
        a = float(alpha) / 255.0
    except (TypeError, ValueError):
        a = 1.0
    r = int(rgb.get("r", 0) or 0)
    g = int(rgb.get("g", 0) or 0)
    b = int(rgb.get("b", 0) or 0)
    if a >= 0.999:
        return f"rgb({r},{g},{b})"
    return f"rgba({r},{g},{b},{round(a, 4)})"


def gradient_to_css(gradient: Dict[str, Any]) -> Optional[str]:
    if not gradient:
        return None
    gtype = (gradient.get("type") or "Linear").lower()
    stops = gradient.get("colorStops") or []
    if not stops:
        return None
    stop_parts = []
    for stop in stops:
        pos = float(stop.get("position", 0) or 0) * 100
        stop_parts.append(f"{color_to_css(stop.get('color'))} {pos:.2f}%")
    stop_css = ", ".join(stop_parts)
    if gtype == "radial":
        return f"radial-gradient(circle at center, {stop_css})"
    # Approximate angle from from/to points in unit space
    frm = gradient.get("from") or {"x": 0.5, "y": 0}
    to = gradient.get("to") or {"x": 0.5, "y": 1}
    dx = float(to.get("x", 0.5)) - float(frm.get("x", 0.5))
    dy = float(to.get("y", 1)) - float(frm.get("y", 0))
    import math

    angle = (math.degrees(math.atan2(dx, -dy)) + 360) % 360
    # Use explicit side keywords for common axis-aligned cases (better browser match)
    if abs(dx) < 0.02 and dy > 0.5:
        return f"linear-gradient(to bottom, {stop_css})"
    if abs(dx) < 0.02 and dy < -0.5:
        return f"linear-gradient(to top, {stop_css})"
    if abs(dy) < 0.02 and dx > 0.5:
        return f"linear-gradient(to right, {stop_css})"
    if abs(dy) < 0.02 and dx < -0.5:
        return f"linear-gradient(to left, {stop_css})"
    return f"linear-gradient({angle:.2f}deg, {stop_css})"


def fill_to_background(fills: List[Dict[str, Any]]) -> Tuple[Optional[str], Optional[str]]:
    """Return (background, background_image) CSS values."""
    if not fills:
        return None, None
    fill = fills[0]
    ftype = fill.get("fillType") or "Color"
    if ftype == "Gradient":
        grad = gradient_to_css(fill.get("gradient") or {})
        return None, grad
    color = color_to_css(fill.get("color"))
    return color, None


def radius_to_css(radius: Optional[List[Optional[float]]]) -> Optional[str]:
    if not radius:
        return None
    vals = [float(v or 0) for v in radius]
    if len(vals) == 1:
        if vals[0] == 0:
            return None
        return f"{_px(vals[0])}"
    if len(vals) == 4:
        if all(v == 0 for v in vals):
            return None
        return " ".join(_px(v) for v in vals)
    # Sketch sometimes stores single corner list differently
    if all(v == vals[0] for v in vals):
        if vals[0] == 0:
            return None
        return _px(vals[0])
    return " ".join(_px(v) for v in vals)


def shadow_to_css(shadows: List[Dict[str, Any]]) -> Optional[str]:
    if not shadows:
        return None
    parts = []
    for sh in shadows:
        inset = "inset " if (sh.get("type") or "").lower() == "inner" else ""
        ox = float(sh.get("offsetX", 0) or 0)
        oy = float(sh.get("offsetY", 0) or 0)
        blur = float(sh.get("blurRadius", 0) or 0)
        spread = float(sh.get("spread", 0) or 0)
        color = color_to_css(sh.get("color"), "rgba(0,0,0,0.2)")
        parts.append(f"{inset}{_px(ox)} {_px(oy)} {_px(blur)} {_px(spread)} {color}")
    return ", ".join(parts) if parts else None


def border_to_css(borders: List[Dict[str, Any]]) -> Dict[str, str]:
    if not borders:
        return {}
    b = borders[0]
    thickness = float(b.get("thickness", 1) or 1)
    out: Dict[str, str] = {"border-width": _px(thickness), "border-style": "solid"}
    if (b.get("fillType") or "Color") == "Gradient":
        # CSS cannot do gradient borders easily; approximate with first/last stop color
        grad = b.get("gradient") or {}
        stops = grad.get("colorStops") or []
        if stops:
            out["border-color"] = color_to_css(stops[0].get("color"))
        else:
            out["border-color"] = "#000"
    else:
        out["border-color"] = color_to_css(b.get("color"), "#000")
    return out


def _px(v: float) -> str:
    if abs(v - round(v)) < 1e-6:
        return f"{int(round(v))}px"
    return f"{round(v, 2)}px"


def _rpx(v: float, design_width: float = 750.0) -> str:
    """WeChat rpx: 750 design width maps to 750rpx."""
    # MeaXure artboards are typically already 750 (iPhone @2x). Keep 1:1 as rpx.
    if abs(v - round(v)) < 1e-6:
        return f"{int(round(v))}rpx"
    return f"{round(v, 2)}rpx"


def font_weight_from_css(css_lines: List[str], font_face: Optional[str]) -> Optional[str]:
    for line in css_lines or []:
        if "font-weight" in line:
            return line.split(":", 1)[1].strip().rstrip(";")
    if font_face:
        lower = font_face.lower()
        if "semibold" in lower or "semi-bold" in lower or "medium" in lower:
            return "600"
        if "bold" in lower:
            return "700"
        if "light" in lower:
            return "300"
    return None


def layer_visual_styles(layer: Layer, unit: str = "px") -> Dict[str, str]:
    """Build visual (non-layout) CSS declarations for a layer."""
    u = _px if unit == "px" else (lambda v: _rpx(v))
    styles: Dict[str, str] = {}

    # Prefer numeric opacity field; ignore MeaXure measure css `opacity: 0`
    if layer.opacity is not None and abs(layer.opacity - 1.0) > 1e-6:
        styles["opacity"] = str(round(layer.opacity, 4))

    transforms: List[str] = []
    if layer.rotation:
        transforms.append(f"rotate({layer.rotation}deg)")

    bg, bg_image = fill_to_background(layer.fills)
    if bg:
        styles["background"] = bg
    if bg_image:
        styles["background-image"] = bg_image

    # Prefer MeaXure-provided css snippets for backgrounds / transforms
    if not bg and not bg_image:
        for line in layer.css or []:
            low = line.lower().strip()
            if low.startswith("background:") or low.startswith("background-image:"):
                key, val = line.split(":", 1)
                styles[key.strip()] = val.strip().rstrip(";")
            elif low.startswith("transform:"):
                val = line.split(":", 1)[1].strip().rstrip(";")
                # Avoid duplicating rotate already applied from rotation field
                if "rotate" in val.lower() and layer.rotation:
                    continue
                transforms.append(val)
            elif low.startswith("border-radius:"):
                val = line.split(":", 1)[1].strip().rstrip(";")
                if unit == "rpx":
                    val = val.replace("px", "rpx")
                styles["border-radius"] = val

    if transforms:
        styles["transform"] = " ".join(transforms)

    radius = radius_to_css(layer.radius)
    if radius:
        styles["border-radius"] = radius.replace("px", "rpx") if unit == "rpx" else radius
        # If unit conversion needed for multi values already with px from radius_to_css
        if unit == "rpx" and layer.radius:
            styles["border-radius"] = " ".join(
                u(float(v or 0)) for v in layer.radius
            ) if len(layer.radius) > 1 else u(float(layer.radius[0] or 0))

    # Ellipse heuristic: name or equal width/height with full radius
    if layer.type == "shape" and layer.name and "椭圆" in layer.name:
        styles["border-radius"] = "50%"

    shadow = shadow_to_css(layer.shadows)
    if shadow:
        if unit == "rpx":
            # rough replace px -> rpx in shadow string
            shadow = shadow.replace("px", "rpx")
        styles["box-shadow"] = shadow

    styles.update(border_to_css(layer.borders))
    if unit == "rpx":
        for k in list(styles):
            if k.startswith("border") and styles[k].endswith("px"):
                styles[k] = styles[k].replace("px", "rpx")

    if layer.type == "text":
        if layer.font_face:
            # Expand Sketch face names to a practical stack
            face = layer.font_face
            styles["font-family"] = (
                f'"{face}", "PingFang SC", "Hiragino Sans GB", '
                f'"WenQuanYi Micro Hei", "Droid Sans Fallback", '
                f'"Microsoft YaHei", sans-serif'
            )
        if layer.font_size is not None:
            styles["font-size"] = u(layer.font_size)
        if layer.color:
            styles["color"] = color_to_css(layer.color)
        if layer.text_align:
            styles["text-align"] = layer.text_align
        if layer.letter_spacing:
            styles["letter-spacing"] = u(layer.letter_spacing)
        if layer.line_height:
            styles["line-height"] = u(layer.line_height)
        elif (
            layer.font_size is not None
            and layer.rect.height
            and "\n" not in (layer.content or "")
            and layer.rect.height <= layer.font_size * 1.8
        ):
            # Single-line labels: match design box height
            styles["line-height"] = u(layer.rect.height)
        weight = font_weight_from_css(layer.css, layer.font_face)
        if weight:
            styles["font-weight"] = weight
        styles["white-space"] = "pre-wrap"
        styles["word-break"] = "break-word"
        # Absolute text boxes should not flex-center; keep Sketch metrics.
        styles["display"] = "block"

    return styles


def decls_to_css(decls: Dict[str, str]) -> str:
    return "; ".join(f"{k}: {v}" for k, v in decls.items() if v is not None) + (
        ";" if decls else ""
    )
