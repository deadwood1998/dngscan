# SPDX-License-Identifier: GPL-3.0-or-later
from __future__ import annotations

import unittest

from dngscan._deps import np
from dngscan.color import rgb_to_oklab
from dngscan.gui.service import parse_render_adjustments
from dngscan.models import (
    ColorGeometryPlan, RenderAdjustments, RenderPlan, ToneCompressionPlan,
)
from dngscan.render import _apply_display_highlight_chroma_retreat
from dngscan.tone import apply_render_adjustments


def _plan(core: str = "agx") -> RenderPlan:
    tone = ToneCompressionPlan(
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
        tone_core=core,
        pivot_ev_offset=-1.25,
        view_brightness=1.1,
    )
    color = ColorGeometryPlan(
        target_gamut="p3",
        raw_clip_retreat_strength=1.0,
        output_gamut_pressure_pct=0.0,
        display_highlight_chroma_retreat=0.0,
    )
    return RenderPlan(tone=tone, color=color, scene=None)  # type: ignore[arg-type]


class RenderAdjustmentPlanTest(unittest.TestCase):
    def test_zero_adjustments_are_exact_identity(self) -> None:
        plan = _plan()
        self.assertIs(apply_render_adjustments(plan, None), plan)
        self.assertIs(apply_render_adjustments(plan, RenderAdjustments()), plan)

    def test_tone_controls_preserve_automatic_anchors(self) -> None:
        plan = _plan()
        adjusted = apply_render_adjustments(
            plan,
            RenderAdjustments(
                midtone_brightness=1.0,
                midtone_contrast=1.0,
                shadow_transition=1.0,
                highlight_transition=1.0,
            ),
        )
        self.assertEqual(adjusted.tone.black_ev, plan.tone.black_ev)
        self.assertEqual(adjusted.tone.white_ev, plan.tone.white_ev)
        self.assertEqual(adjusted.tone.pivot_ev_offset, plan.tone.pivot_ev_offset)
        self.assertGreater(adjusted.tone.view_brightness, plan.tone.view_brightness)
        self.assertGreater(adjusted.tone.contrast, plan.tone.contrast)
        self.assertLess(adjusted.tone.toe_power, plan.tone.toe_power)
        self.assertLess(adjusted.tone.shoulder_power, plan.tone.shoulder_power)

    def test_highlight_fade_is_color_only(self) -> None:
        plan = _plan()
        adjusted = apply_render_adjustments(
            plan, RenderAdjustments(highlight_fade=1.0)
        )
        self.assertEqual(adjusted.tone, plan.tone)
        self.assertGreater(
            adjusted.color.display_highlight_chroma_retreat,
            plan.color.display_highlight_chroma_retreat,
        )
        self.assertLess(
            adjusted.color.display_highlight_chroma_start,
            plan.color.display_highlight_chroma_start,
        )

    def test_neutral_reference_ignores_adjustments(self) -> None:
        plan = _plan("neutral")
        self.assertIs(
            apply_render_adjustments(
                plan,
                RenderAdjustments(
                    midtone_brightness=1.0,
                    midtone_contrast=1.0,
                    shadow_transition=1.0,
                    highlight_transition=1.0,
                    highlight_fade=1.0,
                ),
            ),
            plan,
        )


class HighlightFadeTest(unittest.TestCase):
    def test_signed_control_changes_chroma_without_moving_lightness(self) -> None:
        rgb = np.asarray([[1.0, 0.68, 0.22]], dtype=np.float32)
        faded = _apply_display_highlight_chroma_retreat(rgb, "p3", 0.3)
        retained = _apply_display_highlight_chroma_retreat(rgb, "p3", -0.3)
        before = rgb_to_oklab(rgb, "p3")
        after_fade = rgb_to_oklab(faded, "p3")
        after_retain = rgb_to_oklab(retained, "p3")
        c0 = float(np.hypot(before[1][0], before[2][0]))
        c_fade = float(np.hypot(after_fade[1][0], after_fade[2][0]))
        c_retain = float(np.hypot(after_retain[1][0], after_retain[2][0]))
        self.assertAlmostEqual(float(before[0][0]), float(after_fade[0][0]), places=5)
        self.assertAlmostEqual(float(before[0][0]), float(after_retain[0][0]), places=5)
        self.assertLess(c_fade, c0)
        self.assertGreater(c_retain, c0)


class RenderAdjustmentParserTest(unittest.TestCase):
    def test_gui_names_parse_to_model(self) -> None:
        parsed = parse_render_adjustments(
            {
                "midtoneBrightness": 0.25,
                "midtoneContrast": -0.1,
                "shadowTransition": 0.4,
                "highlightTransition": 0.5,
                "highlightFade": -0.3,
            }
        )
        self.assertEqual(parsed.midtone_brightness, 0.25)
        self.assertEqual(parsed.highlight_fade, -0.3)

    def test_out_of_range_value_is_rejected(self) -> None:
        with self.assertRaises(ValueError):
            parse_render_adjustments({"highlightFade": 1.01})


if __name__ == "__main__":
    unittest.main()
