# SPDX-License-Identifier: GPL-3.0-or-later
"""Evaluate the HDR v2 curve over image data: body below K, log-stop shoulder above.

The split is the design. Below `shoulder_start_ev` this delegates to the same darktable
C1 body the plan compiled, and no headroom value reaches that call -- which is why more
HDR range cannot darken shadows the way v1's global gamma did. Above K the compiled
single Hermite segment runs in output stops and clamps at the content peak. The tuple-shaped
runtime carrier also supports an explicitly subdivided reference-white chroma candidate;
that auxiliary candidate is normalized to native Y and never becomes a tone authority.

Vectorised because it runs per channel on full frames, but it is only a restatement of
`hdr_agx_math`; that module stays the float64 oracle this is checked against.
"""
from __future__ import annotations

from typing import Any

from ._deps import np
from .constants import SCENE_MIDGRAY
from .drt import apply_c1_endpoints
from .models import HdrShoulderSegment, HdrToneCurve

_EPS = np.float32(1e-12)


def _hermite_stops(ev: Any, segments: tuple[HdrShoulderSegment, ...]) -> Any:
    """Output stops for every sample, selecting the owning segment per element."""
    e = np.asarray(ev, dtype=np.float32)
    out = np.empty_like(e)
    # Below the first knot the shoulder is not defined; callers mask those samples away,
    # but seeding with z0 keeps any stray sample continuous rather than undefined.
    out[...] = np.float32(segments[0].z0)
    for seg in segments:
        span = np.float32(seg.e1 - seg.e0)
        if span <= 0.0:
            continue
        inside = (e > np.float32(seg.e0)) & (e <= np.float32(seg.e1))
        if not np.any(inside):
            continue
        u = (e[inside] - np.float32(seg.e0)) / span
        u2 = u * u
        u3 = u2 * u
        out[inside] = (
            (np.float32(2.0) * u3 - np.float32(3.0) * u2 + np.float32(1.0)) * np.float32(seg.z0)
            + (u3 - np.float32(2.0) * u2 + u) * span * np.float32(seg.m0)
            + (np.float32(-2.0) * u3 + np.float32(3.0) * u2) * np.float32(seg.z1)
            + (u3 - u2) * span * np.float32(seg.m1)
        )
    out[e > np.float32(segments[-1].e1)] = np.float32(segments[-1].z1)
    return out


def apply_hdr_curve(
    scene_rgb: Any,
    tone: HdrToneCurve,
    formation: Any,
    peak_linear: float | None = None,
) -> Any:
    """Scene-linear channel values -> display-linear HDR output.

    `peak_linear` overrides the plan's endpoint so the conservative reference-white
    chroma candidate can reuse this same primitive at endpoint 1.0. Passing a different
    peak changes only the shoulder; the body below K is identical either way, which is
    what lets the two candidates be compared without a tone difference confounding them.
    """
    rgb = np.asarray(scene_rgb, dtype=np.float32)
    ev = np.log2(np.maximum(rgb, _EPS) / np.float32(SCENE_MIDGRAY))
    body = apply_c1_endpoints(ev, formation)

    segments = tone.shoulder_segments
    if not segments:
        return np.asarray(body, dtype=np.float32)

    if peak_linear is not None and abs(float(peak_linear) - float(tone.peak_linear)) > 1e-12:
        segments = _rescaled_segments(tone, float(peak_linear))
        if not segments:
            return np.asarray(body, dtype=np.float32)

    knee = np.float32(tone.shoulder_start_ev)
    above = ev > knee
    if not np.any(above):
        return np.asarray(body, dtype=np.float32)

    out = np.asarray(body, dtype=np.float32).copy()
    stops = _hermite_stops(ev[above], segments)
    out[above] = np.float32(SCENE_MIDGRAY) * np.exp2(stops)
    return out


def _rescaled_segments(
    tone: HdrToneCurve, peak_linear: float
) -> tuple[HdrShoulderSegment, ...]:
    """Recompile the shoulder for a different endpoint, holding K's anchor fixed.

    Used only for the conservative chroma candidate. Its fixed 1.0 endpoint is not coupled
    to scene W the way the native H endpoint is, so alpha can exceed the single-segment
    bound on high-W plans. This is the sole production opt-in to subdivision. The result
    is normalized to native luminance before mixing and therefore cannot become a second
    tone curve.
    """
    from .hdr_agx_math import compile_hdr_shoulder_from_anchor
    from .constants import OUTPUT_REFERENCE_WHITE_STOPS
    import math

    if peak_linear <= 0.0:
        return ()
    peak_stops = math.log2(peak_linear / SCENE_MIDGRAY)
    first = tone.shoulder_segments[0]
    return compile_hdr_shoulder_from_anchor(
        knee_ev=float(tone.shoulder_start_ev),
        white_ev=float(tone.white_ev),
        knee_stops=float(first.z0),
        knee_slope=float(first.m0),
        peak_stops=peak_stops,
        allow_subdivision=True,
    )
