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


DEFAULT_HDR_HEADROOM_EV = 3.0


DEFAULT_GAINMAP_SCALE = 2


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

# How the Core Image buffer is aligned to LibRaw's exposure anchor. "measured" applies
# the fitted midtone ratio; "unity" asserts the two agree without correction. The fitted
# correction is smaller than its own per-frame scatter, so both are offered for A/B.
COREIMAGE_SCALE_CHOICES = ("measured", "unity")
COREIMAGE_SCALE_DEFAULT_MODE = "measured"
# Median CI/LibRaw midtone ratio over four Sigma fp frames, macOS 27.0, measured after
# shadowBias was zeroed. Per-frame spread 0.94..1.12, so the 1.0293 correction (0.04 EV)
# is an order of magnitude smaller than the scatter it is drawn from.
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

