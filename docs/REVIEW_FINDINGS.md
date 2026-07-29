# Review findings — HDR AgX pipeline

Reviewed at `538e790`, 306 tests passing. Nothing here blocks the pipeline; it is a
record of gaps between what the design document specifies and what the code does, so a
later reader does not have to rediscover them.

## 1. Delivery tolerances were fitted to the implementation, not to the design

The most substantive finding. Measured on `_SDI0150` at half size, the shipped gates in
`gainmap.py` sit just above the errors the implementation actually produces:

| metric | measured | shipped gate | design §15.4 |
| --- | --- | --- | --- |
| HDR round-trip, median relative | 0.0124 | 0.015 | **0.005** |
| HDR round-trip, p99 relative | 0.0616 | 0.12 | **0.02** |
| SDR base, max code error | 7 | 12 | **pixel-identical** |
| declared headroom error | 0.000 EV | 0.05 | 0.05 ✓ |

Every gate passes, but two of them pass because they were set to pass. The design
document's own constant ledger (§4.1) names this exact anti-pattern: "不得把'测试能通过的
误差范围'反写成 DRT 参数". The headroom gate is the one that was genuinely met.

Note also that §15.4's "SDR-only decode is pixel-identical to the input rendition" is not
achievable through a lossy JPEG base at all. The requirement and the implementation
conflict, and the conflict was resolved silently in favour of the implementation. One of
the two has to change: either the document admits a code-value budget for JPEG, or the
base is stored losslessly.

## 2. Smaller items

- `hdr_color.py:154` — the comment says the common-lift weight is "recovered without a
  second smootherstep evaluation", but the line below it calls `channel_lift_weights`
  again, which evaluates smootherstep on `ev_y`. The code is correct; the comment
  describes an optimisation that is not there.
- `hdr_agx_math.py:32` — imports `DIFFUSE_WHITE_EV` from constants without using it. It
  exists only so `test_hdr_agx_math.py` can import it from this module. That is an
  undeclared re-export; the test should read it from `constants` directly.
- `HdrColorGeometry.snr_gate` is hard-wired to 1.0 and multiplied into rho in
  `hdr_agx.py:139`, so it is a documented no-op. The reason is sound (SNR is only
  measured when `diagnostics=True`, and a render must not depend on a diagnostic flag),
  but the field currently reads as live configuration.
- Design §6.3(4) still states monotonicity in the log-derivative form `TH'/TH = T0'/T0 +
  ...`, which divides by `T0` and invents a singularity at the black end. The
  implementation uses the product rule and is correct; the document was not updated.

## 3. Verified as sound

Checked directly rather than assumed:

- `rho` does not change luminance: relative deviation 1.5e-07 to 2.0e-07 across
  rho = 0.25 / 0.5 / 1.0, i.e. float32 noise.
- `rho = 0` returns the common-lift result exactly, and a zero budget returns the input
  array exactly.
- The HDR gamut projector holds its stated guarantees: output within [0, peak], neutral
  input stays neutral, and Y drifts by at most 2.2e-07 where Y is in range.
- The delivered container is a genuine three-channel gain map
  (`444YpCbCr8BiPlanarFullRange`), not the single-channel L008 the writer rejects. A
  `BytesPerRow` of 4096 against a width of 4042 looks like one byte per pixel but is the
  first plane of a biplanar format.
