# SPDX-License-Identifier: GPL-3.0-or-later
"""Compile an HdrAgxPlan from an SDR plan, RAW evidence and a display target.

The HDR plan is compiled *beside* the SDR plan and never mutates it: the SDR rendition
has to stay byte-identical whether or not an HDR one is also produced, and sharing a
mutable plan is the usual way that guarantee is lost.

Only quantities whose meaning is already established are read. The reliable tail decides
how many stops the scene justifies, so reconstructed highlights cannot buy display range
the sensor never recorded, and the scene median is deliberately not consulted at all --
an HDR capacity must not re-expose a night scene.
"""
from __future__ import annotations

import math

from ._deps import np
from .hdr_agx_math import (
    DIFFUSE_WHITE_EV,
    MAX_LIFT_RATE,
    MINIMUM_WINDOW_EV,
    SMOOTHERSTEP_PEAK_SLOPE,
    compile_budget,
)
from .models import (
    HdrAgxPlan,
    HdrColorGeometry,
    HdrDisplayTarget,
    HdrToneAllocation,
    RenderPlan,
)


def reliable_tail_ev(plan: RenderPlan) -> float:
    """Scene EV of the highest luminance still backed by unclipped RAW evidence.

    Falls back to the unfiltered tail only when the reliable one is unavailable, and to
    the white endpoint if neither is: an absent measurement must not read as an infinite
    tail and hand out headroom on the strength of nothing.
    """
    scene = getattr(plan, "scene", None)
    if scene is None:
        return float(plan.tone.white_ev)
    for name in ("reliable_tail_ev_p9999", "tail_ev_p9999"):
        value = getattr(scene, name, None)
        if value is not None and np.isfinite(value):
            return float(value)
    return float(plan.tone.white_ev)


def compile_hdr_agx_plan(
    sdr_plan: RenderPlan,
    target: HdrDisplayTarget | None = None,
    knee_ev: float | None = None,
    minimum_window_ev: float = MINIMUM_WINDOW_EV,
    max_lift_rate: float = MAX_LIFT_RATE,
) -> HdrAgxPlan:
    """Decide how many extra stops this photograph justifies, and where they go.

    The knee defaults to diffuse white, so HDR range opens above the point where a white
    object is already white rather than lifting the subject. It only marks where the extra
    range *begins*; nothing forces an actual white object to land at 1.0.
    """
    display = target if target is not None else HdrDisplayTarget()
    knee = float(DIFFUSE_WHITE_EV if knee_ev is None else knee_ev)
    white = float(sdr_plan.tone.white_ev)
    tail = reliable_tail_ev(sdr_plan)
    headroom = float(display.display_headroom_ev)

    budget = compile_budget(
        reliable_tail_ev=tail,
        knee_ev=knee,
        white_ev=white,
        display_headroom_ev=headroom,
        minimum_window_ev=minimum_window_ev,
        max_rate=max_lift_rate,
    )

    tone = HdrToneAllocation(
        knee_ev=knee,
        white_ev=white,
        display_headroom_ev=headroom,
        budget_headroom_ev=budget,
        reliable_tail_ev=tail,
        minimum_window_ev=float(minimum_window_ev),
    )
    # Phase 2 renders neutrally: channel_separation is pinned to 0 so the HDR rendition
    # differs from the SDR one in luminance only. Phase 3 compiles it from RAW evidence.
    color = HdrColorGeometry(
        channel_separation=0.0,
        raw_clip_retreat=float(sdr_plan.color.raw_clip_retreat_strength)
        if getattr(sdr_plan, "color", None) is not None
        else 0.0,
        snr_gate=0.0,
        hue_restore=float(sdr_plan.tone.hue_restore),
        primaries_preset=str(sdr_plan.tone.agx_primaries),
        gamut_fit_margin=0.0,
    )
    return HdrAgxPlan(sdr_base=sdr_plan, display=display, tone=tone, color=color)


def describe_hdr_plan(plan: HdrAgxPlan) -> str:
    """One line naming all three headrooms, because only reporting one hides the point."""
    tone = plan.tone
    window = tone.white_ev - tone.knee_ev
    if tone.budget_headroom_ev <= 0.0:
        return (
            f"HDR: 无预算（可靠尾部 {tone.reliable_tail_ev:+.2f}EV，窗宽 {window:.2f}EV，"
            f"显示容量 +{tone.display_headroom_ev:.2f}EV）"
        )
    rate = (
        tone.budget_headroom_ev * SMOOTHERSTEP_PEAK_SLOPE / window
        if window > 0
        else float("inf")
    )
    return (
        f"HDR: 预算 +{tone.budget_headroom_ev:.2f}EV / 容量 +{tone.display_headroom_ev:.2f}EV；"
        f"knee {tone.knee_ev:+.2f}EV，窗宽 {window:.2f}EV，提升速率 {rate:.2f}EV/EV，"
        f"可靠尾部 {tone.reliable_tail_ev:+.2f}EV"
    )
