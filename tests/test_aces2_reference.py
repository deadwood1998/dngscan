# SPDX-License-Identifier: GPL-3.0-or-later
"""ACES 2-derived renderer properties and reusable input stimuli.

The NPZ files contain no expected renderer output. Authoritative constants and
matrix layout are pinned independently in ``test_aces2_matrices.py``.
"""
from __future__ import annotations

import unittest
from pathlib import Path

import numpy as np

from dngscan.aces2 import render_aces2_hdr_p3_linear

ROOT = Path(__file__).resolve().parents[1]
VECTORS = ROOT / "tests" / "aces2_vectors"

NEUTRAL_REL_TOL = 2e-5
RGB_ABS_TOL = 5e-5


def _load_vector(name: str):
    path = VECTORS / name
    if not path.is_file():
        raise unittest.SkipTest(f"missing {path}; run tools/regen_aces2_vectors.py")
    return np.load(path, allow_pickle=False)


class Aces2ReferenceTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        if not VECTORS.is_dir() or not any(VECTORS.glob("*.npz")):
            raise unittest.SkipTest("missing aces2 vectors; run tools/regen_aces2_vectors.py")

    def test_float32_matches_float64_self(self) -> None:
        from dngscan.aces2.output_transform import _build_context

        _build_context.cache_clear()
        for path in sorted(VECTORS.glob("neutral_ramp__*.npz")):
            data = np.load(path, allow_pickle=False)
            scene = data["scene_rec2020"]
            ev = float(data["capacity_ev"])
            out32 = render_aces2_hdr_p3_linear(scene.astype(np.float32), capacity_ev=ev)
            out64 = render_aces2_hdr_p3_linear(scene.astype(np.float64), capacity_ev=ev)
            with self.subTest(path=path.name):
                self.assertTrue(np.all(np.isfinite(out32)))
                self.assertTrue(np.all(np.isfinite(out64)))
                self.assertLessEqual(float(np.max(np.abs(out32 - out64))), RGB_ABS_TOL)

    def test_neutral_stays_neutral(self) -> None:
        from dngscan.aces2.output_transform import _build_context

        _build_context.cache_clear()
        data = _load_vector("neutral_ramp__800nit.npz")
        scene = data["scene_rec2020"]
        # Exclude extremely hot scene values where per-channel peak clamp applies.
        mask = scene[:, 0] <= 16.0
        out = render_aces2_hdr_p3_linear(scene[mask], capacity_ev=3.0)
        spread = np.max(out, axis=-1) - np.min(out, axis=-1)
        rel = spread / np.maximum(np.max(out, axis=-1), 1e-12)
        self.assertLessEqual(float(np.max(rel)), NEUTRAL_REL_TOL)

    def test_j_tone_monotonic_on_neutral(self) -> None:
        data = _load_vector("neutral_ramp__800nit.npz")
        scene = data["scene_rec2020"]
        out = render_aces2_hdr_p3_linear(scene, capacity_ev=3.0)
        y = out[..., 1]
        pos = scene[:, 0] > 0
        y_pos = y[pos]
        self.assertTrue(np.all(np.diff(y_pos) >= -1e-6))

    def test_sanitize_non_finite(self) -> None:
        dirty = np.array([[np.nan, 0.18, 0.18], [0.18, np.inf, 0.18]], dtype=np.float64)
        out = render_aces2_hdr_p3_linear(dirty, capacity_ev=3.0)
        self.assertTrue(np.all(np.isfinite(out)))

    def test_multi_peak_presets(self) -> None:
        scene = np.array([[0.18, 0.18, 0.18], [1.0, 0.2, 0.1]], dtype=np.float64)
        peaks = {
            "100": 0.0,
            "500": np.log2(5.0),
            "1000": np.log2(10.0),
            "2000": np.log2(20.0),
            "4000": np.log2(40.0),
        }
        prev_out = None
        for label, ev in peaks.items():
            out = render_aces2_hdr_p3_linear(scene, capacity_ev=float(ev))
            with self.subTest(peak=label):
                self.assertTrue(np.all(np.isfinite(out)))
                self.assertGreater(float(out[0, 1]), 0.0)
                self.assertLessEqual(float(np.max(out)), float(2.0 ** ev) + 1e-6)
                if prev_out is not None:
                    self.assertFalse(np.array_equal(out, prev_out))
                prev_out = out

    def test_input_stimuli_are_well_formed(self) -> None:
        """Keep the reusable stimuli valid without blessing self-generated output."""
        for path in sorted(VECTORS.glob("*.npz")):
            data = np.load(path, allow_pickle=False)
            scene = data["scene_rec2020"]
            ev = float(data["capacity_ev"])
            with self.subTest(path=path.name):
                self.assertEqual(scene.shape[-1], 3)
                self.assertGreater(scene.size, 0)
                self.assertTrue(np.isfinite(ev))


if __name__ == "__main__":
    unittest.main()
