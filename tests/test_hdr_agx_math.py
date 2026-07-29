# SPDX-License-Identifier: GPL-3.0-or-later
"""Phase 1 math gates for the HDR AgX allocation. No DNG, no image, no pixels."""
from __future__ import annotations

import math
import unittest

import numpy as np

from dngscan.hdr_agx_math import (
    DIFFUSE_WHITE_EV,
    MAX_LIFT_RATE,
    MINIMUM_WINDOW_EV,
    SMOOTHERSTEP_PEAK_SLOPE,
    achieved_headroom_ev,
    allocation_window,
    apply_hdr_allocation,
    clamp_budget_to_lift_rate,
    compile_budget,
    lift_stops,
    max_lift_rate,
    smootherstep,
    smootherstep_derivative,
)

# Three representative compiled plans, taken from real frames measured this session
# rather than invented: a wide default window, a daylight frame, and a frame sitting on
# dngscan's +3.00 EV white endpoint floor, which is the narrow case that matters.
PLANS = {
    "wide": (DIFFUSE_WHITE_EV, 6.52),
    "daylight": (DIFFUSE_WHITE_EV, 4.14),
    "white_floor": (DIFFUSE_WHITE_EV, 3.00),
}
HEADROOMS = (0.0, 1.0, 2.0, 3.0, math.log2(10.0))


def _sdr_reference(ev: np.ndarray, black_ev: float = -10.0, white_ev: float = 6.5) -> np.ndarray:
    """A stand-in monotone SDR response in [0,1] with T(0)=0.18.

    Phase 1 only needs *a* valid T0 to verify the allocation's properties; the real
    darktable curve arrives in Phase 2, where H=0 is checked against it directly.
    """
    x = np.clip((np.asarray(ev, dtype=np.float64) - black_ev) / (white_ev - black_ev), 0.0, 1.0)
    x0 = (0.0 - black_ev) / (white_ev - black_ev)
    return x ** (math.log(0.18) / math.log(x0))


class SmootherstepTests(unittest.TestCase):
    def test_endpoints_and_clamping(self) -> None:
        self.assertAlmostEqual(float(smootherstep(0.0)), 0.0, places=15)
        self.assertAlmostEqual(float(smootherstep(1.0)), 1.0, places=15)
        self.assertAlmostEqual(float(smootherstep(-5.0)), 0.0, places=15)
        self.assertAlmostEqual(float(smootherstep(5.0)), 1.0, places=15)

    def test_first_and_second_derivative_vanish_at_both_ends(self) -> None:
        """The reason for quintic over cubic: the knee inherits curvature, not just slope."""
        self.assertAlmostEqual(float(smootherstep_derivative(0.0)), 0.0, places=15)
        self.assertAlmostEqual(float(smootherstep_derivative(1.0)), 0.0, places=15)
        u = np.linspace(0.0, 1.0, 200001)
        s = smootherstep(u)
        d2 = np.diff(s, 2)
        self.assertLess(abs(float(d2[0])), 1e-12)
        self.assertLess(abs(float(d2[-1])), 1e-12)

    def test_monotone_and_peak_slope(self) -> None:
        u = np.linspace(0.0, 1.0, 200001)
        self.assertGreaterEqual(float(np.min(np.diff(smootherstep(u)))), 0.0)
        self.assertAlmostEqual(
            float(np.max(smootherstep_derivative(u))), SMOOTHERSTEP_PEAK_SLOPE, places=6
        )


class BudgetCompilationTests(unittest.TestCase):
    def test_tail_at_diffuse_white_earns_nothing(self) -> None:
        """HDR capacity is not a target every frame has to reach."""
        budget = compile_budget(DIFFUSE_WHITE_EV, DIFFUSE_WHITE_EV, 6.5, 3.0)
        self.assertEqual(budget, 0.0)

    def test_display_headroom_is_a_ceiling(self) -> None:
        budget = compile_budget(DIFFUSE_WHITE_EV + 9.0, DIFFUSE_WHITE_EV, 12.0, 2.0)
        self.assertLessEqual(budget, 2.0)

    def test_degenerate_window_is_refused(self) -> None:
        white = DIFFUSE_WHITE_EV + MINIMUM_WINDOW_EV - 1e-6
        self.assertEqual(compile_budget(DIFFUSE_WHITE_EV + 5.0, DIFFUSE_WHITE_EV, white, 3.0), 0.0)

    def test_narrow_window_degrades_instead_of_squeezing(self) -> None:
        """The case a width-only gate misses.

        dngscan's white endpoint has a +3.00 EV floor, so with the knee at diffuse white
        a common real frame leaves 0.526 EV of window -- clearing the 0.5 EV gate by a
        hair. Granting the full budget there would climb at 7.1 EV per EV, steeper than
        the SDR body's own contrast, and read as an edge rather than headroom.
        """
        knee, white = PLANS["white_floor"]
        window = allocation_window(knee, white)
        self.assertGreater(window, MINIMUM_WINDOW_EV)
        self.assertLess(window, 0.6)
        naive_rate = max_lift_rate(2.0, window)
        self.assertGreater(naive_rate, 7.0)
        budget = compile_budget(knee + 5.0, knee, white, 3.0)
        self.assertGreater(budget, 0.0)
        self.assertLessEqual(max_lift_rate(budget, window) - MAX_LIFT_RATE, 1e-9)

    def test_wide_window_keeps_its_full_budget(self) -> None:
        knee, white = PLANS["wide"]
        budget = compile_budget(knee + 2.0, knee, white, 3.0)
        self.assertAlmostEqual(budget, 2.0, places=9)

    def test_clamp_is_a_noop_when_the_window_is_generous(self) -> None:
        self.assertAlmostEqual(clamp_budget_to_lift_rate(2.0, 4.0), 2.0, places=9)
        self.assertEqual(clamp_budget_to_lift_rate(2.0, 0.0), 0.0)
        self.assertEqual(clamp_budget_to_lift_rate(0.0, 4.0), 0.0)


class AllocationPropertyTests(unittest.TestCase):
    """The five properties the design doc claims, checked on every plan x headroom."""

    def setUp(self) -> None:
        self.ev = np.linspace(-16.0, 16.0, 65537)
        self.t0 = _sdr_reference(self.ev)

    def test_zero_budget_is_exactly_the_sdr_curve(self) -> None:
        for name, (knee, white) in PLANS.items():
            with self.subTest(plan=name):
                out = apply_hdr_allocation(self.t0, self.ev, knee, white, 0.0)
                self.assertEqual(float(np.max(np.abs(out - self.t0))), 0.0)

    def test_below_the_knee_nothing_moves(self) -> None:
        for name, (knee, white) in PLANS.items():
            for h in HEADROOMS:
                with self.subTest(plan=name, headroom=h):
                    out = apply_hdr_allocation(self.t0, self.ev, knee, white, h)
                    below = self.ev <= knee
                    self.assertEqual(float(np.max(np.abs(out[below] - self.t0[below]))), 0.0)

    def test_midgray_anchor_never_moves(self) -> None:
        """EV 0 is the exposure anchor; HDR capacity must not touch it."""
        for name, (knee, white) in PLANS.items():
            for h in HEADROOMS:
                with self.subTest(plan=name, headroom=h):
                    out = apply_hdr_allocation(
                        _sdr_reference(np.zeros(1)), np.zeros(1), knee, white, h
                    )
                    self.assertLess(abs(float(out[0]) - 0.18), 2e-6)

    def test_monotone(self) -> None:
        for name, (knee, white) in PLANS.items():
            for h in HEADROOMS:
                with self.subTest(plan=name, headroom=h):
                    out = apply_hdr_allocation(self.t0, self.ev, knee, white, h)
                    self.assertGreaterEqual(float(np.min(np.diff(out))), -2e-7)

    def test_bounded_by_two_to_the_budget(self) -> None:
        for name, (knee, white) in PLANS.items():
            for h in HEADROOMS:
                with self.subTest(plan=name, headroom=h):
                    out = apply_hdr_allocation(self.t0, self.ev, knee, white, h)
                    self.assertLessEqual(float(np.max(out)), 2.0 ** h + 2e-6)
                    self.assertTrue(bool(np.all(np.isfinite(out))))

    def test_black_end_needs_no_positive_t0(self) -> None:
        """Monotonicity must hold where T0 is exactly 0.

        The log-derivative form in the design doc divides by T0 and appears to fail here;
        the product rule does not. This pins the working form.
        """
        ev = np.linspace(-16.0, 16.0, 4097)
        t0 = _sdr_reference(ev)
        t0[ev < -10.0] = 0.0
        knee, white = PLANS["wide"]
        out = apply_hdr_allocation(t0, ev, knee, white, 3.0)
        self.assertTrue(bool(np.all(np.isfinite(out))))
        self.assertGreaterEqual(float(np.min(np.diff(out))), -2e-7)

    def test_knee_is_c2_no_seam_at_diffuse_white(self) -> None:
        for name, (knee, white) in PLANS.items():
            with self.subTest(plan=name):
                ev = np.linspace(knee - 0.5, knee + 0.5, 200001)
                out = apply_hdr_allocation(_sdr_reference(ev), ev, knee, white, 3.0)
                ref = _sdr_reference(ev)
                mid = out.size // 2
                d1 = np.diff(out)
                d1_ref = np.diff(ref)
                self.assertLess(abs(float(d1[mid] - d1_ref[mid])), 2e-9)
                d2 = np.diff(out, 2)
                d2_ref = np.diff(ref, 2)
                self.assertLess(abs(float(d2[mid] - d2_ref[mid])), 2e-9)

    def test_achieved_headroom_never_exceeds_budget(self) -> None:
        for name, (knee, white) in PLANS.items():
            for h in HEADROOMS:
                with self.subTest(plan=name, headroom=h):
                    out = apply_hdr_allocation(self.t0, self.ev, knee, white, h)
                    self.assertLessEqual(achieved_headroom_ev(out), h + 1e-9)


class SingleCurveImpossibilityTests(unittest.TestCase):
    def test_stretching_one_c1_curve_to_hdr_has_no_solution(self) -> None:
        """Fossilise the refutation so the idea cannot quietly return.

        Writing Y_hdr = R*q^gamma keeps mid gray fixed but then requires the encoded
        shoulder to climb from q_pivot to 1 over the remaining window. A concave
        shoulder's slope only falls, so its average over that span cannot exceed its
        slope at the pivot -- and here it must be several times larger. The contradiction
        is structural: no choice of target_white, pivot encoding or curve gamma removes it.

        Two of the design doc's figures reproduce exactly (q_pivot and the required
        average shoulder slope). Its third, a pivot slope of 0.842686, does not follow
        from any derivation stated in the document, so this test asserts the inequality
        that carries the argument rather than a constant it cannot re-derive.
        """
        black_ev, white_ev, gamma, ratio = -10.0, 6.5, 2.2, 10.0
        span = white_ev - black_ev
        pivot_x = (0.0 - black_ev) / span

        q_pivot = (0.18 / ratio) ** (1.0 / gamma)
        self.assertAlmostEqual(q_pivot, 0.161043, places=5)

        average_shoulder_slope = (1.0 - q_pivot) / (1.0 - pivot_x)
        self.assertAlmostEqual(average_shoulder_slope, 2.129660, places=5)

        # Slope at the pivot, taken as the secant from the black end -- an upper bound for
        # a convex-then-concave curve, which makes the contradiction conservative.
        pivot_slope_bound = q_pivot / pivot_x
        self.assertLess(pivot_slope_bound, average_shoulder_slope)
        self.assertGreater(average_shoulder_slope / pivot_slope_bound, 2.5)

    def test_the_allocation_solves_what_the_single_curve_cannot(self) -> None:
        """Same target, reached without asking the shoulder to steepen."""
        knee, white = PLANS["wide"]
        ev = np.linspace(-16.0, 16.0, 65537)
        out = apply_hdr_allocation(_sdr_reference(ev), ev, knee, white, math.log2(10.0))
        self.assertLessEqual(float(np.max(out)), 10.0 + 2e-6)
        self.assertGreaterEqual(float(np.min(np.diff(out))), -2e-7)
        mid = apply_hdr_allocation(_sdr_reference(np.zeros(1)), np.zeros(1), knee, white, math.log2(10.0))
        self.assertLess(abs(float(mid[0]) - 0.18), 2e-6)


class LiftStopsTests(unittest.TestCase):
    def test_lift_is_zero_below_and_full_above(self) -> None:
        knee, white = PLANS["wide"]
        ev = np.array([knee - 1.0, knee, white, white + 1.0])
        lift = lift_stops(ev, knee, white, 3.0)
        self.assertEqual(float(lift[0]), 0.0)
        self.assertEqual(float(lift[1]), 0.0)
        self.assertAlmostEqual(float(lift[2]), 3.0, places=12)
        self.assertAlmostEqual(float(lift[3]), 3.0, places=12)

    def test_degenerate_inputs_return_no_lift(self) -> None:
        self.assertEqual(float(np.max(lift_stops(np.linspace(0, 8, 11), 3.0, 3.0, 3.0))), 0.0)
        self.assertEqual(float(np.max(lift_stops(np.linspace(0, 8, 11), 3.0, 6.0, 0.0))), 0.0)


if __name__ == "__main__":
    unittest.main()
