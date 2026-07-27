# SPDX-License-Identifier: GPL-3.0-or-later
from __future__ import annotations

import unittest

import numpy as np

from dngscan.tone import rank_trim_reconstructed_highlights


class Raw9ReliableTailTests(unittest.TestCase):
    def test_raw_clip_fraction_removes_brightest_reconstructed_samples(self) -> None:
        ev = np.arange(1000, dtype=np.float32)
        valid = np.ones(1000, dtype=bool)
        reliable = rank_trim_reconstructed_highlights(ev, valid, 10.0)

        self.assertEqual(int(np.count_nonzero(reliable)), 900)
        self.assertTrue(bool(np.all(reliable[:900])))
        self.assertFalse(bool(np.any(reliable[900:])))

    def test_rank_trim_respects_existing_invalid_samples(self) -> None:
        ev = np.arange(1000, dtype=np.float32)
        valid = np.ones(1000, dtype=bool)
        valid[:100] = False
        reliable = rank_trim_reconstructed_highlights(ev, valid, 10.0)

        self.assertEqual(int(np.count_nonzero(reliable)), 810)
        self.assertFalse(bool(np.any(reliable[:100])))
        self.assertFalse(bool(np.any(reliable[910:])))

    def test_zero_clip_is_identity(self) -> None:
        valid = np.asarray([False] + [True] * 999, dtype=bool)
        reliable = rank_trim_reconstructed_highlights(
            np.arange(1000, dtype=np.float32), valid, 0.0
        )
        np.testing.assert_array_equal(reliable, valid)


if __name__ == "__main__":
    unittest.main()
