# 固定重建与热白平衡迁移

## 决策

用户白平衡不再参与每次 RAW 重建。文件、decoder、Core Image 版本、解拜耳、高光与 opcode 策略不变时，管线只建立一次 `DecodeContext`：

```text
RAW / black / gain map
  -> 固定 AsShot 解拜耳预条件
  -> demosaic + decoder highlight/opcode
  -> 线性 Rec.2020 DecodeContext
  -> 项目 WB / tint（hot）
  -> analysis / tone / gamut / output
```

固定 AsShot 增益只是重建预条件，不再等同于用户当前选择的色温。用户 WB 使用文件或机型的 `XYZ -> camera` ColorMatrix。令 `Cdecode/Ctarget` 为固定重建/目标白点的 `camera -> Rec.2020`（DNG Kelvin 模式按双光源标定插值目标矩阵），G-normalized 增益分别为 `Gdecode`、`Gtarget`，热阶段矩阵为：

```text
Mwb = Ctarget * Gtarget * inverse(Cdecode * Gdecode)
```

实现直接把这个共轭矩阵施加在固定线性 Rec.2020 scene 上；它等价于恢复 camera-linear 通道、替换增益再回到 Rec.2020，但不需要额外保存一份全尺寸三通道缓冲。负值与大于容器白的 headroom 使用 float32 保留，不在 WB 阶段 clip/quantize。

LibRaw 和 Apple RAW 都执行这一项目 WB。Apple 的 `neutralTemperature` 固定为 AsShot；因此 daylight 也不再需要映射到 Core Image 的不透明 temperature/tint 实现。缺少 ColorMatrix 或声明 WB multiplier 时明确退化为 camera AsShot，并在 `wb_degradation` 报告，不能猜测矩阵。

## 缓存边界

- 磁盘和内存 `DecodeContext` key：文件签名、LibRaw evidence runtime、decoder/版本、demosaic、highlight；不含用户 WB。
- `CaptureInvariant`：ceiling、noise、clip、CFA、SNR、mask/guidance，随固定 decode 一次生成。
- `BalanceContext`：WB 后 proxy、scene EV/gamut 指标与 RenderPlan；每个 WB 在一个 DecodeContext 内按需生成并走有界 LRU。
- camera WB 保留 decoder 原始 codes，不经过浮点 identity round-trip。
- 当前 export 对非 camera WB 仍在全分辨率 scene 上重算 percentile/gamut；不能误用 proxy 指标。后续可用 exact fused native analysis 缓存替换，但不得改变采样、阈值或 percentile 定义。

## 一致性边界

这次迁移有两个不同门禁，不能混为一谈。

1. **旧 oracle -> 新 oracle 是一次算法迁移。** 旧 LibRaw 会在 DHT/AHD/VNG 与高光处理前应用用户 WB，无法与后乘矩阵逐 bit 等价。迁移需 A/B 检查细节、moire、噪声、极端色温、饱和高光和负通道，并由人工审片确认固定细节结构符合产品选择。差异必须如实记录，不能称为纯缓存优化。
2. **新 oracle 冻结后要求严格一致。** preview/export 使用同一 `hot_wb_matrix_rec2020` 与同一 WB multiplier solve；同一 DecodeContext 的 memory/disk hit、进程重启、执行顺序和未来 CPU/Metal/CUDA backend，分支中间量逐 bit、最终 RGB8/RGB16/HDR 逐字节一致。任何 backend 不满足即关闭并回退 reference。

## 自动验证

- 矩阵单测：验证 `C·Gtarget·Gdecode^-1·C^-1`、相同 WB identity、G2 缺失、病态/空矩阵退化。
- 缓存单测：WB 不参与 DecodeContext identity；camera -> daylight -> camera 只调用一次 `load_raw/analyze`；同一 BalanceContext 命中同一对象。
- scene 单测：float32 保留 NaN 清理前的负值与大于 65535 headroom，不在 WB 阶段量化。
- decoder 集成：LibRaw 与 Core Image 的 `decode_wb` 均保持 camera AsShot，`applied_wb` 反映用户选择；daylight/Kelvin 都走项目 WB。
- golden 集：至少覆盖 Bayer DHT/AHD/VNG、X-Trans、clip/blend/reconstruct、3200/5500/9300K、daylight、sRGB/P3、正常曝光与饱和高光。记录旧/新 scene float diff 与最终输出 diff，人工审片通过后生成新 golden。
- 性能：在同一进程内先建立 camera DecodeContext，再轮换全部 WB。分别报告 decode 次数、WB matrix、scene reanalysis、plan/render 与端到端 p50/p95；禁止把磁盘 cache hit 冒充首次 prepare。
