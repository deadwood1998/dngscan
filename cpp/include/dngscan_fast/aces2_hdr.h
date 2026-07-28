#pragma once
// SPDX-License-Identifier: GPL-3.0-or-later
//
// Phase 7 placeholder for a contiguous float32 ACES 2-derived HDR kernel.
// NumPy (`dngscan.aces2`) remains the only reference semantics until this C++
// path is implemented and compared against the same reference vectors.
//
// Planned API (not linked yet):
//   void dngscan_aces2_hdr_p3_linear_f32(
//       const float* scene_rec2020_rgb,  // HxWx3 contiguous
//       float* out_p3_rgb,              // HxWx3 contiguous
//       int height, int width,
//       float capacity_ev,
//       float reference_white_nits);
//
// Requirements from docs/ACES2_HDR_IMPLEMENTATION_PLAN.zh-CN.md §9 Phase 7:
// - GIL released, chunked, precomputed P3 cusp/gamut tables
// - strict native mode must not silently fall back
// - every reference vector compared NumPy vs C++ (and CTL when available)

#ifndef DNGSCAN_FAST_ACES2_HDR_H
#define DNGSCAN_FAST_ACES2_HDR_H

#ifdef __cplusplus
extern "C" {
#endif

/* Reserved: returns 0 when the native kernel is unavailable. */
int dngscan_aces2_hdr_native_available(void);

#ifdef __cplusplus
}
#endif

#endif /* DNGSCAN_FAST_ACES2_HDR_H */
