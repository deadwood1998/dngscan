# ACES 2-derived reference kernel

This directory contains a NumPy port of the ACES 2 output transform used for
dngscan HDR rendition validation. It is **ACES 2-derived**, not claimed as a
strict bit-identical ACES 2 implementation until CTL-runtime cross-check vectors
exist.

## Source pins

| Repository | Ref | SHA |
|------------|-----|-----|
| aces (umbrella) | `v2.0.0+2025.04.04` | `35e1e6ac2c26ec75433547d5d0a3a881f39bd9f5` |
| aces-core | `v2-dev-release-2` | `eeac1e140e6f04ef60b0318630ff637e5f5d60d3` |
| aces-output | `main` | `fe92dc725fa9acb053d118f3ab55fb142fcc1c66` |

Local copies live under `_ref/aces-core/` and `_ref/aces-output/`.

## Ported CTL files

| CTL source | Python module |
|------------|---------------|
| `Lib.Academy.Utilities.ctl` | `constants.py` (RGB↔XYZ, HSV, smin) |
| `Lib.Academy.ColorSpaces.ctl` | `constants.py`, `input_transform.py` |
| `Lib.Academy.Tonescale.ctl` | `tone_scale.py` |
| `Lib.Academy.OutputTransform.ctl` | `jmh.py`, `chroma_compression.py`, `gamut_compression.py`, `tables.py`, `output_transform.py` |
| Output preset clamp / linear handoff | `white_limiting.py` |

## Public API

```python
from dngscan.aces2 import render_aces2_hdr_p3_linear

p3 = render_aces2_hdr_p3_linear(scene_rec2020_d65, capacity_ev=3.0, reference_white_nits=100.0)
```

Pipeline:

```text
Rec.2020 D65 → AP0 (Bradford CAT)
→ aces_to_JMh → tonemap_and_compress_fwd → gamut_map_fwd
→ JMh_to_XYZ → clamp → Display P3 linear / reference_white_nits
```

## Reference vectors

Self-consistency vectors (float64 reference, float32/float64 compare) are stored
under `tests/aces2_vectors/`. Regenerate with:

```bash
.venv/bin/python tools/regen_aces2_vectors.py
```

Vectors are **float64 self-consistency** checks until a CTL runtime is wired for
external golden generation.

## Test thresholds (initial)

| Property | Threshold |
|----------|-----------|
| neutral relative error | ≤ 2e-5 |
| finite RGB absolute error (f32 vs f64) | ≤ 5e-5 |
| hue error where M ≠ 0 | ≤ 0.05° |

## Known gaps

- No CTL-runtime golden vectors yet; tests use self-generated float64 references.
- Table building at import/render time is slow (~seconds per peak); cached per peak nits.
- Inverse transform not implemented (forward-only for HDR rendition).
- Chroma `REACH_GAMUT_TABLE` uses AP1 cusp M via `make_gamut_table(AP1)` per CTL
  `chromaCompression` (`cuspFromTable(h, REACH_GAMUT_TABLE)[1]`).
