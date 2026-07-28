# SPDX-License-Identifier: GPL-3.0-or-later
"""Phase-4 HDR bridge property tests."""
from __future__ import annotations

import math
import unittest

import numpy as np

from dngscan.hdr_render import _bridge_jmh, _c1_reveal, _p3_luminance, midgray_match_scale_for_plans
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
        self.assertTrue(np.all(_p3_luminance(out) >= _p3_luminance(sdr) - 2e-3))

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
