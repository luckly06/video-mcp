#!/usr/bin/env python3
"""Regression tests for portable raster measurement, SVG audit, and browser rendering."""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw

from doctor import redact_home
from raster_utils import connected_components, display_path as raster_display_path, estimate_coverage, premultiplied_rgba
from svg_audit import audit_svg, display_path as audit_display_path


SKILL_DIR = Path(__file__).resolve().parent.parent
REQUIRE_BROWSER = False


class RasterTests(unittest.TestCase):
    def test_tight_crop_does_not_reverse_background_and_ink(self) -> None:
        for background, ink in ((247, 60), (24, 235)):
            with self.subTest(background=background, ink=ink):
                normal = Image.new("RGBA", (64, 64), (background, background, background, 255))
                ImageDraw.Draw(normal).rectangle((12, 12, 51, 51), outline=(ink, ink, ink, 255), width=4)
                tight = normal.crop((12, 12, 52, 52))
                _, report = estimate_coverage(tight)
                self.assertLess(np.linalg.norm(np.array(report["background_rgba"][:3]) - background), 5)
                self.assertLess(np.linalg.norm(np.array(report["ink_rgb"]) - ink), 5)
                self.assertFalse(report["ambiguous"])

    def test_hidden_transparent_rgb_does_not_change_visible_metric(self) -> None:
        black_hidden = Image.new("RGBA", (16, 16), (0, 0, 0, 0))
        white_hidden = Image.new("RGBA", (16, 16), (255, 255, 255, 0))
        for image in (black_hidden, white_hidden):
            ImageDraw.Draw(image).rectangle((5, 5, 10, 10), fill=(30, 30, 30, 255))
        np.testing.assert_allclose(premultiplied_rgba(black_hidden), premultiplied_rgba(white_hidden))

        with tempfile.TemporaryDirectory() as directory:
            directory_path = Path(directory)
            source_path = directory_path / "source.png"
            render_path = directory_path / "render.png"
            out_dir = directory_path / "qa"
            white_hidden.save(source_path)
            black_hidden.save(render_path)
            completed = subprocess.run(
                [
                    sys.executable,
                    str(SKILL_DIR / "scripts" / "compare_fit.py"),
                    str(source_path),
                    str(render_path),
                    "--background",
                    "transparent",
                    "--out-dir",
                    str(out_dir),
                ],
                capture_output=True,
                text=True,
                check=False,
            )
            self.assertEqual(completed.returncode, 0, completed.stderr)
            metrics = json.loads((out_dir / "metrics.json").read_text())
            self.assertEqual(metrics["premultiplied_rgb_mae_0_255"], 0.0)
            self.assertEqual(metrics["composited_rgb_mae_0_255"], 0.0)

    def test_run_length_components_preserve_eight_connectivity(self) -> None:
        mask = np.zeros((8, 8), dtype=bool)
        mask[1, 1] = True
        mask[2, 2] = True
        mask[1, 5] = True
        mask[2, 7] = True
        mask[6, 6] = True
        components = connected_components(mask)
        self.assertEqual([component["area_px"] for component in components], [2, 1, 1, 1])


class AuditTests(unittest.TestCase):
    def test_internal_style_url_is_allowed(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            svg = Path(directory) / "internal.svg"
            svg.write_text(
                '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 10 10">'
                '<defs><linearGradient id="g"><stop offset="0" stop-color="red"/></linearGradient></defs>'
                '<rect width="10" height="10" style="fill:url(#g)"/></svg>'
            )
            self.assertTrue(audit_svg(svg)["pass"])

    def test_external_and_raster_references_are_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            svg = Path(directory) / "external.svg"
            svg.write_text(
                '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 10 10">'
                '<image href="data:image/png;base64,AA==" width="10" height="10"/></svg>'
            )
            report = audit_svg(svg)
            self.assertFalse(report["pass"])
            self.assertTrue(any("Forbidden" in error or "forbidden" in error for error in report["errors"]))


class PrivacyTests(unittest.TestCase):
    def test_home_paths_are_redacted_in_diagnostics(self) -> None:
        private_path = Path.home() / "private-work" / "asset.svg"
        self.assertTrue(raster_display_path(private_path).startswith("~/"))
        self.assertTrue(audit_display_path(private_path).startswith("~/"))
        redacted = redact_home({"path": str(private_path), "nested": [str(private_path)]})
        self.assertNotIn(str(Path.home()), json.dumps(redacted))


class BrowserTests(unittest.TestCase):
    def test_fractional_css_dpr_obeys_physical_size_contract(self) -> None:
        node = shutil.which("node")
        if not node:
            if REQUIRE_BROWSER:
                self.fail("Node.js is required for browser regression testing")
            self.skipTest("Node.js is unavailable")
        doctor = subprocess.run(
            [node, str(SKILL_DIR / "scripts" / "render_svg.cjs"), "--doctor"],
            capture_output=True,
            text=True,
            check=False,
        )
        if doctor.returncode != 0:
            if REQUIRE_BROWSER:
                self.fail(doctor.stderr or doctor.stdout)
            self.skipTest("Playwright/Chromium is unavailable")

        with tempfile.TemporaryDirectory() as directory:
            svg = Path(directory) / "icon.svg"
            png = Path(directory) / "icon.png"
            svg.write_text(
                '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 10 10">'
                '<circle cx="5" cy="5" r="4" fill="#333"/></svg>'
            )
            completed = subprocess.run(
                [
                    node,
                    str(SKILL_DIR / "scripts" / "render_svg.cjs"),
                    str(svg),
                    str(png),
                    "--physical-width",
                    "75",
                    "--physical-height",
                    "91",
                    "--dpr",
                    "2",
                    "--background",
                    "#fafafa",
                ],
                capture_output=True,
                text=True,
                check=False,
            )
            self.assertEqual(completed.returncode, 0, completed.stderr)
            with Image.open(png) as rendered:
                self.assertEqual(rendered.size, (75, 91))
            self.assertEqual(json.loads(completed.stdout)["physical_size"], [75, 91])


def main() -> int:
    global REQUIRE_BROWSER
    parser = argparse.ArgumentParser(add_help=False)
    parser.add_argument("--require-browser", action="store_true")
    known, remaining = parser.parse_known_args()
    REQUIRE_BROWSER = known.require_browser
    program = unittest.main(argv=[os.path.basename(__file__), *remaining], exit=False, verbosity=2)
    return 0 if program.result.wasSuccessful() else 1


if __name__ == "__main__":
    raise SystemExit(main())
