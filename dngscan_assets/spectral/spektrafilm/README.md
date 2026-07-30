# spektrafilm 胶片 profile（原样引入）

来源：[spektrafilm / agx-emulsion](https://github.com/andreavolpato/agx-emulsion)
（Andrea Volpato），profile 数据许可为 **CC BY-SA 4.0**（见同目录
`SPEKTRAFILM_LICENSE.txt`），与本仓库代码的 GPL-3.0-or-later 分开适用。
上游从厂商数据手册与科学文献处理得到这些测量数据；原始数据版权属于各厂商。

本目录文件为**未修改的原样副本**（license 要求修改需在 CHANGELOG 中追踪；
当前无修改）。dngscan 的曲线拟合预设与前馈拟合目标由这些 profile 推导，
推导产物同样按 CC BY-SA 4.0 处理并在各自预设的 `source` 字段注明出处。

| 文件 | 内容 | 在 dngscan 中的用途 |
|---|---|---|
| `kodak_portra_400.json` | 光谱感度 380–780nm + 特性曲线 logE −3..+4（Status M） | 曲线预设拟合目标（负片）+ 前馈观察者 |
| `fujifilm_xtra_400.json` | 同上（Superia X-TRA 400；三通道模型，第四感色层已并入） | 同上 |
| `kodak_portra_endura.json` | Portra 400 的配对相纸 | 端到端正像曲线合成 |
| `fujifilm_crystal_archive_typeii.json` | X-TRA 400 的配对相纸 | 端到端正像曲线合成 |

引入日期：2026-07-29。上游 commit 以克隆时 main 为准；重新同步时更新本行。
