# SPDX-License-Identifier: GPL-3.0-or-later
"""GUI page information-display contract.

The docs state the evidence layer's limits; the GUI must state them *at
interaction time*. Three load-bearing wires, each snapped silently once:

1. /prepare failures carry the format-gap guidance (TicoRAW diagnosis, DNG
   Converter / lossless-compression outs) — the page must display j.error
   instead of swallowing it, or the user selects an HE NEF and sees nothing.
2. That guidance is multi-line; the status area needs pre-line whitespace or
   it collapses into an unreadable wall.
3. The per-file two-decoder tier report fires on selection for every decoder,
   not only when Apple RAW is chosen.

These are substring assertions against the served HTML: crude, but they turn
"someone refactored preparePreview and the error path went quiet again" from
a field report into a test failure.
"""
from __future__ import annotations

import unittest

from dngscan.gui.page import PAGE


class PageInformationDisplayTests(unittest.TestCase):
    def test_prepare_failure_is_surfaced_not_swallowed(self) -> None:
        self.assertIn('setStatus(j.error,"err");renderDetectedParams(null);', PAGE)

    def test_status_area_renders_multiline_guidance(self) -> None:
        start = PAGE.index("#status{")
        self.assertIn("white-space:pre-line", PAGE[start:PAGE.index("}", start)])

    def test_tier_report_fires_on_every_prepare(self) -> None:
        prepare = PAGE[PAGE.index("async function preparePreview()"):]
        prepare = prepare[:prepare.index("\n}")]
        self.assertIn("fetchDecodeSupport(body.input);", prepare)

    def test_decode_support_block_exists_and_wraps_lines(self) -> None:
        start = PAGE.index('id="decodeSupport"')
        self.assertIn("white-space:pre-line", PAGE[start:PAGE.index(">", start)])
