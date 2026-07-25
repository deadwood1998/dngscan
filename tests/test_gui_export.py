# SPDX-License-Identifier: GPL-3.0-or-later
"""Tests for GUI export naming."""

from __future__ import annotations

import unittest

from dngscan._deps import np
from dngscan.gui.service import export_suffix_parts
from dngscan.gui.service import downsample_mean
from dngscan.gui.page import render_page


class ExportSuffixTests(unittest.TestCase):
    def test_proxy_downsample_reaches_requested_long_edge(self) -> None:
        source = np.zeros((303, 202, 3), dtype=np.uint16)
        proxy = downsample_mean(source, 128)
        self.assertEqual(proxy.shape, (128, 85, 3))

    def test_public_gui_is_concise_and_has_no_vendor_luts(self) -> None:
        html = render_page("/tmp").decode("utf-8")
        self.assertIn("更新预览", html)
        self.assertIn("导出 JPEG", html)
        self.assertIn("前馈校正", html)
        self.assertIn("中间调亮度", html)
        self.assertIn("中间调对比", html)
        self.assertIn("暗部过渡", html)
        self.assertIn("高光过渡", html)
        self.assertIn("高光褪白", html)
        self.assertIn("中频纯度", html)
        self.assertNotIn('optgroup label="本地 LUT"', html)
        for vendor in ("ARRI Classic", "ARRI Reveal", "Fujifilm", "Kodak", "RED IPP2"):
            self.assertNotIn(vendor, html)

    def test_default_agx_only(self) -> None:
        self.assertEqual(export_suffix_parts("clip", "srgb", "sdr"), "agx")

    def test_blender_reference_path_is_named(self) -> None:
        self.assertEqual(
            export_suffix_parts("clip", "srgb", "sdr", agx_primaries="base"),
            "agx_base",
        )
        self.assertEqual(
            export_suffix_parts("clip", "srgb", "sdr", tone_core="gated", agx_primaries="base"),
            "gated",
        )

    def test_includes_grade(self) -> None:
        self.assertEqual(
            export_suffix_parts("clip", "srgb", "sdr", "look:optic_warm_cyan", 1.0),
            "agx_look_optic_warm_cyan",
        )
        self.assertEqual(
            export_suffix_parts("clip", "srgb", "sdr", "filter:kodak_2383_d65", 1.0),
            "agx_filter_kodak_2383_d65",
        )

    def test_includes_grade_strength_when_not_one(self) -> None:
        self.assertEqual(
            export_suffix_parts("clip", "srgb", "sdr", "look:optic_warm_cyan", 0.8),
            "agx_look_optic_warm_cyan_gs0.8",
        )

    def test_includes_scene_transform(self) -> None:
        self.assertEqual(
            export_suffix_parts("clip", "p3", "sdr", "none", 1.0, "arri_skin_d55", 0.75),
            "agx_p3_arri_skin_d55_st0.75",
        )

    def test_neutral_export_suffix(self) -> None:
        self.assertEqual(
            export_suffix_parts("clip", "srgb", "sdr", tone_core="neutral"),
            "neutral",
        )
        self.assertEqual(
            export_suffix_parts("clip", "srgb", "sdr", tone_core="lum"),
            "lum",
        )
        self.assertEqual(
            export_suffix_parts("clip", "srgb", "sdr", tone_core="lum", lum_norm="power"),
            "lum_power",
        )


if __name__ == "__main__":
    unittest.main()
