# SPDX-License-Identifier: GPL-3.0-or-later
"""Film curve presets: named coordinates in AgX's parameter space.

A preset pins the complete curve (endpoints included) to values solved offline from a
stock's published characteristic curves (tools/fit_film_curve.py). Scene-adaptive tone
compilation is deliberately bypassed while a preset is active: film's response is fixed
— the same scene always receives the same curve — and that whole-roll consistency is
exactly what the user selected. The EV0 -> 0.18 anchor survives by construction (the
fit target is balanced to 18% at mid-scale and AgX's pivot is immovable), and the
paper-Dmax shadow floor rides in through target_black_linear as a declared, measured
part of the look. User tone adjustments still apply on top, reported as departures
from the named coordinate.
"""
from __future__ import annotations

import json
from dataclasses import replace
from pathlib import Path
from typing import Any

from ._deps import np

FILM_CURVE_PRESETS_JSON = Path(__file__).with_name("film_curve_presets.json")


def _load() -> dict[str, dict[str, Any]]:
    try:
        raw = json.loads(FILM_CURVE_PRESETS_JSON.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    presets = raw.get("presets", {})
    return presets if isinstance(presets, dict) else {}


FILM_CURVE_PRESETS: dict[str, dict[str, Any]] = _load()
FILM_CURVE_CHOICES = ("none",) + tuple(FILM_CURVE_PRESETS)


def film_curve_label(name: str) -> str:
    if name == "none":
        return "无"
    preset = FILM_CURVE_PRESETS.get(name)
    return str(preset.get("label", name)) if preset else name


def validate_film_curve(name: str) -> str:
    if name == "none" or name in FILM_CURVE_PRESETS:
        return name
    raise ValueError(
        f"未知胶片曲线预设：{name}（可选：{'/'.join(FILM_CURVE_CHOICES)}）"
    )


_RATIO_FIELD_CACHE: dict[str, tuple[Any, Any] | None] = {}


def channel_ratio_field(name: str) -> tuple[Any, Any] | None:
    """Measured per-channel ratio field r_c(EV) of a preset; None when absent.

    r_c(EV) = T_c(EV) / T_neutral(EV) along the stock's balanced neutral ramp — the
    layer-saturation differential solved by tools/fit_film_curve.py from the same
    channels, balance and surround term as the tone target. Returns
    (ev_grid, ratios[N, 3]) as read-only float32 arrays for np.interp consumption;
    the grid is ascending and covers the fit domain, and interpolation clamps at the
    ends by construction (deep white ratios approach 1, deep shadow ratios approach
    the dye-floor differential).
    """
    key = str(name)
    if key in _RATIO_FIELD_CACHE:
        return _RATIO_FIELD_CACHE[key]
    preset = FILM_CURVE_PRESETS.get(key)
    raw = preset.get("channel_ratio_curve") if isinstance(preset, dict) else None
    field: tuple[Any, Any] | None = None
    if isinstance(raw, dict) and raw.get("ev") and raw.get("ratio_rgb"):
        ev = np.asarray(raw["ev"], dtype=np.float32)
        ratios = np.asarray(raw["ratio_rgb"], dtype=np.float32)
        if ev.ndim == 1 and ratios.shape == (ev.size, 3) and ev.size >= 2:
            # De-duplicate the stored grid (the fitter's index subsample can repeat
            # rows); np.interp requires strictly usable ascending x.
            keep = np.concatenate(([True], np.diff(ev) > 0))
            ev, ratios = ev[keep], ratios[keep]
            ev.setflags(write=False)
            ratios.setflags(write=False)
            field = (ev, ratios)
    _RATIO_FIELD_CACHE[key] = field
    return field


def apply_film_curve_preset(tone_plan: Any, name: str) -> Any:
    """Replace the scene-compiled curve with the preset's fixed coordinate.

    Only curve-shape fields move; everything else on the tone plan (scene metrics,
    colour policy inputs, tone_core) is untouched, so HDR budgeting still reads the
    real scene while the SDR body renders the declared film curve.
    """
    if name == "none":
        return tone_plan
    preset = FILM_CURVE_PRESETS.get(name)
    if preset is None:
        raise ValueError(f"未知胶片曲线预设：{name}")
    p = preset["params"]
    return replace(
        tone_plan,
        black_ev=float(p["black_ev"]),
        white_ev=float(p["white_ev"]),
        dynamic_range_ev=float(p["white_ev"]) - float(p["black_ev"]),
        contrast=float(p["contrast"]),
        toe_power=float(p["toe_power"]),
        shoulder_power=float(p["shoulder_power"]),
        latitude_lo_ev=float(p["latitude_lo_ev"]),
        latitude_hi_ev=float(p["latitude_hi_ev"]),
        toe_start_ev=-float(p["latitude_lo_ev"]),
        shoulder_start_ev=float(p["latitude_hi_ev"]),
        target_black_linear=float(p.get("target_black_linear", 0.0)),
        curve_preset=str(name),
    )
