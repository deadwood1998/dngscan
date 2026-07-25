# dngscan — optional Core Image (RAW9) decoder: implementation handoff

> Status: design handoff for implementation. Delete this file when the work lands.
>
> Scope discipline: this adds a **fifth control path**, parallel in spirit to the
> `lum` / `neutral` tone cores. It is not a quality upgrade, not a default, and it must
> not weaken the RAW-evidence layer. If a change starts to touch tone endpoints, clip
> masks, or the golden fixtures, it has left this plan's scope.

## 1. What was measured (do not re-derive)

All numbers below were measured on macOS 27.0 (26A5388g) against real captures in this
repository's workflow. They define the acceptance targets, so treat them as given.

- **Availability is per camera/format.** `CIRAWFilter.supportedDecoderVersions` on the
  Sigma fp DNG returns `['6.dng','7','7.dng','8','8.dng','9','9.dng']`; on the Fujifilm
  X-S20 RAF it returns only `['7','8']`. Version 9 must be opted into explicitly; the
  default is `8`.
- **`boostAmount = 0` yields strictly scene-linear output.** Rendering the same crop at
  `exposure = 0` and `exposure = +1` gives a median ratio of **2.0101**.
- **Rendering target can be the pipeline's own working space**: render through
  `CGColorSpaceCreateWithName(kCGColorSpaceExtendedLinearITUR_2020)` with
  `kCIFormatRGBAf` → linear Rec.2020 float32, no conversion needed.
- **All subjective processing can be zeroed**: `boostAmount`, `boostShadowAmount`,
  `luminanceNoiseReductionAmount`, `colorNoiseReductionAmount` (defaults to **0.5** —
  must be explicitly cleared), `detailAmount`, `contrastAmount`.
- **Geometry differs.** LibRaw's visible frame for the fp DNG is 4042×6064; the Core
  Image output extent is 4000×6000. Orientation matches (no flip); the correct Core
  Image sampling convention is a **direct top-left rect with no array flip** — the
  intuitive y-flip is wrong and silently drops correlation to 0.35. Sampling matched
  fractional positions correlates at **0.9990**.
- **Scale offset is a constant**: Core Image linear values are **0.9314 ×** the
  pipeline's, i.e. −0.10 EV. Absorb with a constant, do not "fix" per image.
- **Colour differs materially**: R/G **+6.5 %**, B/G **−2.0 %** versus LibRaw, because
  Apple applies its own camera matrix and white-balance interpretation.
- **Through AgX** (same plan, scale compensated): median 8-bit code delta **28**, p95
  **54**, **median Oklab ΔE 0.138**. For calibration: the perceptual threshold is
  ≈0.02, and the measured Sigma fp ↔ ALEXA skin divergence was 0.006. Switching
  decoders therefore changes the image roughly 20× more than changing cameras does.
- **Speed**: Core Image v9 full 24 MP decode+render **1.36 s** versus **4.14 s** for
  LibRaw (DHT) plus the current preprocessing. Nice to have; not the justification.
- **v9 vs v8**: 1.73 % median difference on a low-ISO daylight frame; 6.15 % on a
  high-ISO night frame, where v9 also carries **9.2 % less high-frequency energy with
  noise reduction disabled** — i.e. its reconstruction smooths something we cannot
  characterise or switch off.

## 2. Design

```text
                    ┌─ evidence (ALWAYS LibRaw) ────────────────────────┐
RAW ── LibRaw ──────┤ raw_image mosaic, black/white levels, CFA clip     │
                    │ masks, saturation/SNR analysis, metadata, WB gains │
                    └───────────────────────────────────────────────────┘
                              │
       scene_rec2020_render ──┤── decoder="libraw"     : current path (default)
                              └── decoder="coreimage"  : CIRAWFilter, this plan
                              │
                    RenderPlan → tone core → … (unchanged)
```

The evidence layer never moves. Only the **scene-linear RGB buffer** has an alternative
producer. Everything downstream — plan compilation, tone cores, punch, gamut fit — is
untouched, because the alternative producer lands in the same space and units.

### 2.1 New module: `dngscan/coreimage_decode.py`

Self-contained, import-guarded (PyObjC/Quartz may be absent; on non-macOS it must fail
cleanly, never at import time of the package).

```python
COREIMAGE_DECODER_VERSIONS = ("auto", "9", "8", "7")  # "auto" = newest supported

def available() -> bool: ...                       # Quartz importable + CIRAWFilter present
def supported_versions(path: Path) -> tuple[str, ...]: ...
def decode_scene_rec2020(
    path: Path, *, half_size: bool, version: str = "auto",
    target_shape: tuple[int, int] | None = None,
) -> tuple[np.ndarray, dict]: ...                  # (float32 HxWx3 linear Rec.2020, info)
```

`decode_scene_rec2020` must:

1. Build the filter, select the version (`auto` → highest of `9`/`9.dng` present, else
   fall back and record what it used).
2. Zero every subjective control listed in §1 — including `colorNoiseReductionAmount`,
   whose non-zero default is the single easiest mistake to make here.
3. Leave `neutralTemperature`/`neutralTint` at the file's as-shot values for
   `--wb camera`. For `--wb daylight`, see §4 (open question) — for the first cut,
   **refuse the combination with a clear error** rather than silently rendering a
   different balance.
4. Render with `scaleFactor` for the half-size path (do not render full then downsample:
   Core Image's own scaling is the point), into `kCIFormatRGBAf` +
   `kCGColorSpaceExtendedLinearITUR_2020`, using the **direct rect, no flip** convention.
5. Apply `COREIMAGE_SCALE_COMPENSATION = 1 / 0.9314` (a named module constant carrying
   the measurement and its date) so the buffer matches the pipeline's units.
6. Return `info` for the report: version used, extent, whether NR was clamped, and the
   scale constant applied.

Return `float32` in [0, ~], and convert to the pipeline's `uint16 + scene_scale`
convention at the call site, so `RawBundle` stays as it is.

### 2.2 Geometry: the real work

`RawBundle.clip_masks` is built from the LibRaw mosaic and is documented as aligned to
`scene_rec2020_render`. With the Core Image buffer that alignment no longer holds
(4042×6064 vs 4000×6000). Required:

- Compute the fractional mapping between the two frames once at decode time and store
  it on the bundle (e.g. `scene_geometry_source: str` plus the crop/scale factors).
- Extend `retreat.clip_masks_for_shape` so resizing masks to a Core Image buffer uses
  that mapping rather than assuming a common origin. `resize_clip_masks` already
  resamples by shape; the addition is the crop offset, not new machinery.
- **Verification gate**: after decoding, correlate a downsampled luma of the Core Image
  buffer against the LibRaw scene buffer. Require ≥ 0.98. If it fails, raise — a silent
  misalignment would put clip retreat and the gated core on the wrong pixels, which is
  worse than not offering the decoder at all.

### 2.3 Wiring

- `raw_io.load_raw(..., decoder: str = "libraw")`: after the existing LibRaw work
  (which stays, in full), if `decoder == "coreimage"` replace only
  `scene_rec2020_render` / `scene_scale` and record the geometry mapping. The mosaic,
  masks, levels and analysis inputs are untouched.
- CLI: `--decoder {libraw,coreimage}` (default `libraw`) and
  `--coreimage-version {auto,9,8,7}` (default `auto`, only meaningful with the former).
  Error clearly when unavailable: not macOS, PyObjC missing, or the file offers no
  supported version.
- GUI: a select in the same group as demosaic, hidden/disabled when
  `coreimage_decode.available()` is false. Preview cache keys **must** include decoder
  and version (`dngscan/gui/preview_cache.py`), otherwise a cached proxy from the other
  decoder will be served.
- Report (`dngscan/report.py`): add the decoder and resolved version to the JPEG
  settings line, and the CSV row. A render whose decoder is not recorded is not
  reproducible, which is the same defect class as the earlier GUI-only adjustments.

### 2.4 Guardrails

- **Golden set**: the Core Image path must **not** be added to the case matrix in
  `tests/golden_support.py`. Apple changes this decoder between OS releases; pinned
  bytes would rot. Add a comment there saying so explicitly, so nobody "completes" the
  matrix later.
- **CI**: the Linux jobs must stay green — every new test must skip when
  `coreimage_decode.available()` is false. The macOS jobs will exercise the real path.
- **README (both languages)**: document it in the same voice as the tone cores — an
  alternative *interpretation*, with the ΔE 0.138 number stated plainly, the noise
  finding stated plainly, and the RAF-has-no-v9 caveat. It is not advertised as better.

## 3. Tests

`tests/test_coreimage_decode.py`, all skipping when unavailable:

1. `available()` is False-safe on any platform; importing the module never raises.
2. Version resolution: `auto` picks 9 when offered, falls back otherwise; an explicit
   unsupported version raises rather than silently downgrading.
3. **Linearity contract**: decode a synthetic or real file at two exposures one stop
   apart, assert the ratio is 2.0 ± 0.02 (this is the property the whole integration
   rests on).
4. **Geometry gate**: correlation against the LibRaw buffer ≥ 0.98, and the alignment
   check raises on a deliberately corrupted mapping.
5. **Scale**: median ratio to the LibRaw buffer within ±3 % of 1.0 after compensation.
6. **NR defaults cleared**: assert the filter's `colorNoiseReductionAmount` is 0 in the
   configured filter (guards the easiest regression).
7. Clip masks resized for a Core Image buffer land on the right pixels: build a
   synthetic bundle with a known clipped region and assert the mask maximum falls inside
   it after mapping.

## 4. Open questions to resolve during implementation

- **White balance parity.** The pipeline's `--wb daylight` uses LibRaw's calibrated
  daylight multipliers; Core Image exposes `neutralTemperature`/`neutralTint` instead.
  Whether a faithful mapping exists is unknown. First cut: reject the combination. If a
  mapping is found, it must be validated by comparing rendered neutral patches, not by
  assuming the numbers correspond.
- **Highlight modes.** `--highlight-mode {clip,blend,reconstruct}` is a LibRaw concept.
  Core Image does its own highlight handling with no equivalent switch. First cut:
  record in the report that the flag does not apply to this decoder rather than
  pretending it does.
- **Ultra HDR.** `extendedDynamicRangeAmount` plus `contentHeadroom` may be a better HDR
  reservoir than the current fixed `--hdr-headroom`. Out of scope here; note only.

## 5. Acceptance

- Default renders are **byte-identical** to today: the full golden set passes untouched,
  both `DNGSCAN_FAST=0` and `=1`.
- `--decoder coreimage` on the Sigma fp DNG produces a plausible render whose Oklab ΔE
  against the LibRaw render is ≈0.14 (the measured value; a wildly different number
  means the scale, geometry or colour handling is wrong).
- `--decoder coreimage` on the Fujifilm RAF resolves to version 8 and says so, or errors
  clearly — it must not claim v9.
- Linux CI stays green; macOS CI exercises the path.
- Report and CSV name the decoder and version; the GUI preview cache does not serve a
  cross-decoder proxy.
