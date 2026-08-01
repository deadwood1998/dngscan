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

from dngscan.gui.page import PAGE, render_page


class PageInformationDisplayTests(unittest.TestCase):
    def test_prepare_failure_is_surfaced_not_swallowed(self) -> None:
        self.assertIn('setStatus(j.error,"err");renderDetectedParams(null);', PAGE)

    def test_realtime_preview_is_fixed_960_and_has_no_resolution_control(self) -> None:
        served_page = render_page("").decode("utf-8")
        self.assertIn('id="previewLiveBadge">实时 · 960px', served_page)
        self.assertNotIn('id="previewBtn"', PAGE)
        self.assertNotIn("更新预览", PAGE)
        self.assertNotIn("previewLongEdge", PAGE)
        self.assertNotIn("previewResolution", PAGE)

    def test_realtime_preview_uses_generation_abort_and_automatic_first_frame(self) -> None:
        self.assertIn("PREVIEW_GENERATION", PAGE)
        self.assertIn("new AbortController()", PAGE)
        self.assertIn("body.generation=generation", PAGE)
        self.assertIn("j.superseded", PAGE)
        prepare = PAGE[PAGE.index("async function preparePreview()") :]
        prepare = prepare[: prepare.index("\n}")]
        self.assertIn("PREVIEW_READY=true", prepare)
        self.assertIn("await requestPreview();", prepare)

    def test_image_controls_schedule_live_preview(self) -> None:
        wiring = PAGE[PAGE.index('$("#ev").oninput=') : PAGE.index("restoreSettings();")]
        for control in ("ev", "gradeStrength", "punch", "sceneTransformStrength"):
            with self.subTest(control=control):
                anchor = f'$("#{control}")'
                start = wiring.index(anchor)
                self.assertIn("scheduleLivePreview()", wiring[start : start + 180])
        for control in (
            "midtoneBrightness",
            "midtoneContrast",
            "shadowTransition",
            "highlightTransition",
            "highlightFade",
        ):
            self.assertIn(f'"{control}"', wiring)
        loop = wiring[wiring.index('"midtoneBrightness"') :]
        self.assertIn('forEach(id=>$("#"+id).oninput=', loop)
        self.assertIn("scheduleLivePreview()", loop)

    def test_status_area_renders_multiline_guidance(self) -> None:
        start = PAGE.index("#status{")
        self.assertIn("white-space:pre-line", PAGE[start:PAGE.index("}", start)])

    def test_tier_report_runs_once_per_file_not_every_prepare(self) -> None:
        selection = PAGE[PAGE.index('$("#filePicker").addEventListener') :]
        selection = selection[: selection.index("async function listOutDir")]
        self.assertIn("fetchDecodeSupport(result.path);", selection)
        prepare = PAGE[PAGE.index("async function preparePreview()"):]
        prepare = prepare[:prepare.index("\n}")]
        self.assertNotIn("fetchDecodeSupport", prepare)
        self.assertIn("RAW9_PROBE_REQUESTS", PAGE)
        self.assertEqual(PAGE.count('postJob("/raw9-support"'), 1)

    def test_layout_targets_desktop_landscape(self) -> None:
        # This GUI ships desktop/laptop web only (16:9/16:10 landscape): the
        # canvas opens wide, the preview column owns the viewport height, and
        # the image fills its box instead of guessing a max-height.
        self.assertIn("max-width:1900px", PAGE)
        self.assertIn("height:calc(100vh - 24px)", PAGE)
        self.assertIn("object-fit:contain", PAGE)
        self.assertNotIn("max-height:calc(100vh", PAGE)

    def test_file_picker_sits_below_title_and_uses_full_width(self) -> None:
        top_bar = PAGE[
            PAGE.index('<div class="topBar">'):
            PAGE.index("</div>", PAGE.index('<div class="topBar">'))
        ]
        self.assertLess(top_bar.index("<h1>"), top_bar.index('id="filePicker"'))

        top_bar_start = PAGE.index(".topBar{")
        top_bar_rule = PAGE[top_bar_start:PAGE.index("}", top_bar_start)]
        self.assertIn("flex-direction:column", top_bar_rule)
        self.assertIn("align-items:stretch", top_bar_rule)

        start = PAGE.index(".topBar input[type=file]{")
        rule = PAGE[start:PAGE.index("}", start)]
        self.assertIn("width:100%", rule)
        self.assertNotIn("max-width", rule)

    def test_measured_facts_sit_next_to_their_controls(self) -> None:
        # Data-to-function adjacency: each measured fact renders inside the
        # block whose control consumes it, not in a separate overview card the
        # user must scroll back to while dragging a slider.
        for fact_id, anchor in (
            ("hdrSceneFact", 'id="hdrHeadroom"'),   # scene headroom by the HDR slider
            ("evFact", 'id="evReferenceBtn"'),      # body median by the EV controls
            ("wbFact", 'id="lensFilter"'),          # WB degradation closes the WB row
            ("clipFact", 'id="highlight"'),         # clip share by highlight recovery
            ("toneFact", 'id="highlightTransition"'),  # compiled curve by tone sliders
        ):
            with self.subTest(fact=fact_id):
                gap = PAGE[PAGE.index(anchor):PAGE.index('id="%s"' % fact_id)]
                self.assertLess(len(gap), 600, f"{fact_id} not adjacent to {anchor}")
        self.assertNotIn("detectedParams", PAGE)  # the overview card is gone

    def test_support_probe_lines_route_to_their_controls(self) -> None:
        # The probe report is not one block: file identity + Evidence tier by
        # the picker, decoder tiers by the decoder select, priors by the
        # analysis-plates toggle. No consolidated support panel remains.
        for fact_id, anchor in (
            ("fileFact", 'id="filePicker"'),
            ("decodeTierFact", 'id="demosaic"'),
            ("priorsFact", 'id="png"'),
        ):
            with self.subTest(fact=fact_id):
                gap = PAGE[PAGE.index(anchor):PAGE.index('id="%s"' % fact_id)]
                self.assertLess(len(gap), 700, f"{fact_id} not adjacent to {anchor}")
        self.assertIn("SUPPORT_ROUTE", PAGE)
        self.assertNotIn("decodeSupport", PAGE)

    def test_output_controls_live_in_a_modal_not_the_left_panel(self) -> None:
        control_panel = PAGE[
            PAGE.index('<div class="controlPanel">'):
            PAGE.index('<div class="card previewCard">')
        ]
        dialog = PAGE[
            PAGE.index('<dialog class="outputDialog"'):
            PAGE.index("</dialog>")
        ]
        self.assertNotIn('id="format"', control_panel)
        self.assertNotIn('id="outdir"', control_panel)
        self.assertIn('id="outputDialogTitle">输出参数', dialog)
        for control_id in ("format", "deliveryProfile", "gamut", "quality", "chroma", "outdir", "png"):
            with self.subTest(control=control_id):
                self.assertIn(f'id="{control_id}"', dialog)
        self.assertIn('id="go">导出</button>', PAGE)
        self.assertIn('dialog.showModal()', PAGE)
        self.assertIn('id="exportConfirm"', dialog)
