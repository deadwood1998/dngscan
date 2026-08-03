# SPDX-License-Identifier: GPL-3.0-or-later
"""Exposure & range phase 1: evidence endpoints and bounded toe/shoulder offsets.

Covers the three contracts this feature adds:

1. `endpoint_mode="evidence"` compiles the black endpoint from the measured noise
   floor (prior read-noise when a sensor prior exists, single-frame estimate
   otherwise) and the white endpoint from the reliable RAW tail only, with truthful
   degradation notes when evidence is absent.
2. `toe_end_offset` / `shoulder_start_offset` are bounded plan-level adjustments:
   they move the compiled toe-end / shoulder-start transitions without moving the
   black/white endpoints, and every request — including out-of-range ones — still
   compiles to a monotone curve inside the display range.
3. CLI flags, GUI service parsing, cache keys and the page contract carry the new
   parameters end to end.
"""
from __future__ import annotations

import contextlib
import io
import math
import unittest
from dataclasses import replace

from dngscan._deps import np
from dngscan.analysis import noise_floor_ev_estimate
from dngscan.constants import MIDGRAY_HEADROOM_STOPS
from dngscan.drt import (
    TOE_POWER_SOLVE_MAX,
    TOE_POWER_SOLVE_MIN,
    apply_c1_endpoints,
    compiled_curve_transitions,
)
from dngscan.gui.service import (
    _adjustment_key,
    parse_endpoint_mode,
    parse_render_adjustments,
)
from dngscan.models import (
    ColorGeometryPlan, RenderAdjustments, RenderPlan, ToneCompressionPlan,
)
from dngscan.tone import apply_render_adjustments, build_tone_compression_plan

from tests.golden_support import _analysis_for, _bundle_from_scene, _scene_metrics


def _scene_bundle():
    scene = np.full((32, 32, 3), 6553, dtype=np.uint16)
    return _bundle_from_scene(scene)


def _tone_plan(**overrides) -> ToneCompressionPlan:
    base = dict(
        target_gamut="Rec2020",
        luma_p1=0.01,
        luma_p50=0.18,
        luma_p99=1.0,
        luma_p999=2.0,
        black_ev=-8.0,
        white_ev=4.0,
        dynamic_range_ev=12.0,
        contrast=3.0,
        toe_power=1.5,
        shoulder_power=2.9,
        chroma_p95=0.0,
        negative_rgb_pct=0.0,
        over_rgb_pct=0.0,
        latitude_lo_ev=0.1,
        latitude_hi_ev=0.2,
        toe_start_ev=-0.1,
        shoulder_start_ev=0.2,
        use_c1_endpoints=True,
    )
    base.update(overrides)
    return ToneCompressionPlan(**base)


def _render_plan(tone: ToneCompressionPlan) -> RenderPlan:
    color = ColorGeometryPlan(
        target_gamut="p3",
        raw_clip_retreat_strength=1.0,
        output_gamut_pressure_pct=0.0,
    )
    return RenderPlan(tone=tone, color=color, scene=None)  # type: ignore[arg-type]


def _assert_monotone(test: unittest.TestCase, tone: ToneCompressionPlan) -> None:
    ev = np.linspace(tone.black_ev - 1.0, tone.white_ev + 1.0, 512, dtype=np.float32)
    out = apply_c1_endpoints(ev, tone)
    test.assertTrue(bool(np.all(np.diff(out) >= -1e-6)), "curve must stay monotone")
    test.assertTrue(bool(np.all(out >= -1e-6)))
    test.assertTrue(bool(np.all(out <= float(tone.target_white_linear) + 1e-4)))


class NoiseFloorEvEstimateTests(unittest.TestCase):
    def test_prior_read_noise_floor(self) -> None:
        analysis = _analysis_for(
            median_vs_gray_ev=0.0, ev_median=0.0, ev_p99=2.0, ev_p999=3.0,
            usable_dr_ev=9.0,
        )
        # fullwell_e = noise_floor_e / noise_floor = 2.0 / 0.002 = 1000 e-
        expected = MIDGRAY_HEADROOM_STOPS + math.log2(3.0 / 1000.0)
        value, source = noise_floor_ev_estimate(analysis)
        self.assertEqual(source, "prior")
        self.assertAlmostEqual(value, expected, places=9)

    def test_single_frame_fallback_without_prior(self) -> None:
        analysis = replace(
            _analysis_for(
                median_vs_gray_ev=0.0, ev_median=0.0, ev_p99=2.0, ev_p999=3.0,
                usable_dr_ev=9.0,
            ),
            noise_floor_e=None,
            prior_read_noise_e=None,
        )
        value, source = noise_floor_ev_estimate(analysis)
        self.assertEqual(source, "frame")
        self.assertAlmostEqual(value, MIDGRAY_HEADROOM_STOPS - 9.0, places=9)

    def test_no_estimate_at_all(self) -> None:
        analysis = replace(
            _analysis_for(
                median_vs_gray_ev=0.0, ev_median=0.0, ev_p99=2.0, ev_p999=3.0,
                usable_dr_ev=9.0,
            ),
            noise_floor_e=None,
            prior_read_noise_e=None,
            usable_dr_ev=float("nan"),
        )
        value, source = noise_floor_ev_estimate(analysis)
        self.assertEqual(source, "none")
        self.assertTrue(math.isnan(value))


class EvidenceEndpointCompileTests(unittest.TestCase):
    def _compile(self, analysis, metrics, endpoint_mode):
        return build_tone_compression_plan(
            _scene_bundle(),
            analysis,
            "Rec2020",
            endpoint_mode=endpoint_mode,
            scene_metrics=metrics,
        )

    def test_default_stays_adaptive_with_no_note(self) -> None:
        analysis = _analysis_for(
            median_vs_gray_ev=0.0, ev_median=0.0, ev_p99=2.0, ev_p999=3.0,
            usable_dr_ev=9.0,
        )
        metrics = _scene_metrics(-1.0, tail_ev_p9999=2.0)
        plan = build_tone_compression_plan(
            _scene_bundle(), analysis, "Rec2020", scene_metrics=metrics
        )
        self.assertEqual(plan.endpoint_mode, "adaptive")
        self.assertIsNone(plan.endpoint_note)

    def test_evidence_black_uses_prior_read_noise_floor(self) -> None:
        analysis = _analysis_for(
            median_vs_gray_ev=0.0, ev_median=0.0, ev_p99=2.0, ev_p999=3.0,
            usable_dr_ev=9.0,
        )
        metrics = _scene_metrics(-1.0, tail_ev_p9999=2.0)
        adaptive = self._compile(analysis, metrics, "adaptive")
        evidence = self._compile(analysis, metrics, "evidence")
        expected_black = MIDGRAY_HEADROOM_STOPS + math.log2(3.0 / 1000.0)
        self.assertAlmostEqual(evidence.black_ev, expected_black, places=6)
        self.assertNotAlmostEqual(evidence.black_ev, adaptive.black_ev, places=2)
        self.assertEqual(evidence.endpoint_mode, "evidence")
        self.assertIn("先验读出噪声底", evidence.endpoint_note)
        self.assertAlmostEqual(
            evidence.dynamic_range_ev, evidence.white_ev - evidence.black_ev, places=6
        )

    def test_evidence_black_single_frame_note_without_prior(self) -> None:
        analysis = replace(
            _analysis_for(
                median_vs_gray_ev=0.0, ev_median=0.0, ev_p99=2.0, ev_p999=3.0,
                usable_dr_ev=9.0,
            ),
            noise_floor_e=None,
            prior_read_noise_e=None,
        )
        metrics = _scene_metrics(-1.0, tail_ev_p9999=2.0)
        evidence = self._compile(analysis, metrics, "evidence")
        self.assertAlmostEqual(
            evidence.black_ev, MIDGRAY_HEADROOM_STOPS - 9.0, places=6
        )
        self.assertIn("单帧噪声底估计", evidence.endpoint_note)

    def test_evidence_black_degrades_truthfully_without_any_estimate(self) -> None:
        analysis = replace(
            _analysis_for(
                median_vs_gray_ev=0.0, ev_median=0.0, ev_p99=2.0, ev_p999=3.0,
                usable_dr_ev=9.0,
            ),
            noise_floor_e=None,
            prior_read_noise_e=None,
            usable_dr_ev=float("nan"),
            usable_dr_eff_ev=float("nan"),
        )
        metrics = _scene_metrics(-1.0, tail_ev_p9999=2.0)
        adaptive = self._compile(analysis, metrics, "adaptive")
        evidence = self._compile(analysis, metrics, "evidence")
        self.assertEqual(evidence.black_ev, adaptive.black_ev)
        self.assertIn("黑端点证据缺席", evidence.endpoint_note)

    def test_evidence_white_follows_reliable_tail(self) -> None:
        analysis = _analysis_for(
            median_vs_gray_ev=0.0, ev_median=0.0, ev_p99=2.0, ev_p999=3.0,
            usable_dr_ev=9.0,
        )
        metrics = _scene_metrics(-1.0, tail_ev_p9999=4.4)
        evidence = self._compile(analysis, metrics, "evidence")
        # non-sparse margin 0.30, minimum white +3.00
        self.assertAlmostEqual(evidence.white_ev, 4.7, places=6)
        self.assertIn("白端点=可靠尾部", evidence.endpoint_note)

    def test_evidence_white_falls_back_when_reliable_tail_missing(self) -> None:
        analysis = _analysis_for(
            median_vs_gray_ev=0.0, ev_median=0.0, ev_p99=2.0, ev_p999=3.0,
            usable_dr_ev=9.0,
        )
        metrics = replace(
            _scene_metrics(-1.0, tail_ev_p9999=6.0),
            reliable_tail_ev_p9999=float("nan"),
        )
        adaptive = self._compile(analysis, metrics, "adaptive")
        evidence = self._compile(analysis, metrics, "evidence")
        self.assertEqual(evidence.white_ev, adaptive.white_ev)
        self.assertIn("白端点证据缺席", evidence.endpoint_note)

    def test_evidence_endpoints_pass_curve_legality(self) -> None:
        analysis = _analysis_for(
            median_vs_gray_ev=0.0, ev_median=0.0, ev_p99=2.0, ev_p999=3.0,
            usable_dr_ev=12.0,
        )
        metrics = _scene_metrics(-1.0, tail_ev_p9999=4.0)
        evidence = self._compile(analysis, metrics, "evidence")
        _assert_monotone(self, evidence)


class ToeEndOffsetTests(unittest.TestCase):
    def test_zero_offsets_are_exact_identity(self) -> None:
        plan = _render_plan(_tone_plan())
        self.assertTrue(RenderAdjustments().is_identity())
        self.assertIs(apply_render_adjustments(plan, RenderAdjustments()), plan)
        self.assertFalse(RenderAdjustments(toe_end_offset=-1.0).is_identity())
        self.assertFalse(RenderAdjustments(shoulder_start_offset=1.0).is_identity())

    def test_negative_offset_moves_toe_end_deeper_and_lifts_shadows(self) -> None:
        plan = _render_plan(_tone_plan())
        base_end = compiled_curve_transitions(plan.tone)["toe_end_ev"]
        adjusted = apply_render_adjustments(
            plan, RenderAdjustments(toe_end_offset=-1.5)
        )
        new_end = compiled_curve_transitions(adjusted.tone)["toe_end_ev"]
        self.assertLess(new_end, base_end - 0.5)
        self.assertAlmostEqual(new_end, base_end - 1.5, delta=0.15)
        # endpoints and shoulder side must not move
        self.assertEqual(adjusted.tone.black_ev, plan.tone.black_ev)
        self.assertEqual(adjusted.tone.white_ev, plan.tone.white_ev)
        self.assertEqual(adjusted.tone.latitude_hi_ev, plan.tone.latitude_hi_ev)
        # deep shadows lift, upper mids/highlights stay put
        probe_deep = float(apply_c1_endpoints(np.asarray([-4.0]), adjusted.tone)[0])
        base_deep = float(apply_c1_endpoints(np.asarray([-4.0]), plan.tone)[0])
        self.assertGreater(probe_deep, base_deep)
        probe_high = float(apply_c1_endpoints(np.asarray([2.0]), adjusted.tone)[0])
        base_high = float(apply_c1_endpoints(np.asarray([2.0]), plan.tone)[0])
        self.assertAlmostEqual(probe_high, base_high, places=5)
        _assert_monotone(self, adjusted.tone)

    def test_positive_offset_tightens_the_toe(self) -> None:
        plan = _render_plan(_tone_plan())
        base_end = compiled_curve_transitions(plan.tone)["toe_end_ev"]
        adjusted = apply_render_adjustments(
            plan, RenderAdjustments(toe_end_offset=0.5)
        )
        new_end = compiled_curve_transitions(adjusted.tone)["toe_end_ev"]
        self.assertGreaterEqual(new_end, base_end - 0.02)
        _assert_monotone(self, adjusted.tone)

    def test_out_of_reach_request_clamps_to_legal_toe_power(self) -> None:
        plan = _render_plan(_tone_plan(black_ev=-4.0, dynamic_range_ev=8.0))
        adjusted = apply_render_adjustments(
            plan, RenderAdjustments(toe_end_offset=-3.0)
        )
        self.assertGreaterEqual(adjusted.tone.toe_power, TOE_POWER_SOLVE_MIN - 1e-9)
        self.assertLessEqual(adjusted.tone.toe_power, TOE_POWER_SOLVE_MAX + 1e-9)
        _assert_monotone(self, adjusted.tone)

    def test_out_of_range_value_is_clamped_not_amplified(self) -> None:
        plan = _render_plan(_tone_plan())
        wild = apply_render_adjustments(plan, RenderAdjustments(toe_end_offset=-99.0))
        bounded = apply_render_adjustments(plan, RenderAdjustments(toe_end_offset=-3.0))
        self.assertAlmostEqual(wild.tone.toe_power, bounded.tone.toe_power, places=6)


class LiftedBlackToeEndTests(unittest.TestCase):
    """Floor-relative toe-end semantics on lifted-black (film paper Dmax) plans.

    The near-black reference is TOE_END_DISPLAY_LINEAR ABOVE the compiled
    ``target_black_linear`` floor: identical to the absolute 0.002 level for
    zero-floor plans, and the only definition under which lifted-floor plans keep a
    real, monotone measurement. The old code returned the black endpoint as a
    sentinel and the solver mistook it for a crossing, so every offset — either
    sign — solved to the hardest legal toe (3.5), the reverse of the declared
    control direction.
    """

    PRESETS = ("portra400", "kodachrome64", "vision3250d_theatrical")
    OFFSETS = (-3.0, -1.0, -0.05, 0.0, 0.5)

    @staticmethod
    def _film_plan(preset: str) -> RenderPlan:
        from dngscan.film_curve import apply_film_curve_preset

        return _render_plan(apply_film_curve_preset(_tone_plan(), preset))

    def test_offsets_keep_a_monotone_gradient_with_the_declared_direction(self) -> None:
        for preset in self.PRESETS:
            with self.subTest(preset=preset):
                plan = self._film_plan(preset)
                base_power = float(plan.tone.toe_power)
                base_end = compiled_curve_transitions(plan.tone)["toe_end_ev"]
                self.assertIsNotNone(base_end)
                powers, ends = [], []
                for offset in self.OFFSETS:
                    adjusted = apply_render_adjustments(
                        plan, RenderAdjustments(toe_end_offset=offset)
                    )
                    end = compiled_curve_transitions(adjusted.tone)["toe_end_ev"]
                    self.assertIsNotNone(end)
                    powers.append(float(adjusted.tone.toe_power))
                    ends.append(float(end))
                    if offset < 0.0:
                        # More open (or clamped open), never harder than base.
                        self.assertLessEqual(adjusted.tone.toe_power, base_power + 1e-9)
                        self.assertLessEqual(end, base_end + 1e-9)
                    elif offset > 0.0:
                        self.assertGreaterEqual(adjusted.tone.toe_power, base_power - 1e-9)
                        self.assertGreaterEqual(end, base_end - 1e-9)
                for a, b in zip(powers, powers[1:]):
                    self.assertLessEqual(a, b + 1e-9)
                for a, b in zip(ends, ends[1:]):
                    self.assertLessEqual(a, b + 1e-9)

    def test_lifted_floor_toe_end_is_a_real_crossing_not_the_black_endpoint(self) -> None:
        from dngscan.drt import TOE_END_DISPLAY_LINEAR, _value_at_ev, curve_params_from_plan

        for preset in ("portra400", "kodachrome64"):
            with self.subTest(preset=preset):
                tone = self._film_plan(preset).tone
                end = compiled_curve_transitions(tone)["toe_end_ev"]
                self.assertIsNotNone(end)
                self.assertGreater(end, float(tone.black_ev) + 0.05)
                params = curve_params_from_plan(tone)
                floor = float(params["target_black"]) ** float(params["gamma"])
                self.assertAlmostEqual(
                    _value_at_ev(float(end), params),
                    floor + TOE_END_DISPLAY_LINEAR,
                    places=4,
                )

    def test_zero_floor_measurement_is_unchanged_absolute_reference(self) -> None:
        from dngscan.drt import TOE_END_DISPLAY_LINEAR, _value_at_ev, curve_params_from_plan

        tone = _tone_plan()
        end = compiled_curve_transitions(tone)["toe_end_ev"]
        self.assertIsNotNone(end)
        params = curve_params_from_plan(tone)
        self.assertEqual(float(params["target_black"]), 0.0)
        self.assertAlmostEqual(
            _value_at_ev(float(end), params), TOE_END_DISPLAY_LINEAR, places=4
        )

    def test_unmeasurable_crossing_reports_none_and_the_solver_refuses_to_move(self) -> None:
        from unittest import mock

        from dngscan import drt

        tone = _tone_plan()
        with mock.patch.object(drt, "toe_end_ev_from_params", return_value=None):
            self.assertIsNone(compiled_curve_transitions(tone)["toe_end_ev"])
            solved = drt.solve_toe_power_for_toe_end(tone, -2.0)
            self.assertEqual(solved, float(tone.toe_power))
            plan = _render_plan(tone)
            adjusted = apply_render_adjustments(
                plan, RenderAdjustments(toe_end_offset=-1.0)
            )
            self.assertEqual(adjusted.tone.toe_power, tone.toe_power)

    def test_unmeasurable_toe_end_serializes_to_null_for_the_page(self) -> None:
        from dngscan.gui.service import _finite_or_none

        self.assertIsNone(_finite_or_none(None))

    def test_untouched_sliders_do_not_reclamp_film_preset_powers(self) -> None:
        # vision3250d_theatrical compiles toe_power 3.45, outside the shadow
        # slider's own clamp range; a zero shadow bias must leave it alone even
        # when another slider is active.
        plan = self._film_plan("vision3250d_theatrical")
        self.assertGreater(float(plan.tone.toe_power), 2.5)
        adjusted = apply_render_adjustments(
            plan, RenderAdjustments(midtone_brightness=0.5)
        )
        self.assertEqual(adjusted.tone.toe_power, plan.tone.toe_power)
        self.assertEqual(adjusted.tone.shoulder_power, plan.tone.shoulder_power)
        moved = apply_render_adjustments(
            plan, RenderAdjustments(shadow_transition=0.5)
        )
        self.assertLess(float(moved.tone.toe_power), float(plan.tone.toe_power))


class ShoulderStartOffsetTests(unittest.TestCase):
    def test_positive_offset_raises_compiled_shoulder_start(self) -> None:
        plan = _render_plan(_tone_plan())
        base_start = compiled_curve_transitions(plan.tone)["shoulder_start_ev"]
        adjusted = apply_render_adjustments(
            plan, RenderAdjustments(shoulder_start_offset=1.5)
        )
        new_start = compiled_curve_transitions(adjusted.tone)["shoulder_start_ev"]
        self.assertGreater(new_start, base_start + 0.5)
        self.assertEqual(adjusted.tone.white_ev, plan.tone.white_ev)
        self.assertEqual(adjusted.tone.black_ev, plan.tone.black_ev)
        self.assertEqual(adjusted.tone.shoulder_start_ev, adjusted.tone.latitude_hi_ev)
        _assert_monotone(self, adjusted.tone)

    def test_negative_offset_lowers_shoulder_start_to_zero_latitude(self) -> None:
        plan = _render_plan(_tone_plan())
        adjusted = apply_render_adjustments(
            plan, RenderAdjustments(shoulder_start_offset=-0.5)
        )
        self.assertEqual(adjusted.tone.latitude_hi_ev, 0.0)
        _assert_monotone(self, adjusted.tone)

    def test_extreme_request_stays_inside_display_range_guard(self) -> None:
        # +3 EV against a +4 EV white endpoint: the solver's own clamps must keep
        # the transition inside the curve; the compiled fact reports what was kept.
        plan = _render_plan(_tone_plan())
        adjusted = apply_render_adjustments(
            plan, RenderAdjustments(shoulder_start_offset=3.0)
        )
        compiled = compiled_curve_transitions(adjusted.tone)["shoulder_start_ev"]
        self.assertLess(compiled, plan.tone.white_ev)
        _assert_monotone(self, adjusted.tone)

    def test_neutral_reference_still_ignores_all_adjustments(self) -> None:
        plan = _render_plan(_tone_plan(tone_core="neutral"))
        self.assertIs(
            apply_render_adjustments(
                plan,
                RenderAdjustments(toe_end_offset=-2.0, shoulder_start_offset=2.0),
            ),
            plan,
        )


class ServiceParsingTests(unittest.TestCase):
    def test_offsets_parse_with_their_own_ranges(self) -> None:
        parsed = parse_render_adjustments(
            {"toeEndOffset": -2.5, "shoulderStartOffset": 2.5}
        )
        self.assertEqual(parsed.toe_end_offset, -2.5)
        self.assertEqual(parsed.shoulder_start_offset, 2.5)

    def test_offset_range_rejections(self) -> None:
        for payload in (
            {"toeEndOffset": -3.01},
            {"toeEndOffset": 0.51},
            {"shoulderStartOffset": -0.51},
            {"shoulderStartOffset": 3.01},
        ):
            with self.subTest(payload=payload):
                with self.assertRaises(ValueError):
                    parse_render_adjustments(payload)

    def test_endpoint_mode_parses_and_rejects(self) -> None:
        self.assertEqual(parse_endpoint_mode({}), "adaptive")
        self.assertEqual(parse_endpoint_mode({"endpointMode": "evidence"}), "evidence")
        with self.assertRaises(ValueError):
            parse_endpoint_mode({"endpointMode": "percentile"})

    def test_adjustment_cache_key_carries_the_offsets(self) -> None:
        base = _adjustment_key(RenderAdjustments())
        self.assertEqual(len(base), 7)
        moved = _adjustment_key(RenderAdjustments(toe_end_offset=-1.0))
        self.assertNotEqual(base, moved)
        self.assertEqual(_adjustment_key(None), base)


class CliParsingTests(unittest.TestCase):
    def test_flags_parse(self) -> None:
        from dngscan.cli import parse_args

        args = parse_args(
            [
                "x.dng",
                "--endpoint-mode", "evidence",
                "--toe-end-offset", "-2",
                "--shoulder-start-offset", "1.5",
            ]
        )
        self.assertEqual(args.endpoint_mode, "evidence")
        self.assertEqual(args.toe_end_offset, -2.0)
        self.assertEqual(args.shoulder_start_offset, 1.5)

    def test_default_is_adaptive_and_zero(self) -> None:
        from dngscan.cli import parse_args

        args = parse_args(["x.dng"])
        self.assertEqual(args.endpoint_mode, "adaptive")
        self.assertEqual(args.toe_end_offset, 0.0)
        self.assertEqual(args.shoulder_start_offset, 0.0)

    def test_range_errors(self) -> None:
        from dngscan.cli import parse_args

        for argv in (
            ["x.dng", "--toe-end-offset", "-3.1"],
            ["x.dng", "--toe-end-offset", "0.6"],
            ["x.dng", "--shoulder-start-offset", "-0.6"],
            ["x.dng", "--shoulder-start-offset", "3.1"],
            ["x.dng", "--endpoint-mode", "percentile"],
        ):
            with self.subTest(argv=argv):
                with self.assertRaises(SystemExit), contextlib.redirect_stderr(
                    io.StringIO()
                ):
                    parse_args(argv)


class GuiPageContractTests(unittest.TestCase):
    def test_controls_exist_in_tone_adjust_card(self) -> None:
        from dngscan.gui.page import PAGE

        card = PAGE[
            PAGE.index('<div class="card" id="toneAdjustCard">'):
            PAGE.index('<div class="card">', PAGE.index('<div class="card" id="toneAdjustCard">'))
        ]
        self.assertIn('id="endpointMode"', card)
        self.assertIn('value="adaptive"', card)
        self.assertIn('value="evidence"', card)
        self.assertIn('id="toeEndOffset" min="-3" max="0.5"', card)
        self.assertIn('id="shoulderStartOffset" min="-0.5" max="3"', card)

    def test_payload_carries_the_new_parameters(self) -> None:
        from dngscan.gui.page import PAGE

        body = PAGE[PAGE.index("function payload()"):]
        body = body[: body.index("\n}")]
        self.assertIn('endpointMode:$("#endpointMode").value', body)
        self.assertIn('toeEndOffset:+$("#toeEndOffset").value', body)
        self.assertIn('shoulderStartOffset:+$("#shoulderStartOffset").value', body)

    def test_tone_fact_reports_compiled_transitions(self) -> None:
        from dngscan.gui.page import PAGE

        renderer = PAGE[PAGE.index("function renderDetectedParams(") :]
        renderer = renderer[: renderer.index("\n}")]
        self.assertIn("趾部收黑", renderer)
        self.assertIn("肩部起点", renderer)
        self.assertIn("d.toe_end_ev", renderer)
        self.assertIn("d.shoulder_start_ev", renderer)
        self.assertIn("endpoint_note", renderer)

    def test_new_controls_schedule_preview_and_refresh_facts(self) -> None:
        from dngscan.gui.page import PAGE

        wiring = PAGE[PAGE.index('"toeEndOffset","shoulderStartOffset"') :]
        wiring = wiring[: wiring.index("restoreSettings();")]
        self.assertIn("scheduleLivePreview()", wiring)
        self.assertIn("preparePreview()", wiring)
        self.assertIn('$("#endpointMode").addEventListener("change"', wiring)

    def test_settings_persist_the_new_controls(self) -> None:
        from dngscan.gui.page import PAGE

        save = PAGE[PAGE.index("function saveSettings()"):]
        save = save[: save.index("\n}")]
        self.assertIn("endpointMode", save)
        self.assertIn("toeEndOffset", save)
        self.assertIn("shoulderStartOffset", save)


if __name__ == "__main__":
    unittest.main()
