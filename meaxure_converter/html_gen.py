"""Generate native HTML from a flex layout tree."""

from __future__ import annotations

import html
import re
import shutil
from pathlib import Path
from typing import Dict, List, Optional, Set

from .layout import LayoutNode, iter_assets
from .parser import Artboard, MeaXureDocument
from .preview_crop import crop_preview_layers, upgrade_assets_from_preview
from .gradient_sample import apply_sampled_gradients
from .styles import decls_to_css, layer_visual_styles, _px


def _safe_asset_filename(path: str) -> str:
    base = Path(path).name
    return base.replace("\\", "_").replace("/", "_")


class ClassRegistry:
    def __init__(self) -> None:
        self._rules: Dict[str, Dict[str, str]] = {}
        self._counter = 0

    def add(self, prefix: str, decls: Dict[str, str]) -> str:
        key = decls_to_css(decls)
        for name, existing in self._rules.items():
            if decls_to_css(existing) == key and name.startswith(prefix):
                return name
        self._counter += 1
        name = f"{prefix}-{self._counter}"
        self._rules[name] = dict(decls)
        return name

    def stylesheet(self) -> str:
        lines = []
        for name, decls in self._rules.items():
            body = "\n".join(f"  {k}: {v};" for k, v in decls.items())
            lines.append(f".{name} {{\n{body}\n}}")
        return "\n\n".join(lines)


def _size_decls(node: LayoutNode) -> Dict[str, str]:
    return {
        "width": _px(node.width),
        "height": _px(node.height),
        "box-sizing": "border-box",
        "flex-shrink": "0",
    }


def _flow_decls(node: LayoutNode) -> Dict[str, str]:
    decls: Dict[str, str] = {}
    if node.margin_top:
        decls["margin-top"] = _px(node.margin_top)
    if node.margin_left:
        decls["margin-left"] = _px(node.margin_left)
    return decls


def _absolute_decls(node: LayoutNode) -> Dict[str, str]:
    if not node.is_absolute:
        return {}
    return {
        "position": "absolute",
        "left": _px(node.abs_left or 0),
        "top": _px(node.abs_top or 0),
    }


def _z_decls(node: LayoutNode) -> Dict[str, str]:
    if node.z_index:
        return {"z-index": str(node.z_index)}
    return {}


def render_node_html(
    node: LayoutNode,
    registry: ClassRegistry,
    asset_url_prefix: str,
) -> str:
    if node.kind == "asset":
        return _render_asset(node, registry, asset_url_prefix)
    if node.kind == "text":
        return _render_text(node, registry)
    if node.kind == "shape":
        return _render_shape(node, registry, asset_url_prefix)
    if node.kind == "spacer":
        return _render_spacer(node, registry)
    return _render_container(node, registry, asset_url_prefix)


def _render_spacer(node: LayoutNode, registry: ClassRegistry) -> str:
    decls = {
        "width": "1px",
        "height": _px(node.height),
        "flex-shrink": "0",
        "opacity": "0",
        "pointer-events": "none",
    }
    if node.margin_top:
        decls["margin-top"] = _px(node.margin_top)
    cls = registry.add("spacer", decls)
    return f'<div class="{cls}" aria-hidden="true"></div>'


def _render_asset(node: LayoutNode, registry: ClassRegistry, prefix: str) -> str:
    layer = node.layer
    assert layer is not None
    asset = layer.primary_asset
    src = ""
    override = node.meta.get("asset_src")
    if override:
        src = f"{prefix.rstrip('/')}/{override}"
    elif asset:
        src = f"{prefix.rstrip('/')}/{_safe_asset_filename(asset.path)}"
    decls = {
        "position": "absolute",
        "left": _px(node.abs_left if node.abs_left is not None else node.rect.x),
        "top": _px(node.abs_top if node.abs_top is not None else node.rect.y),
        "width": _px(node.width),
        "height": _px(node.height),
        "display": "block",
        "object-fit": "fill",
        "pointer-events": "none",
    }
    decls.update(_z_decls(node))
    # Slice css often has opacity:0 (MeaXure measure). Prefer layer.opacity field.
    if (
        not node.meta.get("force_visible")
        and layer.opacity is not None
        and abs(layer.opacity - 1) > 1e-6
    ):
        decls["opacity"] = str(round(layer.opacity, 4))
    if layer.rotation:
        decls["transform"] = f"rotate({layer.rotation}deg)"
    cls = registry.add("asset", decls)
    alt = html.escape(layer.name or "")
    oid = html.escape(layer.object_id or "")
    return (
        f'<img class="{cls}" src="{html.escape(src)}" alt="{alt}" '
        f'data-kind="asset" data-oid="{oid}" />'
    )


def _render_text(node: LayoutNode, registry: ClassRegistry) -> str:
    layer = node.layer
    assert layer is not None
    decls = _size_decls(node)
    if node.is_absolute:
        decls.update(_absolute_decls(node))
    else:
        decls.update(_flow_decls(node))
    decls.update(layer_visual_styles(layer, unit="px"))
    decls.update(_z_decls(node))
    # Keep MeaXure text metrics; do NOT flex-center (distorts line boxes).
    decls["display"] = "block"
    decls["overflow"] = "hidden"
    if not node.is_absolute:
        decls["position"] = "relative"
    cls = registry.add("text", decls)
    oid = html.escape(layer.object_id or "")
    content = html.escape(layer.content or "").replace("\n", "<br />")
    return f'<div class="{cls}" data-kind="text" data-oid="{oid}">{content}</div>'


def _render_shape(
    node: LayoutNode, registry: ClassRegistry, prefix: str
) -> str:
    layer = node.layer
    assert layer is not None
    decls = _size_decls(node)
    if node.is_absolute:
        decls.update(_absolute_decls(node))
    else:
        decls.update(_flow_decls(node))
    if not node.meta.get("suppress_fill"):
        decls.update(layer_visual_styles(layer, unit="px"))
        sampled = node.meta.get("sampled_gradient")
        if sampled:
            decls["background-image"] = str(sampled)
            decls.pop("background", None)
    else:
        # Keep opacity / transform / radius without solid fill under crop
        styles = layer_visual_styles(layer, unit="px")
        for k in ("opacity", "transform", "border-radius"):
            if k in styles:
                decls[k] = styles[k]
    decls.update(_z_decls(node))
    if not node.is_absolute:
        decls["position"] = "relative"
    decls.setdefault("display", "block")
    decls["overflow"] = "hidden"

    crop_src = node.meta.get("crop_src")
    if crop_src:
        decls["background-image"] = f'url("{prefix.rstrip("/")}/{crop_src}")'
        decls["background-size"] = "100% 100%"
        decls["background-repeat"] = "no-repeat"
        # Component crops have transparent holes where editable text renders.
        # Keep the original CSS fill beneath those holes.
        if not node.meta.get("component_crop"):
            decls.pop("background", None)
        for k in list(decls):
            if k.startswith("border"):
                decls.pop(k, None)
    cls = registry.add("shape", decls)
    oid = html.escape(layer.object_id or "")
    title = html.escape(layer.name or "")
    return f'<div class="{cls}" data-kind="shape" data-oid="{oid}" title="{title}"></div>'


def _render_container(
    node: LayoutNode, registry: ClassRegistry, prefix: str
) -> str:
    mode = str(node.meta.get("layout_mode") or node.kind)
    decls: Dict[str, str] = {
        "box-sizing": "border-box",
        "flex-shrink": "0",
    }

    if node.kind == "artboard" or mode == "absolute":
        decls["display"] = "block"
        decls["position"] = "relative"
    elif mode == "grid" or node.kind == "grid":
        cols = int(node.meta.get("grid_columns") or 2)
        gap = float(node.meta.get("grid_gap") or node.gap or 0)
        decls["display"] = "grid"
        decls["position"] = "relative"
        decls["grid-template-columns"] = f"repeat({cols}, 1fr)"
        if gap > 0:
            decls["gap"] = _px(gap)
    elif mode == "overlay" or node.kind == "overlay":
        decls["display"] = "block"
        decls["position"] = "relative"
    else:
        decls["display"] = "flex"
        decls["position"] = "relative"
        decls["flex-direction"] = (
            "row" if node.direction == "row" or node.kind == "row" else "column"
        )

    if node.kind == "artboard":
        decls["width"] = _px(node.width)
        decls["min-height"] = _px(node.height)
        decls["height"] = _px(node.height)
        decls["margin"] = "0 auto"
        decls["overflow"] = "hidden"
        decls["background"] = str(node.meta.get("background") or "#EFF4FB")
    else:
        decls["width"] = _px(node.width)
        decls["height"] = _px(node.height)
        if node.is_absolute:
            decls.update(_absolute_decls(node))
        else:
            decls.update(_flow_decls(node))

    if node.layer and not node.meta.get("suppress_fill"):
        decls.update(layer_visual_styles(node.layer, unit="px"))
        sampled = node.meta.get("sampled_gradient")
        if sampled:
            decls["background-image"] = str(sampled)
            decls.pop("background", None)

    prefix_name = {
        "artboard": "page",
        "group": "group",
        "row": "row",
        "grid": "grid",
        "overlay": "overlay",
    }.get(node.kind, "box")
    cls = registry.add(prefix_name, decls)

    # Preserve MeaXure paint order (z_index ascending).
    ordered = sorted(node.children, key=lambda c: c.z_index)
    inner = "\n".join(render_node_html(child, registry, prefix) for child in ordered)
    if inner:
        inner = "\n".join("  " + line for line in inner.splitlines())
        return f'<div class="{cls}" data-kind="{html.escape(node.kind)}">\n{inner}\n</div>'
    return f'<div class="{cls}" data-kind="{html.escape(node.kind)}"></div>'


def generate_html_page(
    artboard: Artboard,
    tree: LayoutNode,
    title: Optional[str] = None,
    asset_url_prefix: str = "./assets",
) -> str:
    registry = ClassRegistry()
    body = render_node_html(tree, registry, asset_url_prefix)
    css = registry.stylesheet()
    page_title = html.escape(title or artboard.name)
    design_w = int(round(artboard.width)) or 750
    design_h = int(round(artboard.height)) or 0
    return f"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1, maximum-scale=1, user-scalable=no" />
  <title>{page_title}</title>
  <style>
* {{ margin: 0; padding: 0; box-sizing: border-box; }}
html, body {{
  background: #f5f5f5;
  font-family: "PingFang SC", "PingFangSC-Regular", "Hiragino Sans GB",
    "WenQuanYi Micro Hei", "Droid Sans Fallback", "Microsoft YaHei", sans-serif;
  -webkit-font-smoothing: antialiased;
  -moz-osx-font-smoothing: grayscale;
  text-rendering: geometricPrecision;
}}
img {{ max-width: none; }}
.stage {{
  width: 100%;
  overflow: hidden;
}}
.stage-inner {{
  width: {design_w}px;
  height: {design_h}px;
  transform-origin: top left;
}}
{css}
  </style>
</head>
<body>
<div class="stage">
  <div class="stage-inner" id="stage-inner">
{body}
  </div>
</div>
<script>
(function () {{
  var designW = {design_w};
  var designH = {design_h};
  var inner = document.getElementById("stage-inner");
  var stage = inner && inner.parentElement;
  function fit() {{
    if (!inner || !stage) return;
    var scale = Math.min(1, stage.clientWidth / designW);
    inner.style.transform = "scale(" + scale + ")";
    stage.style.height = (designH * scale) + "px";
  }}
  fit();
  window.addEventListener("resize", fit);
}})();
</script>
</body>
</html>
"""


def copy_assets(
    doc: MeaXureDocument,
    tree: LayoutNode,
    dest_assets: Path,
) -> List[str]:
    dest_assets.mkdir(parents=True, exist_ok=True)
    src_dir = doc.assets_dir
    copied: List[str] = []
    missing: List[str] = []
    seen: Set[str] = set()
    for node in iter_assets(tree):
        layer = node.layer
        if not layer or not layer.primary_asset:
            continue
        for exp in layer.exportable:
            fname = _safe_asset_filename(exp.path)
            if fname in seen:
                continue
            seen.add(fname)
            src = src_dir / exp.path
            if not src.exists():
                alt = src_dir / Path(exp.path).name
                src = alt if alt.exists() else src
            if src.exists():
                shutil.copy2(src, dest_assets / fname)
                copied.append(fname)
            else:
                missing.append(exp.path)
    if missing:
        (dest_assets / "_missing_assets.txt").write_text(
            "\n".join(missing), encoding="utf-8"
        )
    return copied


def _apply_enhancements(
    doc: MeaXureDocument,
    artboard: Artboard,
    tree: LayoutNode,
    assets_dir: Path,
) -> None:
    """Preview-based fidelity upgrades shared by HTML / miniprogram export."""
    copy_assets(doc, tree, assets_dir)
    crop_preview_layers(doc, artboard, tree, assets_dir)
    upgrade_assets_from_preview(doc, artboard, tree, assets_dir)

    # Sampling from a flattened preview is safe only for broad backgrounds.
    # Sampling card gradients picks up overlaid artwork/text and produces
    # saturated blocks. Card gradients should use their original Sketch stops.
    broad_backgrounds = [
        layer
        for layer in artboard.layers
        if layer.rect.width >= artboard.width * 0.80
        and layer.rect.height >= 80
    ]
    sampled = apply_sampled_gradients(doc, artboard, broad_backgrounds)
    if sampled:

        def walk(n: LayoutNode) -> None:
            if n.layer and n.layer.object_id in sampled and not n.meta.get("crop_src"):
                n.meta["sampled_gradient"] = sampled[n.layer.object_id]
            for c in n.children:
                walk(c)

        walk(tree)


def export_html(
    doc: MeaXureDocument,
    tree: LayoutNode,
    artboard: Artboard,
    out_dir: Path,
) -> Path:
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    assets_dir = out_dir / "assets"
    _apply_enhancements(doc, artboard, tree, assets_dir)
    html_text = generate_html_page(artboard, tree, asset_url_prefix="./assets")
    out_file = out_dir / "index.html"
    out_file.write_text(html_text, encoding="utf-8")
    return out_file
