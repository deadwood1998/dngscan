# SPDX-License-Identifier: GPL-3.0-or-later
from __future__ import annotations

import unittest
from pathlib import Path

import rawpy

from dngscan.libraw_policy import MIN_RAWPY_VERSION, PINNED_LIBRAW_COMMIT


class LibRawPolicyTests(unittest.TestCase):
    def test_source_pin_is_shared_with_build_script(self) -> None:
        values = {}
        pin_file = Path(__file__).parents[1] / "tools" / "libraw-pin.env"
        for line in pin_file.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line and not line.startswith("#"):
                key, value = line.split("=", 1)
                values[key] = value
        self.assertEqual(values["RAWPY_VERSION"], MIN_RAWPY_VERSION)
        self.assertEqual(values["LIBRAW_COMMIT"], PINNED_LIBRAW_COMMIT)

    def test_runtime_uses_upgraded_rawpy(self) -> None:
        self.assertEqual(rawpy.__version__, MIN_RAWPY_VERSION)
        self.assertGreaterEqual(tuple(rawpy.libraw_version), (0, 22, 0))


if __name__ == "__main__":
    unittest.main()
