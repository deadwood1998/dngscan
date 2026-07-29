# SPDX-License-Identifier: GPL-3.0-or-later
"""Phase 2 gates: neutral HDR formation on top of the real AgX core."""
from __future__ import annotations

import dataclasses
import unittest
from pathlib import Path

import numpy as np

from dngscan.analysis import analyze
from dngscan.color import luminance_from_rgb_space
from dngscan.constants import REC2020_LUMA
from dngscan.grade import RENDER_MODE
from dngscan.hdr_agx import (
    achieved_headroom,
    hdr_lift_factor,
    scene_luminance_ev,
    scene_render_to_hdr_display_linear,
)
from dngscan.hdr_agx_plan import compile_hdr_agx_plan, describe_hdr_plan, reliable_tail_ev
from dngscan.models import HdrDisplayTarget
from dngscan.raw_io import load_raw
from dngscan.render import scene_render_to_display_linear
from dngscan.tone import build_render_plan

PICTURES = Path("/Users/itoshikigen/Pictures")
# Daylight, two night frames and a phone capture: the night frames are the ones that
# would expose an HDR capacity quietly re-exposing a dark scene.
FRAMES = {
    "daylight": PICTURES / "_SDI0150.DNG",
    "night_stage": PICTURES / "_SDI0199.DNG",
    "night_bar": PICTURES / "_SDI0133.DNG",
}


def _render_pair(path: Path):
    bundle = load_raw(path, scene_half_size=True)
    analysis, _, _ = analyze(bundle, margin=4, diagnostics=False)
    plan = build_render_plan(bundle, analysis, RENDER_MODE, "p3")
    hdr_plan = compile_hdr_agx_plan(plan, analysis=analysis)
    sdr = scene_render_to_display_linear(bundle, plan, "p3")
    hdr = scene_render_to_hdr_display_linear(bundle, plan, hdr_plan, "p3")
    return bundle, plan, hdr_plan, sdr, hdr


def _p3_luminance(rgb: np.ndarray) -> np.ndarray:
    return luminance_from_rgb_space(rgb.reshape(-1, 3), "p3").reshape(rgb.shape[:-1])


def _rec2020_luminance(rgb: np.ndarray) -> np.ndarray:
    """Scene-side luminance, which is what the allocation window is defined against."""
    w = np.asarray(REC2020_LUMA, dtype=np.float32)
    return np.tensordot(np.asarray(rgb, dtype=np.float32), w, axes=([-1], [0]))


class SceneLuminanceTests(unittest.TestCase):
    def test_midgray_is_ev_zero(self) -> None:
        rgb = np.full((1, 3), 0.18, dtype=np.float32)
        self.assertAlmostEqual(float(scene_luminance_ev(rgb)[0]), 0.0, places=5)

    def test_black_is_finite(self) -> None:
        """log2 of zero must not reach the allocation; the window clamps it anyway."""
        ev = scene_luminance_ev(np.zeros((4, 3), dtype=np.float32))
        self.assertTrue(bool(np.all(np.isfinite(ev))))


class LiftFactorTests(unittest.TestCase):
    def test_zero_budget_is_exactly_one(self) -> None:
        """Not approximately one: bit-identity of the H=0 render depends on this."""
        rgb = np.linspace(0.0, 4.0, 300, dtype=np.float32).reshape(-1, 1).repeat(3, axis=1)
        from dngscan.models import HdrAgxPlan, HdrColorGeometry, HdrToneAllocation

        tone = HdrToneAllocation(2.47, 6.5, 3.0, 0.0, 8.0, 0.5)
        color = HdrColorGeometry(0.0, 0.0, 0.0, 0.6, "base", 0.0)
        fake = HdrAgxPlan(sdr_base=None, display=HdrDisplayTarget(), tone=tone, color=color)
        lift = hdr_lift_factor(rgb, fake)
        self.assertTrue(bool(np.all(lift == np.float32(1.0))))


@unittest.skipUnless(FRAMES["daylight"].is_file(), "sample frames unavailable")
class FormationExitConditionTests(unittest.TestCase):
    """The four conditions Phase 2 must satisfy before Phase 3 may start."""

    def test_zero_budget_render_is_bit_identical_to_sdr(self) -> None:
        """Byte-for-byte, not within a tolerance.

        The HDR dispatcher is a separate function from the SDR one, so nothing but a
        pixel-exact check proves the two have not drifted apart.
        """
        bundle, plan, hdr_plan, sdr, _ = _render_pair(FRAMES["daylight"])
        zeroed = dataclasses.replace(
            hdr_plan, tone=dataclasses.replace(hdr_plan.tone, budget_headroom_ev=0.0)
        )
        rendered = scene_render_to_hdr_display_linear(bundle, plan, zeroed, "p3")
        self.assertTrue(bool(np.array_equal(rendered, sdr)))

    def test_neutral_pixels_stay_neutral(self) -> None:
        """Grey must not acquire a cast, at any rho.

        Replaces a blanket "chromaticity never changes" check that only described Phase 2.
        Channel separation exists precisely to move chromaticity in the highlights, so
        asserting it never moves would now be asserting the feature does nothing.

        Neutrality is judged on the *scene*, not on the SDR output. Those are different
        questions: AgX converges channels toward white in the shoulder, so a coloured
        highlight can leave the SDR render neutral, and separation legitimately restores
        some of that colour. Only a scene that was neutral has nothing to separate.
        """
        for name, path in FRAMES.items():
            if not path.is_file():
                continue
            with self.subTest(frame=name):
                bundle, _, _, _, hdr = _render_pair(path)
                scene = (
                    np.asarray(bundle.scene_rec2020_render, dtype=np.float32)
                    / np.float32(bundle.scene_scale)
                    * np.float32(bundle.exposure_gain)
                )
                span = scene.max(axis=-1) - scene.min(axis=-1)
                neutral = (span < 1e-4) & (scene.max(axis=-1) > 0.05)
                if not bool(np.any(neutral)):
                    continue
                out = hdr[neutral]
                self.assertLess(float(np.max(out.max(axis=-1) - out.min(axis=-1))), 1e-3)

    def test_night_scene_body_does_not_move(self) -> None:
        """An HDR capacity must not re-expose a photograph."""
        for name, path in FRAMES.items():
            if not path.is_file():
                continue
            with self.subTest(frame=name):
                _, _, _, sdr, hdr = _render_pair(path)
                y_sdr, y_hdr = _p3_luminance(sdr), _p3_luminance(hdr)
                body = (y_sdr > 0.02) & (y_sdr < 0.5)
                self.assertTrue(bool(np.any(body)))
                delta = float(np.log2(np.median(y_hdr[body]) / np.median(y_sdr[body])))
                self.assertLess(abs(delta), 1e-4)

    def test_achieved_headroom_stays_within_budget(self) -> None:
        """H_actual <= H_budget <= H_display, all three separately observable."""
        for name, path in FRAMES.items():
            if not path.is_file():
                continue
            with self.subTest(frame=name):
                _, _, hdr_plan, _, hdr = _render_pair(path)
                actual = achieved_headroom(hdr)
                self.assertLessEqual(actual, hdr_plan.tone.budget_headroom_ev + 1e-6)
                self.assertLessEqual(
                    hdr_plan.tone.budget_headroom_ev, hdr_plan.tone.display_headroom_ev + 1e-9
                )

    def test_below_the_knee_hdr_equals_sdr(self) -> None:
        """Shadows and midtones are the SDR render.

        Masked by true scene EV rather than by display luminance, because the two are not
        the same question and the proxy is what let this pass while broken. Channel
        separation is gated by the luminance weight for this reason: without that gate a
        pixel below the knee in luminance but with one bright channel -- a lamp in a dark
        scene -- picked up 0.12 of RGB change here, contradicting the design's own promise
        that nothing below the knee moves. The residual bound covers a boundary effect at
        the knee itself, where this mask and the render's differ by float rounding.
        """
        bundle, _, hdr_plan, sdr, hdr = _render_pair(FRAMES["daylight"])
        scene = (
            np.asarray(bundle.scene_rec2020_render, dtype=np.float32)
            / np.float32(bundle.scene_scale)
            * np.float32(bundle.exposure_gain)
        )
        ev_y = np.log2(np.maximum(_rec2020_luminance(scene), 1e-12) / 0.18)
        below = ev_y <= hdr_plan.tone.knee_ev
        self.assertTrue(bool(np.any(below)))
        self.assertLess(float(np.max(np.abs(hdr[below] - sdr[below]))), 1e-4)
        # Luminance below the knee is untouched to float32 precision, not merely close.
        y_sdr, y_hdr = _p3_luminance(sdr), _p3_luminance(hdr)
        self.assertLess(float(np.max(np.abs(y_hdr[below] - y_sdr[below]))), 1e-6)


@unittest.skipUnless(FRAMES["daylight"].is_file(), "sample frames unavailable")
class PlanCompilationTests(unittest.TestCase):
    def test_headrooms_are_reported_separately(self) -> None:
        _, _, hdr_plan, _, _ = _render_pair(FRAMES["daylight"])
        text = describe_hdr_plan(hdr_plan)
        self.assertIn("预算", text)
        self.assertIn("容量", text)

    def test_bigger_display_target_never_moves_the_knee(self) -> None:
        bundle = load_raw(FRAMES["daylight"], scene_half_size=True)
        analysis, _, _ = analyze(bundle, margin=4, diagnostics=False)
        plan = build_render_plan(bundle, analysis, RENDER_MODE, "p3")
        small = compile_hdr_agx_plan(plan, HdrDisplayTarget(peak_nits=400.0))
        large = compile_hdr_agx_plan(plan, HdrDisplayTarget(peak_nits=4000.0))
        self.assertEqual(small.tone.knee_ev, large.tone.knee_ev)
        self.assertGreaterEqual(large.tone.budget_headroom_ev, small.tone.budget_headroom_ev)

    def test_absent_tail_measurement_does_not_grant_headroom(self) -> None:
        """A missing measurement must not read as an unlimited tail."""
        bundle = load_raw(FRAMES["daylight"], scene_half_size=True)
        analysis, _, _ = analyze(bundle, margin=4, diagnostics=False)
        plan = build_render_plan(bundle, analysis, RENDER_MODE, "p3")
        stripped = dataclasses.replace(plan, scene=None)
        self.assertEqual(reliable_tail_ev(stripped), float(plan.tone.white_ev))

    def test_sdr_plan_is_not_mutated(self) -> None:
        bundle = load_raw(FRAMES["daylight"], scene_half_size=True)
        analysis, _, _ = analyze(bundle, margin=4, diagnostics=False)
        plan = build_render_plan(bundle, analysis, RENDER_MODE, "p3")
        before = dataclasses.asdict(plan.tone)
        compile_hdr_agx_plan(plan, HdrDisplayTarget(peak_nits=4000.0))
        self.assertEqual(dataclasses.asdict(plan.tone), before)


if __name__ == "__main__":
    unittest.main()
