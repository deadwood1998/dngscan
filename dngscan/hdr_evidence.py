# SPDX-License-Identifier: GPL-3.0-or-later
"""Spatial HDR evidence maps for the dual-rendition bridge.

These maps only modulate reveal permission. They never redefine scene EV 0 or
apply content-adaptive global exposure.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from ._deps import np
from .color import luminance_from_rec2020
from .constants import DIFFUSE_WHITE_EV, EPS, GRAY_EV
from .models import Analysis, RawBundle
from .tone import scene_rec2020_to_float


@dataclass(frozen=True)
class HdrEvidenceMaps:
    scene_ev: Any
    local_ev: Any
    local_excess_ev: Any
    broad_highlight_weight: Any
    sparse_emitter_weight: Any
    clip_confidence: Any
    reveal_permission: Any
    spatial_evidence: str
    raw_evidence_strength: float


def _box_blur(plane: Any, radius: int) -> Any:
    """Cheap separable box blur for topology estimates (not edge-preserving)."""
    src = np.asarray(plane, dtype=np.float32)
    if radius <= 0:
        return src
    pad = int(radius)
    kernel = np.ones(2 * pad + 1, dtype=np.float32)
    kernel /= float(kernel.size)
    tmp = np.empty_like(src)
    out = np.empty_like(src)
    for y in range(src.shape[0]):
        row = np.pad(src[y], pad, mode="edge")
        tmp[y] = np.convolve(row, kernel, mode="valid")
    for x in range(src.shape[1]):
        col = np.pad(tmp[:, x], pad, mode="edge")
        out[:, x] = np.convolve(col, kernel, mode="valid")
    return out


def build_hdr_evidence_maps(
    bundle: RawBundle,
    analysis: Analysis | None = None,
    *,
    scene_transform: str = "none",
    scene_transform_strength: float = 1.0,
    max_side: int = 256,
) -> HdrEvidenceMaps:
    """Build low-resolution soft evidence maps in the current decoder frame."""
    from . import scene_transform as scene_transform_engine

    scene = np.asarray(bundle.scene_rec2020_render)
    h, w = int(scene.shape[0]), int(scene.shape[1])
    scale = max(1, int(np.ceil(max(h, w) / float(max_side))))
    small = scene[::scale, ::scale, :3]
    rec = scene_rec2020_to_float(small, bundle.scene_scale, bundle.exposure_gain)
    wb_adapt = scene_transform_engine.wb_adaptation_ratios(
        bundle.wb_mode, bundle.camera_wb, bundle.daylight_wb
    )
    rec = scene_transform_engine.apply_scene_transform_rec2020(
        rec, scene_transform, scene_transform_strength, wb_adapt
    )
    flat = np.asarray(rec, dtype=np.float32).reshape(-1, 3)
    y = luminance_from_rec2020(flat).astype(np.float32, copy=False).reshape(small.shape[:2])
    scene_ev = np.log2(np.maximum(y, np.float32(EPS))) - np.float32(GRAY_EV)
    local_ev = _box_blur(scene_ev, radius=max(2, min(small.shape[:2]) // 16))
    local_excess = scene_ev - local_ev

    decoder = str(getattr(bundle, "scene_decoder", "libraw") or "libraw")
    if decoder == "libraw" and getattr(bundle, "clip_masks", None) is not None:
        spatial = "cfa"
        strength = 1.0
        masks = np.asarray(bundle.clip_masks, dtype=np.float32)
        mh, mw = masks.shape[:2]
        yy = np.clip((np.arange(small.shape[0]) * scale * mh / h).astype(int), 0, mh - 1)
        xx = np.clip((np.arange(small.shape[1]) * scale * mw / w).astype(int), 0, mw - 1)
        clip = masks[yy][:, xx]
        clip_conf = 1.0 - np.clip(np.max(clip, axis=-1), 0.0, 1.0)
    elif decoder == "coreimage":
        spatial = "aggregate"
        strength = 0.55
        union = 0.0
        if analysis is not None:
            union = float(getattr(analysis, "cell_union_pct", 0.0) or 0.0) / 100.0
        clip_conf = np.full(scene_ev.shape, 1.0 - min(1.0, max(0.0, union)), dtype=np.float32)
    else:
        spatial = "none"
        strength = 0.35
        clip_conf = np.ones_like(scene_ev, dtype=np.float32)

    # Broad near-white sheets vs sparse hot emitters.
    hot = np.clip((scene_ev - np.float32(DIFFUSE_WHITE_EV - 0.25)) / np.float32(1.0), 0.0, 1.0)
    excess = np.clip(local_excess / np.float32(1.5), 0.0, 1.0)
    broad = hot * (1.0 - excess)
    sparse = hot * excess
    # Broad sheets get less peak permission; sparse emitters get more.
    topology = np.clip(0.35 + 0.65 * sparse + 0.15 * (1.0 - broad), 0.0, 1.0)
    luminance_reveal = np.clip(
        (scene_ev - np.float32(DIFFUSE_WHITE_EV - 0.5)) / np.float32(1.0), 0.0, 1.0
    )
    reveal = np.clip(luminance_reveal * topology * clip_conf * np.float32(strength), 0.0, 1.0)

    return HdrEvidenceMaps(
        scene_ev=scene_ev,
        local_ev=local_ev,
        local_excess_ev=local_excess,
        broad_highlight_weight=broad,
        sparse_emitter_weight=sparse,
        clip_confidence=clip_conf,
        reveal_permission=reveal,
        spatial_evidence=spatial,
        raw_evidence_strength=float(strength),
    )
