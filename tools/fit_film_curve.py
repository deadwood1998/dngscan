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

STOCKS = {
    "portra400": {
        "label": "Kodak Portra 400",
        "negative": "kodak_portra_400",
        "print": "kodak_portra_endura",
    },
    "superia400": {
        "label": "Fujifilm Superia X-TRA 400",
        "negative": "fujifilm_xtra_400",
        "print": "fujifilm_crystal_archive_typeii",
    },
}

# Fit domain in scene EV. Below -6.5 both film and AgX sit in their deep toes where
# Status-M densitometry and the display floor both stop being meaningful.
FIT_EV_LO, FIT_EV_HI = -6.5, 3.5
TARGET_POINTS_STORED = 64


def _load_curves(name: str) -> tuple[np.ndarray, np.ndarray]:
    data = json.load(open(PROFILE_DIR / f"{name}.json"))["data"]
    log_e = np.asarray(data["log_exposure"], dtype=np.float64)
    density = np.asarray(data["density_curves"], dtype=np.float64)
    keep = np.all(np.isfinite(density), axis=1)
    return log_e[keep], density[keep]


def build_endtoend_target(stock: dict) -> tuple[np.ndarray, np.ndarray, float]:
    """Neutral end-to-end response: scene EV grid, display-linear reflectance, floor.

    The floor is the paper's Dmax expressed as reflectance relative to paper white —
    the print medium's own display black. It is declared from data, not fitted: a
    print never reaches zero, and that lifted shadow floor is a structural part of
    the look AgX must reproduce through target_black_linear.
    """
    le_n, d_neg = _load_curves(stock["negative"])
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

    t_neutral = np.mean(np.stack(channels, axis=1), axis=1)
    ev = le_n / LOG10_2
    # Re-anchor exactly: per-channel balance pins each channel at 0.18, the mean can
    # drift by float epsilon only; assert instead of silently re-normalizing.
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
        (-14.0, -4.0),   # black_ev
        (2.0, 8.5),      # white_ev
        (1.2, 5.5),      # contrast
        (0.8, 3.0),      # toe_power
        (1.2, 6.0),      # shoulder_power
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
        "source": {
            "negative": f"spektrafilm/{stock['negative']}.json",
            "print": f"spektrafilm/{stock['print']}.json",
            "license": "CC BY-SA 4.0 (spektrafilm profiles, Andrea Volpato)",
            "model": "contact print, per-channel neutral balance at mid-scale, "
                     "Status M densities, mean-of-channels neutral",
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
