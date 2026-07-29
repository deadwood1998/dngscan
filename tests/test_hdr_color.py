# SPDX-License-Identifier: GPL-3.0-or-later
"""Phase 3 gates: colour geometry that changes chroma and nothing else."""
from __future__ import annotations

import unittest

import numpy as np

from dngscan.hdr_color import (
    apply_channel_lift,
    channel_lift_weights,
    fit_hdr_color_volume,
    neutral_axis_lambda,
    output_luma_weights,
)

KNEE, WHITE, BUDGET = 2.4739311883324122, 6.5, 3.0
P3_LUMA = output_luma_weights("p3")


def _scene(n: int = 40000, seed: int = 3) -> np.ndarray:
    rng = np.random.default_rng(seed)
    return rng.uniform(0.001, 20.0, size=(n, 3)).astype(np.float32)


def _formation(n: int = 40000, seed: int = 4) -> np.ndarray:
    rng = np.random.default_rng(seed)
    return rng.uniform(0.0, 1.0, size=(n, 3)).astype(np.float32)


class ChannelLiftIdentityTests(unittest.TestCase):
    """The four identities section 8.3 requires, all exact rather than approximate."""

    def test_zero_budget_returns_the_formation_untouched(self) -> None:
        f, s = _formation(), _scene()
        for rho in (0.0, 0.5, 1.0):
            with self.subTest(rho=rho):
                out = apply_channel_lift(f, s, KNEE, WHITE, 0.0, rho, P3_LUMA)
                self.assertTrue(bool(np.array_equal(out, f)))

    def test_rho_zero_needs_no_renormalisation(self) -> None:
        """At rho = 0 the proposal already is the target, so the factor is exactly 1."""
        f, s = _formation(), _scene()
        weights = channel_lift_weights(s, KNEE, WHITE, 0.0)
        expected = f * np.exp2(np.float32(BUDGET) * weights)
        out = apply_channel_lift(f, s, KNEE, WHITE, BUDGET, 0.0, P3_LUMA)
        self.assertTrue(bool(np.array_equal(out, expected)))

    def test_rho_does_not_move_luminance(self) -> None:
        """The whole point: rho is a colour control, not a second tone control."""
        f, s = _formation(), _scene()
        base = apply_channel_lift(f, s, KNEE, WHITE, BUDGET, 0.0, P3_LUMA) @ P3_LUMA
        for rho in (0.25, 0.5, 0.75, 1.0):
            with self.subTest(rho=rho):
                y = apply_channel_lift(f, s, KNEE, WHITE, BUDGET, rho, P3_LUMA) @ P3_LUMA
                rel = np.abs(y - base) / np.maximum(base, 1e-4)
                self.assertLess(float(np.median(rel)), 1e-6)
                self.assertLess(float(np.max(rel)), 1e-2)

    def test_neutral_input_stays_neutral_at_every_rho(self) -> None:
        scene = np.repeat(np.linspace(0.01, 20.0, 2000, dtype=np.float32)[:, None], 3, axis=1)
        form = np.repeat(np.linspace(0.01, 1.0, 2000, dtype=np.float32)[:, None], 3, axis=1)
        for rho in (0.0, 0.5, 1.0):
            with self.subTest(rho=rho):
                out = apply_channel_lift(form, scene, KNEE, WHITE, BUDGET, rho, P3_LUMA)
                self.assertEqual(float(np.max(out.max(1) - out.min(1))), 0.0)

    def test_higher_rho_keeps_more_highlight_chroma(self) -> None:
        """Otherwise rho would be an inert parameter that only looks like a control."""
        rng = np.random.default_rng(11)
        # Saturated scene highlights: the case a common lift washes out.
        scene = rng.uniform(4.0, 40.0, size=(20000, 3)).astype(np.float32)
        scene[:, 2] *= 0.15
        form = np.clip(scene / 60.0, 0.0, 1.0).astype(np.float32)
        sats = []
        for rho in (0.0, 0.5, 1.0):
            out = apply_channel_lift(form, scene, KNEE, WHITE, BUDGET, rho, P3_LUMA)
            hi, lo = out.max(1), out.min(1)
            sats.append(float(np.median((hi - lo) / np.maximum(hi, 1e-6))))
        self.assertLess(sats[0], sats[1])
        self.assertLess(sats[1], sats[2])


class GamutProjectorTests(unittest.TestCase):
    def test_ceiling_is_enforced(self) -> None:
        rng = np.random.default_rng(7)
        rgb = rng.uniform(-2.0, 14.0, size=(60000, 3)).astype(np.float32)
        out = fit_hdr_color_volume(rgb, 8.0, "p3")
        self.assertLessEqual(float(np.max(out)), 8.0 + 1e-5)

    def test_floor_is_left_to_the_sdr_path(self) -> None:
        """Negatives survive here on purpose.

        The SDR render carries out-of-gamut negatives at this stage too and resolves them
        at quantisation. Clamping them here would make the two renditions differ by
        something other than the HDR lift, and that difference is what a gain map encodes.
        """
        rgb = np.array([[-0.05, 0.4, 0.2]], dtype=np.float32)
        out = fit_hdr_color_volume(rgb, 8.0, "p3")
        self.assertTrue(bool(np.array_equal(out, rgb)))

    def test_in_gamut_pixels_are_bit_identical(self) -> None:
        """Reconstructing them as y + 1.0*(arr-y) is not exact in float32."""
        rng = np.random.default_rng(5)
        rgb = rng.uniform(0.0, 3.0, size=(50000, 3)).astype(np.float32)
        self.assertTrue(bool(np.array_equal(fit_hdr_color_volume(rgb, 8.0, "p3"), rgb)))

    def test_neutral_axis_is_preserved_exactly(self) -> None:
        neutral = np.repeat(np.linspace(0.0, 12.0, 500, dtype=np.float32)[:, None], 3, axis=1)
        out = fit_hdr_color_volume(neutral, 8.0, "p3")
        self.assertEqual(float(np.max(out.max(1) - out.min(1))), 0.0)

    def test_luminance_survives_the_projection(self) -> None:
        """Tone decided the luminance; the gamut fit must not be able to overrule it."""
        rng = np.random.default_rng(9)
        rgb = rng.uniform(0.0, 14.0, size=(40000, 3)).astype(np.float32)
        out = fit_hdr_color_volume(rgb, 8.0, "p3")
        y_in, y_out = rgb @ P3_LUMA, out @ P3_LUMA
        inside = (y_in > 1e-3) & (y_in < 8.0)
        rel = np.abs(y_out[inside] - y_in[inside]) / y_in[inside]
        # ~3e-5 floor is structural: the P3 luma row sums to 1.0000274, so the chroma
        # vector is not exactly iso-luminant.
        self.assertLess(float(np.max(rel)), 1e-4)

    def test_projection_reduces_chroma_rather_than_clipping_channels(self) -> None:
        """Per-channel clipping would shift hue; scaling toward neutral does not."""
        rgb = np.array([[20.0, 4.0, 1.0]], dtype=np.float32)
        out = fit_hdr_color_volume(rgb, 8.0, "p3")
        clipped = np.minimum(rgb, 8.0)
        self.assertFalse(bool(np.allclose(out, clipped)))
        lam = float(neutral_axis_lambda(rgb, 8.0, P3_LUMA)[0])
        self.assertGreater(lam, 0.0)
        self.assertLess(lam, 1.0)


if __name__ == "__main__":
    unittest.main()
