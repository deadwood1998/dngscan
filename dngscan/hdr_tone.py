# SPDX-License-Identifier: GPL-3.0-or-later
"""Compile HDR render plans without content-adaptive exposure."""
from __future__ import annotations

import math
from typing import Any

from ._deps import np
from .constants import (
    DEFAULT_HDR_HEADROOM_EV,
    DIFFUSE_WHITE_EV,
    MAX_HDR_HEADROOM_EV,
)
from .hdr_evidence import HdrEvidenceMaps, build_hdr_evidence_maps
from .models import (
    Analysis,
    HdrColorGeometryPlan,
    HdrDisplayTarget,
    HdrRenderPlan,
    HdrSceneMetrics,
    RawBundle,
    RenderPlan,
)
from .scene_scale import scene_scale_contract_from_bundle
from .tone import scene_tone_metrics


def clamp_hdr_capacity_ev(capacity_ev: float) -> float:
    value = float(capacity_ev)
    if not math.isfinite(value):
        raise ValueError("HDR capacity 必须是有限数值")
    if value < 0.0 or value > MAX_HDR_HEADROOM_EV + 1e-9:
        raise ValueError(
            f"HDR capacity 必须在 0–{MAX_HDR_HEADROOM_EV:.6f} EV "
            f"（对应最多 4000 nit @ 100 nit reference white）"
        )
    return value


def build_hdr_display_target(
    capacity_ev: float = DEFAULT_HDR_HEADROOM_EV,
    *,
    reference_white_nits: float = 100.0,
) -> HdrDisplayTarget:
    capacity = clamp_hdr_capacity_ev(capacity_ev)
    peak = float(reference_white_nits) * (2.0 ** capacity)
    return HdrDisplayTarget(
        limiting_gamut="p3",
        white_point="D65",
        reference_white_nits=float(reference_white_nits),
        capacity_ev=capacity,
        peak_nits=peak,
        linear_output=True,
        aces_version="v2.0.0+2025.04.04-derived",
    )


def _scene_metrics(
    bundle: RawBundle,
    analysis: Analysis,
    evidence: HdrEvidenceMaps,
    *,
    scene_transform: str,
    scene_transform_strength: float,
) -> HdrSceneMetrics:
    tone = scene_tone_metrics(
        bundle,
        analysis,
        scene_transform=scene_transform,
        scene_transform_strength=scene_transform_strength,
    )
    contract = scene_scale_contract_from_bundle(bundle)
    broad = float(np.mean(np.asarray(evidence.broad_highlight_weight) > 0.5) * 100.0)
    sparse = float(np.mean(np.asarray(evidence.sparse_emitter_weight) > 0.5) * 100.0)
    union = float(getattr(analysis, "cell_union_pct", 0.0) or 0.0)
    return HdrSceneMetrics(
        body_ev_p50=float(tone.body_ev_p50),
        reliable_tail_ev_p9999=float(
            tone.reliable_tail_ev_p9999
            if math.isfinite(tone.reliable_tail_ev_p9999)
            else tone.tail_ev_p9999
        ),
        diffuse_white_ev=float(DIFFUSE_WHITE_EV),
        broad_highlight_pct=broad,
        sparse_emitter_pct=sparse,
        raw_clip_union_pct=union,
        spatial_evidence=evidence.spatial_evidence,
        scale_confidence=contract.calibration_confidence,
    )


def build_hdr_render_plan(
    bundle: RawBundle,
    analysis: Analysis,
    *,
    capacity_ev: float = DEFAULT_HDR_HEADROOM_EV,
    scene_transform: str = "none",
    scene_transform_strength: float = 1.0,
    midgray_match_scale: float = 1.0,
    evidence: HdrEvidenceMaps | None = None,
) -> HdrRenderPlan:
    """Compile an HDR plan. Headroom only sets target peak; content is not normalized."""
    target = build_hdr_display_target(capacity_ev)
    maps = evidence or build_hdr_evidence_maps(
        bundle,
        analysis,
        scene_transform=scene_transform,
        scene_transform_strength=scene_transform_strength,
    )
    scene = _scene_metrics(
        bundle,
        analysis,
        maps,
        scene_transform=scene_transform,
        scene_transform_strength=scene_transform_strength,
    )
    color = HdrColorGeometryPlan(
        target_peak_nits=target.peak_nits,
        limiting_gamut=target.limiting_gamut,
        chroma_compression_enabled=True,
        gamut_compression_enabled=True,
        white_limiting_enabled=True,
        low_mid_match_enabled=True,
        reveal_start_ev=float(DIFFUSE_WHITE_EV - 0.5),
        reveal_end_ev=float(DIFFUSE_WHITE_EV + 0.5),
        raw_evidence_strength=float(maps.raw_evidence_strength),
    )
    return HdrRenderPlan(
        target=target,
        scene=scene,
        color=color,
        midgray_match_scale=float(midgray_match_scale),
        reference_transform_id="aces2-derived-p3-linear",
    )


def build_dual_rendition_plan(
    bundle: RawBundle,
    analysis: Analysis,
    sdr_plan: RenderPlan,
    *,
    capacity_ev: float = DEFAULT_HDR_HEADROOM_EV,
    scene_transform: str = "none",
    scene_transform_strength: float = 1.0,
    midgray_match_scale: float = 1.0,
) -> Any:
    from .models import DualRenditionPlan

    hdr = build_hdr_render_plan(
        bundle,
        analysis,
        capacity_ev=capacity_ev,
        scene_transform=scene_transform,
        scene_transform_strength=scene_transform_strength,
        midgray_match_scale=midgray_match_scale,
    )
    return DualRenditionPlan(
        sdr=sdr_plan,
        hdr=hdr,
        scale=scene_scale_contract_from_bundle(bundle),
    )
