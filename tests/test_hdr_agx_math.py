# SPDX-License-Identifier: GPL-3.0-or-later
"""HDR AgX v2 tone math gates. No DNG, no image, no pixels."""
from __future__ import annotations

import math
import unittest

from dngscan.constants import (
    AGX_REFERENCE_RANGE_EV,
    DARKTABLE_BASE_GAMMA,
    OUTPUT_REFERENCE_WHITE_STOPS,
    SCENE_MIDGRAY,
)
from dngscan.hdr_agx_math import (
    MAX_SHOULDER_SEGMENTS,
    MAX_SINGLE_SEGMENT_ALPHA,
    HdrShoulderSegment,
    achieved_headroom_ev,
    adaptive_monotone_segments,
    body_anchor_at_ev,
    body_encoded_slope,
    compile_hdr_shoulder,
    evaluate_hdr_shoulder,
    requested_headroom_ev,
    validate_hdr_shoulder,
)

CONTRAST = 3.0
KNEE = 0.20


def _shoulder(peak_stops: float, knee_ev: float = KNEE, white_ev: float = 4.138):
    return compile_hdr_shoulder(knee_ev, white_ev, peak_stops, CONTRAST)


def _linear(scene_ev: float, segments) -> float:
    return SCENE_MIDGRAY * 2.0 ** evaluate_hdr_shoulder(scene_ev, segments)


class CoordinateConstantTests(unittest.TestCase):
    def test_output_reference_white_is_derived_not_stored(self) -> None:
        """One source of truth: the stop count follows from mid gray, nothing else."""
        self.assertEqual(SCENE_MIDGRAY, 0.18)
        self.assertAlmostEqual(
            OUTPUT_REFERENCE_WHITE_STOPS, math.log2(1.0 / SCENE_MIDGRAY), places=15
        )
        self.assertAlmostEqual(OUTPUT_REFERENCE_WHITE_STOPS, 2.473931188332412, places=12)

    def test_contrast_is_not_the_encoded_slope(self) -> None:
        self.assertAlmostEqual(
            body_encoded_slope(3.0), 3.0 / AGX_REFERENCE_RANGE_EV, places=15
        )
        self.assertNotAlmostEqual(body_encoded_slope(3.0), 3.0, places=3)

    def test_body_anchor_matches_its_closed_form(self) -> None:
        """Anchors are computed from midgray/gamma/contrast, never stored separately."""
        q0 = SCENE_MIDGRAY ** (1.0 / DARKTABLE_BASE_GAMMA)
        self.assertAlmostEqual(q0, 0.4586564468643811, places=15)
        value, stops, slope = body_anchor_at_ev(0.0, CONTRAST)
        self.assertAlmostEqual(value, SCENE_MIDGRAY, places=15)
        self.assertAlmostEqual(stops, 0.0, places=15)
        # dz/de at the pivot follows from 0.18 / 2.2 / 3.0 / 16.5 alone.
        self.assertAlmostEqual(slope, 1.2581923143145526, places=12)

    def test_knee_anchor_matches_the_designed_example(self) -> None:
        value, stops, slope = body_anchor_at_ev(KNEE, CONTRAST)
        self.assertAlmostEqual(value, 0.21289732342815634, places=15)
        self.assertAlmostEqual(stops, 0.24216090560632872, places=15)
        self.assertAlmostEqual(slope, 1.1657668767547156, places=15)


class RequestedHeadroomTests(unittest.TestCase):
    def test_tail_at_reference_white_earns_nothing(self) -> None:
        self.assertEqual(
            requested_headroom_ev(OUTPUT_REFERENCE_WHITE_STOPS, 3.0), 0.0
        )

    def test_display_capacity_is_only_a_ceiling(self) -> None:
        self.assertAlmostEqual(
            requested_headroom_ev(OUTPUT_REFERENCE_WHITE_STOPS + 9.0, 2.0), 2.0, places=12
        )

    def test_missing_tail_earns_nothing(self) -> None:
        """An absent measurement must not read as unlimited signal."""
        self.assertEqual(requested_headroom_ev(float("nan"), 3.0), 0.0)
        self.assertEqual(requested_headroom_ev(float("inf"), 3.0), 0.0)
        self.assertEqual(requested_headroom_ev(5.0, float("nan")), 0.0)


class ShoulderStructureTests(unittest.TestCase):
    def setUp(self) -> None:
        self.peak_stops = OUTPUT_REFERENCE_WHITE_STOPS + 1.3641
        self.segments = _shoulder(self.peak_stops)
        _, self.knee_stops, self.knee_slope = body_anchor_at_ev(KNEE, CONTRAST)

    def test_designed_example_needs_one_segment(self) -> None:
        self.assertEqual(len(self.segments), 1)
        self.assertAlmostEqual(self.segments[0].alpha, 1.2767, places=3)
        self.assertLessEqual(self.segments[0].alpha, MAX_SINGLE_SEGMENT_ALPHA)

    def test_joins_the_body_with_matching_value_and_slope(self) -> None:
        """The C1 join at K is the whole point; a limiter that rescaled m0 would break it."""
        self.assertAlmostEqual(
            evaluate_hdr_shoulder(KNEE, self.segments), self.knee_stops, places=15
        )
        step = 1e-7
        numeric = (
            evaluate_hdr_shoulder(KNEE + step, self.segments)
            - evaluate_hdr_shoulder(KNEE, self.segments)
        ) / step
        self.assertAlmostEqual(numeric, self.knee_slope, places=5)

    def test_white_endpoint_is_reached_with_zero_slope(self) -> None:
        white = 4.138
        self.assertAlmostEqual(
            evaluate_hdr_shoulder(white, self.segments), self.peak_stops, places=12
        )
        step = 1e-7
        inner = (
            evaluate_hdr_shoulder(white, self.segments)
            - evaluate_hdr_shoulder(white - step, self.segments)
        ) / step
        self.assertLess(abs(inner), 1e-4)
        # Outside the window the curve clamps, so the outer derivative is exactly zero.
        self.assertEqual(
            evaluate_hdr_shoulder(white + 1.0, self.segments),
            evaluate_hdr_shoulder(white, self.segments),
        )

    def test_monotone_across_the_window(self) -> None:
        samples = [
            evaluate_hdr_shoulder(KNEE + (4.138 - KNEE) * i / 20000.0, self.segments)
            for i in range(20001)
        ]
        self.assertGreaterEqual(min(b - a for a, b in zip(samples, samples[1:])), -1e-12)

    def test_validation_rejects_a_rescaled_start_tangent(self) -> None:
        """Guards the failure mode a generic PCHIP limiter would introduce."""
        broken = (
            HdrShoulderSegment(
                e0=KNEE, e1=4.138,
                z0=self.knee_stops, z1=self.peak_stops,
                m0=self.knee_slope * 0.5, m1=0.0,
            ),
        )
        ok, reason = validate_hdr_shoulder(broken, self.knee_slope, self.peak_stops)
        self.assertFalse(ok)
        self.assertIn("C1", reason)

    def test_validation_accepts_the_compiled_shoulder(self) -> None:
        ok, reason = validate_hdr_shoulder(
            self.segments, self.knee_slope, self.peak_stops
        )
        self.assertTrue(ok, msg=reason)


class BodyInvarianceTests(unittest.TestCase):
    """The invariant v1 could not offer, and the reason v2 exists.

    v1 bought its peak by raising the whole curve's gamma, so more headroom darkened the
    shadows: measured -1.5 EV at -4 EV on a real frame. In v2 nothing below K is a function
    of H at all, so this holds structurally rather than numerically.
    """

    def test_headroom_cannot_reach_below_the_knee(self) -> None:
        shoulders = {
            h: _shoulder(OUTPUT_REFERENCE_WHITE_STOPS + h) for h in (0.5, 1.0, 2.0, 3.0)
        }
        for ev in (-6.0, -5.0, -4.0, -3.0, -1.0, 0.0, KNEE):
            body = body_anchor_at_ev(ev, CONTRAST)[0]
            for h, segments in shoulders.items():
                with self.subTest(ev=ev, headroom=h):
                    if ev < KNEE:
                        # Below K the shoulder is not consulted at all.
                        self.assertEqual(
                            evaluate_hdr_shoulder(ev, segments), segments[0].z0
                        )
                    self.assertAlmostEqual(
                        body_anchor_at_ev(ev, CONTRAST)[0], body, places=15
                    )

    def test_knee_value_is_identical_across_headrooms(self) -> None:
        values = {
            h: _linear(KNEE, _shoulder(OUTPUT_REFERENCE_WHITE_STOPS + h))
            for h in (0.25, 1.0, 3.0)
        }
        reference = body_anchor_at_ev(KNEE, CONTRAST)[0]
        for h, value in values.items():
            with self.subTest(headroom=h):
                self.assertAlmostEqual(value, reference, delta=1e-6)


class AdaptiveSubdivisionTests(unittest.TestCase):
    def test_steep_request_subdivides_instead_of_relaxing_tangents(self) -> None:
        """Synthetic parameters: the production compiler cannot currently reach alpha > 3.

        W and H_content are coupled through the reliable tail -- a wide window implies a
        large peak -- and sweeping the tail over its whole range puts alpha's supremum at
        1.85. The subdivision path is therefore defensive rather than exercised, and the
        margins and floors that produce that bound are themselves marked as awaiting
        corpus calibration. Driving it here with a hand-built steep request keeps it
        tested rather than merely present; see test_production_domain_stays_single_segment.
        """
        knee = 0.0
        _, knee_stops, knee_slope = body_anchor_at_ev(knee, CONTRAST)
        white = knee + 1.0
        peak_stops = knee_stops + 0.30
        single_alpha = knee_slope * (white - knee) / (peak_stops - knee_stops)
        self.assertGreater(single_alpha, MAX_SINGLE_SEGMENT_ALPHA)

        segments = compile_hdr_shoulder(knee, white, peak_stops, CONTRAST)
        self.assertGreater(len(segments), 1)
        ok, reason = validate_hdr_shoulder(segments, knee_slope, peak_stops)
        self.assertTrue(ok, msg=reason)
        samples = [
            evaluate_hdr_shoulder(knee + (white - knee) * i / 5000.0, segments)
            for i in range(5001)
        ]
        self.assertGreaterEqual(min(b - a for a, b in zip(samples, samples[1:])), -1e-12)

    def test_production_domain_stays_single_segment(self) -> None:
        """Records the bound the previous test depends on, so a retune cannot hide it.

        W = clamp(max(E_tail + 0.30, 3.0), 3.0, 8.5) and H = min(3, max(0, E_tail - Zref))
        are both functions of the same tail, which is why alpha stays bounded well under
        the single-segment limit across the whole domain.
        """
        _, knee_stops, knee_slope = body_anchor_at_ev(KNEE, CONTRAST)
        worst = 0.0
        for step in range(0, 12001):
            tail = step / 1000.0
            headroom = requested_headroom_ev(tail, 3.0)
            if headroom <= 0.0:
                continue
            white = min(max(tail + 0.30, 3.0), 8.5)
            peak_stops = OUTPUT_REFERENCE_WHITE_STOPS + headroom
            alpha = knee_slope * (white - KNEE) / (peak_stops - knee_stops)
            worst = max(worst, alpha)
            self.assertEqual(len(_shoulder(peak_stops, white_ev=white)), 1)
        self.assertLess(worst, MAX_SINGLE_SEGMENT_ALPHA)
        self.assertAlmostEqual(worst, 1.8494, places=3)

    def test_subdivision_is_bounded(self) -> None:
        self.assertLessEqual(MAX_SHOULDER_SEGMENTS, 16)
        _, knee_stops, knee_slope = body_anchor_at_ev(KNEE, CONTRAST)
        segments = adaptive_monotone_segments(
            KNEE, KNEE + 1.0, knee_stops, knee_stops + 1.0, knee_slope
        )
        self.assertLessEqual(len(segments), MAX_SHOULDER_SEGMENTS)


class DegenerateRequestTests(unittest.TestCase):
    """Malformed input must produce no shoulder, never something that merely renders."""

    def test_zero_or_negative_window(self) -> None:
        peak = OUTPUT_REFERENCE_WHITE_STOPS + 1.0
        self.assertEqual(compile_hdr_shoulder(KNEE, KNEE, peak, CONTRAST), ())
        self.assertEqual(compile_hdr_shoulder(KNEE, KNEE - 1.0, peak, CONTRAST), ())

    def test_peak_at_or_below_the_knee(self) -> None:
        _, knee_stops, _ = body_anchor_at_ev(KNEE, CONTRAST)
        self.assertEqual(compile_hdr_shoulder(KNEE, 4.0, knee_stops, CONTRAST), ())
        self.assertEqual(
            compile_hdr_shoulder(KNEE, 4.0, knee_stops - 0.5, CONTRAST), ()
        )

    def test_empty_shoulder_fails_validation(self) -> None:
        ok, reason = validate_hdr_shoulder((), 1.0, 1.0)
        self.assertFalse(ok)
        self.assertIn("空", reason)


class AchievedHeadroomTests(unittest.TestCase):
    def test_reports_zero_when_nothing_exceeds_reference_white(self) -> None:
        self.assertEqual(achieved_headroom_ev([[0.2, 0.5, 1.0]]), 0.0)

    def test_reports_the_reached_peak(self) -> None:
        self.assertAlmostEqual(achieved_headroom_ev([[1.0, 4.0]]), 2.0, places=12)


if __name__ == "__main__":
    unittest.main()
