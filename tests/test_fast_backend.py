# SPDX-License-Identifier: GPL-3.0-or-later
"""Tests for the optional C++ AgX backend."""

from __future__ import annotations

import os
import unittest
from unittest import mock

import numpy as np

from dngscan import _fast as fast_backend
from dngscan.agx import apply_core, formation_matrices
from dngscan.fast_plan import NATIVE_ABI_VERSION
from dngscan.models import ToneCompressionPlan
from dngscan.punch import apply_punch_rec2020
from dngscan.render import apply_agx_core


def _sample_plan(**overrides) -> ToneCompressionPlan:
    base = dict(
        target_gamut="Rec2020",
        luma_p1=0.01,
        luma_p50=0.18,
        luma_p99=1.0,
        luma_p999=2.0,
        black_ev=-8.0,
        white_ev=5.0,
        dynamic_range_ev=13.0,
        contrast=3.0,
        toe_power=1.5,
        shoulder_power=2.9,
        chroma_p95=0.5,
        negative_rgb_pct=0.0,
        over_rgb_pct=0.0,
        tone_core="agx",
        use_c1_endpoints=True,
        punch_strength=0.0,
        hue_restore=0.6,
        agx_primaries="base",
    )
    base.update(overrides)
    return ToneCompressionPlan(**base)


def _reference_agx_core(rgb: np.ndarray, plan: ToneCompressionPlan) -> np.ndarray:
    inset, outset = formation_matrices(plan)
    mapped = apply_core(rgb, plan, inset, outset)
    return apply_punch_rec2020(mapped, float(plan.punch_strength))


@unittest.skipUnless(fast_backend.available(), "native extension not built")
class NativeAgxParityTests(unittest.TestCase):
    def setUp(self) -> None:
        self._env = os.environ.get("DNGSCAN_FAST")
        os.environ["DNGSCAN_FAST"] = "1"

    def tearDown(self) -> None:
        if self._env is None:
            os.environ.pop("DNGSCAN_FAST", None)
        else:
            os.environ["DNGSCAN_FAST"] = self._env

    def test_basis_colors_match_reference(self) -> None:
        plan = _sample_plan(hue_restore=1.0)
        rgb = np.asarray(
            [[1.0, 0.0, 0.0], [0.0, 1.0, 0.0], [0.0, 0.0, 1.0]],
            dtype=np.float32,
        )
        ref = _reference_agx_core(rgb, plan)
        out = apply_agx_core(rgb, plan)
        np.testing.assert_allclose(out, ref, rtol=0.0, atol=2e-5)

    def test_punch_zero_is_identity(self) -> None:
        plan = _sample_plan(punch_strength=0.0)
        rng = np.random.default_rng(3)
        rgb = rng.uniform(0.0, 1.0, size=(128, 3)).astype(np.float32)
        ref = _reference_agx_core(rgb, plan)
        out = apply_agx_core(rgb, plan)
        np.testing.assert_allclose(out, ref, rtol=0.0, atol=1e-5)

    def test_punch_matches_python(self) -> None:
        plan = _sample_plan(punch_strength=0.35)
        rgb = np.asarray([[0.28, 0.16, 0.10], [0.55, 0.40, 0.32]], dtype=np.float32)
        ref = _reference_agx_core(rgb, plan)
        out = apply_agx_core(rgb, plan)
        np.testing.assert_allclose(out, ref, rtol=0.0, atol=2e-5)

    def test_synthetic_scene_matches_reference(self) -> None:
        rng = np.random.default_rng(11)
        rgb = rng.uniform(0.0, 1.5, size=(4096, 3)).astype(np.float32)
        for primaries in ("smooth", "base", "punchy", "muted"):
            for hue_restore in (0.0, 0.4, 0.6, 1.0):
                plan = _sample_plan(agx_primaries=primaries, hue_restore=hue_restore)
                ref = _reference_agx_core(rgb, plan)
                out = apply_agx_core(rgb, plan)
                np.testing.assert_allclose(out, ref, rtol=0.0, atol=2e-5, err_msg=primaries)

    def test_view_brightness_both_sides_match_reference(self) -> None:
        rgb = np.asarray([[0.02, 0.08, 0.25], [0.8, 0.45, 0.12]], dtype=np.float32)
        for brightness in (0.64, 1.25):
            plan = _sample_plan(view_brightness=brightness)
            ref = _reference_agx_core(rgb, plan)
            out = apply_agx_core(rgb, plan)
            np.testing.assert_allclose(out, ref, rtol=0.0, atol=2e-5)

    def test_fast_does_not_mutate_input(self) -> None:
        plan = _sample_plan()
        rgb = np.asarray([[0.2, 0.3, 0.4]], dtype=np.float32)
        before = rgb.copy()
        apply_agx_core(rgb, plan)
        np.testing.assert_array_equal(rgb, before)

    def test_nan_inf_contract_matches_python(self) -> None:
        plan = _sample_plan(punch_strength=0.8)
        rgb = np.asarray([[np.nan, 0.3, np.inf], [-np.inf, 0.2, 0.1]], dtype=np.float32)
        ref = _reference_agx_core(rgb, plan)
        out = apply_agx_core(rgb, plan)
        np.testing.assert_allclose(out, ref, rtol=0.0, atol=2e-5)


class NativeDispatchTests(unittest.TestCase):
    def test_fast_unavailable_falls_back(self) -> None:
        plan = _sample_plan()
        rgb = np.asarray([[0.2, 0.3, 0.4]], dtype=np.float32)
        with mock.patch.object(fast_backend, "available", return_value=False):
            ref = _reference_agx_core(rgb, plan)
            out = apply_agx_core(rgb, plan)
        np.testing.assert_allclose(out, ref, rtol=0.0, atol=1e-6)

    def test_fast_rejects_non_agx_cores(self) -> None:
        for core in ("neutral", "lum", "gated"):
            plan = _sample_plan(tone_core=core)
            self.assertFalse(fast_backend.supports_agx(plan))

    def test_fast_accepts_c_contiguous_float32(self) -> None:
        plan = _sample_plan()
        rgb = np.asarray([[0.2, 0.3, 0.4]], dtype=np.float32)
        self.assertTrue(fast_backend.can_use_agx(rgb, plan) or not fast_backend.available())
        rgb_f64 = rgb.astype(np.float64)
        self.assertFalse(fast_backend.can_use_agx(rgb_f64, plan))

    def test_abi_mismatch_is_unavailable(self) -> None:
        with mock.patch.object(fast_backend, "_load_extension", return_value=None):
            with mock.patch.object(fast_backend, "_extension_error", "native ABI mismatch"):
                self.assertFalse(fast_backend.available())


if __name__ == "__main__":
    unittest.main()
