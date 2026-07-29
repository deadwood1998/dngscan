# dngscan：darktable 式 HDR AgX 实施计划

> 状态：**数学方案已确定，暂不接入生产管线**  
> 更新日期：2026-07-28  
> 当前生产输出：SDR JPEG  
> 本文用途：作为后续实现、代码评审和数值验收的唯一 HDR 设计依据。

## 1. 最终决定

dngscan 的 HDR 必须是从同一份 scene-linear 输入重新渲染出来的第二条 AgX，而不是在已经
完成的 SDR 图像上补亮度：

```text
shared scene-linear Rec.2020
├─ current darktable-style AgX -> SDR display-linear P3
└─ HDR darktable-style AgX     -> HDR extended-linear P3

SDR P3 + HDR P3 -> Adaptive HDR / ISO 21496-1 delivery
```

两条分支共享 capture、曝光意图和可靠 RAW 分析，但从 display formation 开始分叉。HDR 分支
需要独立决定：

- reference white 以上的亮度分配；
- shoulder 使用多少显示余量；
- 高光沿什么色彩路径移动；
- 哪些 RAW 高光仍有可信色度，哪些必须向中性轴退让；
- Display P3 峰值色彩体积如何收敛。

旧 ACES 2 bridge、`SDR × spatial gain` 和“把 gain map 当 tone mapper”的方案全部停止。
Gain map 只能在两张已经完成的 SDR/HDR rendition 之间承担交付编码，不能参与决定画面。

## 2. 已由上游实现确认的事实

### 2.1 darktable AgX 提供的是解析式 SDR 骨架

darktable 当前 AgX 的顺序是：

```text
scene RGB
-> negative/low-side guard rail
-> inset + primary rotation
-> per-channel log2 EV
-> toe / linear latitude / shoulder C1 curve
-> internal curve gamma linearisation
-> hue restore
-> outset / unrotation
-> working RGB
```

black EV、white EV、pivot、contrast、latitude、toe/shoulder power、target black/white、
rendering primaries 和 hue restore 都是分开的参数。它很适合作为 dngscan 的可编译 DRT
骨架，但当前公开实现的线性 target white 上限是 `1.0`，输出仍是 SDR。

需要修正一个容易误写的术语：darktable 保证的是 **toe/linear 与 linear/shoulder 两个内部
接缝 C1**。黑、白有限端点在输入 clamp 后是精确有界的，但并没有普遍证明端点左导数为零，
因此不能直接称为“黑白端点 C1”。HDR 测试必须分别检查内部接缝和有限端点。

参考：

- <https://github.com/darktable-org/darktable/blob/master/src/iop/agx.c>
- <https://docs.darktable.org/usermanual/development/en/module-reference/processing-modules/agx/>

### 2.2 Blender 已经把 SDR AgX 与 HDR AgX 分成两条 view transform

Blender 当前 OCIO 配置同时提供：

- `AgX Base Display P3`；
- `AgX Rec.2100-HLG - HDR 1000 nits (P3 D65)`。

HDR 版本以 100 nit 为 reference white、1000 nit 为峰值，通过独立 3D LUT 形成画面，再编码
为 Rec.2100 HLG。它不是把 SDR view 的结果直接放大。

生成 HDR LUT 的参考脚本还提供了几个有用但不应照抄的初值：

- scene middle gray 最终约为 18 nit；
- 1000/100 nit 的显示比值为 10；
- HDR 使用更强的 shoulder power；
- HDR purity 初值为 `0.5`；
- 色相混合本身被作者明确称为经验性做法。

这些是 Blender HDR 的外部参照，不是 dngscan 的数学规范。dngscan 保留 darktable 的解析结构，
并让 RAW 证据编译参数。

参考：

- <https://github.com/blender/blender/blob/main/release/datafiles/colormanagement/config.ocio>
- <https://github.com/EaryChow/AgX_LUT_Gen/blob/main/AgXHLG.py>

### 2.3 Apple Adaptive HDR 是双 rendition 的交付方法

Apple 对 Adaptive HDR 的定义是：文件含一份完整 SDR baseline，再用 SDR/HDR 比值的对数与
元数据表达 alternate HDR。RGB 三通道 gain map 是允许的。Core Image 可以接收 SDR 和 HDR
两张图并重新计算 gain map。

因此正确职责是：

```text
dngscan 决定 SDR/HDR 像素
Core Image / Image I/O 决定如何封装这些像素
```

参考：

- <https://developer.apple.com/videos/play/wwdc2024/10177/>

## 3. 四层架构

### 3.1 Capture layer

职责：把 RAW 变成可信 scene-linear RGB，并保存传感器证据。

- black level、per-channel white/fullwell；
- demosaic、highlight reconstruction；
- white balance、相机矩阵、DNG opcode；
- BaselineExposure 只应用一次；
- decoder scale 与运行时版本；
- CFA clip、SNR/noise floor 及其空间有效性。

LibRaw 和 RAW9 是两条独立 capture pipeline。RAW9 执行 warp/gain-map 后，不允许把 LibRaw 的
CFA mask 假装成逐像素对齐证据；只能共享聚合统计，除非以后完成可靠的几何配准。

### 3.2 Shared scene layer

SDR/HDR 必须完全共享：

- scene-linear Rec.2020；
- camera prefeed / scene transform；
- 固定相机尺度与 BaselineExposure；
- 用户 EV；
- EV=0 对应的 18% scene gray；
- reliable body / reliable tail 分析。

禁止按画面中位数重新曝光。夜景在 HDR 下仍然是夜景。

### 3.3 Display formation layer

从这里分成 SDR/HDR 两个 plan。亮度和色彩必须进一步拆开：

```text
Tone allocation
  display headroom + scene EV -> HDR luminance target

Color geometry
  channel EV + RAW confidence + gamut pressure -> chroma/hue path
```

色域压力不能改 black/white EV，CFA clip 也不能改变全局曝光。

### 3.4 Delivery layer

职责仅限：

- SDR/HDR rendition 的尺寸、方向、profile 对齐；
- gain map 或 alternate representation；
- content headroom / reference white 元数据；
- JPEG/HEIF 编码与 round-trip 验证。

## 4. 数值域与三个 headroom

HDR 内核使用 reference-white-relative、display-linear Display P3：

```text
1.0       = reference white
H_display = log2(display peak / reference white)
R_display = 2^H_display
```

第一版固定：

```text
reference_white_nits = 100
default_peak_nits    = 800
default_H_display    = 3 EV
```

100 nit 与 Blender HDR AgX、现有 SDR P3 和 Apple extended-linear 交接一致。ITU 广播制作常用
203 nit HDR reference white，但该值不等同于 SDR peak white；若以后输出 PQ master，应把
reference white 参数化，不能静默改变照片的 18% 灰锚点。

必须分开记录三个值：

```text
H_display  显示/文件允许的容量
H_budget   当前场景允许 HDR 曲线使用的最大额外档数
H_actual   渲染结果实际达到的内容余量
```

满足：

```text
0 <= H_actual <= H_budget <= H_display
```

不能把每张图的最亮像素归一化到 `R_display`。

## 5. 被否决的单曲线扩展

旧方案曾考虑让 darktable C1 求解器继续输出 `q in [0,1]`，再写成：

```text
Y_hdr = R * q^gamma
q(pivot) = (0.18 / R)^(1/gamma)
```

这个式子能固定中灰，但无法同时保持 pivot contrast 和正常的 concave shoulder。

以现有默认窗口 `[-10,+6.5] EV`、`gamma=2.2`、`R=10` 为例，数值计算得到：

```text
q_pivot                         = 0.161043
保持线性 pivot 导数所需 slope = 0.842686
到白端所需平均 shoulder slope = 2.129660
平均值 / 初始值                = 2.527228
```

一个从 pivot 开始导数只能下降的 concave shoulder，不可能用初始斜率 `0.8427` 完成平均斜率
`2.1297` 的上升。求解器只能进入错误的 fallback 几何或牺牲中间调对比。

因此：**不能通过修改 `target_white`、pivot encoded value 或 curve gamma，把现有单条 SDR C1
直接拉成 HDR。** HDR 需要额外的 EV 分配层。

## 6. HDR 亮度分配：确定的数学形式

### 6.1 基础响应

记当前冻结的 darktable-style SDR 中性响应为：

```text
T0(e) in [0,1]
e = log2(scene Y / 0.18)
```

`T0` 继续由现有 SDR plan 的 black/white/pivot/contrast/toe/shoulder 生成。HDR 不修改 SDR
plan，也不扩大 `target_white_linear`。

### 6.2 HDR 分配窗

定义：

```text
e_k = HDR 开始使用额外显示余量的 scene EV
e_w = 当前 plan 的白端 EV
u   = clamp((e - e_k) / (e_w - e_k), 0, 1)
S(u)= 6u^5 - 15u^4 + 10u^3
```

`S` 是 quintic smootherstep：

```text
S(0)=0, S(1)=1
S'(u)=30u^2(u-1)^2 >= 0
S'(0)=S'(1)=0
S''(0)=S''(1)=0
```

HDR 中性亮度响应定义为：

```text
TH(e) = T0(e) * 2^(H_budget * S(u))
```

也可在 log-output 域写成：

```text
log2 TH(e) = log2 T0(e) + H_budget * S(u)
```

这里出现的乘法只是 HDR DRT 内部的解析表达，不是读取 SDR JPEG 后再做 spatial gain。HDR
分支仍然从 scene RGB 重新执行 formation 和色彩几何。

### 6.3 已证明的性质

在 `T0 >= 0`、`T0' >= 0`、`H_budget >= 0` 下：

1. **SDR 严格退化**

   ```text
   H_budget=0 => TH=T0
   ```

2. **低中调严格不变**

   ```text
   e<=e_k => S=0 => TH=T0
   ```

3. **输出有界**

   ```text
   T0<=1 and S<=1 => TH<=2^H_budget<=R_display
   ```

4. **单调**

   ```text
   TH' = TH * (T0'/T0 + ln(2)*H_budget*S') >= 0
   ```

5. **knee 处至少 C2 继承**

   因为 `S、S'、S''` 在起点均为零，`TH` 在 `e_k` 的值、一阶导数和二阶导数与 `T0`
   相同。额外 HDR range 不会在漫反射白附近制造接缝。

6. **白端行为诚实继承 SDR**

   `S'(1)=0`，HDR lift 不增加白端斜率；有限 `e_w` 处是否与外侧 clamp C1，取决于 `T0`。
   当前 darktable 风格实现只把白端精确钳到目标，不能把它误报为零斜率端点。

### 6.4 已完成的数值探针

对三组代表性 plan（默认宽窗、日景、夜景）和：

```text
H = 0, 1, 2, 3, log2(10)
```

在每组 200001 个 EV 样本上得到：

- `min(diff(TH)) = 0`，无反转；
- `TH(0) = 0.18`，浮点误差小于 `1.3e-7`；
- `e<=e_k` 区域与 SDR 最大差为 `0`；
- H=3 的上限精确为 `8.0`；
- H=log2(10) 的上限为 `9.999999999999998`；
- knee 左右数值导数连续。

这些结果只是公式探针，实施时必须变成正式 float64 oracle + float32 runtime 单测。

## 7. 场景如何编译 HDR 亮度参数

第一版只使用已有、含义清楚的量：

```text
E_diffuse = log2(1/0.18) = 2.473931188...
E_tail    = reliable_tail_ev_p9999
```

初始硬规则：

```text
H_signal = max(0, E_tail - E_diffuse)
H_budget = min(H_display, H_signal)
e_k      = E_diffuse
e_w      = sdr_plan.white_ev
```

并增加退化门：

```text
if e_w <= e_k + minimum_window:
    H_budget = 0
```

第一版 `minimum_window` 先设为 `0.5 EV`，它是数值稳定门，不是审美参数。

解释：

- `E_diffuse` 只定义额外 HDR 亮度从哪里开始，不把实际白物体强制映射到 1.0；
- `E_tail` 来自排除不可靠 CFA clip 后的 scene tail；
- `H_budget` 是可用上限，场景像素没有走到 `e_w` 时不会自动用满；
- broad highlight、sparse emitter 以后可以调节 `e_k` 或窗口形状，但不得改变曝光锚点。

Phase 1 不引入 body percentile 的经验系数。完成样张 corpus 后才允许比较以下候选：

```text
fixed: e_k = E_diffuse
body-protected: e_k = max(E_diffuse, body_ev_p99)
soft body-protected: 在两者间插值
```

选择依据是主体亮度稳定和大面积白物体不过度发光，不是“哪张图看起来更 HDR”。

## 8. HDR 色彩几何：亮度与 chroma path 解耦

只使用公共亮度增益会完全继承 SDR 的高光褪色；只对三个通道独立扩张又容易出现霓虹色。
HDR AgX 使用二者之间的连续几何。

### 8.1 formation 中间量

对每个像素：

```text
scene Rec.2020 -> guard rail -> inset = c_scene
e_c = log2(c_scene / 0.18)        # 三通道
e_Y = log2(Y_scene / 0.18)        # 真正 Rec.2020 scene luminance
f_c = T0(e_c)                     # 当前 darktable-style per-channel formation
```

`e_Y` 必须在 inset 之前用标准 Rec.2020 Y 计算，避免 rendering primaries 反过来改变 tone
判断。

### 8.2 公共与逐通道 lift

定义：

```text
w_Y = S(e_Y)
w_c = S(e_c)
rho in [0,1]

w_mix_c = (1-rho)*w_Y + rho*w_c
p_c     = f_c * 2^(H_budget*w_mix_c)
```

含义：

- `rho=0`：三个通道获得相同 HDR lift，只改变亮度，不改变 SDR chroma；
- `rho=1`：每个通道按自己的 scene EV 使用 HDR 空间，高光色度保留最多；
- 中间值：连续的 HDR path-to-white。

Blender HDR 脚本的 `HDR_purity=0.5` 只作为 `rho` 的第一组 A/B 初值，不能直接设成默认。

### 8.3 强制恢复 tone 目标

逐通道 lift 会改变亮度。为了让 `rho` 只决定 color geometry，必须归一回公共 tone target：

```text
Y0       = luminance(f)
Y_target = Y0 * 2^(H_budget*w_Y)
Y_prop   = luminance(p)

p <- p * Y_target / max(Y_prop, epsilon)
```

由此得到：

- `H_budget=0` 时严格回到当前 SDR formation；
- `rho=0` 时归一化系数严格为 1；
- 改 `rho` 不改变目标亮度；
- 中性输入始终保持中性。

然后才执行 HDR hue restore、outset/unrotation 和 P3 color-volume fit。

### 8.4 rho 的证据来源

`rho` 不能由亮度曲线反推，应由独立 color plan 编译：

```text
rho = rho_base
    * raw_channel_confidence
    * snr_chroma_confidence
    * output_gamut_confidence
    * hue_region_policy
```

约束：

- 未剪切、SNR 充足、P3 压力低：允许较高 `rho`；
- 单通道 CFA clip：降低对应颜色路径自由度，向可靠中性轴退让；
- 多通道 clip：`rho -> 0`，不虚构高光颜色；
- RAW9 没有对齐 CFA mask：不得伪造局部 `rho`，第一版使用保守全局上限；
- skin / green / cyan 策略只改变 `rho` 或 hue path，不改变 `H_budget`、pivot 或 white EV。

## 9. Rendering primaries 与 hue path

第一版先保持当前 darktable base primaries、hue restore 和 outset，单独验证 HDR tone。之后才
开放 HDR-specific geometry：

```text
M_in(H=0)  = current SDR inset
M_out(H=0) = current SDR outset
```

任意 headroom-dependent 矩阵都必须满足：

```text
M * [1,1,1]^T = [1,1,1]^T
det(M) > 0
condition_number(M) < configured_limit
```

即中性轴不漂移、矩阵不翻转、数值条件可控。

调整顺序锁定为：

1. 现有矩阵 + `rho=0`，验证纯亮度 HDR；
2. 现有矩阵 + `rho`，验证高光色度；
3. 与 Blender 1000 nit LUT 的 neutral/primary/hue-wheel probe 比较；
4. 只有稳定残差明确指向 primaries 几何时，才拟合 HDR outset/rotation；
5. 不用样张观感直接反解 3x3 矩阵。

## 10. Extended P3 色彩体积

SDR 的 `fit_to_output_gamut(...[0,1])` 不能直接用于 HDR。HDR 合法立方体为：

```text
0 <= R,G,B <= R_display
```

正确性 oracle 使用 neutral-axis projector。对线性 P3：

```text
Y = dot(P3_luma, rgb)
C = rgb - Y*[1,1,1]
rgb_fit = Y*[1,1,1] + lambda*C
```

最大合法 `lambda`：

```text
lambda_neg = min(Y / -C_i)          for C_i < 0
lambda_pos = min((R_display-Y)/C_i) for C_i > 0
lambda_max = min(1, lambda_neg, lambda_pos)
```

这能严格保证：

- 结果在 `[0,R_display]`；
- 中性轴不变；
- 使用同一 luma 权重时 Y 不变；
- 不做逐通道 clip。

生产版本可以在边界前增加 smooth knee，但必须以 projector 为上界：平滑版本不得产生比
`lambda_max` 更大的 chroma。现有 SDR Oklab fitter 保持不动，HDR 使用独立函数。

## 11. Capture 证据如何进入 HDR

### 11.1 Tone 只读取

- reliable body / tail；
- scene white EV；
- display headroom；
- sparse-emitter topology（后续 phase）。

### 11.2 Color 只读取

- per-channel clip/fullwell；
- CFA soft mask；
- SNR/noise floor；
- P3 gamut pressure；
- hue region policy。

### 11.3 明确禁止

- clip% 改全局曝光；
- gamut pressure 改 white EV；
- RAW9 聚合 clip 伪装成逐像素 mask；
- reconstructed highlight 重新定义 reliable tail；
- HDR 容量改变夜景主体中位亮度。

## 12. 数据模型

不要复用旧 ACES/bridge dataclass。建议新建：

```python
@dataclass(frozen=True)
class HdrDisplayTarget:
    reference_white_nits: float = 100.0
    peak_nits: float = 800.0
    limiting_gamut: str = "p3"

    @property
    def display_headroom_ev(self) -> float:
        return math.log2(self.peak_nits / self.reference_white_nits)


@dataclass(frozen=True)
class HdrToneAllocation:
    knee_ev: float
    white_ev: float
    display_headroom_ev: float
    budget_headroom_ev: float
    reliable_tail_ev: float
    minimum_window_ev: float


@dataclass(frozen=True)
class HdrColorGeometry:
    channel_separation: float       # rho_base
    raw_clip_retreat: float
    snr_gate: float
    hue_restore: float
    primaries_preset: str
    gamut_fit_margin: float


@dataclass(frozen=True)
class HdrAgxPlan:
    sdr_base: RenderPlan
    display: HdrDisplayTarget
    tone: HdrToneAllocation
    color: HdrColorGeometry
```

建议 API：

```python
compile_hdr_agx_plan(
    sdr_plan: RenderPlan,
    analysis: Analysis,
    target: HdrDisplayTarget,
) -> HdrAgxPlan

apply_hdr_lift_window(ev, allocation) -> gain_ev

apply_hdr_agx_formation(
    scene_rgb,
    hdr_plan,
    raw_evidence=None,
) -> extended_linear_rec2020

fit_hdr_p3_color_volume(rgb_p3, peak_linear) -> rgb_p3

render_dual_agx(...) -> tuple[sdr_p3, hdr_p3, diagnostics]
```

## 13. 文件边界

建议新代码：

```text
dngscan/hdr_agx_math.py       # smootherstep、tone allocation、float64 oracle
dngscan/hdr_agx_plan.py       # RAW/scene/display -> immutable plan
dngscan/hdr_agx.py            # formation runtime
dngscan/hdr_color.py          # rho、HDR P3 color-volume fit
dngscan/hdr_delivery.py       # Core Image/Image I/O，最后接
```

禁止重新建立笼统的 `hdr_render.py`，防止 tone、color、gain-map 再次混在一个文件。

现有文件只做窄改动：

- `models.py`：新增上面的 plan 类型；
- `render.py`：增加独立 HDR dispatcher，不修改 SDR dispatcher；
- `raw_io.py`：只暴露已有证据，不为 HDR 改解码像素；
- `coreimage_decode.py`：不启用 Apple 默认 HDR look；
- CLI/GUI：所有数学门通过以前不出现 HDR 选项。

## 14. 分阶段实施

### Phase 0：清理与冻结

- 删除旧 ACES 2、SDR bridge、gain-map tone 实验；
- 保留本文；
- 完整 SDR golden、RAW9/LibRaw brightness contract 必须通过；
- 记录当前 SDR 输出 hash。

退出条件：仓库中没有可调用的旧 HDR 路径，SDR 像素不变。

### Phase 1：纯数学 oracle

新增 `hdr_agx_math.py` 和 `test_hdr_agx_math.py`，只处理一维 EV ramp：

- float64 `T0` reference；
- smootherstep HDR allocation；
- H=0 退化；
- 单调、有界、knee C2；
- 三组 scene plan × 五组 headroom；
- naive single-C1 failure 固化为设计回归测试。

退出条件：不读取 DNG，不产生图片，全部数学门通过。

### Phase 2：中性 HDR formation

- 在 neutral RGB ramp 上运行现有 inset/C1/outset；
- 加公共 `w_Y` lift，固定 `rho=0`；
- 输出 extended-linear Rec.2020/P3；
- 不接 gain map。

退出条件：中性保持中性，H=0 与 SDR float32 逐像素一致，夜景主体不变。

### Phase 3：独立 color geometry

- 实现 `rho` 混合与 luminance renormalization；
- 先用常数 rho 扫描 `0,0.25,0.5,0.75,1`；
- 实现 HDR neutral-axis P3 projector；
- 加 RGB/C/M/Y ramps、hue wheel、skin ramp；
- Blender 1000 nit LUT 只作外部对照。

退出条件：rho 不改变目标 Y，无 hue discontinuity，无逐通道硬裁。

### Phase 4：RAW evidence compiler

- `H_budget` 接 reliable tail；
- `rho` 接 CFA clip/SNR/gamut pressure；
- RAW9 使用 aggregate fallback；
- sparse emitter 与 broad highlight 分类只影响 HDR 窗和 color confidence。

退出条件：相同 scene intent 下，LibRaw/RAW9 的主体亮度一致；证据差异只出现在可信高光。

### Phase 5：双 rendition A/B

- 从同一 immutable bundle/plan 输出 SDR P3 和 HDR P3；
- 禁止共享可变 `exposure_gain`；
- 输出诊断 EXR/NPY 或 float TIFF，不先做 JPEG；
- 在支持 EDR 的预览中核对实际亮度。

退出条件：HDR 图本身成立，不依赖 gain map 阅读端掩盖问题。

### Phase 6：Apple reference delivery

先用 Core Image 的 SDR+HDR 双图 HEIF 写入 API 证明交付数学。理由是 Apple 已公开这条直接
路径，可隔离 DRT 与 JPEG 封装问题。

- SDR base 为冻结 P3 SDR；
- HDR alternate 为完成的 extended-linear P3；
- Core Image 只计算/封装 gain map；
- expand-to-HDR round-trip 后与 HDR alternate 比较。

退出条件：线性 round-trip 误差达标、方向/profile/headroom 正确。

### Phase 7：ISO 21496-1 JPEG

只有 HEIF reference delivery 通过后才恢复 JPEG：

- 探测当前 macOS 公共 API；
- 验证 RGB signed log-ratio 是否保留；
- 不支持时明确失败，不降级成标记错误的 P3 JPEG；
- Android/iOS/Chrome/Quick Look 实机读取。

退出条件：容器检查、像素 round-trip、跨平台识别同时通过。

### Phase 8：性能与 UI

- float32 vectorized reference；
- C++/Metal 只能在 Python oracle 后实现；
- proxy 预览与全分辨率使用同一 plan；
- UI 只暴露 display headroom，诊断显示 H_budget/H_actual；
- 不暴露内部 rho/矩阵，除非进入 debug 模式。

## 15. 数学验收阈值

### 15.1 一维 tone

采样 `e in [-16,+16]`，至少 65537 点；测试 `H={0,1,2,3,log2(10)}`：

- H=0：float32 路径与现有 SDR 最大绝对误差 `<= 2e-7`；
- EV=0：`abs(T(0)-0.18) <= 2e-6`；
- 单调：`min(diff(T)) >= -2e-7`；
- 上界：`max(T) <= 2^H + 2e-6`；
- knee 左右一阶导数相对差 `<= 2e-3`；
- knee 左右二阶导数误差单独记录，float64 oracle 应接近机器精度；
- 所有输出 finite；
- 当前 darktable 内部 toe/linear/shoulder 接缝继续满足既有 C1 测试。

### 15.2 色彩

- 中性 ramp 最大通道差 `<= 2e-6 * max(1,Y)`；
- rho 扫描时 formation-space Y 相对误差 `<= 2e-5`；
- P3 fit 后每通道 `[-2e-6, R+2e-6]`；
- neutral-axis projector 的 Y 相对漂移 `<= 2e-6`；
- hue ramp 相邻样本无大于 5 度的非输入跳变；
- H=0 全彩 probe 与 SDR 最大绝对误差 `<= 3e-6`。

### 15.3 双 rendition

- SDR 输出 hash 完全不变；
- HDR 低于 knee 的线性 RGB 与 SDR 相对差 `<= 2e-5`；
- H_actual 不超过 H_budget；
- 18% 灰、肤色主体和夜景 body 的亮度不随 H_display 改变；
- 不允许用 `max pixel` 单点通过 headroom 验收，同时报告 p99.9、p99.99 和 emitter peak。

### 15.4 Delivery

- SDR-only 解码逐像素等于输入 SDR rendition 的编码结果；
- HDR round-trip 在线性 P3 内：中位相对误差 `<= 0.5%`，p99 `<= 2%`；
- content headroom 元数据与解码测量差 `<= 0.05 EV`；
- RGB gain map 不得静默变成单通道；
- 文件写入成功不算通过，必须由独立读取端识别。

## 16. 样张验证矩阵

固定至少包含：

- Sigma fp 日间宽动态人像；
- Sigma fp 夜间酒吧人像；
- 稀疏灯源与灯罩；
- 白墙、白衣、大面积云层；
- 高饱和绿叶、蓝灯、红灯；
- iPhone 16 Pro Standard RAW；
- RAW9 与 LibRaw 可同时解码的同一 DNG。

每张输出：

```text
SDR AgX
HDR rho=0
HDR rho=0.5
HDR evidence-driven rho
Blender HDR reference（仅 probe/对照，不作为默认）
```

比较项目：主体 Y、中灰、reference-white 以上面积、H_actual、P3 越界率、hue/chroma 路径、
CFA clip 区域和 SDR/HDR 差值图。

## 17. 仍未由数学决定的参数

以下只能通过 corpus 和 EDR 实机 A/B 决定，文档不假装已有答案：

- `rho_base`；
- body-protected knee 是否优于固定 diffuse knee；
- broad highlight 与 sparse emitter 的窗口差异；
- HDR-specific outset/rotation 是否必要；
- RAW9 aggregate color confidence 的保守上限；
- 800 nit 还是 1000 nit 作为默认 authoring target；
- JPEG RGB gain map 对负 log-ratio 的实际 round-trip 能力。

这些参数可以调，但不能破坏前述硬约束。

## 18. 非目标

- 不复活 ACES 2 DRT；
- 不在 SDR JPEG 后做局部提亮；
- 不用 Apple 默认 RAW HDR look 再串 AgX；
- 不让 HDR 自动曝光夜景；
- 不用 gain map 修复错误的 HDR rendition；
- 不在数学核稳定前写 C++/Metal；
- 不在 round-trip 通过前把 HDR 选项放回 GUI。

这条路线保留了 darktable AgX 最有价值的部分：解析式 scene-to-display formation、rendering
primaries、hue restore 与可解释参数；同时用 dngscan 能看到的 RAW 证据，限制 HDR 高光何时
可以保色、何时必须收敛。HDR 与 SDR 的差异由显示目标决定，照片本身的曝光意图仍由同一份
scene-linear 数据决定。
