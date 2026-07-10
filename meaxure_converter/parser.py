"""Parse Sketch MeaXure exported HTML into structured artboard data."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Union
from urllib.parse import unquote


DATA_RE = re.compile(r"let\s+data\s*=\s*(\{.*?\});\s*(?:\n|$)", re.DOTALL)


@dataclass
class Rect:
    x: float
    y: float
    width: float
    height: float

    @classmethod
    def from_dict(cls, d: Dict[str, Any]) -> "Rect":
        return cls(
            x=float(d.get("x", 0) or 0),
            y=float(d.get("y", 0) or 0),
            width=float(d.get("width", 0) or 0),
            height=float(d.get("height", 0) or 0),
        )

    def right(self) -> float:
        return self.x + self.width

    def bottom(self) -> float:
        return self.y + self.height

    def contains(self, other: "Rect", pad: float = 1.0) -> bool:
        return (
            other.x >= self.x - pad
            and other.y >= self.y - pad
            and other.right() <= self.right() + pad
            and other.bottom() <= self.bottom() + pad
        )

    def intersects(self, other: "Rect", min_overlap: float = 2.0) -> bool:
        ox = min(self.right(), other.right()) - max(self.x, other.x)
        oy = min(self.bottom(), other.bottom()) - max(self.y, other.y)
        return ox > min_overlap and oy > min_overlap

    def as_dict(self) -> Dict[str, float]:
        return {"x": self.x, "y": self.y, "width": self.width, "height": self.height}


@dataclass
class Exportable:
    name: str
    format: str
    path: str


@dataclass
class Layer:
    object_id: str
    type: str
    name: str
    rect: Rect
    opacity: float = 1.0
    rotation: float = 0.0
    css: List[str] = field(default_factory=list)
    fills: List[Dict[str, Any]] = field(default_factory=list)
    borders: List[Dict[str, Any]] = field(default_factory=list)
    shadows: List[Dict[str, Any]] = field(default_factory=list)
    radius: Optional[List[Optional[float]]] = None
    content: Optional[str] = None
    font_face: Optional[str] = None
    font_size: Optional[float] = None
    letter_spacing: Optional[float] = None
    line_height: Optional[float] = None
    text_align: Optional[str] = None
    color: Optional[Dict[str, Any]] = None
    exportable: List[Exportable] = field(default_factory=list)
    style_name: Optional[str] = None
    raw: Dict[str, Any] = field(default_factory=dict, repr=False)

    @property
    def is_asset(self) -> bool:
        return self.type == "slice" and bool(self.exportable)

    @property
    def primary_asset(self) -> Optional[Exportable]:
        if not self.exportable:
            return None
        # Prefer @2x png, then any @2x, then webp/png, then first
        ranked = sorted(
            self.exportable,
            key=lambda e: (
                0 if "@2x" in e.path else 1,
                0 if e.format.lower() == "png" else 1,
                0 if e.format.lower() in ("webp", "jpg", "jpeg") else 2,
                e.path,
            ),
        )
        return ranked[0]


@dataclass
class Artboard:
    name: str
    slug: str
    object_id: str
    width: float
    height: float
    page_name: str
    image_path: Optional[str]
    layers: List[Layer]
    notes: List[Dict[str, Any]] = field(default_factory=list)


@dataclass
class MeaXureDocument:
    source_path: Path
    resolution: float
    unit: str
    color_format: str
    artboards: List[Artboard]
    slices: List[Layer]
    colors: List[Dict[str, Any]]
    languages: Dict[str, Any]
    raw: Dict[str, Any] = field(default_factory=dict, repr=False)

    @property
    def assets_dir(self) -> Path:
        return self.source_path.parent / "assets"

    @property
    def preview_dir(self) -> Path:
        return self.source_path.parent / "preview"


def _parse_exportable(items: Any) -> List[Exportable]:
    out: List[Exportable] = []
    if not items:
        return out
    for item in items:
        out.append(
            Exportable(
                name=str(item.get("name", "")),
                format=str(item.get("format", "")),
                path=str(item.get("path", "")),
            )
        )
    return out


def _parse_layer(d: Dict[str, Any]) -> Layer:
    rect = Rect.from_dict(d.get("rect") or {})
    radius = d.get("radius")
    if radius is not None and not isinstance(radius, list):
        radius = [radius]
    return Layer(
        object_id=str(d.get("objectID", "")),
        type=str(d.get("type", "shape")),
        name=str(d.get("name", "")),
        rect=rect,
        opacity=float(d.get("opacity", 1) or 1),
        rotation=float(d.get("rotation", 0) or 0),
        css=list(d.get("css") or []),
        fills=list(d.get("fills") or []),
        borders=list(d.get("borders") or []),
        shadows=list(d.get("shadows") or []),
        radius=radius,
        content=d.get("content"),
        font_face=d.get("fontFace"),
        font_size=float(d["fontSize"]) if d.get("fontSize") is not None else None,
        letter_spacing=float(d["letterSpacing"])
        if d.get("letterSpacing") is not None
        else None,
        line_height=float(d["lineHeight"]) if d.get("lineHeight") is not None else None,
        text_align=d.get("textAlign"),
        color=d.get("color"),
        exportable=_parse_exportable(d.get("exportable")),
        style_name=d.get("styleName"),
        raw=d,
    )


def _parse_artboard(d: Dict[str, Any]) -> Artboard:
    layers = [_parse_layer(l) for l in (d.get("layers") or [])]
    return Artboard(
        name=str(d.get("name", "Artboard")),
        slug=str(d.get("slug", "artboard")),
        object_id=str(d.get("objectID", "")),
        width=float(d.get("width", 0) or 0),
        height=float(d.get("height", 0) or 0),
        page_name=str(d.get("pageName", "")),
        image_path=unquote(d["imagePath"]) if d.get("imagePath") else None,
        layers=layers,
        notes=list(d.get("notes") or []),
    )


def extract_data_json(html_text: str) -> Dict[str, Any]:
    match = DATA_RE.search(html_text)
    if not match:
        raise ValueError(
            "Cannot find `let data = {...}` in MeaXure HTML. "
            "Please provide a Sketch MeaXure Web Export index.html."
        )
    return json.loads(match.group(1))


def parse_meaxure_html(path: Union[str, Path]) -> MeaXureDocument:
    path = Path(path).resolve()
    text = path.read_text(encoding="utf-8", errors="replace")
    raw = extract_data_json(text)
    artboards = [_parse_artboard(a) for a in (raw.get("artboards") or [])]
    slices = [_parse_layer(s) for s in (raw.get("slices") or [])]
    return MeaXureDocument(
        source_path=path,
        resolution=float(raw.get("resolution", 1) or 1),
        unit=str(raw.get("unit", "px")),
        color_format=str(raw.get("colorFormat", "color-hex")),
        artboards=artboards,
        slices=slices,
        colors=list(raw.get("colors") or []),
        languages=dict(raw.get("languages") or {}),
        raw=raw,
    )


def dump_document_summary(doc: MeaXureDocument) -> str:
    lines = [
        f"source: {doc.source_path}",
        f"resolution: {doc.resolution} unit: {doc.unit}",
        f"artboards: {len(doc.artboards)} slices: {len(doc.slices)}",
    ]
    for i, ab in enumerate(doc.artboards):
        types: Dict[str, int] = {}
        for layer in ab.layers:
            types[layer.type] = types.get(layer.type, 0) + 1
        lines.append(
            f"  [{i}] {ab.name} ({ab.width}x{ab.height}) layers={len(ab.layers)} {types}"
        )
    return "\n".join(lines)
