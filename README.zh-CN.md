# dngscan

这是我给自己写的一个 RAW 转 JPEG 小工具。

我很喜欢 AgX 处理数码影像的方式，尤其是它的高光和高纯度颜色，但我通常只想把一张
RAW 用 AgX 显影出来，并不想每次都打开一整套修图软件。darktable 的 scene-linear
管线是这个项目的基础，只是对我来说，它作为完整编辑器提供的东西远远超过了这件事本身。

所以 dngscan 只做这一条路径：读取 RAW，分析传感器真正记录下来的信号，在
scene-linear Rec.2020 中形成图像，用 RAW 分析结果编译 tone plan，再经由 AgX 压缩成
sRGB 或 Display P3 JPEG。它不是修图工具，更像一个非常偏科的数码显影器，或者一个
信号与算法处理的小玩具。

这个仓库公开主要是为了让认识的朋友也能拿去用。有兴趣的人可以自己折腾代码、参数和
不同相机的数据。

[English](README.md) · [许可证](LICENSE) · [第三方声明](NOTICE.md)

## 我为什么要单独做这条管线

我一直觉得 darktable 的 scene-referred 管线很像一间信号处理实验室，乐趣就在于理解每个
模块怎样改变信号。dngscan 从里面取出一条我最常用的路径：LibRaw 解释、scene-linear
Rec.2020，以及 darktable GPL `agx` 模块里的曲线构造与原色几何。AgX 本身来自 Troy
Sobotka，并在 Blender / EaryChow 生态里发展；这里主要通过 darktable 面向照片的实现来
继承它。

但如果这里只是把 darktable 的 AgX 模块单独拆出来，意义其实不大。dngscan 真正想做的，
是把 RAW 采集层的信息一直带到最终显示变换里。

darktable 的 AgX 模块工作在去马赛克、白平衡和曝光之后的浮点图像上。它能看到图像，却
看不到原始 CFA：不知道哪个通道真的在传感器上剪切了，也不知道一块平滑高光究竟来自
真实信号还是高光重建。dngscan 是一体化的小管线，可以在去马赛克前保存这些证据，再用
它们区分可靠的场景主体、传感器尾部和已经丢失的高光信息。

我对“自动”的理解也建立在这里。自动判断不是替照片决定审美，而是把可以测量的东西交给
测量：黑白电平、逐通道 CFA 剪切、噪声底、可用动态范围、亮度主体和高光尾部。这些信息
可以决定曲线需要容纳多少 scene EV、什么时候允许色度向白退让，以及什么时候不应该相信
一个重建出来的像素。

曝光补偿、白平衡、风格和 LUT 是另一回事。它们表达的是拍摄意图或个人口味，因此留在
这套自动分析之外，作为明确的选择。我并不要求曝光与白平衡永远不能动，只是不希望内容
自适应算法在没有说明的情况下把夜景拉成灰色，或者把现场光本来的颜色抹掉。

## 管线

```text
RAW / DNG
  |
  +-- Capture
  |     black / white level
  |     逐通道 CFA 剪切与满阱余量
  |     噪声可信度与可用动态范围
  |     去马赛克、高光处理、相机色彩解释
  |
scene-linear Rec.2020
  |
  +-- 可选的相机响应前馈
  |
  +-- Tone
  |     black point / white point / pivot
  |     contrast / toe / shoulder / view brightness
  |
  +-- Color geometry
  |     AgX inset / outset / hue path
  |     RAW clip retreat / punch / gamut fit
  |     可选风格或本地 LUT
  |
  +-- Delivery
        sRGB / Display P3
        8-bit TPDF dither
        JPEG 质量与色度采样
```

这几个层次是刻意分开的。Tone 层只负责亮度关系和显示动态范围；Color geometry 层负责
色相路径、色度压缩与向白过渡；Capture 层提供事实，但不直接决定口味。这样调整某个环节
时，至少能知道画面为什么发生变化。

## Capture：RAW 证据从哪里来

### 黑白电平与逐通道剪切

dngscan 从 `raw_image_visible` 和 `raw_colors_visible` 读取去马赛克前的 CFA 数据。黑电平
来自 metadata；full-well 则先检查每个通道顶端是否存在可信的饱和堆积，有就使用实测
ceiling，没有才回退到逐通道 metadata white level。它不会拿一个标量替所有 R/G/B，
剪切阈值因此也是一张按 CFA 颜色生成的 threshold map。没有任何通道出现可靠堆积时，
报告会明确把 full-well 标为 metadata fallback，而不是把估计值写成实测值。

这一点会影响的不只是报告里的 clip%。空间剪切图、2×2 cell 指标、高光分类和渲染时的
clip mask 都使用同一套阈值。如果某台相机的绿色通道比红色更早到满阱，后面的管线应该
知道那是绿色信息先丢了，而不是把三个通道都当作同时剪切。

高光重建可以补出连续的亮度和看起来合理的颜色，但它不能重新获得传感器没有记录的信号。
因此剪切证据在重建之前保存，后面重建得再平滑，也不能反过来定义全图的 white endpoint。

### 去马赛克

全分辨率导出的 `auto` 顺序是 DHT → DCB → AHD，具体取当前 rawpy/LibRaw 构建实际支持的
最高优先级算法；X-Trans 等非 Bayer 数据继续走 LibRaw 对应路径。预览使用 half-size
2×2 超像素合并，所以预览适合看曝光、颜色和高光路径，不适合评价最终纹理。

dngscan 不做降噪，因此去马赛克也是主要的纹理选择。DHT 适合低 ISO 的干净信号；重噪声
夜景里，DCB、AAHD、VNG 或 PPG 有时比更激进的细节插值自然。标准 rawpy wheel 不一定包含
AMaZE、LMMSE、VCD、AFD 等 GPL demosaic pack 算法，实际可选项取决于本机 LibRaw 构建。
GUI/CLI 可手动指定 `dht / dcb / ahd / aahd / vng / ppg`；如果本机 LibRaw 还带有其他
算法，把它加入 `DEMOSAIC_CHOICES` 即可交给现有的可用性检测与回退逻辑。

### 可选 Core Image 管线

`--decoder coreimage` 和 `lum` / `neutral` tone 核是同一类东西：第五条**对照路径**，
换的是对这次拍摄的解释方式，不是默认画质升级。它用 `CIRAWFilter`（文件支持时用
RAW 9，否则取最高可用版本——部分 Fujifilm RAF 只到 8，不会被标成 9），把所有主观处理
项清零后渲染到线性 Rec.2020，因此交给 AgX 的工作空间与 LibRaw 路径完全一致。

它是**独立管线，不是 LibRaw 的后端**。Core Image 会执行文件里的 DNG opcode：在
Sigma fp 的 DNG 上是逐平面 `WarpRectilinear` 加一张镜头阴影 `GainMap`。这个畸变校正
把画面角落移动了数十像素（24MP 实测约 70px），所以 LibRaw 的逐像素 CFA 掩码描述的
已经是另一批像素——它们被丢弃而不是重映射，因为沿用会让 clip retreat 作用在错误的
位置上。于是这条路径没有逐像素 CFA 证据：`--tone-core gated` 会被拒绝，clip retreat
不运行，`--highlight-mode` 也不适用（Core Image 有自己的高光处理）。而聚合型 RAW 事实
（黑白电平、剪切百分比、SNR、噪声底、白平衡证词）是分布而非像素位置，依然有效，仍由
LibRaw 提供。报告会写明解码器、版本，以及被执行的 opcode。

在固定补偿 `1/0.9314` 之后，两条管线在中间调上一致（ISO 2500 实测相差 0.01 EV），
差别主要来自相机解释本身——在 Sigma fp 样片上表现为更暖的肤色和不同的高光走向。另有
两点行为差异来自解码器本身而非口味，做 A/B 之前值得知道：

- **`--ev auto` 在两条管线上可能给出不同曝光。** Apple 保留了 diffuse white 以上的细节，
  亮度参考的高光增长预算因此看到更多接近白的像素，提升被更早收住。某张 ISO 2500 片子上
  LibRaw 取 +0.73 EV，Core Image 取 +0.44 EV。想比较解码器本身时，请固定 `--ev`。
- **Apple 的缓冲带有真实的镜面高光余量**——某张片子有 2.7% 的像素在 diffuse white 之上，
  最高到线性 2.06。dngscan 为此保留量化空间，编译出的白端因而可以超过 +3.00 EV 的下限
  （该片实测 +3.67 EV），shoulder 会把这些高光滚下去而不是直接截断。

另外曾观察到 RAW 9 即使强制关掉降噪仍会抹掉约 9% 的高频能量（某张高 ISO 夜景），但另一张
ISO 2500 的片子上没有出现同样差异——把它当作逐场景观察，而不是固定特性。`--wb daylight`
在尚未验证温度/色调映射前会直接拒绝。

### 白平衡

`camera` 使用文件里的 AsShot 测量，`daylight` 使用 LibRaw 的日光标定乘子。前者跟随拍摄
现场，后者适合让同一光线下的一组照片保持固定配平。

日光、阴天和阴影大致落在可预测的日光轨迹上，机内测量通常足够有用；混合光、窄谱 LED、
荧光灯和钠灯则不是一个简单的色温问题。还有些看起来像“白平衡不对”的变化，实际来自
tone curve 对亮度与纯度的重新分配，所以 WB 与 DRT 在管线里保持独立。AsShot 相对日光
乘子的偏离也会写入分析结果，它既是白平衡数据，也是拍摄现场光源留下的信息。

我不把显示器前的肉眼判断当作绝对白点测量：眼睛已经适应了显示器和房间环境。Hunt、
Stevens、Abney、Bezold–Brücke 等色貌效应还会让亮度和纯度变化被感知成色相或冷暖变化，
肤色、天空和植物这些记忆色也不是简单的色度学目标。看见“偏色”时，先区分它来自光源、
相机配平，还是 tone/color geometry，通常比直接转动色温更有用。

### 高光处理

LibRaw 的三种选择处理的是重建后的观感：

- `clip` 在饱和处直接截断，最接近传感器实际状态，但逐通道剪切可能留下色边。
- `blend` 在剪切边界混合，让过渡更平缓。
- `reconstruct` 根据幸存通道估算丢失通道，可以恢复连续结构，但色度属于推断。
- 重建色相往往会向幸存通道偏移，因此连续不等于色彩真实。

我一般用 `reconstruct` 出照片，用 `clip` 检查传感器和算法本身。无论选哪一个，RAW
剪切证据都不会改变。

## Tone：曝光和曲线怎样确定

### 固定曝光锚点

整条管线以 scene-linear `0.18` 作为名义中灰，曝光基准来自固定的相机常数与手动 EV，
而不是让每张照片的中位数自动变成 18% 灰。常数缩放不会破坏场景意图：暗场景进 AgX
之前依然暗，明亮场景依然亮，不同照片之间的相对关系不被内容自适应曝光重新排列。

GUI 里的“亮度参考”是一个主动调用的对照读数，也就是 CLI 的 `--ev auto`。它会尝试把
全图中位放到 18% 灰，并受新增高光剪切预算约束。已经在 CFA 上剪切的灯不占这份预算，
因为它本来就是场景里的发光体；只有本来仍有信息、却被新增 EV 推到显示上限的区域会
限制提升。全图中位仍然可能被背景骗过，所以它只是参考，不是默认曝光。

### 场景统计不是简单 min/max

Tone plan 会把可靠主体和高光尾部分开。主体统计剔除 CFA 已剪切或可信度过低的区域，用来
估计 black、pivot、contrast 和主体所在的中频范围；尾部只负责给 shoulder 留出空间。
点状灯源与大面积明亮表面也不是同一种高光：前者可以进入 roll-off，后者如果被同样压到
顶端，会让整张图显得又暗又刺眼。

因此 tone plan 里的几件事分别有自己的依据：

- `black point` 与 `toe` 参考噪声底、暗部可用范围和目标黑场。
- `white point` 与 `shoulder` 参考可靠亮度尾部、显示余量和发光体拓扑。
- `pivot` 与 `contrast` 参考主体中间调，不让少量极亮像素支配整张照片。
- `view brightness` 只抬曲线内部，保持真黑与目标白端点，用于干净但整体偏暗的场景。

### GUI 中的四个明暗微调

GUI 不直接暴露自动 pivot、black EV 或 white EV，而是在编译好的 tone plan 上提供四个有限
偏置。四个滑块的`自动`中心值就是分析结果，不是另一套 preset；全部归零时直接沿用原来的
render plan，输出不变。

| 选项 | 向左 | 向右 | 不会改变什么 |
| --- | --- | --- | --- |
| `中间调亮度` | 主体更沉、更暗 | 提亮主体和可见暗部 | 不移动 scene exposure、黑点或白点 |
| `中间调对比` | 中间调更柔和 | 拉开自动 pivot 两侧的明暗距离 | 不移动 pivot 本身 |
| `暗部过渡` | toe 更深，更快沉入黑场 | toe 更开放，阴影层次更容易看见 | 不移动黑点，也不会创造低 SNR 信号 |
| `高光过渡` | shoulder 更直接，高光更有冲击力 | shoulder 更柔和，更早保留亮部层次 | 不移动白点或 RAW 剪切位置 |

`中间调亮度`和曝光 EV 最容易混淆。曝光 EV 在 scene-linear 域缩放信号，会改变进入
shoulder 的位置并消耗高光余量；中间调亮度是显示侧的内部曲线调整，真黑和目标白保持不动。
`中间调对比`也不是另一个亮度控制：它围绕自动 pivot 改变斜率，决定主体内部的明暗距离，
而不是把主体整体上下移动。

实际使用时，先用`中间调亮度`确定主体明暗，再用`中间调对比`确定立体感，最后分别调整
`暗部过渡`和`高光过渡`。打开暗部只能展示已经记录到的内容；低 SNR 场景开得过多，也会把
读出噪声和色噪一起带出来。`高光褪白`不属于这四个亮度控制，它只处理接近显示白的色度路径。

### darktable 风格的 C1 曲线

现在的主曲线沿用 darktable AgX 的 C1 分段构造：toe、线性 latitude 和 shoulder 在连接点
同时保持数值与一阶导数连续。black/white EV、contrast、toe/shoulder power 和 latitude
由 tone plan 提供，但 EV 0 到 18% 的校准锚点保持稳定。

我选择这条结构，是因为只把场景 min/max 塞进一条普通 sigmoid 很容易让少数灯源定义
white EV，结果就是高光很刺眼而主体仍然偏暗。C1 端点和主体/尾部分离，让“场景有多宽”
与“主要内容应该落在哪里”成为两件不同的事。

## Color geometry：AgX 真正改变了什么

裸的逐通道 S 曲线会让 R/G/B 以不同速度进入 toe 和 shoulder，高纯度颜色的色相因此会
随亮度漂移。AgX 不只是一条 sigmoid；它的关键是曲线前后的原色几何。

曲线前的 `inset` 把工作原色向中性轴收缩并做小幅旋转，避免极纯颜色直接撞上单通道上限，
给饱和高光留出平滑的 path-to-white。曲线后的 `outset` 再恢复纯度，但它刻意不是 inset
的严格逆矩阵。两者之间的差异，以及可选的 hue restore，共同构成 AgX 的颜色性格。
这也在预先处理裸逐通道曲线的 notorious six：例如纯红随亮度走向橙黄、纯蓝走向 cyan；
inset 的小幅旋转同时承担一部分 Abney 式感知色相补偿。

dngscan 默认使用 darktable 的 `smooth` 原色参数。`base`、`punchy` 和 `muted` 保留为 AgX
生态中的几何对照，不参与 RAW 分析，也不改变曝光算法。

AgX 的代价也来自同一个结构。inset 在曲线前先降低纯度，而这份纯度主要由落入 toe 的内容
通过逐通道扩张赚回来，因此高 ISO 夜景有时反而显得很浓，明亮宽 DR 日景却容易偏平。
Blender 生态常把 Base 与 Punchy look 配套使用，本质上也是在处理这件事。另一个代价是
色度与内容在曲线上的位置耦合：同一个物体换一个构图或曝光，落入不同曲线区间后可能得到
不同纯度。`punch`、`gated` 和 `lum` 都是为了把这些影响拆开观察，而不是否定 AgX。

### 四条压缩核心

四条核心共用同一个曝光锚点、CFA 证据和交付端保护，方便在相同 EV 下拆开比较：

| 核心 | 底层差别 |
| --- | --- |
| `agx` | 完整的 inset → 逐通道 C1 curve → hue path → outset。默认成片路径。 |
| `gated` | 同时计算 AgX 色彩结果与亮度保持结果，由 RAW 剪切、余量和噪声置信度逐像素混合。 |
| `lum` | 同一条场景编译的 C1 曲线只作用于亮度 norm，RGB 比例保持，不进入 AgX inset/outset。 |
| `neutral` | 固定的普通 shoulder，不使用场景编译的 AgX 几何，作为常规转换参考。 |

`gated` 不是另一条曝光曲线。它先把 AgX 候选归一到与 lum 候选相同的 Rec.2020 亮度，再
决定混入多少色度路径，所以亮度只有一个权威，mask 边界不会产生明暗接缝。它利用的是
darktable 模块本身看不到的 CFA 信息：某个颜色变化究竟来自有效通道，还是发生在已经
剪切并被重建的区域。

`lum` 则是刻意保留 RGB 比例。它能保住中频颜色纯度，但亮而饱和的颜色也更容易出现霓虹感，
因为颜色不会像 AgX 那样主动向白退让。`y`、`max` 和 `power` norm 分别在色度学亮度、最响
通道保护和两者折中之间选择。

### RAW clip retreat、punch 与 gamut fit

RAW clip retreat 只在 CFA 证据表明通道信息丢失时工作，在曲线前把颜色向该亮度下的中性轴
收回。它与 AgX 的全局 inset 不同：一个由传感器真实剪切驱动，一个是显示变换本身的颜色
几何。

`punch`（GUI 中的`中频纯度`）用来补偿 AgX inset 在明亮宽动态场景里的整体去纯度。
它在 Oklab 中工作，自动值由主体亮度、可用 DR 和 tone window 共同门控；在中性轴、深影、
亮部、已经很浓的颜色和肤色
区域分别衰减。所有权重都乘在增益的增量上，因此 gain 始终 ≥ 1：它只补纯度，不会在某个
区域反向去饱和。夜景或高 ISO 场景可以精确归零并短路算子，避免放大暗部色噪声。GUI 强度
只是分析值的倍率，`1` 使用自动值，`0` 完全关闭。这仍然是基于有限样张调出的全局策略，
不是传感器测量本身。

`高光褪白`是另一层很轻的显示侧色度偏置。它不改亮度 shoulder，也不冒充 RAW 高光重建；
向右让接近显示白的颜色更早收向中性轴，向左则在最终 gamut fit 的保护下保留更多高光色度。

最后的 gamut fit 发生在 tone 和风格之后。它把无法装进目标 sRGB/P3 的颜色沿 Oklab 色度
方向压回边界，而不是简单逐通道 clip。这样 AgX 或 P3 保下来的高光颜色不会在最后一步突然
崩成硬原色。

## 我还在保留的前馈实验

我很喜欢“在进入 AgX 之前，先用测量数据补偿相机某些可重复缺陷”这个想法。更进一步，
如果两套传感器与滤镜栈的光谱响应都测得足够清楚，也可以在原相机真正记录到的信息范围内，
近似另一台相机的部分响应关系。

项目里的 ARRI-like 前馈来自一个很私人的目标：我想看看能不能让 Sigma fp 稍微靠近我在
ARRI 画面里喜欢的肤色——血色撑起来的温润感，以及偏冷 cyan 环境带来的衬托。我最初的
猜想与 ALEV 滤镜栈较宽松的红光/近红外响应有关，而 fp/IMX410 本身也有不同的滤镜和洋红
行为。

现在这份实现把公开的相机 SSF、光源 SPD 和材料反射谱做光谱积分，对皮肤、植物、cyan、
中性与洋红等材料类别拟合受约束的 3×3 映射，再用 `(R/G, B/G)` 色度平面上的软窗口限制
每个映射的作用域。窗口会通过 von Kries 缩放随所选白平衡移动；中性轴约束避免它变成
隐性白平衡，逐类残差和跨类泄漏则进入置信度。

ALEV III SSF 数字化自 Leonhardt & Brendel 的 CIC23 论文。ARRI 在论文中对五台 ALEXA
的测量取平均，因为传感器叠层的干涉纹理会随个体变化。Sigma fp 一侧使用 AMPAS
`rawtoaces-data` 中由 Weta Digital 测量的 Sony A7 III 整机 SSF；它同样基于 IMX410，
但不能等同于 fp 自己的完整滤镜栈。相机到 Rec.2020 的 profile 使用 AMPAS 的 190 条训练
反射谱拟合。这里的来源和替代关系都保留在标定文件里，不把“同一块 CMOS”写成“同一台
相机”。

它有很明确的物理边界：如果两种材料在 fp 上已经成为同色异谱，逐像素矩阵不可能重新创造
它们在 ALEV 上本应有的区别。而且传感器滤镜栈存在个体差异，严肃标定应该针对手上的每一台
相机。我没有可控光源、标准靶和光谱设备，所以现有结果更接近一个克制的几何颜色映射，离
我最初想要的 ARRI 肤色仍有距离。数据来源、假设、CSV 和拟合报告放在
[`dngscan_assets/spectral/`](dngscan_assets/spectral/) 里。

## 风格与 LUT

仓库自带一个我自己写的 `optic_warm_cyan`，因为我平时确实喜欢用。它是 AgX 之后的 Oklab
色度场，不是厂商 LUT，也不冒充相机前馈。

代码还留了 Kodak 2383、RED IPP2 和 Sony LC-709TypeA 的可选 `.cube` 槽位。合法拥有的
LUT 可以放进 `dngscan_assets/vendor_luts/` 下对应路径，GUI 会自动识别；仓库本身不分发
这些文件。前馈、AgX 几何和显示端 LUT 分属三个不同位置，效果即使相似，含义也不一样。

## 输出

SDR 输出是带确定性 TPDF 抖动的 8-bit JPEG，默认 quality 100、4:4:4。抖动发生在量化前，
用来减轻平滑渐变的断层；它不改变 tone plan。也可以选择 4:2:2 或 4:2:0 来减小文件，
代价是色度分辨率。Display P3 会嵌入 ICC profile，找不到 profile 就停止导出，不写未标记
的宽色域数据。

项目里还有 ISO 21496-1 gain-map HDR JPEG 路径。HDR 输出以 P3 SDR 底图为兼容层，再附加
亮度增益图；`--output-format ultrahdr` 选择它，`--hdr-headroom` 用 EV 指定增益图上限。
这条路径目前仍然是实验项。

## 快速开始

需要 Python 3.10 或更新版本。

```bash
git clone https://github.com/Gen-416/dngscan.git
cd dngscan
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
python -m dngscan.gui
```

打开终端里显示的 localhost 地址即可。GUI 完全在本机运行，不上传 RAW。第一次打开文件会
解码、分析并建立 1280px 代理；后续预览复用内存与磁盘缓存，正式导出始终重新使用全分辨率
scene buffer。全分辨率导出放在一次性工作进程中，结束后大数组随进程释放，不长期留在 GUI
服务里。

macOS 缓存默认在 `~/Library/Caches/dngscan/preview-v1`，上限 768 MB，旧条目自动淘汰。

我通常从 EV 0、`AgX`、`smooth` 原色、camera WB 和 reconstruct 高光开始，再根据照片本身
调整。quality 100 和 4:4:4 是默认输出。

### CLI

```bash
# 默认 AgX JPEG
python -m dngscan photo.dng --jpeg photo.jpg

# 高光重建 + Display P3
python -m dngscan photo.dng --jpeg photo_p3.jpg \
  --highlight-mode reconstruct --output-gamut p3

# RAW 分析图和 CSV
python -m dngscan photo.dng --jpeg photo.jpg --scan --csv photo.csv

# 相同 EV 下比较另一条核心
python -m dngscan photo.dng --jpeg gated.jpg --tone-core gated

# 可选 Core Image scene 缓冲（macOS；证据层仍为 LibRaw）
python -m dngscan photo.dng --jpeg ci.jpg --decoder coreimage
python -m dngscan photo.dng --jpeg ci8.jpg --decoder coreimage --coreimage-version 8

# 主动使用亮度参考
python -m dngscan photo.dng --jpeg reference.jpg --ev auto
```

完整参数见 `python -m dngscan --help`。

### 可选 C++ 加速

NumPy 是参考实现，不编译原生扩展也可以正常使用。pybind11 C++ 内核只加速正常 AgX 的热点
路径：formation、C1 curve、hue restore 和 punch；RAW 分析、tone plan 与回退策略仍然在
Python。

```bash
pip install pybind11 cmake
tools/build_native.sh
```

`DNGSCAN_FAST=auto` 为默认值；`0` 强制 NumPy；`1` 要求必须走原生内核并在失败时报错。
内核释放 GIL，可以与现有的分块导出配合；AgX 热路径实测约 2×。导入时还会检查 ABI 并跑
自测，当前真实场景的线性差异控制在约 `2e-6`，最终 8-bit 差异不超过一个抖动步长。原生
内核的职责只是减少导出时间，不改变成像选择。

## RAW 分析图

`--scan` 输出六面板报告，包括 SNR 对档数、分离的 R/G/B RAW 分布、曝光与色域压力、空间
曝光区与剪切通道图，并列出逐通道 full-well、clip、black level 和 WB 读数。RAW 分布横轴
使用距剪切的 stops，纵轴为峰值归一化线性密度；图上的密度曲线会轻微平滑以便阅读，
clip%、中位、分位和其他统计始终从未平滑的原始样本计算。SNR 与动态范围是单帧估计，不是
完整 photon-transfer 测量；容器 bit depth 也不等于可用动态范围。

## 许可证与来源

dngscan 使用 GPL-3.0-or-later，因为 AgX 曲线与原色几何实现派生自 darktable 的 GPL
`agx` 代码。各项代码、光谱数据和可选依赖来源见 [NOTICE.md](NOTICE.md)。

AgX 由 Troy Sobotka 提出，并在 Blender / EaryChow 生态里发展；这里主要跟随 darktable
面向照片的实现。ARRI、ALEXA、ALEV、Sony、Sigma、RED、Kodak、darktable 和 Blender 等
名称属于各自权利人，这里仅用于说明数据来源、兼容性和管线中的比较对象。
