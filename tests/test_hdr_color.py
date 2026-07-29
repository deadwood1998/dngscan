# SPDX-License-Identifier: GPL-3.0-or-later
"""Phase 3 gates: colour geometry that changes chroma and nothing else."""
from __future__ import annotations

import unittest

import numpy as np

from dngscan.agx import formation_matrices
from dngscan.constants import REC2020_LUMA, RGB_TO_XYZ
from dngscan.hdr_color import (
    apply_channel_lift,
    channel_lift_weights,
    fit_hdr_color_volume,
    neutral_axis_lambda,
    output_luma_weights,
    formation_luma_weights,
    raw_gated_channel_separation,
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
    """Tone/color separation identities of the independent HDR formation."""

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

    def test_bright_channel_can_take_an_independent_path_below_luma_knee(self) -> None:
        # Luminance is below the common knee while red itself is well above it. An HDR DRT
        # may change chroma here; forcing identity would make SDR's scalar knee authoritative
        # over the independent HDR colour formation.
        scene = np.repeat(np.array([[3.0, 0.05, 0.05]], dtype=np.float32), 2000, axis=0)
        formation = np.repeat(np.array([[0.30, 0.10, 0.10]], dtype=np.float32), 2000, axis=0)
        common = channel_lift_weights(scene, KNEE, WHITE, 0.0)
        separated = channel_lift_weights(scene, KNEE, WHITE, 0.5)
        self.assertTrue(bool(np.all(common == 0.0)))
        self.assertGreater(float(separated[:, 0].max()), 0.0)

        out = apply_channel_lift(formation, scene, KNEE, WHITE, BUDGET, 0.5, P3_LUMA)
        self.assertFalse(bool(np.array_equal(out, formation)))
        np.testing.assert_allclose(out @ P3_LUMA, formation @ P3_LUMA, atol=2e-7, rtol=0.0)

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

    def test_cfa_masks_gate_the_corresponding_channel_and_multiclip(self) -> None:
        masks = np.array([[1.0, 0.0, 0.0], [1.0, 1.0, 0.0]], dtype=np.float32)
        rho = raw_gated_channel_separation(0.5, masks)
        np.testing.assert_allclose(rho[0], [0.25, 0.5, 0.5], atol=0.0, rtol=0.0)
        np.testing.assert_allclose(rho[1], [0.0, 0.0, 0.0], atol=0.0, rtol=0.0)

    def test_channel_ev_can_be_measured_in_inset_space(self) -> None:
        scene = np.full((1, 3), 2.0, dtype=np.float32)
        inset = np.array([[8.0, 0.5, 0.5]], dtype=np.float32)
        direct = channel_lift_weights(scene, KNEE, WHITE, 1.0)
        formed = channel_lift_weights(scene, KNEE, WHITE, 1.0, channel_scene_rgb=inset)
        self.assertFalse(bool(np.array_equal(direct, formed)))
        self.assertGreater(float(formed[0, 0]), float(formed[0, 1]))

    def test_formation_luma_uses_the_actual_outset_not_inverse_inset(self) -> None:
        class Plan:
            agx_primaries = "base"

        inset, outset = formation_matrices(Plan())
        got = formation_luma_weights(outset)
        expected = np.asarray(REC2020_LUMA, dtype=np.float64) @ outset
        expected /= expected.sum()
        wrong = np.asarray(REC2020_LUMA, dtype=np.float64) @ np.linalg.inv(inset)
        wrong /= wrong.sum()
        np.testing.assert_allclose(got, expected, atol=5e-8, rtol=0.0)
        self.assertGreater(float(np.max(np.abs(got - wrong))), 0.05)


class GamutProjectorTests(unittest.TestCase):
    def test_ceiling_is_enforced(self) -> None:
        rng = np.random.default_rng(7)
        rgb = rng.uniform(-2.0, 14.0, size=(60000, 3)).astype(np.float32)
        out = fit_hdr_color_volume(rgb, 8.0, "p3")
        self.assertLessEqual(float(np.max(out)), 8.0 + 1e-5)

    def test_floor_is_enforced_by_neutral_axis_projection(self) -> None:
        rgb = np.array([[-0.05, 0.4, 0.2]], dtype=np.float32)
        out = fit_hdr_color_volume(rgb, 8.0, "p3")
        self.assertGreaterEqual(float(np.min(out)), 0.0)
        self.assertFalse(bool(np.array_equal(out, np.clip(rgb, 0.0, 8.0))))

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
        self.assertLess(float(np.max(rel)), 2e-6)

    def test_luma_weights_keep_the_neutral_axis_exact(self) -> None:
        self.assertAlmostEqual(float(np.sum(P3_LUMA)), 1.0, places=7)

    def test_luma_weight_normalisation_does_not_mutate_sdr_matrices(self) -> None:
        before = np.array(RGB_TO_XYZ["P3"], copy=True)
        _ = output_luma_weights("p3")
        self.assertTrue(bool(np.array_equal(RGB_TO_XYZ["P3"], before)))

    def test_projection_reduces_chroma_rather_than_clipping_channels(self) -> None:
        """The output-RGB opponent direction is stable, unlike per-channel clipping."""
        rgb = np.array([[20.0, 4.0, 1.0]], dtype=np.float32)
        out = fit_hdr_color_volume(rgb, 8.0, "p3")
        clipped = np.minimum(rgb, 8.0)
        self.assertFalse(bool(np.allclose(out, clipped)))
        lam = float(neutral_axis_lambda(rgb, 8.0, P3_LUMA)[0])
        self.assertGreater(lam, 0.0)
        self.assertLess(lam, 1.0)


if __name__ == "__main__":
    unittest.main()
