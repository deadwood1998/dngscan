#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
"""Measure the RAW 9 chromaticity transport for prefeed window anchors.

The prefeed windows are calibrated in the LibRaw-decoded reference frame. Apple's
RAW 9 interprets the same scene slightly differently, so materials land at slightly
shifted (R/G, B/G) chromaticities. This tool measures that shift empirically:

- decode each corpus frame with BOTH decoders under the same declared WB (5500K, a
  balance both paths realise from their own calibration);
- box-downsample both renders to a coarse common grid — RAW 9's DNG-opcode warp moves
  corners by ~70 px at 24 MP, which vanishes at block scale (same reasoning as the
  existing geometry_correlation diagnostic);
- pair blocks, drop low-signal and near-clipped ones, and compute per-block
  chromaticity ratios r = chroma_raw9 / chroma_libraw;
- per material class, take the window-membership-weighted median ratio; classes with
  insufficient effective support fall back to the global median.

The result is a von Kries-style transport per class, stored in
dngscan/decoder_anchor_transport.json and composed at runtime with the existing WB
window transport. It moves *windows*, never pixels: neutrality and the matrices are
untouched, only where the class weights center.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import numpy as np

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from dngscan.analysis import analyze  # noqa: E402
from dngscan.raw_io import load_raw  # noqa: E402
from dngscan.scene_transform import SCENE_TRANSFORMS  # noqa: E402
from dngscan.tone import scene_intent_rec2020  # noqa: E402

OUT_PATH = PROJECT_ROOT / "dngscan" / "decoder_anchor_transport.json"
GRID = (64, 96)  # coarse block grid: warp-immune, still ~6k samples per frame
MIN_EFFECTIVE_SUPPORT = 40.0  # weighted sample mass below which a class uses global
RATIO_BOUNDS = (0.72, 1.25)  # measured global bg reaches 0.82: the two decoders realise 5500K differently  # implausible transport = measurement problem, refuse

CORPUS = [
    Path.home() / "Pictures" / n
    for n in ("_SDI0150.DNG", "_SDI0133.DNG", "_SDI0199.DNG", "_SDI0237.DNG", "_SDI0165.DNG")
]


def _block_chroma(bundle) -> np.ndarray:
    flat = bundle.scene_rec2020_render.reshape(-1, bundle.scene_rec2020_render.shape[-1])
    rec = scene_intent_rec2020(flat[:, :3], bundle, 1.0)
    h, w = bundle.scene_rec2020_render.shape[:2]
    rec = rec.reshape(h, w, 3)
    gh, gw = GRID
    ys = (np.linspace(0, h, gh + 1)).astype(int)
    xs = (np.linspace(0, w, gw + 1)).astype(int)
    out = np.empty((gh, gw, 3), dtype=np.float64)
    for i in range(gh):
        for j in range(gw):
            out[i, j] = rec[ys[i]:ys[i + 1], xs[j]:xs[j + 1]].reshape(-1, 3).mean(axis=0)
    return out.reshape(-1, 3)


def main() -> int:
    frames = [p for p in CORPUS if p.is_file()]
    if not frames:
        raise SystemExit("corpus unavailable")

    pairs = []
    for frame in frames:
        row = {}
        for decoder in ("libraw", "coreimage"):
            bundle = load_raw(frame, scene_half_size=True, wb_mode="5500k", decoder=decoder)
            analyze(bundle, margin=4, diagnostics=False)
            row[decoder] = _block_chroma(bundle)
        a, b = row["libraw"], row["coreimage"]
        g_a = np.maximum(a[:, 1], 1e-6)
        g_b = np.maximum(b[:, 1], 1e-6)
        # signal / clipping guards on BOTH decodes
        ok = (
            (a.max(axis=1) > 0.02) & (b.max(axis=1) > 0.02)
            & (a.max(axis=1) < 0.90) & (b.max(axis=1) < 0.90)
        )
        chroma_a = np.stack([a[:, 0] / g_a, a[:, 2] / g_a], axis=1)[ok]
        chroma_b = np.stack([b[:, 0] / g_b, b[:, 2] / g_b], axis=1)[ok]
        pairs.append((chroma_a, chroma_b))
        print(f"{frame.name}: {int(ok.sum())} usable blocks")

    chroma_l = np.concatenate([p[0] for p in pairs])
    chroma_r = np.concatenate([p[1] for p in pairs])
    ratios = chroma_r / np.maximum(chroma_l, 1e-6)

    global_ratio = np.median(ratios, axis=0)
    print(f"global transport: rg x{global_ratio[0]:.4f}  bg x{global_ratio[1]:.4f}")

    # Per-class, weighted by the LibRaw-side window membership of every film preset's
    # material family. Classes share names across presets; measure once per name using
    # the union of matching windows (they are near-identical across stocks by design).
    class_windows: dict[str, list] = {}
    for preset in SCENE_TRANSFORMS.values():
        for region in preset.regions:
            class_windows.setdefault(region.name, []).append(region)

    def _window_weight(chroma: np.ndarray, mu: np.ndarray, cov: np.ndarray) -> np.ndarray:
        inv = np.linalg.pinv(cov)
        d = chroma - mu
        mahal = np.einsum("ni,ij,nj->n", d, inv, d)
        return np.exp(np.clip(-0.5 * mahal, -60.0, 0.0))

    per_class = {}
    for name, regions in sorted(class_windows.items()):
        region = regions[0]
        mu = np.asarray(region.mu_rg_bg, dtype=np.float64)
        cov = np.asarray(region.cov_rg_bg, dtype=np.float64) * (region.scale ** 2)
        # Dual-side membership: a block must belong to the class in BOTH decodes (the
        # RAW9-side window pre-shifted by the global transport). Mixed edge blocks —
        # where the coarse pairing straddles a chroma boundary and the two decoders
        # average different mixtures — fail one side and drop out; they were dragging
        # saturated classes to impossible ratios.
        w = _window_weight(chroma_l, mu, cov) * _window_weight(
            chroma_r, mu * global_ratio, cov * np.outer(global_ratio, global_ratio)
        )
        # Percentile trim inside the class for residual pairing outliers.
        active = w > 1e-4
        if active.sum() >= 12:
            for c in range(2):
                lo, hi = np.percentile(ratios[active, c], [10.0, 90.0])
                w = np.where((ratios[:, c] < lo) | (ratios[:, c] > hi), 0.0, w)
        support = float(w.sum())
        if support >= MIN_EFFECTIVE_SUPPORT:
            order = np.argsort(ratios, axis=0)
            ratio = []
            for c in range(2):
                idx = order[:, c]
                cw = np.cumsum(w[idx])
                ratio.append(float(ratios[idx, c][np.searchsorted(cw, cw[-1] / 2.0)]))
            source = "class-weighted"
        else:
            ratio = [float(global_ratio[0]), float(global_ratio[1])]
            source = "global-fallback"
        clamped = [min(max(r, RATIO_BOUNDS[0]), RATIO_BOUNDS[1]) for r in ratio]
        if clamped != ratio:
            source += "+clamped"
        per_class[name] = {
            "ratio_rg_bg": [round(clamped[0], 5), round(clamped[1], 5)],
            "effective_support": round(support, 1),
            "basis": source,
        }
        ratio = clamped
        print(f"  {name:10s} rg x{ratio[0]:.4f} bg x{ratio[1]:.4f}  support {support:8.1f}  ({source})")

    OUT_PATH.write_text(json.dumps({
        "version": 1,
        "coreimage": {
            "global_ratio_rg_bg": [round(float(global_ratio[0]), 5), round(float(global_ratio[1]), 5)],
            "per_class": per_class,
            "source": {
                "method": "dual-decode block-paired chroma transport, 5500K declared WB, "
                          f"grid {GRID[0]}x{GRID[1]}, weighted median per window class",
                "corpus": [p.name for p in frames],
                "camera_scope": "Sigma fp corpus; other bodies inherit the global ratio "
                                "until measured",
            },
        },
    }, indent=1, ensure_ascii=False) + "\n")
    print(f"wrote {OUT_PATH}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
