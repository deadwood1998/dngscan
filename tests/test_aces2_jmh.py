# SPDX-License-Identifier: GPL-3.0-or-later
"""Hellwig 2022 JMh unit tests for dngscan/aces2."""
from __future__ import annotations

import unittest

import numpy as np

from dngscan.aces2.constants import AP0_RGB_TO_XYZ, REFERENCE_LUMINANCE
from dngscan.aces2.jmh import (
    aces_to_jmh,
    hellwig_j_to_y,
    jmh_to_xyz,
    xyz_to_jmh,
    y_to_hellwig_j,
)


class Aces2JmhTests(unittest.TestCase):
    def test_y_j_roundtrip(self) -> None:
        y = np.array([0.0, 1.0, 10.0, 100.0, 1000.0], dtype=np.float64)
        j = y_to_hellwig_j(y)
        back = hellwig_j_to_y(j)
        np.testing.assert_allclose(back, y, rtol=1e-5, atol=1e-4)

    def test_xyz_jmh_neutral_roundtrip(self) -> None:
        rgb_w = np.array([REFERENCE_LUMINANCE, REFERENCE_LUMINANCE, REFERENCE_LUMINANCE])
        xyz_w = rgb_w @ AP0_RGB_TO_XYZ.T
        xyz = np.stack([0.5 * xyz_w, 1.0 * xyz_w], axis=0)
        jmh = xyz_to_jmh(xyz, xyz_w)
        back = jmh_to_xyz(jmh, xyz_w)
        np.testing.assert_allclose(back, xyz, rtol=1e-4, atol=0.5)
        self.assertLess(float(np.max(jmh[:, 1])), 1e-3)

    def test_aces_to_jmh_neutral(self) -> None:
        aces = np.array([[0.18, 0.18, 0.18], [1.0, 1.0, 1.0]], dtype=np.float64)
        jmh = aces_to_jmh(aces, peak_luminance=800.0)
        self.assertLess(float(np.max(jmh[:, 1])), 1e-3)
        self.assertTrue(np.all(jmh[:, 0] > 0))


if __name__ == "__main__":
    unittest.main()
