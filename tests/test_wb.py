# SPDX-License-Identifier: GPL-3.0-or-later
"""Fixed-Kelvin WB solver gates: known white points, physical multiplier behaviour."""
from __future__ import annotations

import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

import numpy as np

from dngscan.wb import (
    KELVIN_WB_MODES,
    asshot_reference_cct,
    cct_to_xy,
    interpolated_color_matrix,
    kelvin_camera_multipliers,
    kelvin_mode_cct,
    solve_kelvin_wb,
)
from dngscan.constants import RGB_TO_XYZ, XYZ_TO_RGB
from dngscan.models import RawBundle
from dngscan import raw_io as raw_io_module
from dngscan.raw_io import (
    apply_hot_wb_rec2020,
    color_matrix_xyz_to_cam,
    hot_wb_matrix_rec2020,
    normalized_camera_wb,
    rebalance_raw_bundle,
    resolve_hot_wb_c0,
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

    def test_locus_seam_is_pinned_and_modes_keep_clear(self) -> None:
        """The daylight/Planckian switchover at 4000 K is a known discontinuity.

        Two invariants: the seam's size stays the documented ~0.005 in xy (a silent
        change would mean one locus formula moved), and every declared mode keeps at
        least 500 K away from it — a mode near the seam needs a declared bridge first
        (see cct_to_xy docstring).
        """
        below = np.array(cct_to_xy(3999.0))
        above = np.array(cct_to_xy(4001.0))
        seam = float(np.hypot(*(above - below)))
        self.assertGreater(seam, 1e-3)   # the seam is real: do not paper over it
        self.assertLess(seam, 1.2e-2)    # and it stays the documented magnitude
        for mode, (cct, _label) in KELVIN_WB_MODES.items():
            self.assertGreaterEqual(
                abs(cct - 4000.0), 500.0,
                f"mode {mode} ({cct} K) sits too close to the locus seam",
            )


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


class HotWhiteBalanceTests(unittest.TestCase):
    MATRIX = np.array(
        [[0.72, -0.16, -0.06], [-0.31, 1.18, 0.18], [-0.04, 0.12, 0.66]],
        dtype=np.float64,
    )

    def test_matrix_is_camera_gain_conjugated_into_rec2020(self) -> None:
        decode = [2.0, 1.0, 1.5, 1.0]
        target = [1.5, 1.0, 2.25, 1.0]
        actual = hot_wb_matrix_rec2020(self.MATRIX, decode, target)
        camera_to_rec = np.asarray(XYZ_TO_RGB["Rec2020"]) @ np.linalg.inv(self.MATRIX)
        relative = normalized_camera_wb(target) / normalized_camera_wb(decode)
        expected = camera_to_rec @ np.diag(relative) @ np.linalg.inv(camera_to_rec)
        np.testing.assert_allclose(actual, expected, rtol=2e-7, atol=2e-7)

    def test_same_balance_is_identity_and_scene_codes_are_not_rounded(self) -> None:
        wb = [2.0, 1.0, 1.5, 1.0]
        matrix = hot_wb_matrix_rec2020(self.MATRIX, wb, wb)
        np.testing.assert_allclose(matrix, np.eye(3), rtol=0.0, atol=2e-7)
        scene = np.array([[[-2.0, 0.5, 70000.0], [1.0, 2.0, 3.0]]], dtype=np.float32)
        out = apply_hot_wb_rec2020(scene, matrix)
        np.testing.assert_allclose(out, scene, rtol=2e-7, atol=2e-7)
        self.assertLess(float(out.min()), 0.0)
        self.assertGreater(float(out.max()), 65535.0)

    def test_target_white_point_can_use_a_different_color_matrix(self) -> None:
        decode = [2.0, 1.0, 1.5, 1.0]
        target = [1.5, 1.0, 2.25, 1.0]
        target_matrix = self.MATRIX + np.diag([0.03, -0.02, 0.01])
        actual = hot_wb_matrix_rec2020(
            self.MATRIX,
            decode,
            target,
            target_matrix,
        )
        xyz_to_rec = np.asarray(XYZ_TO_RGB["Rec2020"])
        decode_stage = (
            xyz_to_rec
            @ np.linalg.inv(self.MATRIX)
            @ np.diag(normalized_camera_wb(decode))
        )
        target_stage = (
            xyz_to_rec
            @ np.linalg.inv(target_matrix)
            @ np.diag(normalized_camera_wb(target))
        )
        np.testing.assert_allclose(
            actual,
            target_stage @ np.linalg.inv(decode_stage),
            rtol=2e-7,
            atol=2e-7,
        )

    def test_second_green_does_not_perturb_three_channel_reduction(self) -> None:
        reduced = normalized_camera_wb([4.0, 2.0, 6.0, 4.0])
        np.testing.assert_allclose(reduced, [2.0, 1.0, 3.0])


def _synthetic_calibration() -> SimpleNamespace:
    """Sigma fp's real dual-illuminant tags, hardcoded so no sample file is needed."""
    return SimpleNamespace(
        matrix1=np.array(
            [[1.4345, -0.7358, -0.4998],
             [-0.333, 1.1711, -0.0604],
             [-0.075, 0.1639, 0.5884]],
            dtype=np.float64,
        ),
        cct1=2856.0,
        matrix2=np.array(
            [[0.8084, -0.2002, -0.1708],
             [-0.4961, 1.1648, 0.0631],
             [-0.114, 0.1585, 0.356]],
            dtype=np.float64,
        ),
        cct2=6504.0,
    )


# LibRaw's rgb_cam as measured on the same body: camera -> linear sRGB, rows summing
# to one, second-green column zero (three-colour sensor).
_RGB_CAM = np.array(
    [[1.3780453, -0.58152825, 0.20348291, 0.0],
     [-0.09710576, 1.2240125, -0.1269067, 0.0],
     [0.06084216, -0.43904692, 1.3782047, 0.0]],
    dtype=np.float64,
)

_EVIDENCE_MATRIX = np.array(
    [[0.72, -0.16, -0.06], [-0.31, 1.18, 0.18], [-0.04, 0.12, 0.66]],
    dtype=np.float64,
)


def _ladder_bundle(**overrides) -> RawBundle:
    scene = (
        np.linspace(0.02, 0.9, 6 * 4 * 3, dtype=np.float32).reshape(6, 4, 3) * 20000.0
    )
    fields = dict(
        path=Path("synthetic.dng"),
        raw_image=None,
        raw_colors=None,
        xyz_render=scene.copy(),
        render_scale=40000.0,
        scene_rec2020_render=scene,
        scene_scale=40000.0,
        white_level=4095,
        black_levels=[256.0] * 4,
        camera_wb=[1.86, 1.0, 1.79, 0.0],
        color_desc="RGBG",
        raw_pattern=[[0, 1], [3, 2]],
        camera_white_levels=[4095.0] * 4,
        daylight_wb=[2.62, 1.31, 2.28, 0.0],
        applied_wb=[1.86, 1.0, 1.79, 0.0],
        decode_wb=[1.86, 1.0, 1.79, 0.0],
        shot_make="SynthWorks",
        shot_model="Ladder-1",
        wb_xyz_to_cam=None,
        wb_color_matrix=None,
    )
    fields.update(overrides)
    return RawBundle(**fields)


def _patch_calibration(value):
    return mock.patch.object(
        raw_io_module.dng_metadata, "read_dng_color_calibration", return_value=value
    )


class HotWbC0LadderTests(unittest.TestCase):
    """The decode-side C0 ladder: evidence matrix -> LibRaw rgb_cam -> DNG tags -> refusal."""

    def test_rung1_evidence_matrix_wins_and_keeps_current_behaviour(self) -> None:
        bundle = _ladder_bundle(
            wb_xyz_to_cam=_EVIDENCE_MATRIX.copy(), wb_color_matrix=_RGB_CAM.copy()
        )
        with _patch_calibration(None):
            decode, target, source = resolve_hot_wb_c0(bundle, 5500.0)
        self.assertEqual(source, "evidence")
        np.testing.assert_allclose(decode, _EVIDENCE_MATRIX)
        np.testing.assert_allclose(target, _EVIDENCE_MATRIX)
        calib = _synthetic_calibration()
        with _patch_calibration(calib):
            _decode, target, source = resolve_hot_wb_c0(bundle, 5500.0)
        self.assertEqual(source, "evidence")
        np.testing.assert_allclose(target, interpolated_color_matrix(calib, 5500.0))

    def test_rung2_libraw_decode_matrix_used_when_evidence_is_zero(self) -> None:
        bundle = _ladder_bundle(
            wb_xyz_to_cam=np.zeros((4, 3), dtype=np.float64),
            wb_color_matrix=_RGB_CAM.copy(),
        )
        # DNG tags being present must NOT outrank the matrix the decoder truly used.
        with _patch_calibration(_synthetic_calibration()):
            decode, target, source = resolve_hot_wb_c0(bundle, 5500.0)
        self.assertEqual(source, "color_matrix")
        expected = np.linalg.inv(
            np.asarray(RGB_TO_XYZ["sRGB"]) @ _RGB_CAM[:, :3]
        )
        np.testing.assert_allclose(decode, expected, rtol=1e-12, atol=1e-12)
        # Same-matrix target: the rgb_cam convention must never be mixed with an
        # unnormalized interpolated DNG matrix (hidden per-channel WB shift).
        np.testing.assert_allclose(target, decode, rtol=0.0, atol=0.0)

    def test_rung2_folds_the_second_green_column(self) -> None:
        base = _RGB_CAM.copy()
        split = base.copy()
        split[:, 3] = 0.25 * split[:, 1]
        split[:, 1] *= 0.75
        np.testing.assert_allclose(
            color_matrix_xyz_to_cam(split),
            color_matrix_xyz_to_cam(base),
            rtol=1e-12,
            atol=1e-12,
        )
        self.assertIsNone(color_matrix_xyz_to_cam(None))
        self.assertIsNone(color_matrix_xyz_to_cam(np.zeros((3, 4))))

    def test_rung3_dng_tags_anchor_c0_at_the_asshot_white(self) -> None:
        calib = _synthetic_calibration()
        bundle = _ladder_bundle()
        with _patch_calibration(calib):
            decode, target, source = resolve_hot_wb_c0(bundle, 5500.0)
        self.assertEqual(source, "dng_calibration")
        reference_cct = asshot_reference_cct(calib, bundle.decode_wb)
        np.testing.assert_allclose(
            decode, interpolated_color_matrix(calib, reference_cct)
        )
        np.testing.assert_allclose(
            target, interpolated_color_matrix(calib, 5500.0)
        )

    def test_rung4_everything_missing_is_a_refusal(self) -> None:
        bundle = _ladder_bundle()
        with _patch_calibration(None):
            with self.assertRaisesRegex(ValueError, "unavailable"):
                resolve_hot_wb_c0(bundle, 5500.0)

    def test_rebalance_flows_through_the_decode_matrix_rung(self) -> None:
        calib = _synthetic_calibration()
        bundle = _ladder_bundle(
            wb_xyz_to_cam=np.zeros((4, 3), dtype=np.float64),
            wb_color_matrix=_RGB_CAM.copy(),
        )
        source_scene = np.array(bundle.scene_rec2020_render, copy=True)
        with _patch_calibration(calib):
            balanced = rebalance_raw_bundle(bundle, "5500k")
        self.assertEqual(balanced.wb_mode, "5500k")
        self.assertIsNone(balanced.wb_degradation)
        target_wb = solve_kelvin_wb(5500.0, dng_calibration=calib)
        self.assertEqual(balanced.applied_wb, [float(v) for v in target_wb])
        c0 = color_matrix_xyz_to_cam(_RGB_CAM)
        expected = apply_hot_wb_rec2020(
            source_scene,
            hot_wb_matrix_rec2020(c0, list(bundle.decode_wb), target_wb, c0),
        )
        np.testing.assert_allclose(
            balanced.scene_rec2020_render, expected, rtol=1e-6, atol=1e-2
        )

    def test_rebalance_degrades_visibly_when_no_rung_answers(self) -> None:
        bundle = _ladder_bundle()
        with _patch_calibration(None):
            balanced = rebalance_raw_bundle(bundle, "daylight")
        self.assertEqual(balanced.wb_mode, "camera")
        self.assertIsNotNone(balanced.wb_degradation)
        self.assertIn("退化", balanced.wb_degradation)


class AsShotReferenceCctTests(unittest.TestCase):
    def test_solved_cct_is_plausible_for_a_daylight_frame(self) -> None:
        calib = _synthetic_calibration()
        cct = asshot_reference_cct(calib, [1.86, 1.0, 1.79, 0.0])
        self.assertTrue(np.isfinite(cct))
        self.assertGreater(cct, 2500.0)
        self.assertLess(cct, 10000.0)

    def test_solver_inverts_the_declared_multiplier_mapping(self) -> None:
        """Multipliers synthesised for a known CCT must solve back near that CCT."""
        calib = _synthetic_calibration()
        for declared in (3200.0, 5500.0, 6504.0):
            mult = kelvin_camera_multipliers(
                declared, interpolated_color_matrix(calib, declared)
            )
            solved = asshot_reference_cct(calib, mult)
            self.assertLess(
                abs(solved - declared) / declared,
                0.05,
                f"declared {declared} K solved to {solved} K",
            )

    def test_invalid_multipliers_are_refused(self) -> None:
        calib = _synthetic_calibration()
        with self.assertRaises(ValueError):
            asshot_reference_cct(calib, [0.0, 1.0, 1.0])
        with self.assertRaises(ValueError):
            asshot_reference_cct(calib, None)


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
