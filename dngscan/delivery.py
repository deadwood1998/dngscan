# SPDX-License-Identifier: GPL-3.0-or-later
"""Delivery profiles: encode settings that never feed back into formation.

Formation produces finished SDR/HDR masters at full precision. This module only
describes how those masters are packaged. Archive keeps the historical Ultrahdr
contract (quality 100, 4:4:4, tight round-trip gates). Share lowers JPEG quality so
Core Image may emit 4:2:0 and uses wider engineering gates calibrated for that loss.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any


DELIVERY_PROFILE_CHOICES = ("archive", "share")
DEFAULT_DELIVERY_PROFILE = "archive"

# Share defaults for Ultrahdr / SDR when the user picks the profile without overriding
# quality/chroma. Archive keeps the historical q100 / 444 Ultrahdr defaults.
SHARE_JPEG_QUALITY = 90
SHARE_CHROMA = "420"
ARCHIVE_JPEG_QUALITY = 100
ARCHIVE_CHROMA = "444"


@dataclass(frozen=True)
class DeliveryTolerances:
    """Engineering round-trip gates for one encode profile.

    These are not ISO 21496-1 constants. They separate low-frequency colour/tone drift
    from encoder-dependent high-frequency loss at a stated quality/chroma operating point.
    """

    base_mean_code_error: float
    base_channel_bias_code_error: float
    base_block_p99_code_error: float
    hdr_block_median_relative_error: float
    hdr_block_p95_relative_error: float
    hdr_block_p99_relative_error: float
    hdr_block_chroma_error: float
    require_chroma_444: bool
    # Optional Apple gain-map auxiliary downsample. None leaves Core Image default.
    gainmap_subsample_factor: int | None = None


# Calibrated against quality-100 / 4:4:4 Core Image ISO gain-map JPEG.
ARCHIVE_TOLERANCES = DeliveryTolerances(
    base_mean_code_error=4.0,
    base_channel_bias_code_error=1.0,
    base_block_p99_code_error=2.0,
    hdr_block_median_relative_error=0.015,
    hdr_block_p95_relative_error=0.05,
    hdr_block_p99_relative_error=0.06,
    hdr_block_chroma_error=0.015,
    require_chroma_444=True,
    gainmap_subsample_factor=None,
)

# Wider gates for lossier JPEG delivery. Still reject broad tone/chroma transforms.
SHARE_TOLERANCES = DeliveryTolerances(
    base_mean_code_error=8.0,
    base_channel_bias_code_error=2.0,
    base_block_p99_code_error=5.0,
    hdr_block_median_relative_error=0.03,
    hdr_block_p95_relative_error=0.10,
    hdr_block_p99_relative_error=0.12,
    hdr_block_chroma_error=0.04,
    require_chroma_444=False,
    gainmap_subsample_factor=2,
)


@dataclass(frozen=True)
class DeliveryProfile:
    """How a finished rendition pair is encoded. Does not alter AgX/HDR math."""

    name: str
    quality: int
    chroma: str
    container: str = "jpeg"
    tolerances: DeliveryTolerances = ARCHIVE_TOLERANCES

    @property
    def is_archive(self) -> bool:
        return self.name == "archive"


def resolve_delivery_profile(
    name: str,
    *,
    quality: int | None = None,
    chroma: str | None = None,
    container: str = "jpeg",
) -> DeliveryProfile:
    """Build a delivery profile from a named preset plus optional overrides.

    Explicit quality/chroma win over preset defaults for *share*. Archive keeps the
    historical Ultrahdr contract and refuses softer encode knobs -- use share instead.
    """
    key = str(name or DEFAULT_DELIVERY_PROFILE).strip().lower()
    if key not in DELIVERY_PROFILE_CHOICES:
        raise ValueError(
            f"未知 delivery profile：{name}（可选：{'/'.join(DELIVERY_PROFILE_CHOICES)}）"
        )
    if key == "archive":
        if quality is not None and int(quality) != ARCHIVE_JPEG_QUALITY:
            raise ValueError(
                "delivery profile=archive 固定 JPEG quality 100；"
                "投递用更小文件请加 --delivery-profile share"
            )
        if chroma is not None and str(chroma) != ARCHIVE_CHROMA:
            raise ValueError(
                "delivery profile=archive 固定 chroma 4:4:4；"
                "投递用 4:2:0 请加 --delivery-profile share"
            )
        q = ARCHIVE_JPEG_QUALITY
        c = ARCHIVE_CHROMA
        tolerances = ARCHIVE_TOLERANCES
    else:
        q = SHARE_JPEG_QUALITY if quality is None else int(quality)
        c = SHARE_CHROMA if chroma is None else str(chroma)
        tolerances = SHARE_TOLERANCES
    if not 1 <= int(q) <= 100:
        raise ValueError("JPEG quality 必须在 1-100 之间")
    if str(c) not in ("444", "422", "420"):
        raise ValueError(f"未知 chroma：{c}")
    return DeliveryProfile(
        name=key,
        quality=int(q),
        chroma=str(c),
        container=str(container),
        tolerances=tolerances,
    )


def profile_from_encode_settings(quality: int, chroma: str) -> DeliveryProfile:
    """Infer archive vs share from explicit encode knobs (CLI without --delivery-profile).

    Quality >= 98 with 4:4:4 stays on archive gates so historical Ultrahdr defaults keep
    their strict contract. Anything softer uses share gates.
    """
    q = int(quality)
    c = str(chroma)
    if q >= 98 and c == "444":
        return resolve_delivery_profile("archive", quality=q, chroma=c)
    return resolve_delivery_profile("share", quality=q, chroma=c)


@dataclass(frozen=True)
class FinishedPair:
    """Formation masters ready for any encoder. Pixels are already display-referred."""

    sdr_rgb_u8: Any
    hdr_rgba_f16: Any
    display_headroom_ev: float
    output_gamut: str = "p3"
