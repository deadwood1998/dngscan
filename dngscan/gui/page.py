# SPDX-License-Identifier: GPL-3.0-or-later
"""Single-page HTML shell for the local dngscan web GUI."""
from __future__ import annotations

import json

PAGE = """<!doctype html>
<html lang="zh"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>dngscan</title>
<style>
:root{color-scheme:dark}
*{box-sizing:border-box}
body{margin:0;font:14px/1.5 -apple-system,"PingFang SC",system-ui,sans-serif;background:#15171c;color:#e7e9ee}
.wrap{max-width:1900px;margin:0 auto;padding:14px 18px}
h1{font-size:17px;font-weight:600;margin:0;white-space:nowrap}
.topBar{display:flex;flex-direction:column;gap:8px;align-items:stretch;margin:0 0 12px}
.topBar input[type=file]{width:100%;min-width:260px}
.topBar .ctlFact{min-width:260px}
.card{background:#1d2028;border:1px solid #2b2f3a;border-radius:8px;padding:14px;margin-bottom:12px}
.secTitle{font-size:12px;font-weight:600;color:#8fa0c4;text-transform:uppercase;letter-spacing:.06em;margin:0 0 12px}
.workspace{display:grid;grid-template-columns:minmax(360px,460px) minmax(0,1fr);gap:12px;align-items:start}
.controlPanel{min-width:0}
.previewCard{position:sticky;top:12px;height:calc(100vh - 24px);display:flex;flex-direction:column;margin-bottom:0}
.actions{display:flex;gap:10px;flex-wrap:wrap;align-items:center}
label{display:block;font-size:12px;color:#9aa1b0;margin:0 0 6px}
input[type=text],input[type=number],select{width:100%;background:#12141a;border:1px solid #2b2f3a;border-radius:8px;color:#e7e9ee;padding:8px 10px;font:inherit}
input[type=file]{width:100%;background:#12141a;border:1px solid #2b2f3a;border-radius:8px;color:#cdd2dd;padding:5px;font:inherit;cursor:pointer}
input[type=file]::file-selector-button{background:#2c3444;border:1px solid #46536b;border-radius:6px;color:#eef2ff;padding:7px 12px;margin-right:10px;font:inherit;font-weight:600;cursor:pointer}
input[type=file]:disabled{opacity:.5;cursor:default}
.row{display:flex;gap:12px;flex-wrap:wrap}
.row>div{flex:1;min-width:150px}
.row>.evMain{flex:1 1 100%;min-width:0}
.modes{display:flex;gap:8px;flex-wrap:wrap;margin-top:8px}
.modes button{flex:1;min-width:56px;overflow:hidden;background:#12141a;border:1px solid #2b2f3a;border-radius:8px;color:#cdd2dd;padding:8px 4px;cursor:pointer;font:inherit;display:flex;flex-direction:column;align-items:center;justify-content:center;gap:1px;line-height:1.25;min-height:48px}
.modes button .m{font-weight:600;color:#e7e9ee;font-size:13px;font-variant-numeric:tabular-nums;white-space:nowrap}
.modes button .d{font-size:10px;color:#828a99;white-space:nowrap;max-width:100%;overflow:hidden;text-overflow:ellipsis}
.modes button.sel{border-color:#5b8cff;background:#1a2233}
.modes button#evReferenceBtn{flex:1.8;min-width:100px}
.sliderField{flex:1;min-width:170px}
.labelRow{display:flex;justify-content:space-between;align-items:baseline;gap:8px;margin-bottom:6px}
.labelRow label{margin:0;flex:1;min-width:0;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.labelRow .val{font-size:13px;color:#e7e9ee;font-variant-numeric:tabular-nums;white-space:nowrap}
input[type=range]{display:block;width:100%;margin:6px 0 2px;accent-color:#5b8cff;height:18px}
button.go{background:#5b8cff;border:0;border-radius:9px;color:#fff;padding:11px 18px;font:inherit;font-weight:600;cursor:pointer}
button.go:disabled{opacity:.5;cursor:default}
button.ghost{background:#12141a;border:1px solid #2b2f3a;border-radius:8px;color:#cdd2dd;padding:8px 12px;cursor:pointer;font:inherit;white-space:nowrap}
.previewLive{margin-left:auto;border:1px solid #33415c;border-radius:999px;padding:4px 9px;color:#91b4ff;background:#151b27;font-size:12px;font-variant-numeric:tabular-nums;white-space:nowrap}
.previewLive.busy{color:#ffc46b;border-color:#614d2e}
.previewLive.err{color:#ff8a8a;border-color:#663939}
.muted{color:#828a99;font-size:12px}
.coreFacts{display:flex;gap:8px;flex-wrap:wrap;margin-top:12px}
.coreFacts span{background:#151922;border:1px solid #303746;border-radius:6px;padding:5px 8px;color:#9aa1b0;font-size:12px}
.coreFacts span.control{border-color:#4a5568;background:#171b24}
.coreFacts b{color:#e7e9ee;font-weight:500}
#controlHint{margin-top:10px;color:#9aa7c0;font-size:12px;line-height:1.55;min-height:0}
#controlHint:empty{display:none}
#status{margin-top:8px;white-space:pre-line}
#status:empty{display:none}
.err{color:#ff8a8a}.ok{color:#8ae08a}.warn{color:#ffc46b}
.browserList{display:none;margin-top:10px;border:1px solid #2b2f3a;border-radius:8px;max-height:260px;overflow:auto;background:#12141a}
.browserList div{padding:6px 10px;cursor:pointer;border-bottom:1px solid #20242e;font-size:13px}
.browserList div:hover{background:#1a2233}
.browserList div.pick{color:#8ae08a;font-weight:600;position:sticky;top:0;background:#12141a}
#previewWrap{position:relative;margin-top:10px;min-height:260px;flex:1;overflow:hidden;background:#11141a;border:1px solid #2b2f3a;border-radius:8px}
#preview{position:absolute;inset:0;width:100%;height:100%;object-fit:contain;display:none;transition:opacity .15s ease}
#previewWrap.loading #preview{opacity:.4}
#spinner{display:none;position:absolute;left:50%;top:50%;width:34px;height:34px;margin:-17px 0 0 -17px;border:3px solid rgba(255,255,255,.22);border-top-color:#eef2ff;border-radius:50%;animation:spin .8s linear infinite}
#previewWrap.loading #spinner{display:block}
@keyframes spin{to{transform:rotate(360deg)}}
.dim{opacity:.45;pointer-events:none}
#deliveryReport{margin-top:10px;border:1px solid #2b2f3a;border-radius:8px;padding:8px 12px;background:#11141a}
#deliveryReport summary{cursor:pointer;font-size:13px;color:#9aa3b2;user-select:none}
#deliveryReport[open] summary{margin-bottom:8px}
.reportGrid{display:grid;grid-template-columns:auto 1fr;gap:3px 14px;font-size:12.5px}
.reportGrid dt{color:#9aa3b2;white-space:nowrap}
.reportGrid dd{margin:0;color:#e6e9f0;font-variant-numeric:tabular-nums}
.reportGrid dd.warn{color:#f0b35e}
.ctlFact{margin-top:6px;color:#8fa0c4;font-size:11.5px;line-height:1.5;font-variant-numeric:tabular-nums;white-space:pre-line}
.ctlFact:empty{display:none}
.ctlFact.warn{color:#f0b35e}
.chk{display:flex;align-items:center;gap:8px}.chk input{width:auto}
.outdirRow{display:flex;gap:8px;align-items:stretch}
.outdirRow input{flex:1}
dialog.outputDialog{width:min(680px,calc(100vw - 32px));max-height:90vh;padding:0;border:1px solid #353b48;border-radius:12px;background:#1d2028;color:#e7e9ee;box-shadow:0 24px 80px rgba(0,0,0,.55);overflow:hidden}
dialog.outputDialog::backdrop{background:rgba(7,9,13,.72);backdrop-filter:blur(3px)}
.dialogPanel{max-height:90vh;padding:18px;overflow:auto}
.dialogHeader{display:flex;align-items:center;justify-content:space-between;gap:12px;margin-bottom:16px}
.dialogTitle{margin:0;font-size:17px;font-weight:600}
.dialogActions{display:flex;justify-content:flex-end;gap:10px;margin-top:18px;padding-top:14px;border-top:1px solid #2b2f3a}
@media (max-width:980px){
  .wrap{padding:14px}
  .workspace{display:block}
  .previewCard{position:static;height:auto;min-height:60vh}
  .modes button .d{display:none}
}
</style></head>
<body><div class="wrap">
<div class="topBar">
  <h1>dngscan · RAW 分析与转换</h1>
  <input type="file" id="filePicker" accept="RAW_ACCEPT" title="RAW 文件">
  <div class="ctlFact" id="fileFact" style="margin-top:0"></div>
  <input type="hidden" id="input">
</div>

<div class="workspace">
<div class="controlPanel">

<div class="card">
  <div class="secTitle">RAW 解码</div>
  <div class="row">
    <div style="flex:1;min-width:170px" id="decoderBlock">
      <label>解码器</label>
      <select id="decoder" title="scene-linear RGB 来源；CFA 统计始终由 LibRaw 读取。">
        <option value="libraw">LibRaw · 默认</option>
        <option value="coreimage">Apple RAW · 9 优先</option>
      </select>
    </div>
    <div style="flex:1;min-width:140px;display:none" id="coreimageVersionBlock">
      <label>CI 版本</label>
      <select id="coreimageVersion" title="auto 选择文件支持的最高版本；显式版本在不支持时会报错。">
        <option value="auto">自动</option>
        <option value="9">9</option>
        <option value="8">8</option>
        <option value="7">7</option>
      </select>
    </div>
    <div style="flex:1;min-width:170px">
      <label>解拜耳</label>
      <select id="demosaic" title="仅 LibRaw；RAW 9 使用 Apple 的 CoreML 解拜耳与降噪模型。">
        <option value="auto">自动 · DHT</option>
        <option value="dht">DHT</option>
        <option value="dcb">DCB</option>
        <option value="ahd">AHD</option>
        <option value="aahd">AAHD</option>
        <option value="vng">VNG</option>
        <option value="ppg">PPG</option>
      </select>
    </div>
    <div class="ctlFact" id="decodeTierFact" style="flex-basis:100%;margin-top:0"></div>
    <div class="ctlFact" id="decoderFact" style="flex-basis:100%;margin-top:0"></div>
    <div style="flex:1;min-width:170px">
      <label>白平衡</label>
      <select id="wb" title="拍摄值信相机测光；固定色温是声明的标准参考（经文件自身颜色标定求解），用于胶片模拟等需要整卷一致配平的场景，不是肉眼调整。">
        <option value="camera">拍摄值 · As Shot</option>
        <option value="6500k">6500K · D65 显示标准</option>
        <option value="5500k">5500K · 摄影日光/日光卷</option>
        <option value="3400k">3400K · Type A 钨丝卷</option>
        <option value="3200k">3200K · Type B 钨丝卷</option>
        <option value="9300k">9300K · 日本广播白点</option>
        <option value="daylight">相机日光标定 · 旧</option>
      </select>
    </div>
    <div style="flex:1;min-width:160px">
      <label>高光</label>
      <select id="highlight" title="LibRaw 的高光恢复方式；RAW 9 固定使用 Apple 重建。">
        <option value="clip">保持剪切 · 原始</option>
        <option value="blend">通道混合 · 温和</option>
        <option value="reconstruct">邻域重建 · 完整</option>
      </select>
      <div class="ctlFact" id="clipFact"></div>
    </div>
    <div style="flex:1;min-width:170px">
      <label>镜前滤镜</label>
      <select id="lensFilter" title="Wratten 转换滤镜，按柯达出版的 mired 位移推导；作用于前馈之前，可靠尾部与 HDR 预算都透过滤镜测量。">
        <option value="none">无</option>
        <option value="85b">85B · 日光转钨丝</option>
        <option value="85">85 · 日光转 Type A</option>
        <option value="80a">80A · 钨丝转日光</option>
        <option value="81a">81A · 轻度暖化</option>
        <option value="82a">82A · 轻度冷化</option>
      </select>
    </div>
    <div class="ctlFact" id="wbFact" style="flex-basis:100%;margin-top:0"></div>
  </div>
</div>

<div class="card">
  <div class="secTitle">曝光</div>
  <div class="row">
    <div class="evMain">
      <div class="labelRow"><label title="0 EV 保留拍摄时的亮度关系。">曝光 EV</label><span class="val" id="evval">+0.00</span></div>
      <input type="range" id="ev" min="-3" max="3" step="0.05" value="0">
      <div class="modes">
        <button type="button" data-ev="-0.50"><span class="m">-0.50</span></button>
        <button type="button" data-ev="0"><span class="m">0.00</span></button>
        <button type="button" data-ev="0.50"><span class="m">+0.50</span></button>
        <button type="button" data-ev="1.00"><span class="m">+1.00</span></button>
        <button type="button" id="evReferenceBtn" title="将可靠主体中位对齐 18% 灰，并限制高光溢出。"><span class="m">亮度参考</span></button>
      </div>
      <div class="ctlFact" id="evFact"></div>
    </div>
  </div>
</div>

<div class="card" id="toneAdjustCard">
  <div class="secTitle">明暗</div>
  <div class="row">
    <div class="sliderField">
      <div class="labelRow"><label title="只改变曲线内部亮度，不移动曝光、黑点或白点。">中间调亮度</label><span class="val" id="midtoneBrightnessVal">自动</span></div>
      <input type="range" id="midtoneBrightness" min="-1" max="1" step="0.05" value="0" title="向左压低主体，向右提亮主体。">
    </div>
    <div class="sliderField">
      <div class="labelRow"><label title="围绕固定校准 pivot 改变中间调斜率，不移动 pivot 位置。">中间调对比</label><span class="val" id="midtoneContrastVal">自动</span></div>
      <input type="range" id="midtoneContrast" min="-1" max="1" step="0.05" value="0" title="向左柔和，向右增强。">
    </div>
  </div>
  <div class="row" style="margin-top:12px">
    <div class="sliderField">
      <div class="labelRow"><label title="微调 toe 的形状，不移动黑点。">暗部过渡</label><span class="val" id="shadowTransitionVal">自动</span></div>
      <input type="range" id="shadowTransition" min="-1" max="1" step="0.05" value="0" title="向左更深，向右更开放。">
    </div>
    <div class="sliderField">
      <div class="labelRow"><label title="微调 shoulder 的形状，不移动白点。">高光过渡</label><span class="val" id="highlightTransitionVal">自动</span></div>
      <input type="range" id="highlightTransition" min="-1" max="1" step="0.05" value="0" title="向左更直接，向右更柔和。">
    </div>
  </div>
  <div class="ctlFact" id="toneFact"></div>
</div>

<div class="card">
  <div class="secTitle">成像</div>
  <div class="row">
    <div style="flex:1;min-width:190px">
      <label>胶片观察位置</label>
      <select id="film" title="一次设置多层独立声明：白平衡（日光卷 5500K / 钨丝电影卷 3200K）+ 光谱前馈 + 曲线预设 + 风格配对（前馈强度与 AgX 原色几何，编辑初稿可改）。胶片决定观察者看见了什么，AgX 决定怎么显影。选中后相关控件同步更新，随时可单独调整——没有任何一层被烘焙。">
        <option value="none">无 · 场景自适应</option>
FILM_OPTIONS
      </select>
    </div>
    <div style="flex:1;min-width:190px">
      <label>曲线预设</label>
      <select id="filmCurve" title="AgX 参数空间里的具名胶片坐标（数据手册特性曲线最小二乘解）；选中后整卷一致、场景自适应关闭。">
        <option value="none">场景自适应 · 默认</option>
FILM_CURVE_OPTIONS
      </select>
    </div>
  </div>
  <div class="row" style="margin-top:12px">
    <div style="flex:2;min-width:210px">
      <label>压缩核心</label>
      <select id="toneCore" title="选择亮度压缩与高光色彩路径。">
        <optgroup label="成片">
          <option value="agx" selected>AgX · 默认</option>
          <option value="gated">RAW 门控 · 保真</option>
        </optgroup>
        <optgroup label="非 AgX 对照">
          <option value="neutral">固定亮度曲线 · 诊断</option>
          <option value="lum">场景 C1 · 仅亮度</option>
        </optgroup>
      </select>
    </div>
    <div id="lumNormBlock" style="flex:1;min-width:140px;display:none">
      <label>亮度度量</label>
      <select id="lumNorm">
        <option value="y">Y · 场景亮度</option>
        <option value="power">折中 · 亮度与峰值</option>
        <option value="max">最大通道</option>
      </select>
    </div>
    <div id="agxPrimariesBlock" style="flex:1;min-width:150px">
      <label>AgX 色彩路径</label>
      <select id="agxPrimaries" title="控制饱和高光如何向白色收敛。">
        <option value="base" selected>darktable · 默认</option>
        <option value="smooth">平滑收色</option>
        <option value="punchy">纯度增强</option>
        <option value="muted">纯度柔和</option>
      </select>
    </div>
  </div>
  <div class="coreFacts" id="coreFacts" aria-live="polite"></div>
  <div id="controlHint"></div>
</div>

<div class="card">
  <div class="secTitle">颜色</div>
  <div class="row">
    <div id="punchBlock" class="sliderField">
      <div class="labelRow"><label>中频纯度</label><span class="val" id="punchVal">1.00</span></div>
      <input type="range" id="punch" min="0" max="1.5" step="0.05" value="1" title="1 使用场景分析值；0 关闭。">
    </div>
    <div id="highlightFadeBlock" class="sliderField">
      <div class="labelRow"><label title="只调整接近显示白的色度，不改变亮度 shoulder。">高光褪白</label><span class="val" id="highlightFadeVal">自动</span></div>
      <input type="range" id="highlightFade" min="-1" max="1" step="0.05" value="0" title="向左保留更多颜色，向右更早褪向白色。">
    </div>
  </div>
</div>

<div class="card">
  <div class="secTitle">前馈校正 · 实验</div>
  <div class="row">
    <div style="flex:2;min-width:210px">
      <label>前馈校正</label>
      <select id="sceneTransform" title="在 AgX 前校正相机的 scene-linear 色彩响应。">
SCENE_TRANSFORM_OPTIONS
      </select>
    </div>
    <div id="sceneTransformStrengthBlock" class="sliderField" style="display:none">
      <div class="labelRow"><label>前馈强度</label><span class="val" id="sceneTransformStrengthVal">1.00</span></div>
      <input type="range" id="sceneTransformStrength" min="0" max="3" step="0.05" value="1" title="1 为校准强度；更高数值用于比较。">
    </div>
  </div>
</div>

<div class="card">
  <div class="secTitle">风格 · LUT</div>
  <div class="row">
    <div style="flex:2;min-width:210px">
      <label>色彩风格</label>
      <select id="grade" title="曲线后的可选色彩处理。">
GRADE_OPTIONS
      </select>
    </div>
    <div id="gradeStrengthBlock" class="sliderField" style="display:none">
      <div class="labelRow"><label>风格强度</label><span class="val" id="gradeStrengthVal">1.00</span></div>
      <input type="range" id="gradeStrength" min="0" max="1.5" step="0.05" value="1">
    </div>
  </div>
</div>

</div>

<div class="card previewCard">
  <div class="actions">
    <button class="go" id="go">导出</button>
    <button class="ghost" id="revealBtn" style="display:none">在 Finder 显示</button>
    <span class="previewLive" id="previewLiveBadge">实时 · PREVIEW_LONG_EDGEpx</span>
  </div>
  <div id="status"></div>
  <details id="deliveryReport" style="display:none">
    <summary>投递报告 · 本次导出的实测真值</summary>
    <dl class="reportGrid" id="deliveryReportBody"></dl>
  </details>
  <div id="previewWrap"><img id="preview"><div id="spinner"></div></div>
</div>
</div>

<dialog class="outputDialog" id="outputDialog" aria-labelledby="outputDialogTitle">
  <div class="dialogPanel">
    <div class="dialogHeader">
      <h2 class="dialogTitle" id="outputDialogTitle">输出参数</h2>
    </div>
    <div class="row">
      <div style="flex:1;min-width:170px">
        <label>格式</label>
        <select id="format">
          <option value="sdr">SDR JPEG</option>
          <option value="ultrahdr">HDR gain-map · JPEG</option>
          <option value="ultrahdr-heic">HDR gain-map · HEIC</option>
        </select>
      </div>
      <div style="flex:1;min-width:160px">
        <label>交付档</label>
        <select id="deliveryProfile" title="只影响最后编码，不重算 AgX/HDR。archive=q100/4:4:4 验证级保真（全尺寸约 60MB）；share=q90/4:2:0 流媒体发布档（约 11–27MB，微信原图 25MB 限制内，HDR gain map 完整保留）。">
          <option value="archive">Archive · 保真</option>
          <option value="share">Share · 流媒体</option>
        </select>
      </div>
      <div style="flex:1;min-width:140px">
        <label>色域</label>
        <select id="gamut">
          <option value="srgb">sRGB · 兼容优先</option>
          <option value="p3">Display P3 · 宽色域</option>
        </select>
      </div>
      <div style="flex:0;min-width:110px">
        <label>质量</label>
        <input type="number" id="quality" min="1" max="100" value="100">
      </div>
      <div style="flex:1;min-width:140px">
        <label>色度采样</label>
        <select id="chroma" title="4:4:4 保留完整色度，4:2:0 文件更小。Ultrahdr 主图采样主要由 quality 决定。">
          <option value="444">4:4:4 · 完整</option>
          <option value="422">4:2:2</option>
          <option value="420">4:2:0 · 最小</option>
        </select>
      </div>
    </div>
    <div class="row" id="hdrBlock" style="margin-top:12px">
      <div style="min-width:220px">
        <div class="labelRow"><label>HDR 余量上限</label><span class="val" id="hdrHeadroomVal">+3.00 EV</span></div>
        <input type="range" id="hdrHeadroom" min="1" max="MAX_HDR_HEADROOM_ATTR" step="0.02" value="3">
        <div class="ctlFact" id="hdrSceneFact"></div>
        <div class="muted" id="hdrHint">实际余量由场景决定；只恢复漫反射白以上的真实亮度档数。</div>
      </div>
    </div>
    <div style="margin-top:12px">
      <label>文件夹</label>
      <div class="outdirRow">
        <input type="text" id="outdir" placeholder="默认保存到照片文件夹">
        <button class="ghost" id="outdirBtn" type="button">选择</button>
      </div>
      <div id="outdirBrowser" class="browserList"></div>
    </div>
    <div class="chk" style="margin-top:12px">
      <input type="checkbox" id="png"><label for="png" style="margin:0">附带分析图</label>
    </div>
    <div class="ctlFact" id="priorsFact"></div>
    <div class="dialogActions">
      <button class="ghost" id="outputCancel" type="button">取消</button>
      <button class="go" id="exportConfirm" type="button">导出</button>
    </div>
  </div>
</dialog>

<script>
const $=s=>document.querySelector(s);
const STORE_KEY="dngscan.settings.v9";
const V8_STORE_KEY="dngscan.settings.v8";
const V7_STORE_KEY="dngscan.settings.v7";
const V6_STORE_KEY="dngscan.settings.v6";
const V5_STORE_KEY="dngscan.settings.v5";
const LEGACY_STORE_KEY="dngscan.settings.v4";
const COREIMAGE_AVAILABLE=COREIMAGE_AVAILABLE_FLAG;
function setGradeStrengthLabel(){const v=+$("#gradeStrength").value;$("#gradeStrengthVal").textContent=v.toFixed(2);}
function updateGradeUi(){$("#gradeStrengthBlock").style.display=$("#grade").value!=="none"?"block":"none";}
function setPunchLabel(){const v=+$("#punch").value;$("#punchVal").textContent=v.toFixed(2);}
function fmtBias(v){return Math.abs(v)<0.001?"自动":(v>0?"+":"")+v.toFixed(2);}
function setAdjustmentLabels(){
  ["midtoneBrightness","midtoneContrast","shadowTransition","highlightTransition","highlightFade"].forEach(id=>{$("#"+id+"Val").textContent=fmtBias(+$("#"+id).value);});
}
function setSceneTransformStrengthLabel(){const v=+$("#sceneTransformStrength").value;$("#sceneTransformStrengthVal").textContent=v.toFixed(2);}
function updateSceneTransformUi(){$("#sceneTransformStrengthBlock").style.display=$("#sceneTransform").value!=="none"?"block":"none";}
const CORE_FACTS={
  gated:["亮度 <b>darktable C1</b>","色彩 <b>RAW 门控</b>"],
  agx:["亮度 <b>darktable C1</b>","色彩 <b>全图 AgX</b>"],
  lum:["曲线 <b>场景 C1</b>","色彩 <b>保持比例</b>"],
  neutral:["曲线 <b>固定</b>","色彩 <b>保持比例</b>"]
};
const CONTROL_HINTS={
  gated:"RAW 证据决定 AgX 色彩路径的混合量。",
  agx:"默认成片；全图使用 AgX 色彩路径。",
  neutral:"固定 Y 比例曲线，用来检查色调核与高饱和边界。",
  lum:"共用场景 C1，仅压缩亮度。"
};
function updateToneCoreUi(){
  const core=$("#toneCore").value;const lum=core==="lum";const neutral=core==="neutral";const control=neutral||lum;
  $("#lumNormBlock").style.display=lum?"block":"none";
  $("#agxPrimariesBlock").style.display=core==="agx"?"block":"none";
  $("#punchBlock").style.display=control?"none":"block";
  $("#toneAdjustCard").style.display=neutral?"none":"block";
  $("#highlightFadeBlock").style.display=neutral?"none":"block";
  const facts=(CORE_FACTS[core]||[]).map(v=>"<span"+(control?' class="control"':"")+">"+v+"</span>").join("");
  $("#coreFacts").innerHTML=facts;
  $("#controlHint").textContent=CONTROL_HINTS[core]||"";
}
function applyDeliveryConstraints(){
  // Archive pins q100/4:4:4 by contract; share leaves the knobs to the user. Restoring
  // saved settings must not clobber them, so this only enforces, never fills defaults.
  // HDR containers additionally pin chroma to what Core Image actually emits at the
  // profile's quality (q100→4:4:4, share→4:2:0): the control would otherwise promise a
  // subsampling the encoder cannot honour.
  const archive=$("#deliveryProfile").value==="archive";
  const hdr=["ultrahdr","ultrahdr-heic"].includes($("#format").value);
  if(archive){$("#quality").value="100";$("#chroma").value="444";}
  else if(hdr){$("#chroma").value="420";}
  $("#quality").disabled=archive;
  $("#chroma").disabled=archive||hdr;
}
function applyDeliveryDefaults(){
  // Only on an explicit profile switch: seed share's calibrated defaults.
  if($("#deliveryProfile").value==="share"){$("#quality").value="90";$("#chroma").value="420";}
  applyDeliveryConstraints();
}
function updateFormatUi(){
  const hdr=["ultrahdr","ultrahdr-heic"].includes($("#format").value);
  $("#hdrBlock").style.display=hdr?"flex":"none";
  if(hdr){$("#gamut").value="p3";$("#toneCore").value="agx";}
  $("#gamut").disabled=hdr;$("#toneCore").disabled=hdr;
  $("#highlightFade").disabled=hdr;
  $("#highlightFadeBlock").title=hdr?"HDR 色彩几何独立处理高光，不使用 SDR 显示侧褪白。":"";
  applyDeliveryConstraints();
  updateToneCoreUi();
}
async function checkHdrBackend(){
  const option=[...$("#format").options].find(o=>o.value==="ultrahdr");
  const optionHeic=[...$("#format").options].find(o=>o.value==="ultrahdr-heic");
  try{
    const response=await fetch("/hdr-status");const status=await response.json();
    if(status.available){option.disabled=false;optionHeic.disabled=false;return;}
    option.disabled=true;option.textContent="HDR JPEG · 当前不可用";
    optionHeic.disabled=true;optionHeic.textContent="HDR HEIC · 当前不可用";
    $("#hdrHint").textContent=status.reason||"HDR 后端未通过回读验证。";
    if(["ultrahdr","ultrahdr-heic"].includes($("#format").value)){
      $("#format").value="sdr";updateFormatUi();saveSettings();
      setStatus(status.reason||"HDR 后端未通过回读验证，已切回 SDR。","warn");
    }
  }catch(error){
    option.disabled=true;option.textContent="HDR JPEG · 探测失败";
    optionHeic.disabled=true;optionHeic.textContent="HDR HEIC · 探测失败";
  }
}
function setEvLabel(){const v=+$("#ev").value;$("#evval").textContent=(v>=0?"+":"")+v.toFixed(2);}
function setHdrLabel(){const v=+$("#hdrHeadroom").value;$("#hdrHeadroomVal").textContent="+"+v.toFixed(2)+" EV";}
function fmtPct(v){if(v===undefined||!isFinite(v))return "";if(v<=0)return "0%";if(v<0.005)return "<0.01%";if(v<1)return "~"+v.toFixed(2)+"%";return v.toFixed(1)+"%";}
function fmtEv(v){return (v>=0?"+":"")+v.toFixed(2);}
function metricText(j){
  if(!j.metrics)return "";
  const m=j.metrics;
  if(m.luma_p999_pct===undefined)return "";
  const room=m.safe_ev_remaining!==undefined?m.safe_ev_remaining:m.headroom_luma_ev;
  const label=j.metrics_kind==="full"?" · 全分辨率真值":" · 预览估计";
  const roomText=j.metrics_kind==="full"&&room!==undefined?" · 可再加约 "+fmtEv(room)+"EV":"";
  return label+
    " · p99.9亮度 "+fmtPct(m.luma_p999_pct)+
    " · 近白 "+fmtPct(m.near_white_pct)+
    " · 顶白 "+fmtPct(m.clipped_channel_pct)+
    roomText;
}
function fullFrameReferenceText(j){
  if(!j.ev_auto)return "";
  const a=j.ev_auto;
  let t=" · 全图亮度参考 "+fmtEv(a.ev_boost)+" EV";
  if(a.highlight_limited)t+="（高光限制，参考目标 "+fmtEv(a.ev_median_target)+"）";
  return t;
}
function sceneTransformText(j){
  if(!j.scene_transform||j.scene_transform==="无")return "";
  const s=j.scene_transform_strength!==undefined?" "+(+j.scene_transform_strength).toFixed(2):"";
  return "，相机校正 "+j.scene_transform+s;
}
function toneCoreText(j){
  if(!j.tone_core)return "";
  const labels={gated:"实验·RAW 门控",agx:"成片·AgX 全图",lum:"对照·场景 C1 仅亮度",neutral:"诊断·固定 Y 比例曲线"};
  const norms={y:"Y",power:"折中",max:"最大通道"};
  const norm=j.tone_core==="lum"&&j.lum_norm?"（"+(norms[j.lum_norm]||j.lum_norm)+"）":"";
  return "，策略 "+(labels[j.tone_core]||j.tone_core)+norm;
}
function highlightText(v){return ({clip:"保持剪切",blend:"高光混合",reconstruct:"高光重建"})[v]||v;}
function gamutText(v){return ({srgb:"sRGB",p3:"Display P3"})[v]||v;}
function formatText(v){return ({sdr:"SDR JPEG",ultrahdr:"HDR gain-map JPEG","ultrahdr-heic":"HDR gain-map HEIC"})[v]||v;}
function decoderText(j){
  if(j.decoder!=="coreimage")return "";
  const version=(j.decoder_version||"").replace(/\\.dng$/i,"");
  return "，解码 Apple RAW"+(version?" "+version:"");
}
function applyJobEv(j){
  if(j.ev!==undefined){$("#ev").value=j.ev;setEvLabel();saveSettings();}
}
function updateDecoderUi(){
  const block=$("#decoderBlock");
  const ver=$("#coreimageVersionBlock");
  const highlight=$("#highlight");
  const demosaic=$("#demosaic");
  if(!COREIMAGE_AVAILABLE){
    $("#decoder").value="libraw";
    block.classList.add("dim");
    $("#decoder").disabled=true;
    ver.style.display="none";
    highlight.disabled=false;
    demosaic.disabled=false;
    return;
  }
  block.classList.remove("dim");
  $("#decoder").disabled=false;
  const raw9=$("#decoder").value==="coreimage";
  ver.style.display=raw9?"block":"none";
  if(raw9){
    if(!highlight.disabled)highlight.dataset.librawValue=highlight.value;
    if(!demosaic.disabled)demosaic.dataset.librawValue=demosaic.value;
    highlight.value="reconstruct";
    demosaic.value="auto";
    highlight.disabled=true;
    demosaic.disabled=true;
  }else{
    highlight.disabled=false;
    demosaic.disabled=false;
    if(highlight.dataset.librawValue){highlight.value=highlight.dataset.librawValue;delete highlight.dataset.librawValue;}
    if(demosaic.dataset.librawValue){demosaic.value=demosaic.dataset.librawValue;delete demosaic.dataset.librawValue;}
  }
  // RAW 9 accepts fixed-Kelvin declarations natively; only the LibRaw-metadata
  // "daylight" anchor has no validated mapping there.
  if(raw9 && $("#wb").value==="daylight"){
    $("#wb").value="camera";
  }
}
function saveSettings(){
  try{localStorage.setItem(STORE_KEY,JSON.stringify({
    ev:$("#ev").value,quality:$("#quality").value,
    lensFilter:$("#lensFilter").value,filmCurve:$("#filmCurve").value,film:$("#film").value,
    highlight:$("#highlight").dataset.librawValue||$("#highlight").value,gamut:$("#gamut").value,wb:$("#wb").value,demosaic:$("#demosaic").dataset.librawValue||$("#demosaic").value,
    decoder:$("#decoder").value,coreimageVersion:$("#coreimageVersion").value,
    lensFilter:$("#lensFilter").value,filmCurve:$("#filmCurve").value,
    chroma:$("#chroma").value,format:$("#format").value,
    deliveryProfile:$("#deliveryProfile").value,
    toneCore:$("#toneCore").value,lumNorm:$("#lumNorm").value,agxPrimaries:$("#agxPrimaries").value,
    grade:$("#grade").value,gradeStrength:$("#gradeStrength").value,
    sceneTransform:$("#sceneTransform").value,sceneTransformStrength:$("#sceneTransformStrength").value,punch:$("#punch").value,
    midtoneBrightness:$("#midtoneBrightness").value,midtoneContrast:$("#midtoneContrast").value,
    shadowTransition:$("#shadowTransition").value,highlightTransition:$("#highlightTransition").value,highlightFade:$("#highlightFade").value,
    hdrHeadroom:$("#hdrHeadroom").value,outdir:$("#outdir").value,png:$("#png").checked
  }));}catch(e){}
}
function restoreSettings(){
  let s={};let migrated=false;
  try{
    const current=localStorage.getItem(STORE_KEY);
    const v8=localStorage.getItem(V8_STORE_KEY);
    const v7=localStorage.getItem(V7_STORE_KEY);
    const v6=localStorage.getItem(V6_STORE_KEY);
    const v5=localStorage.getItem(V5_STORE_KEY);
    s=JSON.parse(current||v8||v7||v6||v5||localStorage.getItem(LEGACY_STORE_KEY)||"{}")||{};
    // v7 and earlier labelled smooth as the default. The pinned darktable scene
    // default is base, so move stored old defaults to the corrected baseline.
    if(!current&&s.agxPrimaries==="smooth"){
      s.agxPrimaries="base";migrated=true;
    }
    if(!current&&!v7&&!v6&&!v5&&s.toneCore==="gated"&&s.agxPrimaries==="base"){
      s.toneCore="agx";migrated=true;
    }
  }catch(e){}
  if(s.ev!==undefined)$("#ev").value=s.ev;
  if(s.quality)$("#quality").value=s.quality;
  if(s.highlight)$("#highlight").value=s.highlight;
  if(s.gamut)$("#gamut").value=s.gamut;
  if(s.wb)$("#wb").value=s.wb;
  if(s.demosaic)$("#demosaic").value=s.demosaic;
  if(s.decoder&&[...$("#decoder").options].some(o=>o.value===s.decoder))$("#decoder").value=s.decoder;
  if(s.coreimageVersion&&[...$("#coreimageVersion").options].some(o=>o.value===s.coreimageVersion))$("#coreimageVersion").value=s.coreimageVersion;
  if(s.chroma)$("#chroma").value=s.chroma;
  if(s.lensFilter&&[...$("#lensFilter").options].some(o=>o.value===s.lensFilter))$("#lensFilter").value=s.lensFilter;
  if(s.filmCurve&&[...$("#filmCurve").options].some(o=>o.value===s.filmCurve))$("#filmCurve").value=s.filmCurve;
  if(s.film&&[...$("#film").options].some(o=>o.value===s.film))$("#film").value=s.film;
  if(s.deliveryProfile&&[...$("#deliveryProfile").options].some(o=>o.value===s.deliveryProfile))$("#deliveryProfile").value=s.deliveryProfile;
  if(s.toneCore&&[...$("#toneCore").options].some(o=>o.value===s.toneCore))$("#toneCore").value=s.toneCore;
  if(s.lumNorm&&[...$("#lumNorm").options].some(o=>o.value===s.lumNorm))$("#lumNorm").value=s.lumNorm;
  if(s.agxPrimaries&&[...$("#agxPrimaries").options].some(o=>o.value===s.agxPrimaries))$("#agxPrimaries").value=s.agxPrimaries;
  if(s.grade&&[...$("#grade").options].some(o=>o.value===s.grade))$("#grade").value=s.grade;
  else if(s.filter&&s.filter!=="none"){
    const fid="filter:"+s.filter;
    if([...$("#grade").options].some(o=>o.value===fid))$("#grade").value=fid;
    else if([...$("#grade").options].some(o=>o.value===s.filter))$("#grade").value=s.filter;
  }
  else if(s.look&&s.look!=="none"){
    const lid="look:"+s.look;
    if([...$("#grade").options].some(o=>o.value===lid))$("#grade").value=lid;
    else if([...$("#grade").options].some(o=>o.value===s.look))$("#grade").value=s.look;
  }
  if(s.gradeStrength!==undefined)$("#gradeStrength").value=s.gradeStrength;
  else if(s.filterStrength!==undefined&&s.filter&&s.filter!=="none")$("#gradeStrength").value=s.filterStrength;
  else if(s.lookStrength!==undefined)$("#gradeStrength").value=s.lookStrength;
  if(s.sceneTransform&&[...$("#sceneTransform").options].some(o=>o.value===s.sceneTransform))$("#sceneTransform").value=s.sceneTransform;
  if(s.sceneTransformStrength!==undefined)$("#sceneTransformStrength").value=s.sceneTransformStrength;
  if(s.punch!==undefined)$("#punch").value=s.punch;
  ["midtoneBrightness","midtoneContrast","shadowTransition","highlightTransition","highlightFade"].forEach(id=>{if(s[id]!==undefined)$("#"+id).value=s[id];});
  if(s.format)$("#format").value=s.format;
  if(s.hdrHeadroom!==undefined)$("#hdrHeadroom").value=s.hdrHeadroom;
  if(s.outdir)$("#outdir").value=s.outdir;
  if(s.png!==undefined)$("#png").checked=!!s.png;
  setEvLabel();setHdrLabel();setGradeStrengthLabel();setSceneTransformStrengthLabel();setPunchLabel();setAdjustmentLabels();updateGradeUi();updateSceneTransformUi();updateToneCoreUi();updateFormatUi();updateDecoderUi();
  if(migrated)saveSettings();
}
["quality","outdir","png"].forEach(id=>$("#"+id).addEventListener("change",saveSettings));
$("#gamut").addEventListener("change",()=>{saveSettings();scheduleLivePreview();});
$("#highlight").addEventListener("change",()=>{saveSettings();preparePreview();});
$("#demosaic").addEventListener("change",()=>{saveSettings();preparePreview();});
$("#chroma").addEventListener("change",saveSettings);
$("#grade").addEventListener("change",()=>{updateGradeUi();saveSettings();scheduleLivePreview();});
$("#deliveryProfile").addEventListener("change",()=>{applyDeliveryDefaults();saveSettings();});
$("#decoder").addEventListener("change",()=>{updateDecoderUi();saveSettings();preparePreview();});
$("#coreimageVersion").addEventListener("change",()=>{RAW9_APPROVALS.delete($("#input").value.trim());saveSettings();preparePreview();});
$("#wb").addEventListener("change",()=>{updateDecoderUi();updateGradeUi();saveSettings();preparePreview();});
const FILM_COMBOS=FILM_COMBOS_JSON;
$("#film").addEventListener("change",()=>{
  const combo=FILM_COMBOS[$("#film").value];
  if(combo){
    $("#wb").value=combo.wb;
    if([...$("#sceneTransform").options].some(o=>o.value===combo.st))$("#sceneTransform").value=combo.st;
    $("#filmCurve").value=combo.fc;
    if(combo.sts!==undefined){$("#sceneTransformStrength").value=combo.sts;setSceneTransformStrengthLabel();}
    if(combo.pr&&[...$("#agxPrimaries").options].some(o=>o.value===combo.pr))$("#agxPrimaries").value=combo.pr;
  }else{
    $("#wb").value="camera";$("#sceneTransform").value="none";$("#filmCurve").value="none";
    $("#sceneTransformStrength").value=1;setSceneTransformStrengthLabel();
    $("#agxPrimaries").value="base";
  }
  updateDecoderUi();updateSceneTransformUi();saveSettings();preparePreview();
});
$("#lensFilter").addEventListener("change",()=>{saveSettings();scheduleLivePreview();});
$("#filmCurve").addEventListener("change",()=>{saveSettings();scheduleLivePreview();});
$("#toneCore").addEventListener("change",()=>{updateToneCoreUi();saveSettings();preparePreview();});
$("#lumNorm").addEventListener("change",()=>{saveSettings();scheduleLivePreview();});
$("#agxPrimaries").addEventListener("change",()=>{saveSettings();scheduleLivePreview();});
$("#sceneTransform").addEventListener("change",()=>{updateSceneTransformUi();saveSettings();scheduleLivePreview();});
$("#format").addEventListener("change",()=>{updateFormatUi();saveSettings();scheduleLivePreview();});
$("#ev").oninput=()=>{setEvLabel();saveSettings();scheduleLivePreview();};
$("#hdrHeadroom").oninput=()=>{setHdrLabel();saveSettings();};
$("#gradeStrength").oninput=()=>{setGradeStrengthLabel();saveSettings();scheduleLivePreview();};
$("#punch").oninput=()=>{setPunchLabel();saveSettings();scheduleLivePreview();};
[
  "midtoneBrightness","midtoneContrast","shadowTransition","highlightTransition","highlightFade"
].forEach(id=>$("#"+id).oninput=()=>{setAdjustmentLabels();saveSettings();scheduleLivePreview();});
$("#sceneTransformStrength").oninput=()=>{setSceneTransformStrengthLabel();saveSettings();scheduleLivePreview();};
restoreSettings();
checkHdrBackend();
document.querySelectorAll("button[data-ev]").forEach(b=>b.onclick=()=>{$("#ev").value=b.dataset.ev;setEvLabel();saveSettings();scheduleLivePreview();});
let lastSavedPath="";

let curDir=INIT_DIR;
$("#filePicker").addEventListener("change",async()=>{
  const picker=$("#filePicker");const file=picker.files&&picker.files[0];
  if(!file)return;
  // A newly selected source invalidates every in-flight response immediately,
  // including one that might finish while the upload is still in progress.
  beginPreviewSession();
  picker.disabled=true;$("#input").value="";lastSavedPath="";$("#revealBtn").style.display="none";
  setStatus("正在读取 "+file.name+"…","");
  try{
    const response=await fetch("/upload?name="+encodeURIComponent(file.name),{
      method:"POST",headers:{"Content-Type":"application/octet-stream"},body:file
    });
    const result=await response.json();
    if(!result.ok){picker.value="";setStatus("文件选择失败："+result.error,"err");return;}
    $("#input").value=result.path;
    RAW9_PROBES.clear();RAW9_PROBE_REQUESTS.clear();RAW9_APPROVALS.clear();
    if(!$("#outdir").value.trim())$("#outdir").value=INIT_DIR;
    saveSettings();
    setStatus("已选择："+file.name,"ok");
    fetchDecodeSupport(result.path);
    await preparePreview();
  }catch(error){
    picker.value="";
    setStatus("文件选择失败："+error,"err");
  }finally{
    picker.disabled=false;
  }
});

async function listOutDir(d){
  const r=await fetch("/list?dir="+encodeURIComponent(d));const j=await r.json();
  const b=$("#outdirBrowser");b.innerHTML="";
  const mk=(t,fn,cls)=>{const e=document.createElement("div");e.textContent=t;e.onclick=fn;if(cls)e.className=cls;b.appendChild(e);};
  mk("✓ 就用这里："+j.cwd,()=>{$("#outdir").value=j.cwd;b.style.display="none";saveSettings();},"pick");
  mk("↺ 使用默认目录："+INIT_DIR,()=>{$("#outdir").value=INIT_DIR;b.style.display="none";saveSettings();});
  mk("⬆︎ "+j.parent,()=>listOutDir(j.parent));
  j.dirs.forEach(d2=>mk("📁 "+d2,()=>listOutDir(j.cwd+"/"+d2)));
}
$("#outdirBtn").onclick=()=>{
  const b=$("#outdirBrowser");
  if(b.style.display==="block"){b.style.display="none";return;}
  b.style.display="block";
  const seed=$("#outdir").value.trim()
    ||curDir;
  listOutDir(seed);
};

function payload(){
  const input=$("#input").value.trim();
  if(!input){setStatus("请先选择一个 DNG/RAW 文件","err");return null;}
  return {
    input,highlight:$("#highlight").value,gamut:$("#gamut").value,wb:$("#wb").value,demosaic:$("#demosaic").value,
    decoder:$("#decoder").value,coreimageVersion:$("#coreimageVersion").value,
    lensFilter:$("#lensFilter").value,filmCurve:$("#filmCurve").value,
    chroma:$("#chroma").value,format:$("#format").value,
    deliveryProfile:$("#deliveryProfile").value,
    toneCore:$("#toneCore").value,lumNorm:$("#lumNorm").value,agxPrimaries:$("#agxPrimaries").value,
    grade:$("#grade").value,gradeStrength:+$("#gradeStrength").value,
    sceneTransform:$("#sceneTransform").value,sceneTransformStrength:+$("#sceneTransformStrength").value,
    punch:+$("#punch").value,
    midtoneBrightness:+$("#midtoneBrightness").value,midtoneContrast:+$("#midtoneContrast").value,
    shadowTransition:+$("#shadowTransition").value,highlightTransition:+$("#highlightTransition").value,
    highlightFade:["ultrahdr","ultrahdr-heic"].includes($("#format").value)?0:+$("#highlightFade").value,
    hdrHeadroom:+$("#hdrHeadroom").value,ev:+$("#ev").value,quality:+$("#quality").value,
    outdir:$("#outdir").value.trim(),png:$("#png").checked
  };
}

async function postJob(path, body, signal){
  const r=await fetch(path,{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify(body),signal});
  return await r.json();
}
const RAW9_PROBES=new Map();
const RAW9_PROBE_REQUESTS=new Map();
const RAW9_APPROVALS=new Map();
function raw9Probe(input){
  const cached=RAW9_PROBES.get(input);
  if(cached)return Promise.resolve(cached);
  let pending=RAW9_PROBE_REQUESTS.get(input);
  if(!pending){
    pending=postJob("/raw9-support",{input}).then(j=>{
      if(j.ok)RAW9_PROBES.set(input,j);
      return j;
    }).finally(()=>{
      if(RAW9_PROBE_REQUESTS.get(input)===pending)RAW9_PROBE_REQUESTS.delete(input);
    });
    RAW9_PROBE_REQUESTS.set(input,pending);
  }
  return pending;
}
async function ensureRaw9Support(body){
  if(body.decoder!=="coreimage")return true;
  const key=body.input;
  const j=await raw9Probe(key);
  if(!j.ok){setStatus("RAW 9 探测失败："+(j.error||"未知错误"),"err");return false;}
  const switchToLibRaw=(message)=>{
    window.alert(message+"\\n\\n将改用 LibRaw。");
    $("#decoder").value="libraw";updateDecoderUi();saveSettings();
    body.decoder="libraw";body.coreimageVersion="auto";
    setStatus(message+" 已改用 LibRaw。","warn");
  };
  if(!j.coreimage_available||j.probe_error){switchToLibRaw(j.message);return true;}
  const offered=(j.versions_offered||[]).map(v=>String(v).replace(/\\.dng$/i,""));
  if(body.coreimageVersion!=="auto"){
    if(!offered.includes(String(body.coreimageVersion))){
      setStatus(j.message+" 当前指定的 RAW "+body.coreimageVersion+" 也不可用。","err");
      return false;
    }
    if(body.coreimageVersion!=="9"){
      setStatus(j.message+" 当前明确使用 RAW "+body.coreimageVersion+"。","warn");
    }
    return true;
  }
  if(j.raw9_supported)return true;
  if(!j.fallback_version){switchToLibRaw(j.message);return true;}
  const approved=RAW9_APPROVALS.get(key);
  if(approved===j.fallback_version){body.coreimageVersion=approved;return true;}
  const useFallback=window.confirm(
    j.message+"\\n\\n确定：继续使用 Apple RAW "+j.fallback_version+"\\n取消：改用 LibRaw"
  );
  if(useFallback){
    RAW9_APPROVALS.set(key,j.fallback_version);
    body.coreimageVersion=j.fallback_version;
    setStatus("此文件将使用 Apple RAW "+j.fallback_version+"。","warn");
  }else{
    $("#decoder").value="libraw";updateDecoderUi();saveSettings();
    body.decoder="libraw";body.coreimageVersion="auto";
    setStatus("此文件不支持 RAW 9，已改用 LibRaw。","warn");
  }
  return true;
}
let DETECTED_READY=false;
function setFact(sel,text,isWarn){const el=$(sel);el.textContent=text||"";el.classList.toggle("warn",!!isWarn);}
function renderDetectedParams(d){
  // Measured scene facts land NEXT TO the control they inform, so the number
  // is in view while the hand is on the slider — never a scroll away.
  DETECTED_READY=!!(d&&typeof d==="object");
  if(!DETECTED_READY){
    ["#decoderFact","#wbFact","#clipFact","#evFact","#toneFact","#hdrSceneFact"].forEach(s=>setFact(s,""));
    return;
  }
  const ev=v=>(v>=0?"+":"")+(+v).toFixed(2)+" EV";
  setFact("#decoderFact",d.data_support?"⚠ 机型数据："+d.data_support:"",true);
  setFact("#wbFact",d.wb_degradation?"⚠ 白平衡："+d.wb_degradation:"",true);
  setFact("#clipFact",d.raw_clip_union_pct!==null?"实测 RAW 剪切 "+(+d.raw_clip_union_pct).toFixed(2)+"%（≥1 通道）":"");
  const evBits=[];
  if(d.body_median_ev!==null)evBits.push("实测主体中位 "+ev(d.body_median_ev));
  if(d.sparse_emitter)evBits.push("稀疏光源 · 夜景/舞台策略");
  setFact("#evFact",evBits.join(" · "));
  const toneBits=[];
  if(d.black_ev!==null&&d.white_ev!==null)toneBits.push("编译曲线 "+ev(d.black_ev)+" .. "+ev(d.white_ev));
  if(d.contrast!==null)toneBits.push("对比 "+(+d.contrast).toFixed(2));
  setFact("#toneFact",toneBits.join(" · "));
  if(d.reliable_tail_ev!==null){
    const bits=["实测可靠尾部 "+ev(d.reliable_tail_ev)+"（p99.99）"];
    if(d.hdr_earned_ev!==null)bits.push("场景可挣余量 +"+(+d.hdr_earned_ev).toFixed(2)+" EV");
    setFact("#hdrSceneFact",bits.join(" · "));
  }else{
    setFact("#hdrSceneFact","⚠ 可靠尾部不可用 · HDR 余量将为 0",true);
  }
}
// Each probe line lands next to the control it informs: file identity and the
// Evidence tier by the picker, the two decoder tiers by the decoder select,
// the sensor-priors state by the analysis-plates toggle. Unrecognized lines
// (e.g. probe failures) fall through to the decoder slot as warnings.
const SUPPORT_ROUTE=[["机型：","#fileFact"],["Evidence（LibRaw）：","#fileFact"],
  ["LibRaw 场景解码：","#decodeTierFact"],["Apple RAW：","#decodeTierFact"],
  ["传感器先验：","#priorsFact"]];
function renderDecodeSupport(lines){
  const buckets=new Map();
  for(const line of lines||[]){
    const hit=SUPPORT_ROUTE.find(([p])=>line.startsWith(p));
    const sel=hit?hit[1]:"#decodeTierFact";
    if(!buckets.has(sel))buckets.set(sel,[]);
    buckets.get(sel).push(line);
  }
  for(const [prefix,sel] of SUPPORT_ROUTE)if(!buckets.has(sel))buckets.set(sel,[]);
  for(const [sel,rows] of buckets){
    const joined=sel==="#fileFact"?rows.join(" · "):rows.join("\\n");
    setFact(sel,joined,/[✗⚠]|失败/.test(joined));
  }
}
async function fetchDecodeSupport(input){
  // File capability is stable for the selected source. Render it once when the
  // file changes; preview reconfiguration must not replay this success notice.
  try{
    const j=await raw9Probe(input);
    if($("#input").value.trim()===input&&j&&j.support_lines)renderDecodeSupport(j.support_lines);
  }catch(_){/* probe display is best-effort */}
}
const PREVIEW_CLIENT_ID=(globalThis.crypto&&crypto.randomUUID)?crypto.randomUUID():(Date.now()+"-"+Math.random());
let PREVIEW_SESSION_SERIAL=0;
let PREVIEW_SESSION_ID=PREVIEW_CLIENT_ID+":0";
let PREVIEW_GENERATION=0;
let PREVIEW_READY=false;
let previewRaf=0;
let previewAbort=null;
let prepareAbort=null;
function setPreviewBadge(text,state){
  const badge=$("#previewLiveBadge");
  badge.textContent=text;
  badge.className="previewLive"+(state?" "+state:"");
}
function beginPreviewSession(){
  PREVIEW_SESSION_SERIAL+=1;
  PREVIEW_SESSION_ID=PREVIEW_CLIENT_ID+":"+PREVIEW_SESSION_SERIAL;
  PREVIEW_GENERATION=0;PREVIEW_READY=false;
  if(previewRaf){cancelAnimationFrame(previewRaf);previewRaf=0;}
  if(previewAbort){previewAbort.abort();previewAbort=null;}
  if(prepareAbort){prepareAbort.abort();prepareAbort=null;}
  return PREVIEW_SESSION_ID;
}
function scheduleLivePreview(){
  if(!PREVIEW_READY||!$("#input").value.trim())return;
  if(previewRaf)return;
  previewRaf=requestAnimationFrame(()=>{previewRaf=0;requestPreview();});
}
async function requestPreview({includeMetrics=false,busy=false,evAuto=false,prefix="预览"}={}){
  if(!PREVIEW_READY)return false;
  const body=payload();if(!body)return false;
  const generation=++PREVIEW_GENERATION;
  body.previewSession=PREVIEW_SESSION_ID;
  body.generation=generation;
  body.includeMetrics=!!includeMetrics;
  if(evAuto)body.evAuto=true;
  if(previewAbort)previewAbort.abort();
  const controller=new AbortController();previewAbort=controller;
  if(busy)beginBusy();
  setPreviewBadge(evAuto?"亮度参考计算中 · PREVIEW_LONG_EDGEpx":"处理中 · PREVIEW_LONG_EDGEpx","busy");
  try{
    const j=await postJob("/preview",body,controller.signal);
    if(controller.signal.aborted||generation!==PREVIEW_GENERATION||j.superseded)return false;
    if(!handleJobResult(j,prefix)){
      setStatus("错误："+(j.error||"预览失败"),"err");setPreviewBadge("实时预览错误 · PREVIEW_LONG_EDGEpx","err");return false;
    }
    setPreviewBadge("实时 · PREVIEW_LONG_EDGEpx","");
    return true;
  }catch(e){
    if(e&&e.name==="AbortError")return false;
    if(generation===PREVIEW_GENERATION){setStatus("请求失败："+e,"err");setPreviewBadge("实时预览错误 · PREVIEW_LONG_EDGEpx","err");}
    return false;
  }finally{
    if(generation===PREVIEW_GENERATION){
      if(previewAbort===controller)previewAbort=null;
      if(busy)endBusy();
    }
  }
}
async function preparePreview(){
  const body=payload();if(!body)return;
  const session=beginPreviewSession();
  body.previewSession=session;
  setPreviewBadge("准备实时预览 · PREVIEW_LONG_EDGEpx","busy");
  try{if(!await ensureRaw9Support(body)){setPreviewBadge("实时预览未就绪 · PREVIEW_LONG_EDGEpx","err");return;}}catch(e){setStatus("RAW 9 探测失败："+e,"err");setPreviewBadge("实时预览错误 · PREVIEW_LONG_EDGEpx","err");return;}
  const controller=new AbortController();prepareAbort=controller;
  try{
    const j=await postJob("/prepare",body,controller.signal);
    if(controller.signal.aborted||session!==PREVIEW_SESSION_ID)return;
    if(j&&j.ok){
      renderDetectedParams(j.detected);PREVIEW_READY=true;setPreviewBadge("实时 · PREVIEW_LONG_EDGEpx","");
      await requestPreview();
    }else if(j&&j.error){setStatus(j.error,"err");renderDetectedParams(null);setPreviewBadge("实时预览错误 · PREVIEW_LONG_EDGEpx","err");}
  }catch(e){
    if(!(e&&e.name==="AbortError")&&session===PREVIEW_SESSION_ID){setStatus("预览准备失败："+e,"err");setPreviewBadge("实时预览错误 · PREVIEW_LONG_EDGEpx","err");}
  }finally{
    if(prepareAbort===controller)prepareAbort=null;
  }
}
function beginBusy(){const w=$("#previewWrap");w.classList.add("loading");}
function endBusy(){const w=$("#previewWrap");w.classList.remove("loading");}
function setPreviewImage(b64, ondone){
  const img=$("#preview");
  img.onload=()=>{img.style.display="block";endBusy();if(ondone)ondone();};
  img.onerror=()=>{endBusy();};
  img.src="data:image/jpeg;base64,"+b64;
}

function fmtMB(bytes){return bytes>=1048576?(bytes/1048576).toFixed(2)+" MB":Math.round(bytes/1024)+" KB";}
function renderDeliveryReport(j){
  const box=$("#deliveryReport");const body=$("#deliveryReportBody");
  const c=j.hdr_container;
  if(!c||typeof c!=="object"){box.style.display="none";body.innerHTML="";return;}
  const rows=[];
  const add=(k,v,warn)=>{if(v!==undefined&&v!==null&&v!=="")rows.push("<dt>"+k+"</dt><dd"+(warn?' class="warn"':"")+">"+v+"</dd>");};
  add("交付",(c.delivery_profile||"")+" · "+(c.delivery_container==="heic"?"HEIC":"JPEG")+" · q"+c.delivery_quality);
  if(c.file_size_bytes!==undefined)add("文件大小",fmtMB(c.file_size_bytes));
  add("主图采样",c.chroma_subsampling,c.chroma_subsampling!=="4:4:4"&&c.delivery_profile==="archive");
  if(c.rendered_headroom_ev!==undefined){
    add("HDR 余量","场景挣得 +"+(+c.rendered_headroom_ev).toFixed(2)+" EV · 实际使用 +"+(+c.actual_headroom_ev).toFixed(2)+" EV · 容量 +"+(+c.display_headroom_ev).toFixed(2)+" EV");
  }
  if(c.shoulder_segments!==undefined){
    const shape=c.shoulder_segments<=1?"单段":"细分（"+c.shoulder_segments+" 段）";
    add("HDR shoulder",shape+(c.shoulder_alpha!==undefined?" · alpha "+(+c.shoulder_alpha).toFixed(3):""));
  }
  if(c.block_p99_relative_error!==undefined){
    add("HDR 回读误差","块级 p99 "+(+c.block_p99_relative_error*100).toFixed(2)+"% · 块级色品 "+(+c.block_chroma_error*100).toFixed(2)+"% · 像素色品 "+(+c.chroma_error*100).toFixed(2)+"%");
  }
  if(c.base_mean_code_error!==undefined){
    add("SDR 底图误差","平均 "+(+c.base_mean_code_error).toFixed(2)+" 码值 · 8×8 p99 "+(+c.base_block_p99_code_error).toFixed(2)+" 码值");
  }
  if(c.headroom_error_ev!==undefined)add("声明余量误差",(+c.headroom_error_ev).toFixed(4)+" EV");
  if(c.channel_separation!==undefined)add("色度自由度 rho",(+c.channel_separation).toFixed(3));
  if(c.hdr_plan)add("HDR plan",c.hdr_plan);
  body.innerHTML=rows.join("");
  box.style.display=rows.length?"block":"none";
}
function handleJobResult(j, prefix){
  if(!j.ok)return false;
  applyJobEv(j);
  renderDeliveryReport(j);
  // Scene facts live in the detection card and export truth in the delivery report;
  // a successful preview needs no small print. Auto-EV keeps its one-line feedback.
  setStatus(j.ev_auto?prefix+"：EV "+fmtEv(j.ev)+fullFrameReferenceText(j):"","ok");
  setPreviewImage(j.preview);
  return true;
}

$("#evReferenceBtn").onclick=async()=>{
  if(!payload())return;
  if(!PREVIEW_READY)await preparePreview();
  if(!PREVIEW_READY)return;
  $("#evReferenceBtn").disabled=true;$("#revealBtn").style.display="none";setStatus("正在计算亮度参考…","");
  try{await requestPreview({includeMetrics:true,busy:true,evAuto:true,prefix:"全图亮度参考预览"});}
  finally{$("#evReferenceBtn").disabled=false;}
};

function openOutputDialog(){
  const dialog=$("#outputDialog");
  if(typeof dialog.showModal==="function")dialog.showModal();
  else dialog.setAttribute("open","");
}
function closeOutputDialog(){
  const dialog=$("#outputDialog");
  if(typeof dialog.close==="function")dialog.close();
  else dialog.removeAttribute("open");
}
$("#go").onclick=openOutputDialog;
$("#outputCancel").onclick=closeOutputDialog;
$("#exportConfirm").onclick=async()=>{
  const body=payload();if(!body){closeOutputDialog();return;}
  try{if(!await ensureRaw9Support(body))return;}catch(e){setStatus("RAW 9 探测失败："+e,"err");return;}
  closeOutputDialog();
  $("#go").disabled=true;$("#exportConfirm").disabled=true;$("#revealBtn").style.display="none";beginBusy();setStatus("正在全尺寸导出…","");
  try{
    const j=await postJob("/export",body);
    if(!j.ok){endBusy();setStatus("错误："+j.error,"err");}
    else{applyJobEv(j);setStatus("已保存："+j.saved.join(" · ")+"（"+formatText(j.format)+"，EV "+fmtEv(j.ev)+"，曝光增益 "+j.gain.toFixed(3)+"，高光 "+highlightText(j.highlight)+"，色域 "+gamutText(j.gamut)+decoderText(j)+toneCoreText(j)+sceneTransformText(j)+fullFrameReferenceText(j)+metricText(j)+"）","ok");
      renderDeliveryReport(j);
      lastSavedPath=j.saved[0]||"";$("#revealBtn").style.display=lastSavedPath?"inline-block":"none";setPreviewImage(j.preview);}
  }catch(e){endBusy();setStatus("请求失败："+e,"err");}
  $("#go").disabled=false;$("#exportConfirm").disabled=false;
};
$("#revealBtn").onclick=async()=>{
  if(!lastSavedPath)return;
  $("#revealBtn").disabled=true;
  try{
    const j=await postJob("/reveal",{path:lastSavedPath});
    if(!j.ok)setStatus("Finder 打开失败："+j.error,"err");
  }catch(e){setStatus("Finder 请求失败："+e,"err");}
  $("#revealBtn").disabled=false;
};
function setStatus(t,c){const s=$("#status");s.textContent=t;s.className=c||"";}
</script>
</div></body></html>
"""


_LOOK_LABELS = {
    "optic_warm_cyan": "暖肤冷调",
}


def _grade_options_html() -> str:
    from ..display_filter import DISPLAY_FILTERS, FILTER_CHOICES, filter_available
    from ..grade import grade_id_for_filter, grade_id_for_look
    from ..look import LOOK_CHOICES

    lines = ['        <option value="none">无</option>']
    lines.append('        <optgroup label="内置风格">')
    for name in LOOK_CHOICES:
        if name == "none":
            continue
        label = _LOOK_LABELS.get(name, name.replace("fuji_", "Fujifilm ").replace("_", " "))
        gid = grade_id_for_look(name)
        lines.append(f'          <option value="{gid}">{label}</option>')
    lines.append("        </optgroup>")
    available_filters = [name for name in FILTER_CHOICES if name != "none" and filter_available(name)]
    if available_filters:
        lines.append('        <optgroup label="本地 LUT">')
        for name in available_filters:
            gid = grade_id_for_filter(name)
            lines.append(f'          <option value="{gid}">{DISPLAY_FILTERS[name].label}</option>')
        lines.append("        </optgroup>")
    return "\n".join(lines)


def _scene_transform_options_html() -> str:
    from ..scene_transform import SCENE_TRANSFORM_CHOICES, scene_transform_label

    lines = []
    for name in SCENE_TRANSFORM_CHOICES:
        lines.append(f'        <option value="{name}">{scene_transform_label(name)}</option>')
    return "\n".join(lines)


def _film_options_html() -> tuple[str, str, str]:
    from ..film_curve import FILM_CURVE_PRESETS, film_style_pairing
    from ..scene_transform import SCENE_TRANSFORMS

    film_opts, curve_opts, combos = [], [], {}
    for key, preset in FILM_CURVE_PRESETS.items():
        label = str(preset.get("label", key))
        fit = preset.get("fit", {})
        rms = fit.get("rms_stop")
        note = f"（拟合残差 {rms:.3f} stop）" if isinstance(rms, (int, float)) else ""
        film_opts.append(f'        <option value="{key}">{label}</option>')
        curve_opts.append(f'        <option value="{key}" title="{note}">{label}</option>')
        combo = preset.get("combo", {})
        st = str(combo.get("scene_transform", "none"))
        strength, primaries = film_style_pairing(key)
        combos[key] = {
            "wb": str(combo.get("wb", "5500k")),
            "st": st if st in SCENE_TRANSFORMS else "none",
            "fc": key,
            # Editorial style pairing (observe mode's declared look layer): the
            # combo sets these controls visibly, same as the other layers —
            # nothing baked, everything overridable.
            "sts": strength,
            "pr": primaries,
        }
    return "\n".join(film_opts), "\n".join(curve_opts), json.dumps(combos, ensure_ascii=False)


def render_page(init_dir: str) -> bytes:
    from dngscan import coreimage_decode
    from dngscan.constants import MAX_HDR_HEADROOM_EV
    from dngscan.gui.constants import RAW_EXTS, REALTIME_PREVIEW_LONG_EDGE

    film_opts, curve_opts, combos_json = _film_options_html()

    html = (
        PAGE.replace("INIT_DIR", json.dumps(init_dir))
        .replace("RAW_ACCEPT", ",".join(sorted(RAW_EXTS)))
        .replace("PREVIEW_LONG_EDGE", str(REALTIME_PREVIEW_LONG_EDGE))
        .replace("GRADE_OPTIONS", _grade_options_html())
        .replace("SCENE_TRANSFORM_OPTIONS", _scene_transform_options_html())
        .replace("COREIMAGE_AVAILABLE_FLAG", "true" if coreimage_decode.available() else "false")
        # Keep the slider ceiling on the same source of truth as the CLI's
        # --hdr-headroom bound (log2(4000/100) = 5.32); step 0.02 lands on it exactly.
        .replace("MAX_HDR_HEADROOM_ATTR", f"{MAX_HDR_HEADROOM_EV:.2f}")
        .replace("FILM_OPTIONS", film_opts)
        .replace("FILM_CURVE_OPTIONS", curve_opts)
        .replace("FILM_COMBOS_JSON", combos_json)
    )
    return html.encode("utf-8")
