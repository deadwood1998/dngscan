#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
"""Generate reusable input stimuli for dngscan/aces2 property tests.

No expected renderer output is stored: it would be circular to generate a
reference with the same NumPy implementation being tested.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

OUT_DIR = ROOT / "tests" / "aces2_vectors"

PEAK_PRESETS = {
    "100": 0.0,
    "500": np.log2(5.0),
    "800": 3.0,
    "1000": np.log2(10.0),
    "2000": np.log2(20.0),
    "4000": np.log2(40.0),
}


def neutral_ramp() -> np.ndarray:
    stops = np.arange(-16, 9, dtype=np.float64)
    values = np.power(2.0, stops)
    return np.stack([values, values, values], axis=-1)


def primary_secondaries() -> np.ndarray:
    colors = [
        (1, 0, 0),
        (0, 1, 0),
        (0, 0, 1),
        (1, 1, 0),
        (0, 1, 1),
        (1, 0, 1),
        (1, 1, 1),
    ]
    scales = [0.01, 0.18, 1.0, 4.0, 16.0, 64.0]
    out = []
    for rgb in colors:
        for s in scales:
            out.append(np.array(rgb, dtype=np.float64) * s)
    return np.stack(out, axis=0)


def color_checker_linear() -> np.ndarray:
    # Approximate ColorChecker linear Rec.2020 values (scene-referred, not exact Munsell).
    patches = np.array(
        [
            [0.031, 0.032, 0.025],
            [0.165, 0.128, 0.089],
            [0.064, 0.078, 0.145],
            [0.042, 0.062, 0.038],
            [0.088, 0.102, 0.165],
            [0.145, 0.198, 0.312],
            [0.128, 0.095, 0.045],
            [0.045, 0.062, 0.128],
            [0.145, 0.078, 0.055],
            [0.038, 0.028, 0.095],
            [0.165, 0.095, 0.045],
            [0.055, 0.095, 0.038],
            [0.095, 0.045, 0.038],
            [0.038, 0.095, 0.055],
            [0.128, 0.055, 0.095],
            [0.165, 0.128, 0.025],
            [0.025, 0.095, 0.128],
            [0.128, 0.165, 0.055],
            [0.095, 0.038, 0.128],
            [0.055, 0.128, 0.165],
            [0.165, 0.055, 0.095],
            [0.095, 0.165, 0.038],
            [0.038, 0.055, 0.165],
            [0.128, 0.038, 0.095],
        ],
        dtype=np.float64,
    )
    return patches


def hue_wheel() -> np.ndarray:
    hues = np.linspace(0.0, 359.0, 360, endpoint=False)
    exposures = np.array([-4, -2, 0, 1, 2, 3, 4, 5], dtype=np.float64)
    out = []
    for ev in exposures:
        scale = 0.18 * (2.0 ** ev)
        for h in hues:
            rad = np.radians(h)
            # Rec.2020-ish saturated RGB on a neutral-balanced wheel
            rgb = np.array([1.0, 1.0, 1.0], dtype=np.float64)
            rgb[0] += np.cos(rad) * 0.8
            rgb[1] += np.cos(rad + 2.094) * 0.8
            rgb[2] += np.cos(rad + 4.189) * 0.8
            rgb = np.maximum(rgb, 0.0) * scale
            out.append(rgb)
    return np.stack(out, axis=0)


def edge_cases() -> np.ndarray:
    return np.array(
        [
            [0, 0, 0],
            [-0.1, 0.05, 0.02],
            [np.nan, 0.1, 0.1],
            [0.1, np.inf, 0.1],
            [0.1, 0.1, -np.inf],
            [1e6, 1e6, 1e6],
        ],
        dtype=np.float64,
    )


def generate() -> None:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    sets = {
        "neutral_ramp": neutral_ramp(),
        "primaries": primary_secondaries(),
        "color_checker": color_checker_linear(),
        "hue_wheel": hue_wheel(),
        "edge_cases": edge_cases(),
    }
    for name, scene in sets.items():
        for peak_name, capacity_ev in PEAK_PRESETS.items():
            path = OUT_DIR / f"{name}__{peak_name}nit.npz"
            np.savez_compressed(
                path,
                scene_rec2020=scene.astype(np.float64),
                capacity_ev=np.float64(capacity_ev),
                peak_nits=np.float64(100.0 * (2.0 ** capacity_ev)),
                provenance=np.array("input stimulus only; no expected renderer output"),
            )
            print(f"wrote {path.relative_to(ROOT)}")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.parse_args()
    generate()


if __name__ == "__main__":
    main()
