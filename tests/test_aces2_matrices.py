# SPDX-License-Identifier: GPL-3.0-or-later
"""ACES CTL matrix-convention tests independent of the HDR renderer."""
from __future__ import annotations

import unittest

import numpy as np

from dngscan.aces2.constants import (
    AP0_RGB_TO_XYZ,
    AP0,
    REC2020_D65,
    calculate_cat_matrix,
    calculate_rgb_to_rgb_matrix,
    xy_y_to_xyz,
)
from dngscan.aces2.input_transform import rec2020_d65_to_ap0


# ACES2065-1 to XYZ matrix in the row-vector layout used by CTL. These values
# are published by the ACES reference implementation and do not depend on the
# dngscan renderer.
AP0_TO_XYZ_CTL = np.array(
    [
        [0.9525523959, 0.3439664498, 0.0],
        [0.0, 0.7281660966, 0.0],
        [0.0000936786, -0.0721325464, 1.0088251844],
    ],
    dtype=np.float64,
)

# Rec.2020 D65 to ACES2065-1 D60, including Bradford adaptation, in the same
# row-vector layout. Computed from the published chromaticities and Bradford
# matrix, not from dngscan output vectors.
REC2020_TO_AP0_CTL = np.array(
    [
        [0.679085635, 0.046002003, -0.000573943],
        [0.157700915, 0.859054673, 0.028467768],
        [0.163213451, 0.094943324, 0.972106175],
    ],
    dtype=np.float64,
)


class Aces2MatrixConventionTests(unittest.TestCase):
    def test_ap0_matrix_uses_ctl_row_vector_layout(self) -> None:
        np.testing.assert_allclose(AP0_RGB_TO_XYZ, AP0_TO_XYZ_CTL, atol=2e-10, rtol=0.0)

    def test_bradford_cat_maps_source_white_to_destination_white(self) -> None:
        source = xy_y_to_xyz((*REC2020_D65.white, 1.0))
        destination = xy_y_to_xyz((*AP0.white, 1.0))
        cat = calculate_cat_matrix(REC2020_D65.white, AP0.white)
        np.testing.assert_allclose(source @ cat, destination, atol=2e-9, rtol=0.0)

    def test_rec2020_to_ap0_matches_published_chromaticities(self) -> None:
        matrix = calculate_rgb_to_rgb_matrix(REC2020_D65, AP0)
        np.testing.assert_allclose(matrix, REC2020_TO_AP0_CTL, atol=8e-9, rtol=0.0)
        np.testing.assert_allclose(np.ones(3) @ matrix, np.ones(3), atol=2e-9, rtol=0.0)

    def test_rec2020_to_ap0_is_continuous_at_neutral_axis(self) -> None:
        samples = np.array(
            [[1.0, 1.0, 1.0], [1.0, 1.0, 1.0 + 5e-10], [1.0, 1.0, 1.0 + 2e-9]],
            dtype=np.float64,
        )
        out = rec2020_d65_to_ap0(samples)
        expected = samples @ REC2020_TO_AP0_CTL
        np.testing.assert_allclose(out, expected, atol=2e-9, rtol=0.0)
        self.assertLess(float(np.max(np.abs(np.diff(out, axis=0)))), 2e-8)


if __name__ == "__main__":
    unittest.main()
