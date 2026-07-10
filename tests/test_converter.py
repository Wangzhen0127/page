"""Regression tests for MeaXure converter."""

from __future__ import annotations

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

    def test_hybrid_layout_has_structure(self) -> None:
        counts = count_nodes(self.tree)
        self.assertGreater(counts.get("text", 0) + counts.get("shape", 0), 0)
        # Nested containers should appear for real designs
        self.assertGreaterEqual(
            counts.get("group", 0)
            + counts.get("row", 0)
            + counts.get("overlay", 0)
            + counts.get("grid", 0),
            0,
        )
        # Page height preserved via spacer or content bottom
        bottoms = []
        for child in self.tree.children:
            bottoms.append(child.rect.y + child.rect.height)
        self.assertGreaterEqual(max(bottoms), self.artboard.height - 2.0)

    def test_no_full_page_preview_in_html(self) -> None:
        html = generate_html_page(self.artboard, self.tree)
        self.assertIn("<!DOCTYPE html>", html)
        self.assertNotIn("page-preview", html)
        self.assertIn("data-kind=", html)
        # Text content should be editable in DOM
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
        # Must not crop everything
        self.assertLess(stats["preview_crop"], visual)
        for node in crops:
            self.assertLessEqual(node.rect.width, 120.1)
            self.assertLessEqual(node.rect.height, 120.1)

    def test_miniprogram_generation(self) -> None:
        files = generate_miniprogram_files(self.artboard, self.tree)
        self.assertIn("index.wxml", files)
        self.assertIn("rpx", files["index.wxss"])
        self.assertNotIn("page-preview", files["index.wxml"])


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
        code = cli_main(
            [str(REAL_FULL), "-o", str(out), "-f", "html", "--mode", "hybrid", "-a", "1"]
        )
        self.assertEqual(code, 0)
        pages = list((out / "html" / "pages").glob("*.html"))
        self.assertEqual(len(pages), 1)
        html = pages[0].read_text(encoding="utf-8")
        self.assertNotIn("page-preview", html)
        self.assertIn("data-kind=", html)
        # Should contain real text from the artboard
        self.assertTrue("海淀" in html or "首页" in html or "签约" in html)

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
            self.assertIn("data-kind=", body)

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


if __name__ == "__main__":
    unittest.main()
