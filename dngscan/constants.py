# SPDX-License-Identifier: GPL-3.0-or-later
"""Shared numeric constants and color primary matrices."""
from __future__ import annotations

import math
from typing import Any

from ._deps import np
from . import agx as agx_engine


EPS = 1e-12


GAMUT_EPS = 1e-3


EV_REPORT_FLOOR = -14.0


GRAY_EV = math.log2(0.18)


MIDGRAY_HEADROOM_STOPS = 3.0


NOISE_DR_EPS = 1e-9


SNR_TILE = 16


SNR_LOW_PERCENTILE = 20.0


SNR_BRIGHT_UNRELIABLE_STOP = -2.5


CEILING_MIN_PILE_PIXELS = 256


CEILING_MIN_PILE_FRACTION = 2e-5


OUTPUT_GAMUT_SPACES = {"srgb": "sRGB", "p3": "P3"}


OUTPUT_GAMUT_LABELS = {"srgb": "sRGB", "p3": "Display P3"}


JPEG_OUTPUT_FORMATS = ("sdr", "ultrahdr")


# HDR authoring policy. Apple defines headroom as a ratio and does not mandate an
# absolute reference-white luminance. 100 nit matches the Blender HDR AgX reference and
# dngscan's SDR normalization; 800/4000 nit are project defaults, not format limits.
HDR_REFERENCE_WHITE_NITS = 100.0
DEFAULT_HDR_HEADROOM_EV = 3.0
DEFAULT_HDR_PEAK_NITS = HDR_REFERENCE_WHITE_NITS * (2.0 ** DEFAULT_HDR_HEADROOM_EV)
MAX_HDR_PEAK_NITS = 4000.0
MAX_HDR_HEADROOM_EV = math.log2(MAX_HDR_PEAK_NITS / HDR_REFERENCE_WHITE_NITS)
# Nominal 100%-reflectance white relative to the fixed 18% scene-gray anchor. This is a
# scene-coordinate convention, not a claim that measured diffuse white always lands here.
DIFFUSE_WHITE_EV = math.log2(1.0 / 0.18)

# ITU-R BT.2020/D65 linear-light Y coefficients. Keep these separate from the inverse
# of dngscan's legacy rounded XYZ matrix: that inverse is intentionally frozen for SDR
# pixel compatibility and its Y row does not sum to exactly one.
REC2020_LUMA = (0.2627, 0.6780, 0.0593)
# HDR display rendering transforms. "agx" is the darktable-style HDR AgX being
# built per docs/DARKTABLE_HDR_AGX_DESIGN.zh-CN.md; it is the only intended DRT, and
# HDR output stays suspended until its math gates pass.
HDR_DRT_CHOICES = ("agx",)
DEFAULT_HDR_DRT = "agx"


XYZ_TO_RGB = {
    "sRGB": np.array(  # type: ignore[union-attr]
        [[3.2406, -1.5372, -0.4986], [-0.9689, 1.8758, 0.0415], [0.0557, -0.2040, 1.0570]],
        dtype=np.float64,
    )
    if np is not None
    else None,
    "P3": np.array(  # type: ignore[union-attr]
        [[2.4934, -0.9314, -0.4027], [-0.8295, 1.7627, 0.0236], [0.0358, -0.0762, 0.9569]],
        dtype=np.float64,
    )
    if np is not None
    else None,
    "Rec2020": np.array(  # type: ignore[union-attr]
        [[1.7167, -0.3557, -0.2534], [-0.6667, 1.6165, 0.0158], [0.0176, -0.0428, 0.9421]],
        dtype=np.float64,
    )
    if np is not None
    else None,
}


RGB_TO_XYZ = {
    name: np.linalg.inv(matrix).astype(np.float64) if np is not None and matrix is not None else None
    for name, matrix in XYZ_TO_RGB.items()
}


REC2020_TO_SRGB = (
    (XYZ_TO_RGB["sRGB"] @ RGB_TO_XYZ["Rec2020"]).astype(np.float64)
    if np is not None
    else None
)


SRGB_TO_REC2020 = (
    (XYZ_TO_RGB["Rec2020"] @ RGB_TO_XYZ["sRGB"]).astype(np.float64)
    if np is not None
    else None
)


AGX_INSET = agx_engine.AGX_INSET_REC2020


AGX_OUTSET = agx_engine.AGX_OUTSET_REC2020


WB_CHOICES = ("camera", "daylight")


DEMOSAIC_CHOICES = ("auto", "dht", "dcb", "ahd", "aahd", "vng", "ppg")


DEMOSAIC_AUTO_PREFERENCE = ("DHT", "DCB", "AHD")


# Scene-linear RGB producers. Evidence (CFA masks, mosaic) always stays on LibRaw.
DECODER_CHOICES = ("libraw", "coreimage")


COREIMAGE_VERSION_CHOICES = ("auto", "9", "8", "7")

# Core Image scale policy. ``aligned`` is the production comparison contract: a single
# per-file scalar puts RAW 9 on the LibRaw decoded-green median without targeting any
# absolute brightness. ``unity`` preserves Apple's native units, while ``measured`` is
# the old fixed Sigma-fp fit kept only for reproducing historical A/B renders.
COREIMAGE_SCALE_CHOICES = ("aligned", "unity", "measured")
COREIMAGE_SCALE_DEFAULT_MODE = "aligned"
# Legacy median ratio retained for explicit --coreimage-scale measured runs.
COREIMAGE_SCALE_MEASURED_RATIO = 1.0293


OKLAB_M1 = (
    np.array(  # XYZ(D65) -> LMS
        [
            [0.8189330101, 0.3618667424, -0.1288597137],
            [0.0329845436, 0.9293118715, 0.0361456387],
            [0.0482003018, 0.2643662691, 0.6338517070],
        ],
        dtype=np.float64,
    )
    if np is not None
    else None
)


OKLAB_M2 = (
    np.array(  # LMS' -> Oklab
        [
            [0.2104542553, 0.7936177850, -0.0040720468],
            [1.9779984951, -2.4285922050, 0.4505937099],
            [0.0259040371, 0.7827717662, -0.8086757660],
        ],
        dtype=np.float64,
    )
    if np is not None
    else None
)


OKLAB_M1_INV = np.linalg.inv(OKLAB_M1).astype(np.float64) if np is not None and OKLAB_M1 is not None else None


OKLAB_M2_INV = np.linalg.inv(OKLAB_M2).astype(np.float64) if np is not None and OKLAB_M2 is not None else None


CHROMA_CHOICES = ("444", "422", "420")
