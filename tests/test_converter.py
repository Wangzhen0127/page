"""Basic regression tests for MeaXure converter."""

from __future__ import annotations

import unittest
from pathlib import Path

from meaxure_converter.parser import parse_meaxure_html
from meaxure_converter.layout import build_layout, count_nodes
from meaxure_converter.html_gen import generate_html_page
from meaxure_converter.miniprogram_gen import generate_miniprogram_files

ROOT = Path(__file__).resolve().parents[1]
SAMPLE = ROOT / "samples" / "meaxure-index.html"


class MeaXureConverterTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.doc = parse_meaxure_html(SAMPLE)
        cls.artboard = cls.doc.artboards[0]
        cls.tree = build_layout(cls.artboard)

    def test_parse_artboard(self) -> None:
        self.assertEqual(len(self.doc.artboards), 1)
        self.assertEqual(self.artboard.width, 750)
        self.assertGreater(len(self.artboard.layers), 50)

    def test_layout_uses_flex_and_assets(self) -> None:
        counts = count_nodes(self.tree)
        self.assertIn("row", counts)
        self.assertGreater(counts.get("asset", 0), 0)
        self.assertGreater(counts.get("text", 0), 0)
        # Page height should be preserved by flow margins
        y = 0.0
        for child in self.tree.children:
            if child.is_absolute:
                continue
            y += child.margin_top + child.height
        self.assertAlmostEqual(y, self.artboard.height, delta=2.0)

    def test_html_generation(self) -> None:
        html = generate_html_page(self.artboard, self.tree)
        self.assertIn("<!DOCTYPE html>", html)
        self.assertIn("display: flex", html)
        self.assertIn(self.artboard.name, html)

    def test_miniprogram_generation(self) -> None:
        files = generate_miniprogram_files(self.artboard, self.tree)
        self.assertIn("index.wxml", files)
        self.assertIn("index.wxss", files)
        self.assertIn("<view", files["index.wxml"])
        self.assertIn("rpx", files["index.wxss"])
        self.assertIn("position: absolute", files["index.wxss"])


if __name__ == "__main__":
    unittest.main()
