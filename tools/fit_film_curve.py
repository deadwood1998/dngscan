#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
"""Fit AgX curve parameters to a film stock's published characteristic curves.

This is the formation leg of the film-observation contract
(docs/FILM_OBSERVATION_PLAN.zh-CN.md §4): AgX is not replaced — a film preset is a
*named coordinate* in AgX's existing parameter space, solved by least squares against
the end-to-end (negative + paired print) neutral response derived from spektrafilm's
datasheet-processed profiles. Every preset records its source and fit residual; the
runtime only ever consumes the fitted AgX parameters through the same compiled C1
machinery every render already uses.

End-to-end target construction (contact-print model, per channel c):
    D_neg_c(logE)            negative Status-M density from the profile
    logEp_c = k_c - D_neg_c  print exposure through the negative
    D_p_c   = print_curve_c(logEp_c)
    T_c     = 10^-(D_p_c - Dmin_c)   reflectance relative to paper white
The per-channel balance k_c is solved so the mid-scale neutral exposure (logE = 0 in
spektrafilm's normalization) prints to exactly 18% reflectance — which anchors the
target at dngscan's EV0 -> 0.18 contract by construction. Scene EV = logE / log10(2).

Offline tool: writes dngscan/film_curve_presets.json entries and a comparison plot.
No scipy; a compact Nelder-Mead is included.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from types import SimpleNamespace

import numpy as np

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from dngscan.drt import apply_c1_endpoints, curve_params_from_plan  # noqa: E402

PROFILE_DIR = PROJECT_ROOT / "dngscan_assets" / "spectral" / "spektrafilm"
PRESET_PATH = PROJECT_ROOT / "dngscan" / "film_curve_presets.json"
LOG10_2 = np.log10(2.0)

_KEY_OVERRIDES = {"fujifilm_xtra_400": "superia400"}  # the name people remember
_LABEL_OVERRIDES = {"fujifilm_xtra_400": "Fujifilm Superia X-TRA 400"}


def _short_key(profile_key: str) -> str:
    """Stable preset key from a profile name: vendor prefix dropped, joined."""
    if profile_key in _KEY_OVERRIDES:
        return _KEY_OVERRIDES[profile_key]
    trimmed = profile_key
    for prefix in ("kodak_", "fujifilm_"):
        if trimmed.startswith(prefix):
            trimmed = trimmed[len(prefix):]
            break
    return trimmed.replace("_", "")


def _default_wb(profile_key: str, info: dict) -> str:
    """Combo WB declaration from the stock's balance: tungsten cine stocks are
    calibrated at 3200K (that is what the T suffix means), everything else here is
    daylight film at 5500K."""
    if profile_key.endswith("t") and profile_key.split("_")[-1][:-1].isdigit():
        return "3200k"
    return "5500k"


def discover_stocks() -> dict[str, dict]:
    """Every filming-stage profile in the data directory, negatives and reversals.

    Data-driven on purpose: adding a stock is dropping its (CC BY-SA) profile into
    dngscan_assets/spectral/spektrafilm/ and re-running this tool. Negatives carry
    their declared target print; positives (slides) are their own display medium.
    """
    stocks: dict[str, dict] = {}
    for path in sorted(PROFILE_DIR.glob("*.json")):
        profile = json.loads(path.read_text(encoding="utf-8"))
        info = profile.get("info", {})
        if str(info.get("stage")) != "filming":
            continue
        profile_key = path.stem
        positive = str(info.get("type")) == "positive"
        target_print = info.get("target_print")
        if not positive and not target_print:
            continue
        name = _LABEL_OVERRIDES.get(profile_key, str(info.get("name", profile_key)))
        stocks[_short_key(profile_key)] = {
            "label": name if positive else f"{name}（负片+相纸）",
            "negative": profile_key,
            "print": None if positive else str(target_print),
            "positive": positive,
            "wb": _default_wb(profile_key, info),
        }
    return stocks


STOCKS = discover_stocks()

# Fit domain in scene EV. Below -6.5 both film and AgX sit in their deep toes where
# Status-M densitometry and the display floor both stop being meaningful.
# Dark-surround appearance compensation for projection media (reversal film). The
# classic photographic imaging-science value: slides are built ~1.5x contrastier than
# a bright-surround rendering of the same scene because dark-surround viewing lowers
# perceived contrast by roughly that factor (Hunt/Giorgianni-Madden tradition;
# broadcast's dim-surround convention uses 1.2 for the milder case). A declared
# constant, recorded in each reversal preset's source.model.
DARK_SURROUND_GAMMA = 1.5

FIT_EV_LO, FIT_EV_HI = -6.5, 6.0
TARGET_POINTS_STORED = 192


def _load_curves(name: str) -> tuple[np.ndarray, np.ndarray]:
    data = json.load(open(PROFILE_DIR / f"{name}.json"))["data"]
    log_e = np.asarray(data["log_exposure"], dtype=np.float64)
    density = np.asarray(data["density_curves"], dtype=np.float64)
    keep = np.all(np.isfinite(density), axis=1)
    return log_e[keep], density[keep]


def _build_reversal_target(
    le: np.ndarray, dens: np.ndarray
) -> tuple[np.ndarray, np.ndarray, float]:
    """Slide film is its own display medium: no print stage, densities read directly.

    Reversal film is designed for dark-surround projection: the medium's high gamma
    (~1.6-1.8) is the classic surround compensation — in a dark surround perceived
    contrast drops and the medium must overshoot physically to look right. Mapping raw
    transmittance straight onto a bright-surround display would apply that compensation
    twice (the fits confirmed it: black_ev and toe_power pinned at their bounds with
    the residual concentrated in the shadows). The declared appearance transform
    T^(1/DARK_SURROUND_GAMMA) removes the projection-side compensation once, with the
    exponent from the imaging-science dark-surround convention, and brings the curve
    into the AgX family's expressible range. Per-channel balance then places mid-scale
    at exactly 18% post-transform; the scalar tone target is the luminance of the
    neutral ramp, same definition as the print path. The shadow floor is the slide's
    own Dmax relative to its base, viewed through the same transform."""
    d_min = np.nanmin(dens, axis=0)
    floor = float(
        np.mean(
            np.power(
                np.power(10.0, -(np.nanmax(dens, axis=0) - d_min)),
                1.0 / DARK_SURROUND_GAMMA,
            )
        )
    )
    channels = []
    for c in range(3):
        t = np.power(
            np.power(10.0, -(dens[:, c] - d_min[c])), 1.0 / DARK_SURROUND_GAMMA
        )
        order = np.argsort(t)
        le_mid = np.interp(0.18, t[order], le[order])
        channels.append(np.interp(le + le_mid, le, t))
    luma = np.array([0.2126, 0.7152, 0.0722], dtype=np.float64)
    t_neutral = np.stack(channels, axis=1) @ luma
    ev = le / LOG10_2
    t0 = float(np.interp(0.0, ev, t_neutral))
    if abs(t0 - 0.18) > 5e-4:
        raise RuntimeError(f"reversal mid-gray anchor drifted: T(0)={t0:.5f}")
    return ev, t_neutral, floor


def build_endtoend_target(stock: dict) -> tuple[np.ndarray, np.ndarray, float]:
    """Neutral end-to-end response: scene EV grid, display-linear reflectance, floor.

    The floor is the paper's Dmax expressed as reflectance relative to paper white —
    the print medium's own display black. It is declared from data, not fitted: a
    print never reaches zero, and that lifted shadow floor is a structural part of
    the look AgX must reproduce through target_black_linear.
    """
    le_n, d_neg = _load_curves(stock["negative"])
    if stock.get("positive"):
        return _build_reversal_target(le_n, d_neg)
    le_p, d_prt = _load_curves(stock["print"])
    d_min = d_prt.min(axis=0)
    target_mid_density = d_min + (-np.log10(0.18))
    floor = float(np.mean(np.power(10.0, -(d_prt.max(axis=0) - d_min))))

    channels = []
    for c in range(3):
        # Invert the (monotone) print curve to find the exposure that yields 18% gray,
        # then balance the channel so mid-scale negative density lands exactly there.
        order = np.argsort(d_prt[:, c])
        log_ep_mid = np.interp(target_mid_density[c], d_prt[order, c], le_p[order])
        d_neg_mid = np.interp(0.0, le_n, d_neg[:, c])
        k_c = log_ep_mid + d_neg_mid

        log_ep = k_c - d_neg[:, c]
        d_p = np.interp(log_ep, le_p, d_prt[:, c])
        channels.append(np.power(10.0, -(d_p - d_min[c])))

    # The scalar tone target is the LUMINANCE of the printed neutral ramp, by
    # definition of what the scalar curve carries in this architecture: tone is the Y
    # coordinate, and per-channel highlight behaviour (a runaway layer shifting hue
    # before white) belongs to the separation/per-channel-AgX layers, not to the
    # luminance curve. An arithmetic channel mean would let one imbalanced layer drag
    # the whole tone coordinate — measured on Superia X-TRA 400, whose blue layer
    # (gamma 0.76 vs 0.59, Status M with Fuji's stronger masking couplers) saturates
    # far earlier than red: the mean fitted white_ev 3.5 where the luminance target
    # fits a believable one. Portra's matched layers render both definitions nearly
    # identical, which is exactly why the bug stayed invisible on the baseline stock.
    luma = np.array([0.2126, 0.7152, 0.0722], dtype=np.float64)
    t_neutral = np.stack(channels, axis=1) @ luma
    ev = le_n / LOG10_2
    # Re-anchor exactly: per-channel balance pins each channel at 0.18, the weighted
    # sum can drift by float epsilon only; assert instead of silently re-normalizing.
    t0 = float(np.interp(0.0, ev, t_neutral))
    if abs(t0 - 0.18) > 5e-4:
        raise RuntimeError(f"target mid-gray anchor drifted: T(0)={t0:.5f}")
    return ev, t_neutral, floor


def _agx_curve(ev: np.ndarray, params_vec: np.ndarray, target_black: float = 0.0) -> np.ndarray:
    black_ev, white_ev, contrast, toe_p, shoulder_p, lat_lo, lat_hi = params_vec
    plan = SimpleNamespace(
        black_ev=float(black_ev),
        white_ev=float(white_ev),
        contrast=float(contrast),
        toe_power=float(toe_p),
        shoulder_power=float(shoulder_p),
        latitude_lo_ev=float(lat_lo),
        latitude_hi_ev=float(lat_hi),
        pivot_ev_offset=0.0,
        target_black_linear=float(target_black),
        target_white_linear=1.0,
        curve_gamma=2.2,
    )
    params = curve_params_from_plan(plan)
    return np.asarray(
        apply_c1_endpoints(ev.astype(np.float32), plan, params=params), dtype=np.float64
    )


BOUNDS = np.array(
    [
        (-14.0, -2.5),   # black_ev (reversal media die shallow; -4 pinned pre-surround)
        (2.0, 8.5),      # white_ev
        (1.2, 5.5),      # contrast
        (0.8, 3.5),      # toe_power
        (1.2, 10.0),     # shoulder_power (slides clip highlights harder than any paper)
        (0.0, 1.5),      # latitude_lo_ev
        (0.0, 1.5),      # latitude_hi_ev
    ]
)


def _clip_bounds(vec: np.ndarray) -> np.ndarray:
    return np.clip(vec, BOUNDS[:, 0], BOUNDS[:, 1])


def _residual_stops(ev: np.ndarray, target: np.ndarray, vec: np.ndarray, floor_black: float = 0.0) -> np.ndarray:
    fitted = _agx_curve(ev, _clip_bounds(vec), target_black=floor_black)
    floor = 1e-4
    mask = target > floor
    return np.log2(np.maximum(fitted[mask], floor)) - np.log2(target[mask])


def _objective(ev: np.ndarray, target: np.ndarray, vec: np.ndarray, floor_black: float = 0.0) -> float:
    r = _residual_stops(ev, target, vec, floor_black)
    # RMS with a soft penalty on the worst point so the tail cannot be sacrificed
    # wholesale for the body.
    return float(np.sqrt(np.mean(r * r)) + 0.15 * np.max(np.abs(r)))


def nelder_mead(fn, x0: np.ndarray, steps: np.ndarray, iters: int = 900) -> np.ndarray:
    n = x0.size
    simplex = [x0.copy()]
    for i in range(n):
        v = x0.copy()
        v[i] += steps[i]
        simplex.append(v)
    values = [fn(v) for v in simplex]
    for _ in range(iters):
        order = np.argsort(values)
        simplex = [simplex[i] for i in order]
        values = [values[i] for i in order]
        centroid = np.mean(simplex[:-1], axis=0)
        worst = simplex[-1]
        reflected = centroid + (centroid - worst)
        f_r = fn(reflected)
        if f_r < values[0]:
            expanded = centroid + 2.0 * (centroid - worst)
            f_e = fn(expanded)
            simplex[-1], values[-1] = (
                (expanded, f_e) if f_e < f_r else (reflected, f_r)
            )
        elif f_r < values[-2]:
            simplex[-1], values[-1] = reflected, f_r
        else:
            contracted = centroid + 0.5 * (worst - centroid)
            f_c = fn(contracted)
            if f_c < values[-1]:
                simplex[-1], values[-1] = contracted, f_c
            else:
                best = simplex[0]
                simplex = [best] + [best + 0.5 * (v - best) for v in simplex[1:]]
                values = [values[0]] + [fn(v) for v in simplex[1:]]
    order = np.argsort(values)
    return simplex[order[0]]


def fit_stock(key: str, stock: dict) -> dict:
    ev_full, target_full, floor_black = build_endtoend_target(stock)
    mask = (ev_full >= FIT_EV_LO) & (ev_full <= FIT_EV_HI)
    ev, target = ev_full[mask], target_full[mask]

    x0 = np.array([-8.0, 4.0, 3.0, 1.5, 2.9, 0.1, 0.2])
    steps = np.array([1.0, 0.6, 0.4, 0.25, 0.5, 0.15, 0.15])
    fn = lambda v: _objective(ev, target, v, floor_black)  # noqa: E731
    best = nelder_mead(fn, x0, steps)
    # Warm-restart refinement: a fresh small simplex around the incumbent escapes the
    # collapsed simplex of the first pass; two rounds measurably tighten the deep toe.
    for shrink in (0.25, 0.08):
        best = nelder_mead(fn, _clip_bounds(best), steps * shrink, iters=600)
    best = _clip_bounds(best)
    r = _residual_stops(ev, target, best, floor_black)
    rms, worst = float(np.sqrt(np.mean(r * r))), float(np.max(np.abs(r)))

    idx = np.linspace(0, ev.size - 1, TARGET_POINTS_STORED).round().astype(int)
    return {
        "label": stock["label"],
        "params": {
            "black_ev": round(float(best[0]), 4),
            "white_ev": round(float(best[1]), 4),
            "contrast": round(float(best[2]), 4),
            "toe_power": round(float(best[3]), 4),
            "shoulder_power": round(float(best[4]), 4),
            "latitude_lo_ev": round(float(best[5]), 4),
            "latitude_hi_ev": round(float(best[6]), 4),
            "target_black_linear": round(floor_black, 6),
        },
        "fit": {
            "rms_stop": round(rms, 5),
            "max_stop": round(worst, 5),
            "domain_ev": [FIT_EV_LO, FIT_EV_HI],
        },
        "target_curve": {
            "ev": [round(float(v), 5) for v in ev[idx]],
            "display_linear": [round(float(v), 7) for v in target[idx]],
        },
        "combo": {
            # The film-observation expansion: declared WB (tungsten cine stocks are
            # 3200K by name), and the stock's spectral separation preset when the
            # prefeed calibrator has produced one.
            "wb": stock.get("wb", "5500k"),
            # Push processing changes development, not the emulsion: push variants
            # share the base stock's spectral separation preset.
            "scene_transform": f"{key.split('push')[0]}_d55",
        },
        "source": {
            "film": f"spektrafilm/{stock['negative']}.json",
            "print": (
                f"spektrafilm/{stock['print']}.json"
                if stock.get("print")
                else "none (reversal: the slide is its own display medium)"
            ),
            "license": "CC BY-SA 4.0 (spektrafilm profiles, Andrea Volpato)",
            "model": (
                "reversal transmittance through dark-surround appearance "
                f"T^(1/{DARK_SURROUND_GAMMA:g}), per-channel mid-scale balance, "
                "printed-luminance neutral"
                if stock.get("positive")
                else "contact print, per-channel neutral balance at mid-scale, "
                "Status M densities, printed-luminance neutral"
            ),
        },
    }


def plot(presets: dict, out_path: Path) -> None:
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    fig, axes = plt.subplots(1, len(presets), figsize=(6.4 * len(presets), 4.6), dpi=150)
    axes = np.atleast_1d(axes)
    fig.patch.set_facecolor("#101218")
    for ax, (key, p) in zip(axes, presets.items()):
        ax.set_facecolor("#101218")
        ev = np.array(p["target_curve"]["ev"])
        tgt = np.array(p["target_curve"]["display_linear"])
        vec = np.array([p["params"][k] for k in (
            "black_ev", "white_ev", "contrast", "toe_power",
            "shoulder_power", "latitude_lo_ev", "latitude_hi_ev")])
        fit = _agx_curve(ev, vec, target_black=p["params"].get("target_black_linear", 0.0))
        ax.plot(ev, np.log2(np.maximum(tgt, 1e-5)), color="#f0b35e", lw=2.4,
                label="datasheet end-to-end (neg + print)")
        ax.plot(ev, np.log2(np.maximum(fit, 1e-5)), color="#5ea8f0", lw=1.8, ls="--",
                label=f"AgX fit (rms {p['fit']['rms_stop']:.3f} stop)")
        ax.set_title(p["label"], color="#e6e9f0", fontsize=11)
        ax.set_xlabel("scene EV", color="#c9cfdb")
        ax.set_ylabel("output stops", color="#c9cfdb")
        ax.tick_params(colors="#9aa3b2")
        for s in ax.spines.values():
            s.set_color("#3a4152")
        leg = ax.legend(loc="lower right", framealpha=0.15, fontsize=8.5)
        for t in leg.get_texts():
            t.set_color("#e6e9f0")
    fig.tight_layout()
    fig.savefig(out_path, facecolor=fig.get_facecolor())
    print(f"wrote {out_path}")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--stocks", nargs="*", default=list(STOCKS))
    ap.add_argument("--plot", type=Path,
                    default=PROJECT_ROOT / "docs" / "assets" / "film-curve-fits.png")
    args = ap.parse_args()

    presets = {}
    if PRESET_PATH.is_file():
        presets = json.load(open(PRESET_PATH)).get("presets", {})
    for key in args.stocks:
        preset = fit_stock(key, STOCKS[key])
        presets[key] = preset
        print(f"{key}: rms {preset['fit']['rms_stop']:.4f} stop, "
              f"max {preset['fit']['max_stop']:.4f} stop, params {preset['params']}")
    PRESET_PATH.write_text(
        json.dumps({"version": 1, "presets": presets}, indent=1, ensure_ascii=False)
        + "\n",
        encoding="utf-8",
    )
    print(f"wrote {PRESET_PATH}")
    if args.plot:
        plot(presets, args.plot)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
