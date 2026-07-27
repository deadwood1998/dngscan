// SPDX-License-Identifier: GPL-3.0-or-later

#include "dngscan_fast/agx_core.h"

#include <pybind11/numpy.h>
#include <pybind11/pybind11.h>

#include <stdexcept>

namespace py = pybind11;

namespace {

float read_float(const py::object& obj, const char* name) {
  return obj.attr(name).cast<float>();
}

bool read_bool(const py::object& obj, const char* name) {
  return obj.attr(name).cast<bool>();
}

void copy_matrix9(const py::object& seq, float out[9]) {
  const py::sequence items = py::reinterpret_borrow<py::sequence>(seq);
  if (py::len(items) != 9) {
    throw std::invalid_argument("matrix must have 9 elements");
  }
  for (int i = 0; i < 9; ++i) {
    out[i] = py::cast<float>(items[i]);
  }
}

dngscan_fast::CurveParams curve_from_py(const py::object& obj) {
  dngscan_fast::CurveParams c{};
  c.black_ev = read_float(obj, "black_ev");
  c.range_ev = read_float(obj, "range_ev");
  c.gamma = read_float(obj, "gamma");
  c.target_black = read_float(obj, "target_black");
  c.target_white = read_float(obj, "target_white");
  c.toe_power = read_float(obj, "toe_power");
  c.toe_transition_x = read_float(obj, "toe_transition_x");
  c.toe_transition_y = read_float(obj, "toe_transition_y");
  c.toe_scale = read_float(obj, "toe_scale");
  c.need_convex_toe = read_bool(obj, "need_convex_toe");
  c.toe_fallback_power = read_float(obj, "toe_fallback_power");
  c.toe_fallback_coefficient = read_float(obj, "toe_fallback_coefficient");
  c.slope = read_float(obj, "slope");
  c.intercept = read_float(obj, "intercept");
  c.shoulder_power = read_float(obj, "shoulder_power");
  c.shoulder_transition_x = read_float(obj, "shoulder_transition_x");
  c.shoulder_transition_y = read_float(obj, "shoulder_transition_y");
  c.shoulder_scale = read_float(obj, "shoulder_scale");
  c.need_concave_shoulder = read_bool(obj, "need_concave_shoulder");
  c.shoulder_fallback_power = read_float(obj, "shoulder_fallback_power");
  c.shoulder_fallback_coefficient = read_float(obj, "shoulder_fallback_coefficient");
  return c;
}

dngscan_fast::NativeAgxPlan plan_from_py(const py::object& obj) {
  dngscan_fast::NativeAgxPlan plan{};
  copy_matrix9(obj.attr("inset"), plan.inset);
  copy_matrix9(obj.attr("outset"), plan.outset);
  plan.curve = curve_from_py(obj.attr("curve"));
  plan.hue_restore = read_float(obj, "hue_restore");
  plan.view_brightness = read_float(obj, "view_brightness");
  plan.punch_strength = read_float(obj, "punch_strength");
  copy_matrix9(obj.attr("rec2020_to_xyz"), plan.rec2020_to_xyz);
  copy_matrix9(obj.attr("xyz_to_rec2020"), plan.xyz_to_rec2020);
  copy_matrix9(obj.attr("oklab_m1"), plan.oklab_m1);
  copy_matrix9(obj.attr("oklab_m2"), plan.oklab_m2);
  copy_matrix9(obj.attr("oklab_m1_inv"), plan.oklab_m1_inv);
  copy_matrix9(obj.attr("oklab_m2_inv"), plan.oklab_m2_inv);
  return plan;
}

}  // namespace

PYBIND11_MODULE(_dngscan_fast, m) {
  m.doc() = "dngscan optional C++ AgX core";

  m.def("native_abi_version", []() { return dngscan_fast::NATIVE_ABI_VERSION; });

  m.def(
      "apply_agx_core_f32",
      [](py::array_t<float, py::array::c_style | py::array::forcecast> rgb, const py::object& plan_obj) {
        if (rgb.ndim() != 2 || rgb.shape(1) != 3) {
          throw std::invalid_argument("rgb must be (N, 3) float32");
        }
        const auto plan = plan_from_py(plan_obj);
        const py::ssize_t n = rgb.shape(0);
        auto out = py::array_t<float>({n, py::ssize_t(3)});
        py::gil_scoped_release release;
        dngscan_fast::apply_agx_core_f32(
            rgb.data(),
            out.mutable_data(),
            static_cast<std::size_t>(n),
            plan);
        return out;
      },
      py::arg("rgb"),
      py::arg("plan"));

  m.def(
      "self_test",
      []() {
        dngscan_fast::NativeAgxPlan plan{};
        plan.curve.black_ev = -10.0f;
        plan.curve.range_ev = 16.5f;
        plan.curve.gamma = 2.2f;
        plan.curve.target_white = 1.0f;
        plan.curve.slope = 0.1f;
        plan.curve.toe_transition_x = 0.1f;
        plan.curve.shoulder_transition_x = 0.9f;
        plan.curve.toe_transition_y = 0.01f;
        plan.curve.shoulder_transition_y = 0.99f;
        plan.curve.toe_power = 1.5f;
        plan.curve.shoulder_power = 3.3f;
        plan.curve.toe_scale = 1.0f;
        plan.curve.shoulder_scale = 1.0f;
        plan.inset[0] = plan.inset[4] = plan.inset[8] = 1.0f;
        plan.outset[0] = plan.outset[4] = plan.outset[8] = 1.0f;
        plan.rec2020_to_xyz[0] = 0.637f;
        plan.rec2020_to_xyz[4] = 1.0f;
        plan.rec2020_to_xyz[8] = 1.0f;
        plan.xyz_to_rec2020[0] = 1.7167f;
        plan.xyz_to_rec2020[4] = 1.6165f;
        plan.xyz_to_rec2020[8] = 0.9421f;
        plan.oklab_m1[0] = plan.oklab_m1[4] = plan.oklab_m1[8] = 1.0f;
        plan.oklab_m2[0] = plan.oklab_m2[4] = plan.oklab_m2[8] = 1.0f;
        plan.oklab_m1_inv[0] = plan.oklab_m1_inv[4] = plan.oklab_m1_inv[8] = 1.0f;
        plan.oklab_m2_inv[0] = plan.oklab_m2_inv[4] = plan.oklab_m2_inv[8] = 1.0f;
        float in[3] = {0.18f, 0.18f, 0.18f};
        float out[3] = {};
        dngscan_fast::apply_agx_core_f32(in, out, 1, plan);
        return out[0] >= 0.0f && out[1] >= 0.0f && out[2] >= 0.0f;
      });
}
