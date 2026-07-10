"""Regression tests for MeaXure converter."""

from __future__ import annotations

import re
import unittest
from pathlib import Path

from meaxure_converter.cli import main as cli_main
from meaxure_converter.fidelity import resolve_preview_path
from meaxure_converter.html_gen import generate_html_page
from meaxure_converter.layout import (
    build_layout,
    count_nodes,
    iter_preview_crops,
    summarize_render_stats,
)
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
        cls.tree = build_layout(cls.artboard, cls.doc.slices)

    def test_parse_artboard(self) -> None:
        self.assertGreaterEqual(len(self.doc.artboards), 1)
        self.assertEqual(self.artboard.width, 750)
        self.assertGreater(len(self.artboard.layers), 20)

    def test_absolute_layout_preserves_coords(self) -> None:
        counts = count_nodes(self.tree)
        self.assertGreater(counts.get("text", 0) + counts.get("shape", 0), 0)
        # Flat absolute tree: no inferred flex containers
        self.assertEqual(counts.get("group", 0), 0)
        self.assertEqual(counts.get("row", 0), 0)
        self.assertEqual(counts.get("grid", 0), 0)
        self.assertEqual(counts.get("overlay", 0), 0)

        # Every leaf uses original MeaXure rect as absolute coords
        for child in self.tree.children:
            self.assertTrue(child.is_absolute, msg=child.name)
            self.assertEqual(child.abs_left, child.rect.x)
            self.assertEqual(child.abs_top, child.rect.y)
            self.assertIsNotNone(child.layer)
            # z-index matches original paint order index
            self.assertEqual(child.z_index, self.artboard.layers.index(child.layer))

        # Children sorted by paint order
        zs = [c.z_index for c in self.tree.children]
        self.assertEqual(zs, sorted(zs))

    def test_no_full_page_preview_in_html(self) -> None:
        html = generate_html_page(self.artboard, self.tree)
        self.assertIn("<!DOCTYPE html>", html)
        self.assertNotIn("page-preview", html)
        self.assertIn("data-kind=", html)
        self.assertIn("position: absolute", html)
        # Text must not use flex centering
        self.assertNotRegex(html, r"\.text-\d+\s*\{[^}]*align-items:\s*center")
        # Editable text present
        self.assertTrue(
            any(
                (layer.content or "").strip() and (layer.content or "") in html
                for layer in self.artboard.layers
                if layer.type == "text"
            )
        )

    def test_crop_is_partial_only(self) -> None:
        crops = iter_preview_crops(self.tree)
        stats = summarize_render_stats(self.tree)
        code = stats["text"] + stats["css_shape"]
        visual = code + stats["asset"] + stats["preview_crop"]
        self.assertGreater(visual, 0)
        self.assertLess(stats["preview_crop"], visual)
        board_area = self.artboard.width * self.artboard.height
        for node in crops:
            self.assertLessEqual(node.rect.width, 120.1)
            self.assertLessEqual(node.rect.height, 120.1)
            self.assertLessEqual(
                node.rect.width * node.rect.height / board_area, 0.01 + 1e-9
            )

    def test_miniprogram_generation(self) -> None:
        files = generate_miniprogram_files(self.artboard, self.tree)
        self.assertIn("index.wxml", files)
        self.assertIn("rpx", files["index.wxss"])
        self.assertNotIn("page-preview", files["index.wxml"])
        self.assertIn("position: absolute", files["index.wxss"])


class MultiArtboardHybridTests(unittest.TestCase):
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

    def test_cli_hybrid_exports_editable_page(self) -> None:
        out = ROOT / "output" / "test_hybrid"
        if out.exists():
            import shutil

            shutil.rmtree(out)
        # Use a form-like artboard that previously broke with flex inference
        code = cli_main(
            [str(REAL_FULL), "-o", str(out), "-f", "html", "--mode", "hybrid", "-a", "2"]
        )
        self.assertEqual(code, 0)
        pages = list((out / "html" / "pages").glob("*.html"))
        self.assertEqual(len(pages), 1)
        html = pages[0].read_text(encoding="utf-8")
        self.assertNotIn("page-preview", html)
        self.assertIn("position: absolute", html)
        self.assertIn("data-kind=\"artboard\"", html)
        # No nested flex containers from failed inference
        self.assertNotIn("data-kind=\"group\"", html)
        self.assertNotIn("data-kind=\"row\"", html)
        # Real text from the artboard
        self.assertTrue(
            "签约" in html or "机构" in html or "团队" in html or "中关村" in html
        )
        # Absolute left/top present for leaves
        self.assertRegex(html, r"left:\s*\d")
        self.assertRegex(html, r"top:\s*\d")

    def test_cli_hybrid_exports_all_artboards(self) -> None:
        out = ROOT / "output" / "test_hybrid_all"
        if out.exists():
            import shutil

            shutil.rmtree(out)
        code = cli_main(
            [str(REAL_FULL), "-o", str(out), "-f", "html", "--mode", "hybrid"]
        )
        self.assertEqual(code, 0)
        pages = list((out / "html" / "pages").glob("*.html"))
        self.assertEqual(len(pages), len(self.doc.artboards))
        gallery = out / "html" / "index.html"
        self.assertTrue(gallery.exists())
        for page in pages[:3]:
            body = page.read_text(encoding="utf-8")
            self.assertNotIn("page-preview", body)
            self.assertIn("position: absolute", body)
            self.assertNotIn("data-kind=\"group\"", body)

    def test_cli_fidelity_still_available(self) -> None:
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

    def test_absolute_coords_match_meaxure_rect(self) -> None:
        ab = self.doc.artboards[2]
        tree = build_layout(ab, self.doc.slices)
        texts = [c for c in tree.children if c.kind == "text"]
        self.assertGreater(len(texts), 0)
        for node in texts[:5]:
            layer = node.layer
            assert layer is not None
            self.assertAlmostEqual(node.abs_left or -1, layer.rect.x, places=2)
            self.assertAlmostEqual(node.abs_top or -1, layer.rect.y, places=2)


if __name__ == "__main__":
    unittest.main()
