# SPDX-License-Identifier: GPL-3.0-or-later
"""Film curve preset gates: declared coordinates, pinned residuals, HDR feasibility."""
from __future__ import annotations

import unittest
from pathlib import Path
from types import SimpleNamespace

import numpy as np

from dngscan.constants import OUTPUT_REFERENCE_WHITE_STOPS
from dngscan.drt import apply_c1_endpoints
from dngscan.film_curve import (
    FILM_CURVE_CHOICES,
    FILM_CURVE_PRESETS,
    apply_film_curve_preset,
    validate_film_curve,
)
from dngscan.hdr_agx_math import (
    body_anchor_at_ev,
    compile_hdr_shoulder,
    requested_headroom_ev,
    validate_hdr_shoulder,
)

SAMPLE = Path.home() / "Pictures" / "_SDI0150.DNG"
SAMPLE_NIGHT = Path.home() / "Pictures" / "_SDI0199.DNG"

RMS_GATE = 0.08
MAX_GATE = 0.15
STORED_RESIDUAL_SLACK = 0.005

CURVE_FIELDS = (
    "black_ev", "white_ev", "contrast", "toe_power", "shoulder_power",
    "latitude_lo_ev", "latitude_hi_ev", "target_black_linear",
)


def _compiled_curve(params: dict, ev: np.ndarray) -> np.ndarray:
    plan = SimpleNamespace(
        black_ev=params["black_ev"],
        white_ev=params["white_ev"],
        contrast=params["contrast"],
        toe_power=params["toe_power"],
        shoulder_power=params["shoulder_power"],
        latitude_lo_ev=params["latitude_lo_ev"],
        latitude_hi_ev=params["latitude_hi_ev"],
        pivot_ev_offset=0.0,
        target_black_linear=params["target_black_linear"],
        target_white_linear=1.0,
        curve_gamma=2.2,
    )
    return np.asarray(
        apply_c1_endpoints(ev.astype(np.float32), plan), dtype=np.float64
    )


class PresetRegistryTests(unittest.TestCase):
    def test_flagship_presets_exist_with_provenance(self) -> None:
        for name in ("portra400", "superia400"):
            preset = FILM_CURVE_PRESETS[name]
            self.assertIn("CC BY-SA", preset["source"]["license"])
            self.assertTrue(preset["source"]["negative"])
            self.assertTrue(preset["source"]["print"])
            self.assertIn(name, FILM_CURVE_CHOICES)

    def test_validate_rejects_unknown(self) -> None:
        self.assertEqual(validate_film_curve("none"), "none")
        with self.assertRaises(ValueError):
            validate_film_curve("kodachrome_imaginary")


class ResidualGateTests(unittest.TestCase):
    """The stored fit must reproduce against the stored target — and stay in gate."""

    def test_residuals_recompute_within_gates(self) -> None:
        for name, preset in FILM_CURVE_PRESETS.items():
            with self.subTest(preset=name):
                ev = np.array(preset["target_curve"]["ev"], dtype=np.float64)
                target = np.array(
                    preset["target_curve"]["display_linear"], dtype=np.float64
                )
                fitted = _compiled_curve(preset["params"], ev)
                mask = target > 1e-4
                r = np.log2(np.maximum(fitted[mask], 1e-4)) - np.log2(target[mask])
                rms = float(np.sqrt(np.mean(r * r)))
                worst = float(np.max(np.abs(r)))
                self.assertLessEqual(rms, RMS_GATE)
                self.assertLessEqual(worst, MAX_GATE)
                # Regression pinning: drift cannot hide beneath the ceiling.
                self.assertLessEqual(
                    abs(rms - float(preset["fit"]["rms_stop"])),
                    STORED_RESIDUAL_SLACK,
                )

    def test_ev0_anchor_is_exact(self) -> None:
        for name, preset in FILM_CURVE_PRESETS.items():
            with self.subTest(preset=name):
                t0 = float(_compiled_curve(preset["params"], np.array([0.0]))[0])
                self.assertLess(abs(t0 - 0.18), 1e-5)

    def test_paper_floor_is_declared_and_reached(self) -> None:
        """The print's Dmax floor must survive into the compiled curve's deep toe."""
        for name, preset in FILM_CURVE_PRESETS.items():
            with self.subTest(preset=name):
                floor = float(preset["params"]["target_black_linear"])
                self.assertGreater(floor, 0.0)
                deep = float(_compiled_curve(preset["params"], np.array([-12.0]))[0])
                self.assertAlmostEqual(deep, floor, delta=floor * 0.25)


class HdrFeasibilityTests(unittest.TestCase):
    def test_preset_contrasts_compile_valid_shoulders_across_headroom(self) -> None:
        """Film bodies must keep the HDR contract: a validated shoulder everywhere."""
        for name, preset in FILM_CURVE_PRESETS.items():
            contrast = float(preset["params"]["contrast"])
            for knee in (0.0, 0.2):
                _, knee_stops, knee_slope = body_anchor_at_ev(knee, contrast)
                for headroom_tenths in range(5, 54, 6):
                    headroom = headroom_tenths / 10.0
                    for tail in (3.0, 5.5, 8.0, 12.0):
                        requested = requested_headroom_ev(tail, headroom)
                        if requested <= 0.0:
                            continue
                        white = min(max(tail + 0.3, 3.0), 8.5)
                        peak = OUTPUT_REFERENCE_WHITE_STOPS + requested
                        segments = compile_hdr_shoulder(
                            knee, white, peak, contrast, allow_subdivision=True
                        )
                        with self.subTest(
                            preset=name, knee=knee, headroom=headroom, tail=tail
                        ):
                            ok, reason = validate_hdr_shoulder(
                                segments, knee_slope, peak
                            )
                            self.assertTrue(ok, msg=reason)


class ApplyPresetTests(unittest.TestCase):
    def _tone_stub(self):
        from dngscan.models import ToneCompressionPlan

        return ToneCompressionPlan(
            target_gamut="Rec2020",
            luma_p1=0.01, luma_p50=0.18, luma_p99=1.0, luma_p999=2.0,
            black_ev=-7.0, white_ev=4.5, dynamic_range_ev=11.5,
            contrast=3.0, toe_power=1.5, shoulder_power=3.3,
            chroma_p95=0.0, negative_rgb_pct=0.0, over_rgb_pct=0.0,
            toe_start_ev=-3.0, shoulder_start_ev=0.2, use_c1_endpoints=True,
        )

    def test_none_is_identity(self) -> None:
        tone = self._tone_stub()
        self.assertIs(apply_film_curve_preset(tone, "none"), tone)

    def test_preset_pins_every_curve_field(self) -> None:
        tone = apply_film_curve_preset(self._tone_stub(), "portra400")
        p = FILM_CURVE_PRESETS["portra400"]["params"]
        for field in CURVE_FIELDS:
            self.assertEqual(getattr(tone, field), float(p[field]), msg=field)
        self.assertEqual(tone.curve_preset, "portra400")
        self.assertEqual(tone.dynamic_range_ev, tone.white_ev - tone.black_ev)


class FilmPrefeedPresetTests(unittest.TestCase):
    """The film-separation prefeed presets obey the scene-transform contract."""

    PRESETS = ("portra400_d55", "superia400_d55")

    def test_presets_load_with_confidence_and_windows(self) -> None:
        from dngscan.scene_transform import SCENE_TRANSFORMS

        for name in self.PRESETS:
            with self.subTest(preset=name):
                preset = SCENE_TRANSFORMS[name]
                self.assertEqual(preset.illuminant, "D55")
                names = {r.name for r in preset.regions}
                self.assertLessEqual(
                    {"skin", "foliage", "cyan", "neutral", "magenta"}, names
                )
                for region in preset.regions:
                    self.assertGreater(region.confidence, 0.0)
                    self.assertLessEqual(region.confidence, 1.0)

    def test_neutral_axis_survives_film_separation(self) -> None:
        from dngscan._deps import np as _np
        from dngscan.scene_transform import apply_scene_transform_rec2020

        gray = _np.full((64, 3), 0.18, dtype=_np.float32)
        for name in self.PRESETS:
            with self.subTest(preset=name):
                out = apply_scene_transform_rec2020(gray, name, 1.0)
                _np.testing.assert_allclose(out, gray, atol=2e-3)

    def test_film_separation_changes_nonneutral_colour(self) -> None:
        from dngscan._deps import np as _np
        from dngscan.scene_transform import SCENE_TRANSFORMS, apply_scene_transform_rec2020

        for name in self.PRESETS:
            with self.subTest(preset=name):
                skin_region = next(
                    r for r in SCENE_TRANSFORMS[name].regions if r.name == "skin"
                )
                rg, bg = skin_region.mu_rg_bg
                skin_like = _np.array([[0.18 * rg, 0.18, 0.18 * bg]] * 8,
                                      dtype=_np.float32)
                out = apply_scene_transform_rec2020(skin_like, name, 1.0)
                self.assertGreater(
                    float(_np.max(_np.abs(out - skin_like))), 1e-4
                )


@unittest.skipUnless(
    SAMPLE.is_file() and SAMPLE_NIGHT.is_file(), "sample frames unavailable"
)
class WholeRollConsistencyTests(unittest.TestCase):
    def test_two_scenes_receive_the_identical_curve(self) -> None:
        """Film response is fixed: day and night frames share one declared curve."""
        from dngscan.analysis import analyze
        from dngscan.grade import RENDER_MODE
        from dngscan.raw_io import load_raw
        from dngscan.tone import build_render_plan

        curves = []
        scenes = []
        for path in (SAMPLE, SAMPLE_NIGHT):
            bundle = load_raw(path, scene_half_size=True)
            analysis, _, _ = analyze(bundle, margin=4, diagnostics=False)
            plan = build_render_plan(
                bundle, analysis, RENDER_MODE, "p3", film_curve="portra400"
            )
            curves.append(tuple(getattr(plan.tone, f) for f in CURVE_FIELDS))
            scenes.append(plan.scene)
        self.assertEqual(curves[0], curves[1])
        # Scene metrics stay scene-derived — HDR budgeting still reads the capture.
        self.assertNotEqual(
            scenes[0].reliable_tail_ev_p9999, scenes[1].reliable_tail_ev_p9999
        )


if __name__ == "__main__":
    unittest.main()
