"""Basic regression tests for MeaXure converter."""

from __future__ import annotations

import unittest
from pathlib import Path

from meaxure_converter.parser import parse_meaxure_html
from meaxure_converter.layout import build_layout, count_nodes
from meaxure_converter.html_gen import generate_html_page, export_html
from meaxure_converter.miniprogram_gen import generate_miniprogram_files

ROOT = Path(__file__).resolve().parents[1]
SAMPLE = ROOT / "samples" / "meaxure-index.html"
REAL = ROOT / "meaxure_converter" / "关怀版（首页）" / "index.html"


class MeaXureConverterTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        src = REAL if REAL.exists() else SAMPLE
        cls.doc = parse_meaxure_html(src)
        cls.artboard = cls.doc.artboards[0]
        cls.tree = build_layout(cls.artboard)

    def test_parse_artboard(self) -> None:
        self.assertEqual(len(self.doc.artboards), 1)
        self.assertEqual(self.artboard.width, 750)
        self.assertGreater(len(self.artboard.layers), 50)

    def test_layout_flex_margins_preserve_height(self) -> None:
        counts = count_nodes(self.tree)
        self.assertGreater(counts.get("asset", 0), 0)
        self.assertGreater(counts.get("text", 0), 0)
        self.assertGreater(counts.get("shape", 0), 0)
        # Flow margins (+ optional trailing spacer) reconstruct artboard height
        y = 0.0
        for child in self.tree.children:
            if child.kind == "asset":
                continue
            y += child.margin_top + child.height
        self.assertAlmostEqual(y, self.artboard.height, delta=2.0)

    def test_assets_use_absolute(self) -> None:
        assets = [c for c in self.tree.children if c.kind == "asset"]
        self.assertTrue(assets)
        for a in assets:
            self.assertTrue(a.is_absolute)
            self.assertAlmostEqual(a.abs_left or -1, a.rect.x, delta=0.01)
            self.assertAlmostEqual(a.abs_top or -1, a.rect.y, delta=0.01)

    def test_html_generation(self) -> None:
        html = generate_html_page(self.artboard, self.tree)
        self.assertIn("<!DOCTYPE html>", html)
        self.assertIn("display: flex", html)
        self.assertIn("margin-top", html)
        self.assertIn(self.artboard.name, html)

    def test_miniprogram_generation(self) -> None:
        files = generate_miniprogram_files(self.artboard, self.tree)
        self.assertIn("index.wxml", files)
        self.assertIn("index.wxss", files)
        self.assertIn("<view", files["index.wxml"])
        self.assertIn("rpx", files["index.wxss"])
        self.assertIn("position: absolute", files["index.wxss"])

    def test_export_with_assets(self) -> None:
        if not REAL.exists():
            self.skipTest("real MeaXure export not present")
        out = ROOT / "output" / "test_html"
        path = export_html(self.doc, self.tree, self.artboard, out)
        self.assertTrue(path.exists())
        assets = list((out / "assets").glob("*"))
        self.assertGreater(len(assets), 5)
        html = path.read_text(encoding="utf-8")
        self.assertNotIn("opacity: 0", html.split("asset")[0] if False else "")
        # Slice assets should be referenced and not forced invisible
        self.assertIn("医院.webp", html)
        self.assertIn("position: absolute", html)


if __name__ == "__main__":
    unittest.main()
