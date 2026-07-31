# 传感器支持与开放接口策略

> 2026-07-30 建立。回应"很多相机导出显示接口不支持"：两条解码接口全部开放，
> 缺数据的机型**降级并警示**，不再拒绝。本文记录策略、数据出处与逐机型状态。

## 策略：三级降级，永不静默

1. **颜色标定阶梯**（固定 Kelvin 白平衡求解，`raw_io.solve_wb_for_mode`）：
   文件自带 DNG 双光源标定 → 安装版 LibRaw 的机型矩阵 → 本项目回退矩阵表
   （`camera_matrices.py`，取自 LibRaw master 的 Adobe 系数，GPL 同族许可）→
   全部缺失时**退化为相机 AsShot 并在报告显式警示**（"结果可用，但白平衡声明
   与色彩精度可能有偏差"）。渲染照常出片——声明的降级可用，静默的降级等于
   隐藏白平衡。
2. **传感器先验**（`priors.py` + `sensor_priors.json`）：有 PhotonsToPhotos
   实测曲线的机型获得绝对标尺（PDR/读出噪声/unity gain/满阱）；无条目的机型
   全部退回单帧实测，报告注明"传感器标定数据不完整，绝对档位/动态范围数字
   可能有偏差，渲染仍可正常使用"。
3. **RAW 9**：逐文件探测支持版本（Apple 兼容列表是文件属性不是配置），不支持
   时明确提示降级 RAW 8/7 或改用 LibRaw，从不静默换算法（原有行为，未变）。

LibRaw 主解码对未知新机型的可用性事实：ARW/RAF/NEF 等容器格式跨代稳定，
文件几乎总能解包；缺的是**逐机型颜色矩阵**——这正是回退表补的洞。注意边界：
回退矩阵服务于 Kelvin 求解与报告，无法注入 LibRaw 内部的色彩转换；对 LibRaw
完全不认识的机型，Rec.2020 转换精度取决于 LibRaw 的内部回退，报告如实说明。

## 新机型适配状态（2026-07-30）

| 机型 | 先验（P2P） | 颜色矩阵 | 备注 |
|---|---|---|---|
| Sony A7 V (ILCE-7M5) | ✓ unityEv 7.16 / FWC 71k | ✓ LibRaw master | |
| Sony A7S III (ILCE-7SM3) | ✓ unityEv 8.51 / FWC 228k | ✓ LibRaw master | 大像素签名明显 |
| Sony A7R VI (ILCE-7RM6) | ✓ unityEv 6.18 / FWC 36k | **无**（刻意缺席） | 未找到已发布系数；矩阵宁缺毋猜，降级路径覆盖 |
| Ricoh GR IV | ✓ unityEv 5.73 / FWC 27k | 无需（DNG 自带 ColorMatrix） | |
| Nikon Zf | ✓ unityEv 7.42 / FWC 82k | ✓ LibRaw master | |
| Fujifilm X100VI | ✓ unityEv 5.98 / FWC 24k | ✓ LibRaw master | |
| Fujifilm X-E5 | ✓ unityEv 5.91 / FWC 23k | ✓ 借自 X100VI | 同款 40MP X-Trans CMOS 5 HR，声明的借用 |

数据出处：PhotonsToPhotos PDR.htm / RN_e.htm（2026-07-30 提取，含 P2P 直接
发布的 `fwc` 与 `unityEv` 字段；PDR 曲线只取实心点，三角标记起点记入
`suspect_iso_min`）；矩阵出处逐条记录在 `camera_matrices.py`。

## LibRaw 升级路径（2026-07-30，已执行）

轮子版 rawpy 0.27.0 捆绑 LibRaw **0.22.1 发布版**。对本清单实测：0.22.1 已知
A7S III、X100VI、**Zf**（初判"缺 Zf"是 `strings` 默认 4 字符下限吃掉了
"Z f" 3 字符串的工具假象，已用 `-n 3` 复核更正——教训：用工具探测前先想清
工具自己的截断规则）；master（快照 2026-07-18，commit e419de08）对本清单的
增益是 **A7 V** 一台，外加约两年其他新机型表项。X-E5/GR IV/A7R VI 连 master
都没有——回退矩阵表对它们仍是必需层。

升级不能走 dylib 换装：master 把共享库 soname 从 25 升到 26（ABI 声明不兼容，
结构体布局可能变化，强行换装是内存踩踏不是升级）。受支持的路径是**源码重建
rawpy**：其 sdist 自带"编译并捆绑 external/LibRaw"的官方构建机制。注意 sdist
**自带 vendored 的发布版 LibRaw**，必须强制替换而非"缺了才装"——第一次构建
就是这样"成功"地重编了旧表。一键脚本：`tools/build_libraw_master.sh`（钉住
验证过的 commit；换钉必须全套回归 + 若解码输出漂移则重基线 SDR 冻结/金标）。

本机已按此路径升级并验证：`rawpy.libraw_version = (0, 22, 0)`（master 线），
soname 26，A7 V 入表；**全套 411 项测试零漂移通过**——master 对既有机型
（fp/iPhone 样张）的解码逐字节兼容，SDR 冻结与金标均未失效。

两层的分工从此明确：**LibRaw 升级**解决"LibRaw 内部色彩转换缺矩阵"（回退表
够不到的那一半）；**回退矩阵表**覆盖"比 master 还新"的机型窗口期（当前：
X-E5 借 X100VI 矩阵、A7R VI 待上游、GR IV 走 DNG 自带标签）。0.22.1 已知
机型的回退条目（A7S III/X100VI/Zf）永不触发，保留作老构建环境的防御层。

## 解码格式支持：颜色表缺口 vs 格式缺口（2026-07-30）

新机型解码失败分两类，处置完全不同：

- **颜色表缺口**（文件能解包、缺机型矩阵）：走上文的标定阶梯优雅降级，回退表
  可补——A7 V、X-E5 这类属于此类。
- **格式缺口**（LibRaw 根本打不开文件）：回退表无能为力，升级 LibRaw 也未必
  有用。**典型案例：尼康高效压缩（HE/HE\*）NEF**——Z9/Z8/Z6III/Z50II 世代的
  HE 格式使用 intoPIX **TicoRAW** 编码，授权原因 LibRaw（连 master）与
  darktable 的 rawspeed 都无法解码。同机身的『无损压缩』NEF 不受影响。

格式缺口的处置（`raw_io._unsupported_format_guidance`，报错即引导）：

1. **Adobe DNG Converter**（免费，持有 TicoRAW 授权）把 HE NEF 转 DNG——
   转换后本工具全功能可用（含 CFA 证据层与 HDR）；
2. 相机内改用**无损压缩** RAW；
3. **Apple RAW 独立解码路径（规划中）**：Z50 II 等新机在
   [Apple 的 RAW 兼容名单](https://support.apple.com/en-us/122870)上，
   Core Image 可以解这些文件——但当前架构里 coreimage 分支仍依赖 LibRaw
   提供证据层（聚合统计、对齐参考），LibRaw 打不开则整体失败。独立路径需要
   "无证据分析"降级形态（analysis 层约 21 处原始数据依赖的重构）+ 真实 HE
   样张做端到端验证，已立项；落地后此类文件可用 RAW9 出片（SDR 优先，HDR
   预算因无 CFA 证据将如实拒绝或降级）。

Apple 覆盖注记：Z50 II 在 macOS Sequoia/26 名单上（标准 NEF 确认；HE 变体的
Apple 原生解码覆盖待真实样张验证——第三方如 RAW Power 以自带扩展解码支持
HE/HE\*，说明系统级覆盖可能不完整）。

## 逐文件支持探针（`--support`）

"支持"不再是一个模糊词：`dngscan <文件> --support` 输出该文件在两条解码线上的
**逐档确定性报告**（`decode_support.probe_decode_support`，只读元数据不解码；
GUI 选中文件后同一报告显示在解码器控件下方）：

```text
机型：SIGMA SIGMA fp
LibRaw：✓ 完整支持（文件自带 DNG 双光源标定）
Apple RAW：✓ RAW 9（最新解码模型）
传感器先验：✓ 有（PhotonsToPhotos 实测标尺）
```

分层定义——LibRaw：`✗ 格式缺口`（打不开，回退表无效，如 HE NEF）→
`△ 色彩无锚`（可解码、零标定）→ `△ 回退矩阵`（WB 已代偿、内部转换仍缺）→
`✓ DNG 自带标定` / `✓ 矩阵在表`；Apple RAW：`✗ 不支持` → `△ 仅 RAW 7/8`
（可显式降级）→ `✓ RAW 9`，另有 `⚠ 证据层依赖 LibRaw` 的耦合标记（格式缺口
文件即使 Apple 支持也整体不可用，直到独立路径落地）；传感器先验单列（只影响
分析标尺，不影响渲染）。格式缺口的报错信息与本探针互链。

## iPhone 双解码对照

同一 iPhone 16 Pro ProRAW 帧、同一 AgX plan：LibRaw 施加文件内的 DNG `GainMap`，
RAW 9 施加 `FixVignetteRadial`——两条路径的角部暗场矫正相互印证：

![iPhone 16 Pro 同帧双解码对照](assets/decoder-iphone-libraw-vs-raw9.jpg)

## iPhone 主摄 CMOS（IMX903）检索纪要

iPhone 16 Pro / Pro Max 主摄为索尼定制 IMX903：48MP quad-Bayer、1.22µm、
**双层晶体管像素**（光电二极管与像素晶体管分层堆叠，放大管加大使饱和信号量
约翻倍——这是它 FWC 的结构来源）、14-bit ADC、像素级 DCG（双转换增益）、
22nm 制程、100% Focus Pixels。

**尺寸存在信源分歧**：多数英文信源与实拍规格链为 1/1.28"（与 15 Pro 光学
连续），部分中文信源沿用早期 1/1.14" 传闻。判定：**1/1.28" 更可信**——
1/1.14" 出自发布前的传闻链，未获拆解证实。

**为什么 iPhone 不进先验表**：①PhotonsToPhotos 无 iPhone 16 Pro 条目（最近
的是 14 Pro Max/IMX803，同为 48MP 1/1.28" 前代架构，可作参照级但不可冒充
本机数据）；②ProRAW DNG 是多帧计算融合（Deep Fusion）的产物，单帧光子
转移意义上的"传感器先验"对它定义不良——RAW 健康度检查（lag1 自相关、
直方图空码）才是逐文件的实测判据；③ProRAW DNG 自带完整双光源 ColorMatrix，
颜色标定阶梯第一级直接命中，无需回退。

来源：[GHOSTEK iPhone 16 相机规格](https://ghostek.com/blogs/ghostek-insider/the-iphone-16-camera-pros-cons-specs) ·
[MacRumors 论坛 IMX903 讨论](https://forums.macrumors.com/threads/sony-imx903-sensor-on-16-pro.2438445/) ·
[AppleInsider 规格泄露](https://forums.appleinsider.com/discussion/237374/exclusive-every-iphone-16-iphone-16-pro-camera-spec-capture-button-detail-revealed) ·
[知乎：索尼主摄级传感器综述](https://zhuanlan.zhihu.com/p/15276408972) ·
[EET-China IMX903 报道](https://www.eet-china.com/mp/a318363.html)
