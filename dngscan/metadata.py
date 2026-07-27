# SPDX-License-Identifier: GPL-3.0-or-later
"""Minimal RAW metadata reader for camera identification and shot metadata.

Reads only what the priors layer needs (Make, Model, ISO, AsShotNeutral) with
targeted seeks — no external dependency, no full-file load. Handles TIFF-based
containers (DNG/NEF/ARW/CR2/...) natively and Fujifilm RAF via its proprietary
header plus the EXIF block of the embedded JPEG preview. Returns None fields
on any parse trouble; callers must treat everything as best-effort.
"""

from __future__ import annotations

import io
import struct
from dataclasses import dataclass
from pathlib import Path

TAG_MAKE = 271
TAG_MODEL = 272
TAG_EXIF_IFD = 34665
TAG_ISO = 34855
TAG_AS_SHOT_NEUTRAL = 50728
TAG_SUB_IFDS = 330
# DNG 1.7 §5: log2 exposure the renderer is expected to apply on top of the raw data.
# Most vendors use it as a fixed per-model calibration constant (Sigma fp writes 1.0 on
# every frame); Apple uses it to carry a per-shot capture decision, writing 0.4973 at
# base ISO and exactly 2.0 more on an underexposed low-light frame.
TAG_BASELINE_EXPOSURE = 50730

_TYPE_SIZES = {1: 1, 2: 1, 3: 2, 4: 4, 5: 8, 6: 1, 7: 1, 8: 2, 9: 4, 10: 8, 11: 4, 12: 8}


@dataclass
class DngShotInfo:
    make: str | None = None
    model: str | None = None
    iso: int | None = None
    as_shot_neutral: tuple[float, float, float] | None = None
    # None when the file carries no BaselineExposure, which is not the same as 0.0.
    baseline_exposure: float | None = None


def _read_ifd_entries(fh, offset: int, endian: str) -> list[tuple[int, int, int, bytes]]:
    fh.seek(offset)
    count_raw = fh.read(2)
    if len(count_raw) < 2:
        return []
    (count,) = struct.unpack(endian + "H", count_raw)
    if count > 4096:
        return []
    data = fh.read(count * 12)
    entries = []
    for i in range(count):
        tag, typ, num = struct.unpack(endian + "HHL", data[i * 12 : i * 12 + 8])
        entries.append((tag, typ, num, data[i * 12 + 8 : i * 12 + 12]))
    return entries


def _entry_values(fh, typ: int, num: int, raw: bytes, endian: str) -> list:
    size = _TYPE_SIZES.get(typ)
    if size is None:
        return []
    total = size * num
    if total <= 4:
        buf = raw[:total]
    else:
        (off,) = struct.unpack(endian + "L", raw)
        fh.seek(off)
        buf = fh.read(total)
        if len(buf) < total:
            return []
    if typ == 2:  # ASCII
        return [buf.split(b"\x00")[0].decode("ascii", errors="replace").strip()]
    fmt = {1: "B", 3: "H", 4: "L", 8: "h", 9: "l", 11: "f", 12: "d"}.get(typ)
    if fmt:
        return list(struct.unpack(endian + fmt * num, buf))
    if typ in (5, 10):  # RATIONAL / SRATIONAL
        sub = "l" if typ == 10 else "L"
        parts = struct.unpack(endian + sub * (2 * num), buf)
        return [parts[2 * i] / parts[2 * i + 1] if parts[2 * i + 1] else 0.0 for i in range(num)]
    return []


def _parse_tiff_shot_info(fh, info: DngShotInfo) -> None:
    """Fill `info` from a TIFF stream. `fh` must be seekable and positioned so that
    offset 0 is the TIFF header ('II'/'MM')."""
    head = fh.read(8)
    if len(head) < 8 or head[:2] not in (b"II", b"MM"):
        return
    endian = "<" if head[:2] == b"II" else ">"
    (magic,) = struct.unpack(endian + "H", head[2:4])
    if magic != 42:
        return
    (ifd0_off,) = struct.unpack(endian + "L", head[4:8])
    exif_off = None
    sub_ifd_offsets: list[int] = []
    for tag, typ, num, raw in _read_ifd_entries(fh, ifd0_off, endian):
        if tag == TAG_BASELINE_EXPOSURE:
            vals = _entry_values(fh, typ, num, raw, endian)
            if vals:
                info.baseline_exposure = float(vals[0])
        elif tag == TAG_SUB_IFDS:
            vals = _entry_values(fh, typ, num, raw, endian)
            sub_ifd_offsets = [int(v) for v in vals]
        if tag == TAG_MAKE:
            vals = _entry_values(fh, typ, num, raw, endian)
            info.make = vals[0] if vals else None
        elif tag == TAG_MODEL:
            vals = _entry_values(fh, typ, num, raw, endian)
            info.model = vals[0] if vals else None
        elif tag == TAG_ISO and info.iso is None:
            vals = _entry_values(fh, typ, num, raw, endian)
            info.iso = int(vals[0]) if vals else None
        elif tag == TAG_EXIF_IFD:
            vals = _entry_values(fh, typ, num, raw, endian)
            exif_off = int(vals[0]) if vals else None
        elif tag == TAG_AS_SHOT_NEUTRAL:
            vals = _entry_values(fh, typ, num, raw, endian)
            if len(vals) >= 3:
                info.as_shot_neutral = (float(vals[0]), float(vals[1]), float(vals[2]))
    if info.iso is None and exif_off:
        for tag, typ, num, raw in _read_ifd_entries(fh, exif_off, endian):
            if tag == TAG_ISO:
                vals = _entry_values(fh, typ, num, raw, endian)
                info.iso = int(vals[0]) if vals else None
                break
    # Writers may put BaselineExposure on the raw SubIFD rather than IFD0.
    if info.baseline_exposure is None:
        for sub_off in sub_ifd_offsets[:4]:
            for tag, typ, num, raw in _read_ifd_entries(fh, sub_off, endian):
                if tag == TAG_BASELINE_EXPOSURE:
                    vals = _entry_values(fh, typ, num, raw, endian)
                    if vals:
                        info.baseline_exposure = float(vals[0])
                    break
            if info.baseline_exposure is not None:
                break


_RAF_MAGIC = b"FUJIFILMCCD-RAW "
_RAF_MODEL_OFFSET = 0x1C
_RAF_MODEL_LENGTH = 0x20
_RAF_JPEG_OFFSET_FIELD = 0x54  # uint32 BE pair: embedded JPEG offset, length


def _exif_tiff_from_jpeg(data: bytes) -> bytes | None:
    """Extract the TIFF payload of the Exif APP1 segment from JPEG bytes."""
    if len(data) < 4 or data[:2] != b"\xff\xd8":
        return None
    pos = 2
    while pos + 4 <= len(data):
        if data[pos] != 0xFF:
            return None
        marker = data[pos + 1]
        if marker in (0xD8, 0x01) or 0xD0 <= marker <= 0xD7:
            pos += 2
            continue
        if marker == 0xDA:  # start of scan: no EXIF past this point
            return None
        (seg_len,) = struct.unpack(">H", data[pos + 2 : pos + 4])
        seg_start = pos + 4
        seg_end = pos + 2 + seg_len
        if marker == 0xE1 and data[seg_start : seg_start + 6] == b"Exif\x00\x00":
            return data[seg_start + 6 : seg_end]
        pos = seg_end
    return None


def _parse_raf_shot_info(fh, info: DngShotInfo) -> None:
    """Fujifilm RAF: model string lives in the proprietary header; Make/ISO come
    from the EXIF block of the embedded JPEG preview."""
    info.make = "FUJIFILM"
    fh.seek(_RAF_MODEL_OFFSET)
    model_raw = fh.read(_RAF_MODEL_LENGTH)
    model = model_raw.split(b"\x00")[0].decode("ascii", errors="replace").strip()
    if model:
        info.model = model

    fh.seek(_RAF_JPEG_OFFSET_FIELD)
    field = fh.read(8)
    if len(field) < 8:
        return
    jpeg_off, jpeg_len = struct.unpack(">LL", field)
    if jpeg_off <= 0 or jpeg_len <= 0:
        return
    fh.seek(jpeg_off)
    # EXIF sits in the first APP1 segment; 128 KiB comfortably covers it without
    # pulling the whole multi-megabyte preview.
    head = fh.read(min(jpeg_len, 128 * 1024))
    tiff = _exif_tiff_from_jpeg(head)
    if tiff:
        _parse_tiff_shot_info(io.BytesIO(tiff), info)
        info.make = info.make or "FUJIFILM"
        if info.model is None and model:
            info.model = model


def read_dng_shot_info(path: Path) -> DngShotInfo:
    info = DngShotInfo()
    try:
        with open(path, "rb") as fh:
            magic = fh.read(len(_RAF_MAGIC))
            fh.seek(0)
            if magic == _RAF_MAGIC:
                _parse_raf_shot_info(fh, info)
            else:
                _parse_tiff_shot_info(fh, info)
    except (OSError, struct.error):
        pass
    return info
