# SPDX-License-Identifier: GPL-3.0-or-later
"""Core datatypes passed through the dngscan pipeline."""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .constants import DEFAULT_GAINMAP_SCALE, DEFAULT_HDR_HEADROOM_EV


@dataclass
class RawBundle:
    path: Path
    raw_image: Any
    raw_colors: Any
    xyz_render: Any
    render_scale: float
    scene_rec2020_render: Any
    scene_scale: float
    white_level: int
    black_levels: list[float]
    camera_wb: list[float]
    color_desc: str
    raw_pattern: list[list[int]]
    camera_white_levels: list[float]
    scene_highlight_mode: str = "clip"
    orientation_flip: int = 0
    exposure_gain: float = 1.0
    wb_mode: str = "camera"
    daylight_wb: list[float] | None = None
    shot_make: str | None = None
    shot_model: str | None = None
    shot_iso: int | None = None
    # Half-resolution, orientation-correct RGB soft clip masks in raw/CFA space.
    # Shape is (H, W, 3), aligned to scene_rec2020_render when scene_half_size=True.
    # Full-resolution renders resize this mask to the render buffer on demand.
    clip_masks: Any | None = None
    # Lazily filled by retreat.clip_masks_for_shape when a render resizes masks.
    _clip_masks_cache_shape: tuple[int, int] | None = None
    _clip_masks_resized: Any | None = None
    # Optional RAW-gated guidance maps (headroom, clip class, SNR confidence).
    raw_guidance: Any | None = None
    # Lazily resized RAW guidance for the current render geometry.
    _raw_guidance_cache_shape: tuple[int, int] | None = None
    _raw_guidance_resized: Any | None = None
    _raw_guidance_has_sensor_snr: bool = False
    # Scene-linear RGB producer. Mosaic and levels are always LibRaw-derived; per-pixel
    # clip_masks are LibRaw-derived on the libraw path and absent (None) on the coreimage
    # one, whose frame geometry cannot carry them.
    scene_decoder: str = "libraw"
    scene_decoder_version: str | None = None
    # Which --coreimage-scale mode produced the buffer ("measured" | "unity"), so a
    # comparison between the two is visible in the report rather than implicit.
    scene_scale_mode: str | None = None
    # DNG opcodes the decoder executed (Core Image path only). Reported, not acted on:
    # their presence is why that path cannot share LibRaw's per-pixel CFA evidence.
    scene_opcode_names: tuple[str, ...] = ()
    # Shape of clip_masks / LibRaw scene frame when scene_decoder != "libraw".
    evidence_shape: tuple[int, int] | None = None
    # Crop of the evidence frame covering the current scene, in evidence pixel coords
    # (y0, x0, y1, x1). None means the full evidence frame maps onto the scene (pure scale).
    scene_geometry_crop: tuple[float, float, float, float] | None = None
    scene_geometry_corr: float | None = None


@dataclass
class RawGuidanceMaps:
    """Per-pixel RAW permission rasters aligned to clip_masks resolution."""

    headroom: Any
    clip_class: Any
    snr_confidence: Any


@dataclass
class Analysis:
    channel_ids: list[int]
    labels: dict[int, str]
    ceilings: dict[int, int]
    ceil_spike_counts: dict[int, int]
    ceil_near_counts: dict[int, int]
    ceil_spike_ok: dict[int, bool]
    fullwell_channel_ids: list[int]
    fullwell_note: str
    saturation_levels: dict[int, int]
    channel_fullwell: dict[int, int]
    channel_thresholds: dict[int, int]
    fullwell: int
    threshold: int
    clip_pct: dict[int, float]
    cfa_cell_supported: bool
    cell_union_pct: float
    cell_ge2_of_clipped_pct: float
    cell_k_of_clipped_pct: dict[int, float]
    cell_k_of_all_pct: dict[int, float]
    ev_p1: float
    ev_raw_p1: float
    ev_median: float
    ev_p99: float
    ev_p999: float
    ev_dr_p1_p999: float
    ev_floor_hit_pct: float
    median_vs_gray_ev: float
    median_y: float
    noise_floor: float
    usable_dr_ev: float
    snr_curves: dict[str, dict[str, Any]]
    snr1_dr: dict[str, float]
    snr1_stop: dict[str, float]
    gamut_out_pct: dict[str, float]
    bright_pixel_pct: float
    survivor_channel: str
    container_bits_est: int
    prior_id: str | None = None
    gain_e_per_dn: float | None = None
    noise_floor_e: float | None = None
    prior_read_noise_e: float | None = None
    prior_pdr_ev: float | None = None
    usable_dr_eff_ev: float = float("nan")
    health_lag1_corr: float = float("nan")
    health_hist_empty_pct: float = float("nan")


@dataclass
class ToneCompressionPlan:
    target_gamut: str
    luma_p1: float
    luma_p50: float
    luma_p99: float
    luma_p999: float
    black_ev: float
    white_ev: float
    dynamic_range_ev: float
    contrast: float
    toe_power: float
    shoulder_power: float
    chroma_p95: float
    negative_rgb_pct: float
    over_rgb_pct: float
    # Linear latitude around the pivot (EV): shoulder starts latitude_hi_ev above mid
    # gray instead of at it, keeping bright subject colors out of the channel-converging
    # shoulder; a small lower run keeps upper shadows off the toe. Zero = pure sigmoid.
    latitude_lo_ev: float = 0.0
    latitude_hi_ev: float = 0.0
    # Scene-driven purity compensation applied after the AgX curve (see dngscan/punch.py).
    # 0 = identity (night/high-ISO scenes gate to exactly zero).
    punch_strength: float = 0.0
    # Tone core selector: "agx" keeps inset/outset AgX, "lum" uses luminance-ratio shoulder.
    tone_core: str = "agx"
    # Norm for the luminance core: "y", "power", or "max".
    lum_norm: str = "y"
    # Optional manual pivot offset. The automatic compiler keeps this at zero until a
    # constrained C1 solver can move local contrast without moving the EV=0 anchor.
    pivot_ev_offset: float = 0.0
    # Fraction of per-channel AgX hue skew kept after the curve. Default 0.6 follows
    # darktable; explicit Blender-reference primary plans use 0.4.
    hue_keep: float = 0.6
    # Linear output floor of the curve; >0 lifts blacks for faded film looks.
    target_black_linear: float = 0.0
    # Linear output ceiling of the curve (darktable target_white); <1 converges the
    # shoulder to a faded, sub-display-white top for milky/print-style looks.
    target_white_linear: float = 1.0
    # AgX primaries preset (base/punchy/muted/smooth); darktable smooth is the default.
    agx_primaries: str = "smooth"
    # The endpoint-normalized C1 DRT keeps the calibrated scene EV=0 pivot fixed while
    # re-scaling only its black/white bounds. These values share that scene-relative EV
    # domain; `shoulder_start_ev` is the requested linear latitude above the pivot.
    toe_start_ev: float = -4.0
    shoulder_start_ev: float = 1.0
    use_c1_endpoints: bool = True
    # Display-referred dark-scene lift, implemented like darktable's look brightness:
    # it leaves encoded black/white fixed and is never an exposure gain.
    view_brightness: float = 1.0


@dataclass(frozen=True)
class SceneToneMetrics:
    """Scene-referred luminance facts used only to compile the tone plan.

    The reliable distribution excludes CFA sites with exhausted headroom. Its purpose is
    to prevent reconstructed lamps and single-channel clipping from defining the global
    shoulder. It deliberately contains no creative or output-gamut decisions.
    """

    reliable_sample_pct: float
    body_ev_p1: float
    body_ev_p5: float
    body_ev_p50: float
    body_ev_p95: float
    body_ev_p99: float
    body_ev_p999: float
    tail_ev_p9999: float
    tail_area_ev0_pct: float
    tail_area_ev2_pct: float
    tail_extremity: float
    sparse_emitter_tail: bool
    raw_clip_union_pct: float
    # Same percentile as tail_ev_p9999, excluding RAW sites with exhausted CFA headroom.
    # This is the only tail statistic allowed to set a global white endpoint.
    reliable_tail_ev_p9999: float = float("nan")


@dataclass(frozen=True)
class ColorGeometryPlan:
    """Colour-only decisions for one output gamut.

    `raw_clip_retreat_strength` is applied only through the CFA-derived mask. Output
    gamut pressure controls the final hue-preserving fit, never the tone endpoints.
    """

    target_gamut: str
    raw_clip_retreat_strength: float
    output_gamut_pressure_pct: float
    gamut_fit_alpha: float = 0.05
    # A restrained display-side safety valve for the luminance core. AgX already has
    # its own inset/outset path toward white, so this is zero for the AgX core.
    display_highlight_chroma_retreat: float = 0.0
    display_highlight_chroma_start: float = 0.75
    display_highlight_chroma_end: float = 0.98
    # RAW-gated DRT (tone_core=gated): master scale on color-path blend weight.
    color_path_master: float = 1.0
    gated_midtone_protect: float = 0.92
    color_path_highlight_ev_lo: float = 0.25
    color_path_highlight_ev_hi: float = 2.75
    # Scene EV below which SNR is too low to open the color path on scene evidence alone.
    gated_noise_ev_floor: float = -12.0


@dataclass(frozen=True)
class RenderPlan:
    """Immutable contract between analysis and the renderer."""

    tone: ToneCompressionPlan
    color: ColorGeometryPlan
    scene: SceneToneMetrics


@dataclass(frozen=True)
class RenderAdjustments:
    """Bounded user biases applied after the automatic render plan is compiled.

    Zero is an exact identity. These controls deliberately do not expose or replace the
    automatically derived pivot, tone endpoints, or RAW evidence decisions.
    """

    midtone_brightness: float = 0.0
    midtone_contrast: float = 0.0
    shadow_transition: float = 0.0
    highlight_transition: float = 0.0
    highlight_fade: float = 0.0

    def is_identity(self) -> bool:
        return all(
            abs(float(value)) <= 1e-12
            for value in (
                self.midtone_brightness,
                self.midtone_contrast,
                self.shadow_transition,
                self.highlight_transition,
                self.highlight_fade,
            )
        )


@dataclass
class AutoEvResult:
    ev: float
    ev_median_target: float
    ev_boost: float
    highlight_limited: bool
    highlight_cap_ev: float
    anchored_median_ev: float


@dataclass
class GainMapMetadata:
    headroom: float
    gamma: float = 1.0
    min_gain: float = 0.0
    max_gain: float = DEFAULT_HDR_HEADROOM_EV
    hdr_capacity_min: float = 0.0
    hdr_capacity_max: float = DEFAULT_HDR_HEADROOM_EV
    gainmap_scale: int = DEFAULT_GAINMAP_SCALE
