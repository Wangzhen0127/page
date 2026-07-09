"""Generate WeChat Mini Program pages from a flex layout tree."""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Dict, List, Optional

from .html_gen import ClassRegistry, _safe_asset_filename, copy_assets
from .layout import LayoutNode
from .parser import Artboard, MeaXureDocument
from .styles import decls_to_css, layer_visual_styles, _rpx


def _size_decls_rpx(node: LayoutNode) -> Dict[str, str]:
    return {
        "width": _rpx(node.width),
        "height": _rpx(node.height),
        "box-sizing": "border-box",
        "flex-shrink": "0",
    }


def _flow_decls_rpx(node: LayoutNode) -> Dict[str, str]:
    decls: Dict[str, str] = {}
    if node.margin_top:
        decls["margin-top"] = _rpx(node.margin_top)
    if node.margin_left:
        decls["margin-left"] = _rpx(node.margin_left)
    return decls


def render_node_wxml(
    node: LayoutNode,
    registry: ClassRegistry,
    asset_url_prefix: str,
) -> str:
    if node.kind == "asset":
        return _render_asset(node, registry, asset_url_prefix)
    if node.kind == "text":
        return _render_text(node, registry)
    if node.kind == "shape":
        return _render_shape(node, registry)
    return _render_container(node, registry, asset_url_prefix)


def _absolute_decls_rpx(node: LayoutNode) -> Dict[str, str]:
    if not node.is_absolute:
        return {}
    return {
        "position": "absolute",
        "left": _rpx(node.abs_left or 0),
        "top": _rpx(node.abs_top or 0),
    }


def _escape_text(text: str) -> str:
    return (
        (text or "")
        .replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
    )


def _render_asset(node: LayoutNode, registry: ClassRegistry, prefix: str) -> str:
    layer = node.layer
    assert layer is not None
    asset = layer.primary_asset
    src = ""
    if asset:
        src = f"{prefix.rstrip('/')}/{_safe_asset_filename(asset.path)}"
    decls = {
        "position": "absolute",
        "left": _rpx(node.abs_left or 0),
        "top": _rpx(node.abs_top or 0),
        "width": _rpx(node.width),
        "height": _rpx(node.height),
        "display": "block",
    }
    if layer.opacity is not None and abs(layer.opacity - 1) > 1e-6:
        decls["opacity"] = str(round(layer.opacity, 4))
    if layer.rotation:
        decls["transform"] = f"rotate({layer.rotation}deg)"
    cls = registry.add("asset", decls)
    return f'<image class="{cls}" src="{src}" mode="aspectFit" />'


def _render_text(node: LayoutNode, registry: ClassRegistry) -> str:
    layer = node.layer
    assert layer is not None
    decls = _size_decls_rpx(node)
    if node.is_absolute:
        decls.update(_absolute_decls_rpx(node))
    else:
        decls.update(_flow_decls_rpx(node))
    decls.update(layer_visual_styles(layer, unit="rpx"))
    decls.setdefault("display", "flex")
    decls.setdefault("align-items", "center")
    cls = registry.add("text", decls)
    content = _escape_text(layer.content or "")
    return f'<view class="{cls}">{content}</view>'


def _render_shape(node: LayoutNode, registry: ClassRegistry) -> str:
    layer = node.layer
    assert layer is not None
    decls = _size_decls_rpx(node)
    if node.is_absolute:
        decls.update(_absolute_decls_rpx(node))
    else:
        decls.update(_flow_decls_rpx(node))
    decls.update(layer_visual_styles(layer, unit="rpx"))
    decls.setdefault("display", "block")
    cls = registry.add("shape", decls)
    return f'<view class="{cls}"></view>'


def _render_container(
    node: LayoutNode, registry: ClassRegistry, prefix: str
) -> str:
    decls: Dict[str, str] = {
        "display": "flex",
        "flex-direction": "row" if node.direction == "row" else "column",
        "position": "relative",
        "box-sizing": "border-box",
        "flex-shrink": "0",
    }
    if node.kind == "artboard":
        decls["width"] = _rpx(node.width)
        decls["min-height"] = _rpx(node.height)
        decls["overflow"] = "hidden"
        decls["background"] = "#ffffff"
    else:
        decls["width"] = _rpx(node.width)
        decls["height"] = _rpx(node.height)
        if node.is_absolute:
            decls.update(_absolute_decls_rpx(node))
        else:
            decls.update(_flow_decls_rpx(node))

    if node.layer:
        decls.update(layer_visual_styles(node.layer, unit="rpx"))

    if (
        node.meta.get("position_relative")
        or any(c.kind == "asset" or c.is_absolute for c in node.children)
    ):
        decls["position"] = "relative"

    prefix_name = {"artboard": "page", "group": "group", "row": "row"}.get(
        node.kind, "box"
    )
    cls = registry.add(prefix_name, decls)
    inner = "\n".join(
        render_node_wxml(child, registry, prefix) for child in node.children
    )
    if inner:
        inner = "\n".join("  " + line for line in inner.splitlines())
        return f'<view class="{cls}">\n{inner}\n</view>'
    return f'<view class="{cls}"></view>'


def generate_miniprogram_files(
    artboard: Artboard,
    tree: LayoutNode,
    page_name: str = "index",
    asset_url_prefix: str = "/assets",
) -> Dict[str, str]:
    registry = ClassRegistry()
    wxml = render_node_wxml(tree, registry, asset_url_prefix)
    wxss = registry.stylesheet()
    js = (
        f"Page({{\n"
        f"  data: {{\n"
        f"    title: {json.dumps(artboard.name, ensure_ascii=False)}\n"
        f"  }}\n"
        f"}});\n"
    )
    json_cfg = json.dumps(
        {
            "navigationBarTitleText": artboard.name,
            "usingComponents": {},
        },
        ensure_ascii=False,
        indent=2,
    )
    return {
        f"{page_name}.wxml": wxml + "\n",
        f"{page_name}.wxss": (
            "page {\n  background: #f5f5f5;\n}\n\n" + wxss + "\n"
        ),
        f"{page_name}.js": js,
        f"{page_name}.json": json_cfg + "\n",
    }


def export_miniprogram(
    doc: MeaXureDocument,
    tree: LayoutNode,
    artboard: Artboard,
    out_dir: Path,
    page_name: str = "index",
    with_app_scaffold: bool = True,
) -> Path:
    out_dir = Path(out_dir)
    pages_dir = out_dir / "pages" / page_name
    pages_dir.mkdir(parents=True, exist_ok=True)
    assets_dir = out_dir / "assets"
    copy_assets(doc, tree, assets_dir)

    files = generate_miniprogram_files(
        artboard, tree, page_name=page_name, asset_url_prefix="/assets"
    )
    for fname, content in files.items():
        (pages_dir / fname).write_text(content, encoding="utf-8")

    if with_app_scaffold:
        app_js = "App({});\n"
        app_json = {
            "pages": [f"pages/{page_name}/{page_name}"],
            "window": {
                "navigationBarTitleText": artboard.name,
                "navigationBarBackgroundColor": "#ffffff",
                "navigationBarTextStyle": "black",
                "backgroundColor": "#f5f5f5",
            },
        }
        (out_dir / "app.js").write_text(app_js, encoding="utf-8")
        (out_dir / "app.json").write_text(
            json.dumps(app_json, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        (out_dir / "app.wxss").write_text(
            "page { background: #f5f5f5; }\n", encoding="utf-8"
        )
        (out_dir / "project.config.json").write_text(
            json.dumps(
                {
                    "description": f"MeaXure export: {artboard.name}",
                    "packOptions": {"ignore": []},
                    "setting": {
                        "urlCheck": False,
                        "es6": True,
                        "postcss": True,
                        "minified": True,
                    },
                    "compileType": "miniprogram",
                    "appid": "touristappid",
                    "projectname": re.sub(r"\W+", "-", artboard.name) or "meaxure",
                },
                ensure_ascii=False,
                indent=2,
            )
            + "\n",
            encoding="utf-8",
        )
        (out_dir / "sitemap.json").write_text(
            json.dumps({"rules": [{"action": "allow", "page": "*"}]}, indent=2)
            + "\n",
            encoding="utf-8",
        )
    return pages_dir
