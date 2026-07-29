# SPDX-License-Identifier: GPL-3.0-or-later
"""Phase-4 HDR bridge property tests."""
from __future__ import annotations

import math
import unittest

import numpy as np

from dngscan.hdr_render import (
    _bridge_jmh,
    _c1_reveal,
    _fit_p3_to_peak_preserve_y,
    _jmh_to_relative_p3,
    _p3_luminance,
    _relative_p3_to_jmh,
    _upsample_bilinear,
    midgray_match_scale_for_plans,
)
from dngscan.models import ColorGeometryPlan, RenderPlan, SceneToneMetrics
from dngscan.tone import neutral_tone_plan


def _neutral_plan() -> RenderPlan:
    return RenderPlan(
        tone=neutral_tone_plan("Rec2020"),
        color=ColorGeometryPlan(
            target_gamut="Rec2020",
            raw_clip_retreat_strength=0.0,
            output_gamut_pressure_pct=0.0,
        ),
        scene=SceneToneMetrics(
            reliable_sample_pct=100.0,
            body_ev_p1=-4.0,
            body_ev_p5=-2.0,
            body_ev_p50=0.0,
            body_ev_p95=1.5,
            body_ev_p99=2.0,
            body_ev_p999=3.0,
            tail_ev_p9999=4.0,
            tail_area_ev0_pct=10.0,
            tail_area_ev2_pct=2.0,
            tail_extremity=1.0,
            sparse_emitter_tail=False,
            raw_clip_union_pct=0.0,
        ),
    )


class HdrBridgeTests(unittest.TestCase):
    def test_zero_reveal_copies_sdr(self) -> None:
        sdr = np.array([[[0.2, 0.18, 0.15]]], dtype=np.float32)
        hdr = np.array([[[1.5, 1.2, 0.9]]], dtype=np.float32)
        out = _bridge_jmh(sdr, hdr, np.zeros((1, 1), dtype=np.float32), 800.0)
        np.testing.assert_allclose(out, sdr, atol=0.0, rtol=0.0)

    def test_y_hdr_not_below_sdr(self) -> None:
        sdr = np.array([[[0.4, 0.35, 0.3], [0.8, 0.75, 0.7]]], dtype=np.float32)
        hdr = np.array([[[0.2, 0.5, 0.2], [0.5, 0.9, 0.5]]], dtype=np.float32)
        reveal = np.ones((1, 2), dtype=np.float32)
        out = _bridge_jmh(sdr, hdr, reveal, 800.0)
        self.assertTrue(np.all(_p3_luminance(out) >= _p3_luminance(sdr) - 2e-5))

    def test_relative_p3_jmh_round_trip_at_hdr_peak(self) -> None:
        rgb = np.array(
            [[0.18, 0.18, 0.18], [1.0, 0.4, 0.1], [4.0, 2.0, 0.5]], dtype=np.float64
        )
        out = _jmh_to_relative_p3(_relative_p3_to_jmh(rgb, 800.0), 800.0)
        np.testing.assert_allclose(out, rgb, rtol=2e-6, atol=2e-6)

    def test_peak_fit_preserves_luminance_while_reducing_chroma(self) -> None:
        rgb = np.array([[12.0, 0.5, -0.5], [0.4, 9.0, 0.2]], dtype=np.float32)
        y = _p3_luminance(rgb)
        out = _fit_p3_to_peak_preserve_y(rgb, 8.0)
        self.assertTrue(np.all(out >= 0.0))
        self.assertTrue(np.all(out <= 8.0))
        np.testing.assert_allclose(_p3_luminance(out), y, rtol=2e-6, atol=2e-6)

    def test_bridge_fuzz_respects_floor_and_peak(self) -> None:
        rng = np.random.default_rng(1234)
        sdr = rng.random((20_000, 3), dtype=np.float32)
        hdr = rng.uniform(-1.0, 12.0, size=(20_000, 3)).astype(np.float32)
        reveal = rng.random(20_000, dtype=np.float32)
        out = _bridge_jmh(sdr, hdr, reveal, 800.0)
        self.assertTrue(np.all(np.isfinite(out)))
        self.assertTrue(np.all(out >= 0.0))
        self.assertTrue(np.all(out <= 8.0 + 1e-6))
        self.assertTrue(np.all(_p3_luminance(out) >= _p3_luminance(sdr) - 2e-5))

    def test_evidence_upsample_is_continuous(self) -> None:
        source = np.array([[0.0, 1.0], [0.0, 1.0]], dtype=np.float32)
        out = _upsample_bilinear(source, (8, 8))
        self.assertTrue(np.all(np.diff(out[4]) >= -1e-7))
        self.assertGreater(len(np.unique(out[4])), 2)

    def test_c1_reveal_endpoints(self) -> None:
        ev = np.array([-1.0, 2.0, 2.473931, 2.973931, 5.0], dtype=np.float32)
        w = _c1_reveal(ev, 2.473931 - 0.5, 2.473931 + 0.5)
        self.assertEqual(float(w[0]), 0.0)
        self.assertEqual(float(w[-1]), 1.0)
        self.assertGreaterEqual(float(w[2]), 0.4)
        self.assertLessEqual(float(w[2]), 0.6)

    def test_midgray_match_finite(self) -> None:
        scale = midgray_match_scale_for_plans(_neutral_plan(), 3.0)
        self.assertTrue(math.isfinite(scale))
        self.assertGreater(scale, 0.0)


if __name__ == "__main__":
    unittest.main()
