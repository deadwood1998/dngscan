# SPDX-License-Identifier: GPL-3.0-or-later
"""Fixed-Kelvin WB solver gates: known white points, physical multiplier behaviour."""
from __future__ import annotations

import unittest
from pathlib import Path

import numpy as np

from dngscan.wb import (
    KELVIN_WB_MODES,
    cct_to_xy,
    kelvin_camera_multipliers,
    kelvin_mode_cct,
)

SAMPLE = Path.home() / "Pictures" / "_SDI0150.DNG"


class CctChromaticityTests(unittest.TestCase):
    def test_d65_lands_on_the_modern_white_point(self) -> None:
        x, y = cct_to_xy(6500.0)
        self.assertAlmostEqual(x, 0.3127, delta=2e-3)
        self.assertAlmostEqual(y, 0.3290, delta=2e-3)

    def test_d55_matches_photographic_daylight(self) -> None:
        x, y = cct_to_xy(5500.0)
        self.assertAlmostEqual(x, 0.3324, delta=2e-3)
        self.assertAlmostEqual(y, 0.3474, delta=2e-3)

    def test_9300k_matches_the_japanese_broadcast_white(self) -> None:
        x, y = cct_to_xy(9300.0)
        self.assertAlmostEqual(x, 0.2831, delta=3e-3)
        self.assertAlmostEqual(y, 0.2971, delta=3e-3)

    def test_tungsten_targets_sit_on_the_planckian_locus(self) -> None:
        x, y = cct_to_xy(3200.0)
        self.assertAlmostEqual(x, 0.4234, delta=3e-3)
        self.assertAlmostEqual(y, 0.3990, delta=3e-3)

    def test_out_of_range_is_refused(self) -> None:
        with self.assertRaises(ValueError):
            cct_to_xy(1000.0)


class KelvinMultiplierTests(unittest.TestCase):
    # An identity-ish matrix stands in for a camera whose channels read XYZ directly.
    MATRIX = np.eye(3)

    def _mult(self, cct: float) -> list[float]:
        return kelvin_camera_multipliers(cct, self.MATRIX)

    def test_green_is_the_normalization_anchor(self) -> None:
        for cct in (3200.0, 5500.0, 9300.0):
            m = self._mult(cct)
            self.assertEqual(m[1], 1.0)
            self.assertEqual(m[3], 1.0)

    def test_multiplier_direction_follows_physics(self) -> None:
        """Warm targets need less red gain; cool targets need less blue gain."""
        m3200 = self._mult(3200.0)
        m5500 = self._mult(5500.0)
        m9300 = self._mult(9300.0)
        self.assertLess(m3200[0], m5500[0])  # tungsten white is red-rich
        self.assertLess(m9300[2], m5500[2])  # blue-rich white needs less blue gain
        self.assertLess(m5500[2], m3200[2])

    def test_empty_matrix_is_refused_with_a_clear_error(self) -> None:
        with self.assertRaisesRegex(ValueError, "ColorMatrix|matrix"):
            kelvin_camera_multipliers(5500.0, np.zeros((3, 3)))

    def test_mode_table_is_consistent(self) -> None:
        for name, (cct, label) in KELVIN_WB_MODES.items():
            self.assertEqual(kelvin_mode_cct(name), cct)
            self.assertTrue(label)
        self.assertIsNone(kelvin_mode_cct("camera"))
        self.assertIsNone(kelvin_mode_cct("daylight"))


@unittest.skipUnless(SAMPLE.is_file(), "sample frame unavailable")
class KelvinDecodeIntegrationTests(unittest.TestCase):
    def test_5500k_flows_through_the_libraw_decode(self) -> None:
        from dngscan.raw_io import load_raw

        bundle = load_raw(SAMPLE, scene_half_size=True, wb_mode="5500k")
        self.assertEqual(bundle.wb_mode, "5500k")
        self.assertIsNotNone(bundle.applied_wb)
        applied = bundle.applied_wb
        self.assertEqual(applied[1], 1.0)
        # 5500K on this daylight frame must differ from both as-shot and the
        # manufacturer daylight point, in the physically warm direction vs D65.
        d65 = [float(v) for v in bundle.daylight_wb]
        self.assertLess(applied[0], d65[0] / d65[1] * 1.02)
        self.assertGreater(applied[2], d65[2] / d65[1] * 1.02)

    def test_dual_calibration_beats_the_daylight_metadata_roundtrip(self) -> None:
        """Solving 6500K must land within a couple percent of the manufacturer point."""
        from dngscan.metadata import read_dng_color_calibration
        from dngscan.wb import solve_kelvin_wb
        import rawpy

        calib = read_dng_color_calibration(SAMPLE)
        self.assertIsNotNone(calib)
        self.assertIsNotNone(calib.matrix2)
        with rawpy.imread(str(SAMPLE)) as raw:
            d = [float(v) for v in raw.daylight_whitebalance]
        solved = solve_kelvin_wb(6500.0, dng_calibration=calib)
        self.assertLess(abs(solved[0] / (d[0] / d[1]) - 1.0), 0.05)
        self.assertLess(abs(solved[2] / (d[2] / d[1]) - 1.0), 0.05)


if __name__ == "__main__":
    unittest.main()
