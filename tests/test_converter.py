"""Regression tests for MeaXure converter."""

from __future__ import annotations

import unittest
from pathlib import Path

from meaxure_converter.cli import main as cli_main
from meaxure_converter.fidelity import resolve_preview_path, slugify_page
from meaxure_converter.html_gen import export_html, generate_html_page
from meaxure_converter.layout import build_layout, count_nodes
from meaxure_converter.miniprogram_gen import generate_miniprogram_files
from meaxure_converter.parser import parse_meaxure_html

ROOT = Path(__file__).resolve().parents[1]
SAMPLE = ROOT / "samples" / "meaxure-index.html"
REAL_HOME = ROOT / "meaxure_converter" / "关怀版（首页）" / "index.html"
REAL_FULL = ROOT / "关怀版" / "index.html"


class MeaXureConverterTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        src = REAL_HOME if REAL_HOME.exists() else SAMPLE
        cls.doc = parse_meaxure_html(src)
        cls.artboard = cls.doc.artboards[0]
        cls.tree = build_layout(cls.artboard)

    def test_parse_artboard(self) -> None:
        self.assertGreaterEqual(len(self.doc.artboards), 1)
        self.assertEqual(self.artboard.width, 750)
        self.assertGreater(len(self.artboard.layers), 20)

    def test_layout_flex_margins_preserve_height(self) -> None:
        counts = count_nodes(self.tree)
        self.assertGreater(counts.get("text", 0) + counts.get("shape", 0), 0)
        y = 0.0
        for child in self.tree.children:
            if child.kind == "asset":
                continue
            y += child.margin_top + child.height
        self.assertAlmostEqual(y, self.artboard.height, delta=2.0)

    def test_html_generation(self) -> None:
        html = generate_html_page(self.artboard, self.tree)
        self.assertIn("<!DOCTYPE html>", html)
        self.assertIn("display: flex", html)

    def test_miniprogram_generation(self) -> None:
        files = generate_miniprogram_files(self.artboard, self.tree)
        self.assertIn("index.wxml", files)
        self.assertIn("rpx", files["index.wxss"])


class MultiArtboardFidelityTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        if not REAL_FULL.exists():
            raise unittest.SkipTest("full 关怀版 export not present")
        cls.doc = parse_meaxure_html(REAL_FULL)

    def test_parses_all_artboards(self) -> None:
        self.assertGreaterEqual(len(self.doc.artboards), 2)
        for ab in self.doc.artboards:
            preview = resolve_preview_path(self.doc, ab)
            self.assertIsNotNone(preview, msg=f"missing preview for {ab.slug}")
            self.assertTrue(preview.exists())

    def test_cli_fidelity_exports_each_artboard(self) -> None:
        out = ROOT / "output" / "test_fidelity"
        if out.exists():
            import shutil

            shutil.rmtree(out)
        code = cli_main(
            [str(REAL_FULL), "-o", str(out), "-f", "html", "--mode", "fidelity", "-a", "1"]
        )
        self.assertEqual(code, 0)
        pages = list((out / "html" / "pages").glob("*.html"))
        self.assertEqual(len(pages), 1)
        html = pages[0].read_text(encoding="utf-8")
        self.assertIn("page-preview", html)
        self.assertIn("preview_", html)
        assets = list((out / "html" / "assets").glob("preview_*"))
        self.assertGreaterEqual(len(assets), 1)

    def test_cli_exports_all_artboards(self) -> None:
        out = ROOT / "output" / "test_fidelity_all"
        if out.exists():
            import shutil

            shutil.rmtree(out)
        code = cli_main(
            [str(REAL_FULL), "-o", str(out), "-f", "html", "--mode", "fidelity"]
        )
        self.assertEqual(code, 0)
        pages = list((out / "html" / "pages").glob("*.html"))
        self.assertEqual(len(pages), len(self.doc.artboards))
        gallery = out / "html" / "index.html"
        self.assertTrue(gallery.exists())
        text = gallery.read_text(encoding="utf-8")
        self.assertIn("画板", text)
        # each page references its own preview
        for page in pages[:3]:
            body = page.read_text(encoding="utf-8")
            self.assertIn("page-preview", body)


if __name__ == "__main__":
    unittest.main()
