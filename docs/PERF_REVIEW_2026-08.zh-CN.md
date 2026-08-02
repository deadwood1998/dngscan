# 全管线性能 review 与实施台账（2026-08-01）

实测环境：Sigma fp 24.5MP 样张，Apple Silicon 4P 核，rawpy `0.27.0+libraw.e419de08`，原生扩展 ABI 4。
基准（本轮开始时）：SDR 全尺寸导出 **9.4s**；HDR 导出 **17.0s**（峰值 RSS **3.7GB**）；平均核利用率 ~2.2/4。
注意：长时间压测后本机热降频，同日基线漂移至 ~20s——**跨批次的墙钟对比无效，只认组件级实测**。

## 结构性结论

原生核覆盖不对称：`_fast` 的 AgX core（38ms/Mpx）与融合 finalize（19ms/Mpx）只从 `render.py`
的 SDR 路径可达；**HDR 全链、auto-EV 探针、非 AgX 核全部是 320–600ms/Mpx 的 NumPy**。
HDR 导出的大头即来源于此。次级结构问题：GUI `/export` 在隔离进程从零重做 `/prepare` 已
持久化的分析；解码侧 opcode/掩膜运算是纯 Python 热点；并行度配置不对称（SDR 池硬编码 2）。

## 分级清单与状态

| 项 | 内容 | 预期（24MP） | 状态 |
|---|---|---|---|
| A1 | `_form_hdr_chunk` 全 NumPy formation → 原生 HDR kernel（agx_core 加 shoulder 段表 + peak 变体） | 并行段 −10~14s 工作量，墙钟 −2~3s | **待做（最大票）** |
| A2 | ultrahdr SDR 底图接入 `finalize_output_u8_f32` 融合核 | RSS 3.7→2.6GB；墙钟中性（关键路径在 A1） | ✅ ce3e7a7 |
| A3 | `color.fit_to_output_gamut` 路由 `fit_output_gamut_f32`（惠及 auto_ev/render_output_linear） | 浮点路径微变，需按门禁单独评审 | 待做，并入 B5 评审 |
| B4 | `/export` worker 按 `_cache_identity` 复用 npz 里已持久化的全分辨率 Analysis | GUI 导出 −1.5~2.5s（实测命中 1ms 替代 ~2.3s 重算） | ✅ |
| B5 | auto-EV 探针：plan 复用是纯去重（~0.1s）；降采样与原生 finalize 会微变估计值=声明级变化 | 大头（~2.7s 的 14 次探针渲染）需声明后才能动 | 冻结待批 |
| C6 | GainMap 施加：`np.ix_`→步进视图、角点 gather 减半（**逐位相同**已验证） | 0.90→0.45s | ✅ 1bfa02b |
| C7 | 掩膜羽化半分辨率先行（0.37→0.10s） | **改变掩膜数值=效果变化**，需单独审批 | 冻结待批 |
| C8 | rawpy fork 构建开 `LIBRAW_USE_OPENMP`（DHT 2.4s，2-3× 空间） | 解码 −1~1.6s | 待做（换钉全套回归） |
| D9 | 修正：gamut 统计需要完整 XYZ，计算不可省；可做的是 analyze 后释放常驻（-144MB，want_png 时重建）与 Y 单通道同路径提取 | −144MB 常驻 | 待做（范围缩小） |
| D10 | percentile 合并/CFA gather 复用/导出显示指标子采样 | −1~1.5s | 待做（子采样项是效果声明变化） |
| E12a | `_base_roundtrip_error` 分带 + 精确 top-K（镜像既有先例；均值 float64 累加已声明） | −0.7s −700MB 瞬时 | ✅ 1bfa02b |
| E12b | 导出抖动平面复用：预生成全帧噪声内存代价过高、按组重播种改字节——仅剩双缓冲重叠方案（~0.2s，复杂度高，缓办）；`_clip_masks_resized` 双拷贝与 `y/ev` 死重仍待做 | −0.3s −400MB | 部分冻结 |
| E12c | matplotlib 迟滞到出图时；JPEG 直接走 PIL | `import dngscan` 0.42→0.15s（每个 spawn worker 均摊） | ✅ 1bfa02b |
| E18 | SDR 渲染池 2 worker → 与 HDR 侧统一定容（`_stream_render_workers`） | pre-tone NumPy 段并行度翻倍；字节稳定性由冻结/金标夹具证实 | ✅ |

全部改动受 `PIPELINE_PERFORMANCE_EQUIVALENCE_PLAN` 的等价性门禁约束：像素 memcmp 级、
诊断标量声明级。C7/D10 子采样两项因触碰效果边界被显式冻结，启动需单独决定。

## 预期总账（组件实测外推，最终以同热状态 A/B 为准）

HDR 24MP：17s 级 → **5-6s**（A1 主导）；GUI 内叠 B4/B5 → ~4s。SDR：9.4s → ~6s；60MP 收益 ~2.5×。
