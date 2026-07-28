# SPDX-License-Identifier: GPL-3.0-or-later
"""Daniele Evo tonescale unit tests for dngscan/aces2."""
from __future__ import annotations

import unittest

import numpy as np

from dngscan.aces2.tone_scale import init_ts_params, tonescale_fwd, tonescale_inv


class Aces2ToneScaleTests(unittest.TestCase):
    def test_fwd_inv_roundtrip(self) -> None:
        for peak in (100.0, 500.0, 800.0, 1000.0, 4000.0):
            params = init_ts_params(peak)
            x = np.array([0.0, 0.001, 0.18, 1.0, 4.0], dtype=np.float64)
            y = tonescale_fwd(x, params)
            back = tonescale_inv(y / params.n_r, params)
            with self.subTest(peak=peak):
                np.testing.assert_allclose(back, x, rtol=1e-5, atol=1e-4)
                self.assertTrue(np.all(np.diff(y) >= -1e-9))

    def test_zero_input_zero_output(self) -> None:
        params = init_ts_params(800.0)
        y = tonescale_fwd(np.array([0.0, -1.0]), params)
        np.testing.assert_allclose(y, [0.0, 0.0], atol=1e-12)

    def test_peak_scales_with_nits(self) -> None:
        p100 = init_ts_params(100.0)
        p1000 = init_ts_params(1000.0)
        x = np.array([64.0], dtype=np.float64)
        self.assertGreater(float(tonescale_fwd(x, p1000)[0]), float(tonescale_fwd(x, p100)[0]))


if __name__ == "__main__":
    unittest.main()
