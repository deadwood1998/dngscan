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
    hdr_encoded_pivot_slope,
    lift_stops,
    max_lift_rate,
    sdr_encoded_slope,
    single_curve_minimum_white_ev,
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


def _hdr_base_reference(ev: np.ndarray, black_ev: float = -10.0, white_ev: float = 6.5) -> np.ndarray:
    """A stand-in monotone HDR base response in [0,1] with T(0)=0.18.

    Phase 1 only needs a valid monotone HDR base response to verify allocation properties.
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
        hair. Granting the full budget there would climb at 7.1 EV per EV and exceed the
        prototype rate cap; whether that cap is perceptually right remains a corpus test.
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
        self.t0 = _hdr_base_reference(self.ev)

    def test_zero_budget_returns_the_hdr_base_curve(self) -> None:
        for name, (knee, white) in PLANS.items():
            with self.subTest(plan=name):
                out = apply_hdr_allocation(self.t0, self.ev, knee, white, 0.0)
                self.assertEqual(float(np.max(np.abs(out - self.t0))), 0.0)

    def test_allocation_is_zero_before_its_start(self) -> None:
        for name, (knee, white) in PLANS.items():
            for h in HEADROOMS:
                with self.subTest(plan=name, headroom=h):
                    below = self.ev <= knee
                    allocated = lift_stops(self.ev, knee, white, h)
                    self.assertEqual(float(np.max(np.abs(allocated[below]))), 0.0)

    def test_midgray_anchor_remains_an_hdr_scene_intent_guard(self) -> None:
        """The HDR allocation must not turn available display capacity into auto exposure."""
        for name, (knee, white) in PLANS.items():
            for h in HEADROOMS:
                with self.subTest(plan=name, headroom=h):
                    out = apply_hdr_allocation(
                        _hdr_base_reference(np.zeros(1)), np.zeros(1), knee, white, h
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

    def test_black_end_needs_no_positive_base(self) -> None:
        """Monotonicity must hold where the HDR base is exactly 0.

        The log-derivative form divides by the base and appears to fail here;
        the product rule does not. This pins the working form.
        """
        ev = np.linspace(-16.0, 16.0, 4097)
        t0 = _hdr_base_reference(ev)
        t0[ev < -10.0] = 0.0
        knee, white = PLANS["wide"]
        out = apply_hdr_allocation(t0, ev, knee, white, 3.0)
        self.assertTrue(bool(np.all(np.isfinite(out))))
        self.assertGreaterEqual(float(np.min(np.diff(out))), -2e-7)

    def test_knee_is_c2_no_seam_at_diffuse_white(self) -> None:
        for name, (knee, white) in PLANS.items():
            with self.subTest(plan=name):
                ev = np.linspace(knee - 0.5, knee + 0.5, 200001)
                out = apply_hdr_allocation(_hdr_base_reference(ev), ev, knee, white, 3.0)
                ref = _hdr_base_reference(ev)
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
    def test_hdr_pivot_slope_preserves_linear_contrast(self) -> None:
        """The closed form, checked against the linear derivatives it is defined by.

        Everything here is in the *encoded* curve domain. The main case uses dngscan's
        current contrast 3.0; Blender's historical 2.4 is checked separately below.
        """
        gamma, ratio, s_sdr = 2.2, 10.0, 3.0
        q_sdr = 0.18 ** (1.0 / gamma)
        q_hdr = (0.18 / ratio) ** (1.0 / gamma)
        self.assertAlmostEqual(q_sdr, 0.45865645, places=8)
        self.assertAlmostEqual(q_hdr, 0.16104307, places=8)
        # The relation that makes the q^(g-1) factors cancel.
        self.assertAlmostEqual(q_hdr, q_sdr * ratio ** (-1.0 / gamma), places=15)

        s_hdr = hdr_encoded_pivot_slope(s_sdr, ratio, gamma)
        self.assertAlmostEqual(s_hdr, 1.0533575203, places=9)
        # Long form before simplification must agree bit for bit.
        long_form = s_sdr * q_sdr ** (gamma - 1.0) / (ratio * q_hdr ** (gamma - 1.0))
        self.assertEqual(s_hdr, long_form)
        # And the property it was solved for: equal linear contrast at the pivot.
        dy_sdr = gamma * q_sdr ** (gamma - 1.0) * s_sdr
        dy_hdr = ratio * gamma * q_hdr ** (gamma - 1.0) * s_hdr
        self.assertAlmostEqual(dy_sdr, dy_hdr, places=12)

    def test_stretching_one_c1_curve_to_hdr_has_no_solution(self) -> None:
        """Fossilise the refutation so the idea cannot quietly return.

        Matching SDR contrast at the pivot forces the HDR encoded slope *down* by
        R^(-1/gamma), while reaching encoded white over the remaining window demands an
        average slope well above it. A concave shoulder's slope only falls, so no such
        curve exists over dngscan's current gamma and white-EV range. Raising gamma far
        enough can change that geometry, but that is a different HDR curve solve, not a
        target-white extension of the frozen SDR DRT.
        """
        black_ev, white_ev, gamma, ratio, s_sdr = -10.0, 6.5, 2.2, 8.0, 3.0
        pivot_x = (0.0 - black_ev) / (white_ev - black_ev)
        q_hdr = (0.18 / ratio) ** (1.0 / gamma)

        pivot_slope = hdr_encoded_pivot_slope(s_sdr, ratio, gamma)
        average_shoulder_slope = (1.0 - q_hdr) / (1.0 - pivot_x)
        self.assertAlmostEqual(pivot_slope, 1.165805, places=5)
        self.assertAlmostEqual(average_shoulder_slope, 2.086020, places=5)
        self.assertAlmostEqual(average_shoulder_slope / pivot_slope, 1.789339, places=5)
        self.assertGreater(average_shoulder_slope, pivot_slope)

        # The black endpoint cancels. Even at dngscan's largest compiled white endpoint,
        # a concave shoulder cannot reach either the default R=8 or Blender-reference R=10.
        # Calls the shipped function rather than restating the formula: a test that
        # re-implements what it checks would pass over a broken implementation.
        for peak_ratio, minimum_white_ev in ((8.0, 11.6307034203), (10.0, 13.1415868186)):
            required_white = single_curve_minimum_white_ev(3.0, peak_ratio, gamma)
            self.assertAlmostEqual(required_white, minimum_white_ev, places=9)
            self.assertGreater(required_white, 8.5)

    def test_contrast_is_not_the_encoded_slope(self) -> None:
        """`contrast = 3.0` only equals slope 3.0 on the 16.5 EV reference window.

        Every plan dngscan actually compiles is narrower, so reading the parameter as a
        slope overstates it -- which would make the refutation look weaker than it is.
        """
        self.assertAlmostEqual(sdr_encoded_slope(3.0, -10.0, 6.5), 3.0, places=12)
        narrow = sdr_encoded_slope(3.0, -5.68, 3.12)
        self.assertLess(narrow, 3.0)
        self.assertAlmostEqual(narrow, 3.0 * 8.8 / 16.5, places=9)
        # Widening the window raises it proportionally; the black end matters here, unlike
        # in the threshold above where it cancels.
        self.assertGreater(sdr_encoded_slope(3.0, -14.0, 8.5), 3.0)

    def test_minimum_white_ev_is_the_boundary_of_the_refutation(self) -> None:
        """At exactly the threshold the two slopes meet; the claim is about being under it."""
        gamma, ratio = 2.2, 8.0
        w_min = single_curve_minimum_white_ev(3.0, ratio, gamma)
        q_hdr = (0.18 / ratio) ** (1.0 / gamma)
        for black_ev in (-14.0, -10.0, -5.0):
            with self.subTest(black_ev=black_ev):
                s_hdr = hdr_encoded_pivot_slope(
                    sdr_encoded_slope(3.0, black_ev, w_min), ratio, gamma
                )
                s_avg = (1.0 - q_hdr) * (w_min - black_ev) / w_min
                self.assertAlmostEqual(s_hdr, s_avg, places=9)

    def test_blender_contrast_value_is_only_a_reference_case(self) -> None:
        self.assertAlmostEqual(hdr_encoded_pivot_slope(2.4, 10.0, 2.2), 0.8426860162, places=9)

    def test_pivot_slope_is_not_a_universal_constant(self) -> None:
        """1.0534 holds only at R=10, gamma=2.2, s_sdr=3.0 and equal windows."""
        base = hdr_encoded_pivot_slope(3.0, 10.0, 2.2)
        self.assertNotAlmostEqual(hdr_encoded_pivot_slope(3.0, 8.0, 2.2), base, places=3)
        self.assertNotAlmostEqual(hdr_encoded_pivot_slope(3.0, 10.0, 2.4), base, places=3)
        self.assertNotAlmostEqual(hdr_encoded_pivot_slope(2.4, 10.0, 2.2), base, places=3)
        # No HDR expansion means no slope change at all.
        self.assertAlmostEqual(hdr_encoded_pivot_slope(3.0, 1.0, 2.2), 3.0, places=12)
        # A longer HDR window scales the requirement proportionally.
        self.assertAlmostEqual(
            hdr_encoded_pivot_slope(3.0, 10.0, 2.2, window_ratio=2.0), base * 2.0, places=12
        )

    def test_the_allocation_solves_what_the_single_curve_cannot(self) -> None:
        """Same target, reached without asking the shoulder to steepen."""
        knee, white = PLANS["wide"]
        ev = np.linspace(-16.0, 16.0, 65537)
        out = apply_hdr_allocation(_hdr_base_reference(ev), ev, knee, white, math.log2(10.0))
        self.assertLessEqual(float(np.max(out)), 10.0 + 2e-6)
        self.assertGreaterEqual(float(np.min(np.diff(out))), -2e-7)
        mid = apply_hdr_allocation(
            _hdr_base_reference(np.zeros(1)), np.zeros(1), knee, white, math.log2(10.0)
        )
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
