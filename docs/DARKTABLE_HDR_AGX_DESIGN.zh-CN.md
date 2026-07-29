# dngscan：原生扩展白 HDR AgX 设计与实现

> 状态：数学核、HDR AgX、RAW 证据编译与 Apple ISO 21496-1 JPEG 交付已接入
> 更新日期：2026-07-29
> 当前边界：macOS/Core Image 本机 round-trip 已验证；跨 Android/Chrome/社交平台仍需实机测试

## 1. 目标

HDR 是从同一份 scene-linear Rec.2020 重新形成的第二张 rendition，不是把 SDR 成片乘亮。SDR
和 HDR 共享解码、白平衡、曝光意图、相机矩阵、可选前馈与 RAW 分析，进入显示形成后完全分开：

```text
scene-linear Rec.2020
├─ SDR AgX -> SDR P3 -> uint8 JPEG base
└─ HDR AgX -> extended-linear P3 -> float16 HDR alternate
                                      ↓
                Core Image ISO 21496-1 RGB gain-map JPEG
```

这条设计必须同时满足：

- HDR 不读取 SDR 像素，不要求任何 knee 以下区域与 SDR 相等；
- EV=0 仍映射到 linear 0.18，显示容量不能变成自动曝光；
- HDR 白点只能来自可靠 RAW 高光尾部和显示容量；
- 高光必须由 AgX 曲线本身形成，不再外挂空间/亮度 gain ramp；
- 不允许通用 solver 用 `power < 1` 的加速 fallback 硬顶峰值；
- 色度自由与亮度决定分离，RAW 剪切只能撤回颜色可信度，不能重写 tone；
- Core Image 只负责 gain-map 包装，不负责替 dngscan 设计 HDR look。

## 2. 为什么删除旧 smootherstep 分配层

旧实现先把 HDR 分支压进 reference white，再在曲线后乘：

```text
2 ** (H * smootherstep(u))
```

它虽然可以做到单调和 knee 处 C2 连续，但亮度的真正形成由“AgX base + 外挂增益”共同决定。
`minimum_window`、`max_lift_rate`、knee 和窗宽也都只是为了约束这层外挂而存在，并不是 AgX
curve formation 的概念。

现实现把 scene-earned peak 直接交给同一 darktable-style C1 solver。HDR 的高光动态来自一条
完整曲线：

```text
guard rail -> inset -> log2 scene window -> native extended-white AgX C1
           -> linearize -> hue restore -> outset
```

因此以下符号和运行代码已删除：

```text
lift_stops
apply_hdr_allocation
MINIMUM_WINDOW_EV
MAX_LIFT_RATE
knee_ev
smootherstep HDR allocation
```

## 3. 三种 headroom 必须分开

### 3.1 显示容量

```text
H_display = log2(peak_nits / reference_white_nits)
```

默认 `reference_white=100 nit`、`peak=800 nit`，所以 `H_display=3 EV`。100 nit 是项目的
authoring normalization，不是 Apple 或 ITU 强制常数。

### 3.2 RAW 请求

```text
E_diffuse   = log2(1 / 0.18) = 2.473931188...
E_tail      = reliable_tail_ev_p9999
H_requested = min(H_display, max(0, E_tail - E_diffuse))
```

`E_diffuse` 来自 scene-linear 约定；p99.99 是项目的鲁棒统计政策。LibRaw 从 CFA 对齐 mask 中
排除不可靠高光后计算 tail；Core Image/RAW9 没有可对齐的逐像素 CFA 几何，只能按全分辨率
clipped-cell 比例从亮度排序顶部保守剔除。tail 缺失或非 finite 时请求严格为 0。

### 3.3 曲线可实现白点

```text
H_rendered <= H_requested <= H_display
R_curve = 2 ** H_rendered
```

`H_rendered` 是约束 AgX solver 真正能够形成的 endpoint。请求不可行时降低 endpoint，不进入
加速 fallback。最终还会从实际像素 p99.99 报告 `H_actual`；一张图没有像素走到曲线白端时，
`H_actual` 小于 `H_rendered` 是正确结果。

## 4. HDR scene white EV

HDR 不继承 SDR 的 white EV。它由可靠 tail 单独编译：

```text
普通场景: white_ev = clamp(max(E_tail + 0.30, 3.00), 3.00, 8.50)
稀疏灯源: white_ev = clamp(max(E_tail + 0.50, 3.50), 3.50, 8.50)
```

这些 margin/floor/cap 是显式项目策略，不是 darktable、AgX 或 Apple 标准。margin 的直接作用
是让可靠像素尾部停在数学 endpoint 之前，避免少量重建值或离群值贴住硬输入 clamp。

scene median 不参与 HDR white/headroom，因此夜景不会因为屏幕能显示 800 nit 就被拉成日景。

## 5. 原生 HDR AgX 曲线

### 5.1 坐标

```text
e = log2(scene_channel / 0.18)
x = clamp((e - B) / (W - B), 0, 1)
T(e) = q(x) ** gamma
```

`q` 是 AgX 内部 encoded curve，`T` 是 display-linear 输出。约束为：

```text
T(0 EV) = 0.18
T(W)    = R_curve
```

所以：

```text
q_pivot = 0.18 ** (1/gamma)
q_white = R_curve ** (1/gamma)
```

toe、linear latitude 与 shoulder 的连接继续使用 darktable-style scaled sigmoid，内部连接保持 C1。

### 5.2 pivot 导数

提高 gamma 时不能随意改变中灰局部对比。现有 darktable 补偿保持 linear pivot derivative：

```text
s(g) = s_ref / D(g)

D(g) = [g * q_pivot(g) ** (g-1)]
       / [2.2 * q_pivot(2.2) ** 1.2]
```

`s_ref = contrast * (W-B)/16.5`。`16.5` 来自 AgX 历史 `-10..+6.5 EV` 参数归一化，不是
HDR 峰值常数。HDR 当前固定 `pivot_ev_offset=0`；移动 pivot 和求解 gamma 必须将 EV0 anchor
一起联立，不能让 diagonal-pivot 逻辑在事后覆盖 gamma。

### 5.3 shoulder 可行性

设 shoulder 从 `(x_s, q_s)` 开始，进入 shoulder 时的 encoded slope 为 `s`。到白点的平均所需
slope 是：

```text
a = (q_white - q_s) / (1 - x_s)
```

真正的减速肩部必须满足：

```text
s >= a
```

通用 solver 中已有的量：

```text
p_fallback = s * (1-x_s) / (q_white-q_s) = s/a
```

正好表达这个条件：

- `p_fallback < 1`：终点需要更大 slope，只能用导数向白端发散的加速 power 曲线；HDR 禁止；
- `p_fallback = 1`：shoulder 退化成直线，scaled-sigmoid scale 趋向无穷；
- `p_fallback > 1`：存在真正减速的 sigmoid shoulder。

生产编译要求：

```text
p_fallback >= 1.01
```

`1.01` 是防止参数取整后掉回退化边界的 1% 数值余量，不是观感强度滑块。

### 5.4 求解顺序

```text
1. 请求完整 H_requested。
2. 在 gamma=[2.2, 5.0] 中二分最小可行 gamma。
3. 若 gamma=5.0 仍不可行，在 headroom 上二分降低 endpoint。
4. 对降低后的 endpoint 再求最小可行 gamma。
5. 把 target_white_linear=2^H_rendered 与 curve_gamma 写入 HDR formation plan。
```

`2.2` 是 SDR 历史 encoding；`5.0` 与项目现有 diagonal-pivot AgX solver 的上界一致。限制 gamma
比允许任意高 power 更诚实：非常高的 gamma 会显著改变 toe/中间调形状，不能藏在“自动 HDR”里。

## 6. 端点连续性的准确表述

darktable-style toe/latitude/shoulder **内部连接**是 C1。有限 `white_ev` 之后，输入 x 被 clamp 到
1，因此外侧导数为 0；当前 scaled sigmoid 在 endpoint 的内侧导数有限但不保证恰好为 0，不能
把整个 white clamp 误报为 C1 零斜率端点。

当前安全边界来自三点：

- 禁止 `power < 1` 的导数发散 fallback；
- reliable tail 与 white endpoint 之间保留 0.30/0.50 EV margin；
- 输出色彩体积以 curve endpoint 为硬上界。

若将来需要数学上的零端点导数，应更换 sigmoid 边界条件，而不是重新加一层 gain ramp。

## 7. HDR 色彩几何

### 7.1 两条色度候选

同一个 inset RGB `c` 运行两次 HDR-branch curve primitive：

```text
F_native = T(target=R_curve, gamma=gamma_hdr, c)
F_ref    = T(target=1.0,    gamma=gamma_hdr, c)
```

`F_ref` 只提供保守的 reference-white path-to-white 色度，不提供 tone target，也不是读取 SDR
rendition。`F_native` 的 formation luminance 是唯一亮度权威。

outset 后的 Rec.2020 Y 在 formation 空间对应：

```text
w_form = normalize(Rec2020_luma_row @ outset_matrix)
Y_native = dot(F_native, w_form)
Y_ref    = dot(F_ref,    w_form)
F_common = F_ref * Y_native / max(Y_ref, eps)
```

### 7.2 rho 只改变 chroma

```text
P = (1-rho) * F_common + rho * F_native
F = P * Y_native / max(dot(P, w_form), eps)
```

因此：

- `rho=0`：保留 reference-white AgX 色度路径，但亮度仍为 native HDR Y；
- `rho=1`：保留原生扩展白逐通道路径；
- 任意 rho：最终 Y 回到 `Y_native`，不会成为第二个 tone control；
- 两条曲线相同时直接返回原数组，H=0 不产生无意义浮点漂移。

### 7.3 RAW 门控

全局 `rho_base` 由 multi-channel clipping、输出 P3 压力和 decoder 几何可信度编译。LibRaw 的
CFA soft mask 还能逐像素/逐通道撤回 native path：单通道 clip 降低对应通道权限，两通道 clip
逐渐把整个像素拉回 common path。RAW9 没有空间对齐 mask，只使用更低的全局 cap，禁止用
aggregate clip 伪造局部 mask。

## 8. HDR 色彩体积

完成 hue restore/outset/punch 后转 extended-linear Display P3。合法体积是：

```text
0 <= RGB_P3 <= R_curve
```

超界像素沿线性 P3 中性轴方向缩短 opponent vector，并在可行时保持 Y，不逐通道 hard clip。
它保证的是线性 RGB 几何，不是 CAM/JMh 意义上的严格感知 hue。ACES 2 式 peak-aware JMh
压缩仍是未来可替换模块，不与 tone solver 混在一起。

## 9. 数据模型

```python
@dataclass(frozen=True)
class HdrToneCurve:
    white_ev: float
    display_headroom_ev: float
    requested_headroom_ev: float
    budget_headroom_ev: float       # 实际曲线 endpoint，保留字段名兼容报告
    reliable_tail_ev: float
    curve_gamma: float
    shoulder_slope_reserve: float


@dataclass(frozen=True)
class HdrAgxPlan:
    formation: ToneCompressionPlan  # target_white_linear + curve_gamma 已求解
    display: HdrDisplayTarget
    tone: HdrToneCurve
    color: HdrColorGeometry
```

`budget_headroom_ev` 现在表示 constrained native curve 能承诺的 endpoint，不再表示 smootherstep
分配量。

## 10. 文件边界

```text
dngscan/hdr_agx_math.py   # RAW 请求 -> native endpoint/gamma 的数学求解
dngscan/hdr_agx_plan.py   # scene/display/color evidence -> immutable HdrAgxPlan
dngscan/hdr_agx.py        # 独立 HDR formation runtime
dngscan/hdr_color.py      # rho chroma path + extended-P3 projector
dngscan/gainmap.py        # Core Image 写入与逐文件回读验收
```

SDR `render.py` dispatcher、解码像素和曝光标定不因 HDR 改写。

## 11. 数学与图像验收

### 11.1 一维曲线

- EV=0 输出 `0.18`；
- `min(diff(T)) >= -2e-6`；
- white EV 输出 `2^H_rendered`；
- `need_concave_shoulder == False`；
- `shoulder_fallback_power >= 1.01`；
- gamma 不超过 `5.0`；
- 短 scene window 下 endpoint 降低，而不是进入 fallback；
- shoulder 的 log-output slope 向 white 下降；
- 所有输出 finite。

### 11.2 色彩

- `rho` 扫描不改变 native Y；
- `rho=1` bit-exact 返回 native formation；
- 中性 ramp 在任意 rho 下保持中性；
- CFA mask 正确撤回对应通道与 multi-clip 权限；
- P3 projector 输出不超过 `[0,R_curve]`，体积内像素不改写；
- 可行范围内 projector 的 Y 相对漂移 `<=2e-6`。

### 11.3 真实 RAW 回归（2026-07-29）

默认 800/100 nit 目标、半尺寸渲染：

| 样张 | H_requested/rendered | gamma | H_actual | HDR-SDR body |
|---|---:|---:|---:|---:|
| `_SDI0150` 日景 | +1.36 EV | 4.714 | +0.887 EV | +0.0368 EV |
| `_SDI0199` 夜景 | +1.26 EV | 4.561 | +0.862 EV | +0.0188 EV |
| `_SDI0133` 夜景 | +1.32 EV | 4.640 | +0.875 EV | +0.0221 EV |

三张都保留完整 RAW 请求，shoulder reserve 为 1.010。实际 headroom 低于 endpoint，说明可靠
像素没有被强制推满显示；夜景主体变化远低于 0.5 EV 守门线。

### 11.4 Delivery

- SDR base 固定为 P3、quality 100、4:4:4；
- HDR alternate 为完成的 float16 extended-linear P3；
- Core Image 输出 ISO 21496-1 RGB auxiliary gain map；
- 写完必须回读验证 P3 profile、subsampling、RGB auxiliary、content headroom；
- 全图 HDR round-trip：median `<=1.5%`、p95 `<=8%`、p99 `<=12%`；
- content headroom 误差 `<=0.05 EV`；
- 任一门失败则不保留文件。

## 12. 尚未由数学决定的部分

以下仍需要 EDR 屏幕和更多照片 corpus，而不是继续增加隐藏常数：

- `rho_base=0.5` 是否最合适；
- p99.99 是否是最佳 reliable-tail 统计；
- 一档可靠 scene tail 是否应严格最多兑换一档 display headroom；
- white endpoint 的 0.30/0.50 EV margin 与 3.00/3.50 EV floor；
- gamma 上限 5.0 是否需要在更大 corpus 中收紧；
- HDR-specific outset/rotation；
- 用 peak-aware JMh/CAM 替换线性 P3 projector；
- RAW9 aggregate color-confidence cap；
- 800 nit 或 1000 nit 的默认 authoring target；
- Android、Chrome、Quick Look 与社交平台对 RGB gain map 的互认。

## 13. 非目标

- 不在 SDR JPEG 后做局部提亮；
- 不恢复 ACES/SDR bridge 原型；
- 不让 Core Image 默认 HDR look 再串 AgX；
- 不让 HDR 容量自动曝光夜景；
- 不用 gain map 修复错误的 HDR rendition；
- 不把有限白端写成已经具有零导数；
- 不把本机 Core Image round-trip 写成跨平台兼容性结论。
