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
    Analysis,
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


# Prototype ceiling on channel separation. Not a calibrated value: the design lists
# Blender's HDR_purity=0.5 as a probe starting point whose semantics are not the same
# thing, so this stays a conservative cap until an EDR corpus says otherwise.
RHO_BASE = 0.5


def compile_channel_separation(
    analysis: Analysis,
    scene_decoder: str = "libraw",
    rho_base: float = RHO_BASE,
) -> float:
    """How much per-channel freedom the RAW evidence supports.

    Highlight chroma is only worth preserving where the sensor actually measured it. Each
    factor below withdraws that freedom for a different reason, and they multiply because
    any one of them alone is sufficient grounds for caution:

    - multi-channel CFA clipping means the hue up there is reconstructed, so separating
      channels would be inventing colour rather than keeping it;
    - a poor SNR ceiling means chroma in the tail is noise, and per-channel expansion
      amplifies exactly that noise;
    - heavy out-of-gamut pressure means the projector will be pulling chroma back anyway,
      so spending range on it just moves work downstream.

    The Core Image path is capped harder still. It has no aligned per-pixel CFA mask, so
    it cannot tell a clipped highlight from a bright one locally, and the design forbids
    fabricating a local rho from aggregate statistics.
    """
    # cell_ge2_of_clipped_pct is conditional on cells that clipped at all, so it reads as
    # a huge number on frames with almost no clipping -- 86 % of 0.08 % of the image. The
    # absolute share is what matters here, and cell_k_of_all_pct already carries it.
    k_all = getattr(analysis, "cell_k_of_all_pct", None) or {}
    multi_pct = sum(float(k_all.get(k, 0.0) or 0.0) for k in (2, 3, 4))
    if not k_all:
        clipped = float(getattr(analysis, "cell_union_pct", 0.0) or 0.0)
        conditional = float(getattr(analysis, "cell_ge2_of_clipped_pct", 0.0) or 0.0)
        multi_pct = clipped * conditional / 100.0
    # Multi-channel clipping is the decisive one: single-channel clipping still leaves two
    # measured channels to place the hue. 10 % of the frame is treated as total loss of
    # confidence, which is deliberately strict for a first cut.
    raw_confidence = float(np.clip(1.0 - multi_pct / 10.0, 0.0, 1.0))

    # The design also lists an SNR confidence factor, and it is deliberately not applied
    # here. snr1_dr is only populated when analyze() runs with diagnostics=True, which a
    # plain --jpeg export does not, so including it would make the same photograph render
    # differently depending on whether --csv or --scan happened to be passed. A render
    # must not depend on an unrelated diagnostic flag. Restore this factor once SNR is
    # measured on the production path, not by inventing a fallback confidence for it.

    gamut = getattr(analysis, "gamut_out_pct", None) or {}
    out_pct = float(gamut.get("Display P3", gamut.get("P3", 0.0)) or 0.0)
    gamut_confidence = float(np.clip(1.0 - out_pct / 20.0, 0.0, 1.0))

    rho = float(rho_base) * raw_confidence * gamut_confidence
    if str(scene_decoder) != "libraw":
        rho = min(rho, 0.25)
    return float(np.clip(rho, 0.0, 1.0))


def compile_hdr_agx_plan(
    sdr_plan: RenderPlan,
    target: HdrDisplayTarget | None = None,
    analysis: Analysis | None = None,
    scene_decoder: str = "libraw",
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
    # Compiled from RAW evidence when it is available. Without an Analysis there is no
    # evidence to justify per-channel freedom, so the answer is none rather than a guess:
    # a neutral HDR is always defensible, an invented highlight hue is not.
    rho = (
        compile_channel_separation(analysis, scene_decoder)
        if analysis is not None
        else 0.0
    )
    color = HdrColorGeometry(
        channel_separation=rho,
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
