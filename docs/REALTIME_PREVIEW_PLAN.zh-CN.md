## 实时预览技术方案

实时预览与导出继续使用同一套颜色算法和参数语义。优化只拆分计算阶段、改变缓存边界和预览载体质量，不减少 AgX gamut fit 的 16 轮，也不建立一套“预览专用”的颜色数学。

### 分辨率与画质

- 预览固定为 1920 像素长边。1920 可被 3 整除，常见 3:2 照片得到 1920×1280；其他画幅严格按照解码后原图比例四舍五入，不裁切、不拉伸，也不强制改成 3:2。
- 冷路径用用户选择的解拜耳算法完成全分辨率 RAW 解码，再在线性 Rec.2020 场景数据上用 Lanczos 缩到目标尺寸。旧实现的半尺寸解码会绕过所选解拜耳算法，再叠加 BOX 缩放，容易让细节发软。
- 浏览器预览固定使用 JPEG q95、4:4:4。导出质量和色度采样仍由交付档独立控制。

### 冷热路径与缓存依赖

冷路径包含 RAW evidence、解码器及其版本、Core Image 版本、白平衡、高光恢复、解拜耳、全分辨率场景解码和 Lanczos 缩放。它们共同组成磁盘代理缓存键；任意依赖变化都会构建新代理。进程内保留最近两个冷配置，允许用户切换回来时直接复用，旧项按 LRU 淘汰。

热路径按以下层次复用：

1. 1920 线性场景代理和分析结果；
2. 与场景/色调结构有关的不可变 RenderPlan；
3. 与所有可见图像参数有关的 RGB8 像素（最近两帧）；
4. 包含 metrics 开关和 JPEG 表示的完整响应帧。

metrics 的延迟/补算不再重复颜色渲染，只读取同一份不可变 RGB8 像素。像素缓存不用于带自动曝光文字叠层的临时帧，避免把 UI 标注混入正常预览。

内存上限是两个冷代理、32 个小型 RenderPlan、两个 RGB8 像素帧和 24 个压缩响应帧；磁盘代理仍按文件数与总容量双重淘汰。

### Native 优化顺序

AgX 核心已经是 C++17/pybind native 实现，无需先整体重写。完成上述拆分后重新 profile；若非缓存热帧仍主要耗在 16 轮色域拟合，再把常量矩阵预合并，并在一个 native chunk kernel 内融合 Oklab 往返、二分和 gamut test。之后才考虑融合 transfer+dither 和复用常驻 worker pool。每一步都必须通过现有像素回归，保持预览与导出的算法一致。

### 输出后处理 native 优化方案

#### 计算边界

新增独立于 AgX 的 `NativeOutputPlan` 和两种入口，所有 tone core 共用，不把输出优化绑到 AgX：

- `finalize_rec2020_u8_f32`：输入 tone core 产生的 Rec.2020 float32，融合 Rec.2020→输出空间、16 轮 Oklab gamut-fit、sRGB/P3 OETF 和 TPDF dither/uint8 quantize；普通 baseline 走这条最短路径。
- `finalize_output_u8_f32`：输入已经执行 display filter、look 或 highlight chroma retreat 的输出空间 float32，融合后续 gamut-fit、OETF 和 quantize；这些功能仍保留原有 Python 色彩操作，但不再退回 NumPy 的 16 轮拟合。
- `fit_output_gamut_f32`：只暴露同一 native gamut 实现的 float32 结果，用于与 NumPy 参考逐点验证，不在产品路径额外增加一次计算。

`NativeOutputPlan` 在 Python 侧按输出色域和 gamut alpha 缓存，并预合并三组常量矩阵：Rec.2020→目标 RGB、目标 RGB→Oklab LMS、Oklab LMS→目标 RGB。kernel 每个像素只在寄存器中保存 RGB、L/a/b、L0、lo、hi；越界像素严格执行 16 次二分，容差固定为现有 `1e-4`，in-gamut 像素保持原值后只做最终 `[0,1]` 夹取。

TPDF dither 的随机序列仍由 `np.random.default_rng(0)` 按当前顺序生成两组 float32 值并传入 native。第一版不替换 RNG，确保流式 chunk、预览和导出继续消费完全相同的噪声；native 只融合 `noise_a-noise_b`、floor、clip 和 uint8 转换。这样先消除颜色中间数组和 16 轮内存往返，同时把随机算法变更隔离到未来独立决策。

native 输出 kernel 在一个 quantize group 上独占自身的像素并行；Python 外层继续按顺序提交 tone chunk，最终帧顺序和 RNG 消费顺序不变。先 profile 线程创建成本，只有它成为可测瓶颈后才引入常驻 pool，避免在同一变更里同时改变数值实现和调度生命周期。

#### 回退与失效

- `DNGSCAN_FAST=0` 永远走原 NumPy 参考；`auto` 在扩展缺失或 native 调用失败时用已经生成的同一组噪声回退；`DNGSCAN_FAST=1` 继续把 native 失败视为错误。
- native ABI 升级，旧扩展不会被静默加载。
- sRGB/P3、gamut alpha、look/display filter/highlight retreat、所有 tone core 都进入已有 plan/frame cache key；输出 plan 只缓存不可变小矩阵，不缓存像素结果。
- `render_output_linear`、HDR/gain-map 和分析路径保持原 float reference；本次只替换 SDR uint8 的最终交付路径。

### 一致性验证方案

按以下顺序设门禁，前一层失败不得进入性能验收：

1. **算法常量门禁**：native 迭代数只能是编译期 16；测试检查 ABI、sRGB/P3 plan、`alpha` 与 `1e-4` tolerance。预览和导出不得传入不同迭代数。
2. **float gamut parity**：对 sRGB/P3 和实际计划范围内的多个 alpha，覆盖 RGB 基色、灰阶、`-1e-4/0/1/1.0001` 边界、负值/高值、随机广色域值及 NaN/Inf。由于预合并矩阵会去掉 NumPy 两段矩阵间的一次 float32 舍入，要求最大绝对误差≤`1e-4`、p99.99≤`5e-5`（实测最大 `8.6e-5`）；in-gamut 输入在 float32 下不被改写，输出全部有限且落在 `[0,1]`。
3. **编码/量化 parity**：向参考与 native 注入完全相同的两组 float32 噪声；相同线程数和不同线程数必须逐字节一致。对参考完整链路，最终 uint8 要求 p99 差值为 0、最大差值≤1 code value、变化通道占比≤1%。
4. **端到端统一性**：AgX/neutral/lum/gated，sRGB/P3，EV、look、scene transform、highlight retreat 各取代表组合，对照 `DNGSCAN_FAST=0/1`；跑 stream ordering、golden、SDR freeze 和现有 native parity。预览与导出继续调用同一 finalizer，不增加预览专用近似。
5. **完整回归**：`DNGSCAN_FAST=0` 与 `DNGSCAN_FAST=1` 全量测试都通过；构建 wheel 后再从安装产物验证 ABI 和 self-test，避免只测试源码树中的旧 `.so`。
6. **性能门禁**：真实 1920px NEF 与 A7M5 各记录 output matrix、gamut-fit、transfer、dither、pixel pipeline 和端到端 p50/p95。融合阶段 p50 至少降低 25%，连续热帧 p50 不得回退超过 5%；若不满足，保留参考实现并不启用 native dispatch。

### 输出融合 profile 结果

2026-08-02 在 Apple Silicon 上用 `tools/benchmark_realtime_preview.py --output-backend numpy/native --iterations 40` 测量。两组都保持 `DNGSCAN_FAST=1`，因此 AgX native core 完全相同，只切换本次输出 finalizer；输入命中相同的磁盘冷代理，帧缓存通过逐轮改变 EV 避免命中。

| RAW | 尺寸 | NumPy 连续 p50 / p95 | Native 连续 p50 / p95 | NumPy→Native 像素管线 p50 | 首帧 |
| --- | --- | --- | --- | --- | --- |
| Nikon NEF | 1920×1275 | 153.91 / 159.23 ms | 73.87 / 75.63 ms | 143.82→62.91 ms（-56.3%） | 169.78→89.66 ms |
| SONY ILCE-7M5 compressed ARW | 1920×1281 | 120.08 / 147.75 ms | 74.23 / 80.97 ms | 109.98→64.76 ms（-41.1%） | 137.14→92.62 ms |

融合 finalizer 在每个 quantize group 上的 p50 / p95：NEF 为 5.49 / 9.26 ms，A7M5 为 4.39 / 7.47 ms。连续热帧 p50 分别降低 52.0% 和 38.2%，通过 ≥25% 性能门禁；测试仍固定 16 轮拟合并注入原 NumPy TPDF 噪声序列。

### 验收

- 3:2 横竖图分别得到 1920×1280 / 1280×1920，非 3:2 图保持自身比例；
- 更改解码器、版本、白平衡、高光或解拜耳会失效冷代理，更改 EV/色调参数只进入热路径；
- 同一参数的完整帧命中压缩帧缓存，metrics 表示变化命中 RGB8 缓存；
- JPEG 采样为 4:4:4，颜色回归和 16 轮 gamut fit 不变；
- 用真实 RAW 分别记录冷准备、首次热渲染、RGB8 复用和完整帧命中耗时。
