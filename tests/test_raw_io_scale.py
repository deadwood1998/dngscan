# SPDX-License-Identifier: GPL-3.0-or-later
"""Exposure-unit contracts at the LibRaw uint16 handoff."""
from __future__ import annotations

import unittest
from pathlib import Path

import numpy as np

from dngscan.raw_io import (
    baseline_exposure_gain,
    libraw_scene_scale,
    libraw_wb_headroom_gain,
    scene_rec2020_to_xyz_render,
    load_raw,
)


class LibRawSceneScaleTests(unittest.TestCase):
    def test_clip_uses_full_uint16_range(self) -> None:
        self.assertEqual(libraw_scene_scale(65535.0, "clip", [1.48, 1.0, 2.33, 0.0]), 65535.0)

    def test_reconstruction_scale_removes_wb_storage_attenuation(self) -> None:
        wb = [1.48, 1.0, 2.33, 0.0]
        self.assertAlmostEqual(libraw_wb_headroom_gain(wb), 2.33)
        self.assertAlmostEqual(libraw_scene_scale(65535.0, "reconstruct", wb), 65535.0 / 2.33)
        self.assertAlmostEqual(libraw_scene_scale(65535.0, "blend", wb), 65535.0 / 2.33)

    def test_wb_gain_is_invariant_to_coefficient_normalization(self) -> None:
        self.assertAlmostEqual(libraw_wb_headroom_gain([2.0, 1.0, 4.0, 1.0]), 4.0)
        self.assertAlmostEqual(libraw_wb_headroom_gain([1.0, 0.5, 2.0, 0.5]), 4.0)

    def test_baseline_exposure_divides_the_scale_rather_than_scaling_the_buffer(self) -> None:
        """The gain reaches 5.65x on an iPhone low-light frame. Applied to a uint16 buffer
        normalised to sensor saturation it would clip everything above 0.18, so it has to
        arrive as a change of scale, leaving the codes untouched."""
        self.assertAlmostEqual(baseline_exposure_gain(1.0), 2.0)
        self.assertAlmostEqual(baseline_exposure_gain(2.4973), 5.6465, places=3)
        self.assertAlmostEqual(
            libraw_scene_scale(65535.0, "clip", None, baseline_exposure=1.0), 65535.0 / 2.0
        )
        # Composes with the highlight-mode storage scaling rather than replacing it.
        wb = [1.48, 1.0, 2.33, 0.0]
        self.assertAlmostEqual(
            libraw_scene_scale(65535.0, "reconstruct", wb, baseline_exposure=1.0),
            65535.0 / 2.33 / 2.0,
        )

    def test_absent_baseline_exposure_is_not_zero(self) -> None:
        """A file without the tag must render unchanged; 0.0 is a real value meaning 1x."""
        self.assertEqual(baseline_exposure_gain(None), 1.0)
        self.assertEqual(baseline_exposure_gain(0.0), 1.0)
        self.assertEqual(baseline_exposure_gain(float("nan")), 1.0)
        self.assertEqual(
            libraw_scene_scale(65535.0, "clip", None, baseline_exposure=None), 65535.0
        )
        # A corrupt tag must not rewrite the exposure by an absurd amount.
        self.assertEqual(baseline_exposure_gain(99.0), 2.0 ** 8)

    def test_xyz_analysis_buffer_preserves_reconstruction_headroom(self) -> None:
        scale = 65535.0 / 2.0
        scene = np.full((1, 1, 3), 32768, dtype=np.uint16)
        xyz = scene_rec2020_to_xyz_render(scene, scale)
        decoded = xyz.astype(np.float64) / scale
        self.assertGreater(float(decoded[0, 0, 2]), 1.05)

    def test_sigma_clip_and_reconstruct_keep_the_same_body_exposure(self) -> None:
        source = Path("/Users/itoshikigen/Pictures/_SDI0150.DNG")
        if not source.is_file():
            raise unittest.SkipTest(f"missing {source}")
        clip = load_raw(source, "clip", scene_half_size=True)
        reconstruct = load_raw(source, "reconstruct", scene_half_size=True)

        def body_median(bundle) -> float:
            rgb = np.asarray(bundle.scene_rec2020_render, dtype=np.float32) / float(bundle.scene_scale)
            y = 0.2627 * rgb[:, :, 0] + 0.6780 * rgb[:, :, 1] + 0.0593 * rgb[:, :, 2]
            return float(np.median(y[(y > 0.002) & (y < 0.5)]))

        delta_ev = float(np.log2(body_median(reconstruct) / body_median(clip)))
        self.assertAlmostEqual(delta_ev, 0.0, delta=0.03)


if __name__ == "__main__":
    unittest.main()
