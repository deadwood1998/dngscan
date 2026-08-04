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

    def test_realtime_preview_is_fixed_1920_and_has_no_resolution_control(self) -> None:
        served_page = render_page("").decode("utf-8")
        self.assertIn('id="previewLiveBadge">实时 · 1920px', served_page)
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

    def test_demosaic_change_rebuilds_the_cold_proxy(self) -> None:
        wiring = PAGE[PAGE.index('$("#demosaic").addEventListener') :]
        wiring = wiring[: wiring.index("\n")]
        self.assertIn("saveSettings()", wiring)
        self.assertIn("preparePreview()", wiring)

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
        # The desktop shell is a single-viewport dashboard: neither the page nor
        # either column scrolls, and the preview consumes the remaining height.
        self.assertIn("max-width:1900px", PAGE)
        self.assertIn("html,body{width:100%;height:100%;overflow:hidden}", PAGE)
        self.assertIn("height:100dvh", PAGE)
        self.assertIn("grid-template-rows:auto minmax(0,1fr)", PAGE)
        self.assertIn(".previewCard{height:100%;min-height:0", PAGE)
        self.assertIn("object-fit:contain", PAGE)
        self.assertNotIn("max-height:calc(100vh", PAGE)

    def test_file_picker_sits_after_title_and_uses_header_space(self) -> None:
        top_bar = PAGE[
            PAGE.index('<div class="topBar">'):
            PAGE.index("</div>", PAGE.index('<div class="topBar">'))
        ]
        self.assertLess(top_bar.index("<h1>"), top_bar.index('id="filePicker"'))

        top_bar_start = PAGE.index(".topBar{")
        top_bar_rule = PAGE[top_bar_start:PAGE.index("}", top_bar_start)]
        self.assertIn("display:grid", top_bar_rule)
        self.assertIn("grid-template-columns:max-content minmax(280px,1fr)", top_bar_rule)

        start = PAGE.index(".topBar input[type=file]{")
        rule = PAGE[start:PAGE.index("}", start)]
        self.assertIn("width:100%", rule)
        self.assertNotIn("max-width", rule)

    def test_controls_are_partitioned_into_accessible_dashboard_tabs(self) -> None:
        control_panel = PAGE[
            PAGE.index('<div class="controlPanel">'):
            PAGE.index('<div class="card previewCard">')
        ]
        for tab_id, panel_id in (
            ("captureTab", "capturePanel"),
            ("toneTab", "tonePanel"),
            ("colorTab", "colorPanel"),
        ):
            with self.subTest(panel=panel_id):
                self.assertIn(f'id="{tab_id}" role="tab"', control_panel)
                self.assertIn(f'aria-controls="{panel_id}"', control_panel)
                self.assertIn(f'id="{panel_id}" role="tabpanel"', control_panel)
        self.assertIn("function setDashboardPanel(", PAGE)
        self.assertIn('tab.addEventListener("click"', PAGE)
        self.assertIn('tab.addEventListener("keydown"', PAGE)

    def test_measured_facts_sit_next_to_their_controls(self) -> None:
        # Data-to-function adjacency: each measured fact renders inside the
        # block whose control consumes it, not in a separate overview card the
        # user must scroll back to while dragging a slider.
        for fact_id, anchor in (
            ("hdrSceneFact", 'id="hdrHeadroom"'),   # scene headroom by the HDR slider
            ("evFact", 'id="evReferenceBtn"'),      # body median by the EV controls
            ("wbFact", 'id="lensFilter"'),          # WB degradation closes the WB row
            ("clipFact", 'id="highlight"'),         # clip share by highlight recovery
            ("toneFact", 'id="shoulderStartOffset"'),  # compiled curve by tone sliders
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

    def test_hdr_delivery_does_not_collapse_realtime_tone_core_choices(self) -> None:
        tone_select = PAGE[
            PAGE.index('<select id="toneCore"'):
            PAGE.index('</select>', PAGE.index('<select id="toneCore"'))
        ]
        for core in ("agx", "gated", "neutral", "lum"):
            with self.subTest(core=core):
                self.assertIn(f'value="{core}"', tone_select)

        format_ui = PAGE[PAGE.index("function updateFormatUi()") :]
        format_ui = format_ui[: format_ui.index("async function checkHdrBackend()")]
        self.assertNotIn('$("#toneCore").value="agx"', format_ui)
        self.assertNotIn('$("#toneCore").disabled=hdr', format_ui)
        self.assertIn("updateToneCoreExportUi()", format_ui)
        self.assertIn('id="toneCoreExportHint"', PAGE)


class RealtimeHistogramPageTests(unittest.TestCase):
    """Two histograms, each glued to the function it describes, display-only."""

    def test_scene_histogram_sits_in_the_exposure_card_below_ev_fact(self) -> None:
        exposure_card = PAGE[
            PAGE.index('<div class="secTitle">曝光</div>'):
            PAGE.index('<div class="card" id="toneAdjustCard">')
        ]
        self.assertIn('id="sceneHist"', exposure_card)
        self.assertLess(
            exposure_card.index('id="evFact"'), exposure_card.index('id="sceneHist"')
        )
        gap = PAGE[PAGE.index('id="evFact"'):PAGE.index('id="sceneHist"')]
        self.assertLess(len(gap), 200, "scene histogram not adjacent to #evFact")

    def test_display_histogram_sits_in_the_preview_card_below_preview_wrap(self) -> None:
        preview_card = PAGE[
            PAGE.index('<div class="card previewCard">'):
            PAGE.index('<dialog class="outputDialog"')
        ]
        self.assertIn('id="displayHist"', preview_card)
        self.assertLess(
            preview_card.index('id="previewWrap"'), preview_card.index('id="displayHist"')
        )
        gap = PAGE[PAGE.index('id="previewWrap"'):PAGE.index('id="displayHist"')]
        self.assertLess(len(gap), 300, "display histogram not adjacent to #previewWrap")

    def test_histograms_render_from_the_preview_response_only(self) -> None:
        self.assertIn("function renderSceneHistogram(", PAGE)
        self.assertIn("function renderDisplayHistogram(", PAGE)
        handle = PAGE[PAGE.index("function handleJobResult(") :]
        handle = handle[: handle.index("\n}")]
        self.assertIn("renderSceneHistogram(j.scene_histogram)", handle)
        self.assertIn(
            "renderDisplayHistogram(j.display_histogram,j.hdr_earned_ev)", handle
        )

    def test_histograms_are_display_only_no_interaction(self) -> None:
        # First version is deliberately zero-interaction: no hover, no range
        # selection, no listeners of any kind on either canvas.
        for canvas in ("sceneHist", "displayHist"):
            with self.subTest(canvas=canvas):
                self.assertNotIn(f'#{canvas}").addEventListener', PAGE)
                self.assertNotIn(f'#{canvas}").on', PAGE)
                tag_start = PAGE.index(f'id="{canvas}"')
                tag = PAGE[PAGE.rindex("<canvas", 0, tag_start):PAGE.index(">", tag_start)]
                self.assertNotIn("onclick", tag)
                self.assertNotIn("onmouse", tag)

    def test_hdr_note_uses_served_scalar_and_draws_nothing_without_it(self) -> None:
        renderer = PAGE[PAGE.index("function renderDisplayHistogram(") :]
        renderer = renderer[: renderer.index("\n}")]
        self.assertIn("earnedEv!=null", renderer)
        self.assertIn("HDR 已挣余量", renderer)
