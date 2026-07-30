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
    T_c     = (10^-(D_p_c - Dmin_c))^s   reflectance relative to paper white, viewed
                                         through the surround term s (see
                                         SURROUND_GAMMA: identity for reflection
                                         paper, 1/1.5 for dark-surround projection)
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

# Viewing-condition-complete translation (docs/FILM_OBSERVATION_PLAN §4): a medium's
# response is a report read in its native surround, and carrying the report to the
# delivery condition applies the classic surround term between the two conditions —
# and only that term. Perceived contrast drops as the surround darkens
# (Bartleson-Breneman equations; Fairchild 2013, Colour Appearance Models 3rd ed.),
# so media built for dark-surround viewing are ~1.5x contrastier than an
# average-surround rendering of the same scene (projected slides, theatrical prints)
# and dim-surround media ~1.2x (broadcast television convention;
# Giorgianni-Madden tradition). Delivery is declared average surround — the typical
# reading condition of a photograph; sRGB's stricter dim reference (IEC 61966-2-1,
# 64 lux) would add a <=1.2x term that is recorded as a known approximation, not
# modelled. All other colour-appearance phenomena (Hunt, Stevens, ...) need absolute
# luminance, which neither the print nor sRGB pins down: declared out of scope.
SURROUND_GAMMA = {"dark": 1.5, "dim": 1.2, "average": 1.0}
TARGET_SURROUND = "average"

# Native viewing surround of each *display medium* in the chain. Positives project in
# a dark room. These print stocks are theatrical projection films — the cine chain's
# display medium shares the slide's surround, which is why both take the same term.
# Reflection papers are read in bright/average light: their identity term is a
# coincidence of conditions, derived, not assumed.
PRINT_SURROUND = {"kodak_2383": "dark", "kodak_2393": "dark"}


def surround_exponent(native_surround: str) -> float:
    """Exponent translating a medium's transmittance to the delivery surround.

    T_delivery = T_native ** (gamma_target / gamma_native): a dark-surround medium
    (gamma 1.5) flattens by T^(1/1.5) on the average-surround delivery; a medium whose
    native condition matches the delivery passes through exactly.
    """
    return SURROUND_GAMMA[TARGET_SURROUND] / SURROUND_GAMMA[native_surround]


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
) -> tuple[np.ndarray, np.ndarray, np.ndarray, float]:
    """Slide film is its own display medium: no print stage, densities read directly.

    Reversal film is designed for dark-surround projection: the medium's high gamma
    (~1.6-1.8) is the classic surround compensation — in a dark surround perceived
    contrast drops and the medium must overshoot physically to look right. Mapping raw
    transmittance straight onto an average-surround display would apply that
    compensation twice (the fits confirmed it: black_ev and toe_power pinned at their
    bounds with the residual concentrated in the shadows). The surround term
    T^surround_exponent("dark") translates the report to the delivery condition once,
    and brings the curve into the AgX family's expressible range. Per-channel balance
    then places mid-scale at exactly 18% post-transform; the scalar tone target is the
    luminance of the neutral ramp, same definition as the print path. The shadow floor
    is the slide's own Dmax relative to its base, viewed through the same transform."""
    exp = surround_exponent("dark")
    d_min = np.nanmin(dens, axis=0)
    # The floor must be composed exactly like the target: LUMINANCE of the per-channel
    # floors, not their arithmetic mean. Slide dyes carry very different Dmax per layer
    # (Velvia most of all); the mean floor sat 0.57 stop above the luminance target's
    # own asymptote and the fitted toe was pinned to that wrong shelf — the same
    # mean-vs-luminance defect as the scalar tone target, one constant further down.
    luma = np.array([0.2126, 0.7152, 0.0722], dtype=np.float64)
    floor = float(
        np.power(np.power(10.0, -(np.nanmax(dens, axis=0) - d_min)), exp) @ luma
    )
    channels = []
    for c in range(3):
        t = np.power(np.power(10.0, -(dens[:, c] - d_min[c])), exp)
        order = np.argsort(t)
        le_mid = np.interp(0.18, t[order], le[order])
        channels.append(np.interp(le + le_mid, le, t))
    t_channels = np.stack(channels, axis=1)
    t_neutral = t_channels @ luma
    ev = le / LOG10_2
    t0 = float(np.interp(0.0, ev, t_neutral))
    if abs(t0 - 0.18) > 5e-4:
        raise RuntimeError(f"reversal mid-gray anchor drifted: T(0)={t0:.5f}")
    return ev, t_channels, t_neutral, floor


def build_endtoend_target(
    stock: dict, surround_override: str | None = None
) -> tuple[np.ndarray, np.ndarray, np.ndarray, float]:
    """Neutral end-to-end response: EV grid, per-channel display linear, luma, floor.

    The floor is the paper's Dmax expressed as reflectance relative to paper white —
    the print medium's own display black. It is declared from data, not fitted: a
    print never reaches zero, and that lifted shadow floor is a structural part of
    the look AgX must reproduce through target_black_linear.

    surround_override="native" quotes the report verbatim (no surround term) — the
    theatrical variants, per the contract's quotation-vs-translation distinction.
    """
    le_n, d_neg = _load_curves(stock["negative"])
    if stock.get("positive"):
        return _build_reversal_target(le_n, d_neg)
    le_p, d_prt = _load_curves(stock["print"])
    # Surround term for the chain's display medium: theatrical projection prints share
    # the slide's dark surround; reflection papers already match the delivery
    # condition (exponent 1, exactly).
    if surround_override == "native":
        exp = 1.0
    else:
        exp = surround_exponent(PRINT_SURROUND.get(stock["print"], "average"))
    d_min = d_prt.min(axis=0)
    # Mid-scale must read 18% *after* the surround term: T_raw = 0.18^(1/exp).
    target_mid_density = d_min + (-np.log10(0.18)) / exp
    # Luminance-composed floor, same definition as the scalar target (see the
    # reversal path for the mean-vs-luminance defect this avoids).
    luma_w = np.array([0.2126, 0.7152, 0.0722], dtype=np.float64)
    floor = float(np.power(np.power(10.0, -(d_prt.max(axis=0) - d_min)), exp) @ luma_w)

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
        channels.append(np.power(np.power(10.0, -(d_p - d_min[c])), exp))

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
    t_channels = np.stack(channels, axis=1)
    t_neutral = t_channels @ luma
    ev = le_n / LOG10_2
    # Re-anchor exactly: per-channel balance pins each channel at 0.18, the weighted
    # sum can drift by float epsilon only; assert instead of silently re-normalizing.
    t0 = float(np.interp(0.0, ev, t_neutral))
    if abs(t0 - 0.18) > 5e-4:
        raise RuntimeError(f"target mid-gray anchor drifted: T(0)={t0:.5f}")
    return ev, t_channels, t_neutral, floor


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


PARAM_NAMES = (
    "black_ev", "white_ev", "contrast", "toe_power",
    "shoulder_power", "latitude_lo_ev", "latitude_hi_ev",
)

BOUNDS = np.array(
    [
        (-14.0, -2.5),   # black_ev (reversal media die shallow; -4 pinned pre-surround)
        (2.0, 8.5),      # white_ev
        (1.2, 5.5),      # contrast
        (0.8, 3.5),      # toe_power
        (1.2, 10.0),     # shoulder_power (slides clip highlights harder than any paper)
        (0.0, 2.5),      # latitude_lo_ev (cine negatives carry straight-line > 1.5 EV)
        (0.0, 2.5),      # latitude_hi_ev
    ]
)


def _pinned_params(vec: np.ndarray) -> list[str]:
    """Parameters sitting on a fit bound — the declared-extrapolation report.

    An endpoint pinned at a bound *outside the fit domain* (black_ev at -14 when the
    domain stops at -6.5, white_ev at 8.5 against +6.0) is not measured by the data:
    the toe/shoulder it describes lies beyond every sample, and the recorded value is
    an extrapolation the optimizer parked at the fence. Publishing the list keeps that
    honest instead of letting the JSON read as if every parameter were determined.
    Zero latitude is a legitimate interior solution (no linear mid-segment), not a pin.
    """
    pins = []
    for i, name in enumerate(PARAM_NAMES):
        lo, hi = BOUNDS[i]
        if abs(vec[i] - hi) < 5e-3:
            pins.append(f"{name}@{hi:g}")
        elif abs(vec[i] - lo) < 5e-3 and not (name.startswith("latitude") and lo == 0.0):
            pins.append(f"{name}@{lo:g}")
    return pins


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


def _model_note(stock: dict, theatrical: bool = False) -> str:
    """Provenance string: the chain model plus its viewing-condition translation."""
    if stock.get("positive"):
        g = SURROUND_GAMMA["dark"]
        return (
            f"reversal transmittance, dark-surround report to {TARGET_SURROUND} "
            f"surround via T^(1/{g:g}), per-channel mid-scale balance, "
            "printed-luminance neutral"
        )
    surround = PRINT_SURROUND.get(str(stock.get("print")), "average")
    base = (
        "contact print, per-channel neutral balance at mid-scale, "
        "Status M densities, printed-luminance neutral"
    )
    if theatrical:
        return (
            base
            + ", theatrical quotation: dark-surround report carried verbatim "
            "(no surround term — a quotation, not a translation; see contract §8.1)"
        )
    if surround == TARGET_SURROUND:
        return base + ", surround term identity (reflection paper matches delivery)"
    g = SURROUND_GAMMA[surround]
    return (
        base
        + f", {surround}-surround projection print to {TARGET_SURROUND} "
        f"surround via T^(1/{g:g})"
    )


def fit_stock(key: str, stock: dict, theatrical: bool = False) -> dict:
    ev_full, channels_full, target_full, floor_black = build_endtoend_target(
        stock, surround_override="native" if theatrical else None
    )
    mask = (ev_full >= FIT_EV_LO) & (ev_full <= FIT_EV_HI)
    ev, target = ev_full[mask], target_full[mask]
    channels = channels_full[mask]

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

    # Exposure-dependent colour, phase 1 (contract boundary #1 work package): the
    # per-channel ratio field r_c(EV) = T_c / T_neutral along the balanced neutral
    # ramp. This is the stock's own measured layer-saturation differential — the
    # first-order source of "highlights warm as the blue layer saturates" — with
    # provenance identical to the tone target (same channels, same balance, same
    # surround term). r_c(0) = 1 exactly by the per-channel mid-scale balance.
    # Runtime semantics (phase 2): out_c = C(EV_c) * r_c(EV_c) / r_c(EV_Y), which is
    # exactly 1 on the neutral axis (EV_c = EV_Y) — boundary #2 held by construction.
    ratio = channels / np.maximum(target[:, None], 1e-6)
    r0 = np.array([float(np.interp(0.0, ev, ratio[:, c])) for c in range(3)])
    if np.max(np.abs(r0 - 1.0)) > 5e-3:
        raise RuntimeError(f"channel ratio mid-gray anchor drifted: r(0)={r0}")

    return {
        "label": stock["label"] + ("（影院放映外观）" if theatrical else ""),
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
            "pinned": _pinned_params(best),
        },
        "target_curve": {
            "ev": [round(float(v), 5) for v in ev[idx]],
            "display_linear": [round(float(v), 7) for v in target[idx]],
        },
        "channel_ratio_curve": {
            "ev": [round(float(v), 5) for v in ev[idx]],
            "ratio_rgb": [
                [round(float(ratio[i, c]), 5) for c in range(3)] for i in idx
            ],
        },
        "combo": {
            # The film-observation expansion: declared WB (tungsten cine stocks are
            # 3200K by name), and the stock's spectral separation preset when the
            # prefeed calibrator has produced one.
            "wb": stock.get("wb", "5500k"),
            # Push processing changes development, not the emulsion, and the
            # theatrical quotation changes only the viewing translation: both share
            # the base stock's spectral separation preset.
            "scene_transform": (
                f"{key.removesuffix('_theatrical').split('push')[0]}_d55"
            ),
        },
        "source": {
            "film": f"spektrafilm/{stock['negative']}.json",
            "print": (
                f"spektrafilm/{stock['print']}.json"
                if stock.get("print")
                else "none (reversal: the slide is its own display medium)"
            ),
            "license": "CC BY-SA 4.0 (spektrafilm profiles, Andrea Volpato)",
            "model": _model_note(stock, theatrical=theatrical),
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
        stock = STOCKS[key]
        preset = fit_stock(key, stock)
        presets[key] = preset
        print(f"{key}: rms {preset['fit']['rms_stop']:.4f} stop, "
              f"max {preset['fit']['max_stop']:.4f} stop, params {preset['params']}")
        # Theatrical quotation variants for dark-surround projection chains: the
        # report carried verbatim (contract §8.1 — a quotation, not a translation).
        if PRINT_SURROUND.get(str(stock.get("print"))) == "dark":
            tkey = f"{key}_theatrical"
            tpreset = fit_stock(tkey, stock, theatrical=True)
            presets[tkey] = tpreset
            print(f"{tkey}: rms {tpreset['fit']['rms_stop']:.4f} stop, "
                  f"max {tpreset['fit']['max_stop']:.4f} stop")
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
