"""
H5 模板配置与 HTML 生成模块

包含：
  _TEMPLATES  — 四套视觉主题配置字典
  build_h5_html() — 构建完整 H5 海报 HTML 字符串
"""
from __future__ import annotations

import json

from .h5_utils import gen_waveform_data

# ═══════════════════════════════════════════════════════════════════════════════
# 模板配置（四套视觉主题）
# ═══════════════════════════════════════════════════════════════════════════════

TEMPLATES: dict[str, dict] = {
    "apple_dark": {
        "bg":          "#0A0A0F",
        "card_bg":     "rgba(28,28,30,0.85)",
        "text":        "#FFFFFF",
        "text_sub":    "rgba(255,255,255,0.55)",
        "accent":      "#FF375F",
        "accent2":     "#FF9500",
        "bar_color":   "#FF375F",
        "border":      "rgba(255,255,255,0.08)",
        "pill_bg":     "rgba(255,255,255,0.10)",
        "waveform_bg": "rgba(255,55,95,0.15)",
        "blur":        "20px",
        "gradient":    "linear-gradient(135deg, #1a0010 0%, #0A0A0F 50%, #001020 100%)",
    },
    "apple_light": {
        "bg":          "#F2F2F7",
        "card_bg":     "rgba(255,255,255,0.80)",
        "text":        "#1C1C1E",
        "text_sub":    "rgba(0,0,0,0.45)",
        "accent":      "#007AFF",
        "accent2":     "#34C759",
        "bar_color":   "#007AFF",
        "border":      "rgba(0,0,0,0.06)",
        "pill_bg":     "rgba(0,122,255,0.10)",
        "waveform_bg": "rgba(0,122,255,0.08)",
        "blur":        "20px",
        "gradient":    "linear-gradient(135deg, #E8F4FF 0%, #F2F2F7 50%, #E8FFE8 100%)",
    },
    "neon": {
        "bg":          "#050508",
        "card_bg":     "rgba(10,10,20,0.90)",
        "text":        "#E0F7FF",
        "text_sub":    "rgba(0,245,255,0.55)",
        "accent":      "#00F5FF",
        "accent2":     "#FF00AA",
        "bar_color":   "#00F5FF",
        "border":      "rgba(0,245,255,0.15)",
        "pill_bg":     "rgba(0,245,255,0.10)",
        "waveform_bg": "rgba(0,245,255,0.08)",
        "blur":        "16px",
        "gradient":    "linear-gradient(135deg, #050508 0%, #0A0020 50%, #000A0A 100%)",
    },
    "minimal": {
        "bg":          "#FFFFFF",
        "card_bg":     "rgba(248,248,248,0.95)",
        "text":        "#1A1A1A",
        "text_sub":    "rgba(0,0,0,0.40)",
        "accent":      "#222222",
        "accent2":     "#666666",
        "bar_color":   "#333333",
        "border":      "rgba(0,0,0,0.08)",
        "pill_bg":     "rgba(0,0,0,0.05)",
        "waveform_bg": "rgba(0,0,0,0.04)",
        "blur":        "0px",
        "gradient":    "linear-gradient(135deg, #FAFAFA 0%, #FFFFFF 100%)",
    },
}

# 模板描述（供 list_h5_templates 使用）
TEMPLATE_META: list[dict] = [
    {
        "id":            "apple_dark",
        "name":          "苹果暗色",
        "description":   "深色毛玻璃背景，白色文字，苹果 Music 风格，适合夜晚分享",
        "primary_color": "#1C1C1E",
        "accent_color":  "#FF375F",
    },
    {
        "id":            "apple_light",
        "name":          "苹果亮色",
        "description":   "白色磨砂背景，深色文字，清新简约，适合日间分享",
        "primary_color": "#F2F2F7",
        "accent_color":  "#007AFF",
    },
    {
        "id":            "neon",
        "name":          "霓虹电子",
        "description":   "深黑背景 + 霓虹渐变，电子感十足，适合现代音乐",
        "primary_color": "#0A0A0F",
        "accent_color":  "#00F5FF",
    },
    {
        "id":            "minimal",
        "name":          "极简白",
        "description":   "纯白背景，极简排版，专注于乐谱内容本身",
        "primary_color": "#FFFFFF",
        "accent_color":  "#333333",
    },
]


# ═══════════════════════════════════════════════════════════════════════════════
# HTML 生成
# ═══════════════════════════════════════════════════════════════════════════════

def build_h5_html(
    title: str,
    notes: list[dict],
    template: str,
    source_format: str,
    abc_content: str,
    bpm: int,
    key: str,
    composer: str,
    extra_info: str,
) -> str:
    """构建完整的 H5 海报 HTML 字符串。"""
    t = TEMPLATES.get(template, TEMPLATES["apple_dark"])

    notes_json_str = json.dumps(notes[:256], ensure_ascii=False)
    abc_escaped    = json.dumps(abc_content or "", ensure_ascii=False)
    note_count     = len(notes)
    duration_ms    = (
        (notes[-1]["time_ms"] + notes[-1].get("duration_ms", 200)) if notes else 0
    )
    duration_str = (
        f"{duration_ms // 60000}:{(duration_ms % 60000) // 1000:02d}"
        if duration_ms else "--:--"
    )

    fmt_badges = {"midi": "MIDI", "abc": "ABC", "sky_json": "Sky JSON"}
    fmt_label  = fmt_badges.get(source_format, source_format.upper())

    waveform_bars = gen_waveform_data(notes, bars=40)
    waveform_json = json.dumps(waveform_bars)

    composer_html = (
        f'<div class="meta-item"><span class="meta-icon">👤</span>'
        f'<span>{composer}</span></div>'
        if composer else ""
    )
    extra_html = f'<div class="extra-info">{extra_info}</div>' if extra_info else ""

    abc_section = ""
    if abc_content:
        abc_section = """
        <div class="abc-section" id="abcSection">
            <div class="section-title">乐谱预览</div>
            <div id="abcOutput" class="abc-output"></div>
        </div>"""

    abc_svg_filter = (
        "invert(1) hue-rotate(180deg)"
        if template in ("apple_dark", "neon") else "none"
    )

    return f"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0, viewport-fit=cover">
<meta name="theme-color" content="{t['bg']}">
<title>{title} — EP-Agent 乐谱海报</title>
<meta property="og:title" content="{title}">
<meta property="og:description" content="乐谱海报 · {note_count} 音符 · {duration_str}">
<script src="https://cdnjs.cloudflare.com/ajax/libs/abcjs/6.4.4/abcjs-basic-min.js"></script>
<style>
  *, *::before, *::after {{ box-sizing: border-box; margin: 0; padding: 0; }}

  :root {{
    --bg:         {t['bg']};
    --card-bg:    {t['card_bg']};
    --text:       {t['text']};
    --text-sub:   {t['text_sub']};
    --accent:     {t['accent']};
    --accent2:    {t['accent2']};
    --bar-color:  {t['bar_color']};
    --border:     {t['border']};
    --pill-bg:    {t['pill_bg']};
    --wave-bg:    {t['waveform_bg']};
    --blur:       {t['blur']};
    --gradient:   {t['gradient']};
    --safe-bottom: env(safe-area-inset-bottom, 0px);
    --safe-top:    env(safe-area-inset-top, 0px);
  }}

  html, body {{
    width: 100%; height: 100%;
    background: var(--bg);
    color: var(--text);
    font-family: -apple-system, BlinkMacSystemFont, "SF Pro Display",
                 "PingFang SC", "Helvetica Neue", sans-serif;
    -webkit-font-smoothing: antialiased;
    overflow-x: hidden;
  }}

  .page-wrapper {{ position: relative; min-height: 100dvh; }}

  /* ── 封面层（全屏固定，下拉后滑走） ── */
  .cover-layer {{
    position: fixed;
    top: 0; left: 0; right: 0;
    height: 100dvh;
    background: var(--gradient);
    z-index: 100;
    display: flex;
    flex-direction: column;
    align-items: center;
    justify-content: flex-end;
    padding: calc(var(--safe-top) + 20px) 24px calc(var(--safe-bottom) + 40px);
    transition: transform 0.55s cubic-bezier(0.32, 0.72, 0, 1),
                opacity   0.55s cubic-bezier(0.32, 0.72, 0, 1);
    will-change: transform, opacity;
    overflow: hidden;
  }}
  .cover-layer.pulled {{
    transform: translateY(-100%);
    opacity: 0;
    pointer-events: none;
  }}

  .cover-bg-canvas {{
    position: absolute; inset: 0;
    width: 100%; height: 100%;
    pointer-events: none; opacity: 0.35;
  }}

  .cover-content {{
    position: relative; z-index: 2;
    width: 100%; max-width: 420px; text-align: center;
  }}

  .cover-disc {{
    width: 160px; height: 160px;
    border-radius: 50%;
    background: conic-gradient(
      var(--accent) 0deg, var(--accent2) 120deg,
      var(--accent) 240deg, var(--accent2) 360deg
    );
    margin: 0 auto 28px;
    display: flex; align-items: center; justify-content: center;
    box-shadow: 0 0 60px color-mix(in srgb, var(--accent) 40%, transparent),
                0 20px 60px rgba(0,0,0,0.5);
    animation: discSpin 8s linear infinite paused;
    position: relative;
  }}
  .cover-disc.playing {{ animation-play-state: running; }}
  .cover-disc::after {{
    content: ""; position: absolute;
    width: 48px; height: 48px; border-radius: 50%;
    background: var(--bg);
    box-shadow: inset 0 2px 8px rgba(0,0,0,0.4);
  }}
  @keyframes discSpin {{
    from {{ transform: rotate(0deg); }}
    to   {{ transform: rotate(360deg); }}
  }}

  .cover-title {{
    font-size: clamp(22px, 6vw, 32px);
    font-weight: 700; letter-spacing: -0.02em;
    line-height: 1.15; margin-bottom: 8px;
  }}
  .cover-composer {{
    font-size: 15px; color: var(--text-sub);
    margin-bottom: 24px; letter-spacing: 0.01em;
  }}

  .pills-row {{
    display: flex; gap: 8px; justify-content: center;
    flex-wrap: wrap; margin-bottom: 32px;
  }}
  .pill {{
    padding: 5px 14px; border-radius: 20px;
    background: var(--pill-bg); border: 1px solid var(--border);
    font-size: 12px; font-weight: 600; color: var(--accent);
    letter-spacing: 0.04em;
    backdrop-filter: blur(var(--blur));
    -webkit-backdrop-filter: blur(var(--blur));
  }}
  .pill.secondary {{ color: var(--text-sub); font-weight: 500; }}

  .waveform-wrap {{
    width: 100%; height: 56px;
    display: flex; align-items: flex-end; gap: 2px;
    margin-bottom: 28px; padding: 0 4px;
    background: var(--wave-bg); border-radius: 14px; overflow: hidden;
  }}
  .wave-bar {{
    flex: 1; background: var(--bar-color);
    border-radius: 2px 2px 0 0;
    transition: height 0.3s ease; opacity: 0.75; min-height: 3px;
  }}
  .wave-bar.active {{ opacity: 1; }}

  .player-controls {{
    display: flex; align-items: center;
    justify-content: center; gap: 20px; margin-bottom: 32px;
  }}
  .ctrl-btn {{
    width: 48px; height: 48px; border-radius: 50%;
    border: none; cursor: pointer;
    display: flex; align-items: center; justify-content: center;
    font-size: 20px;
    transition: transform 0.15s, opacity 0.15s;
    background: var(--pill-bg); color: var(--text);
    -webkit-tap-highlight-color: transparent;
  }}
  .ctrl-btn:active {{ transform: scale(0.88); opacity: 0.7; }}
  .ctrl-btn.primary {{
    width: 64px; height: 64px; font-size: 26px;
    background: var(--accent); color: #fff;
    box-shadow: 0 8px 24px color-mix(in srgb, var(--accent) 40%, transparent);
  }}

  .pull-hint {{
    display: flex; flex-direction: column;
    align-items: center; gap: 6px;
    color: var(--text-sub); font-size: 12px; letter-spacing: 0.04em;
    animation: hintBounce 2s ease-in-out infinite;
  }}
  .pull-hint-arrow {{
    width: 28px; height: 28px;
    border-left: 2px solid var(--text-sub);
    border-bottom: 2px solid var(--text-sub);
    transform: rotate(-45deg) translateY(-4px);
  }}
  @keyframes hintBounce {{
    0%, 100% {{ transform: translateY(0); opacity: 0.5; }}
    50%       {{ transform: translateY(6px); opacity: 1; }}
  }}

  /* ── 详情层 ── */
  .detail-layer {{
    position: relative; z-index: 1;
    padding-top: 100dvh; min-height: 200dvh; background: var(--bg);
  }}
  .detail-inner {{
    padding: 40px 20px calc(var(--safe-bottom) + 60px);
    max-width: 480px; margin: 0 auto;
  }}

  .card {{
    background: var(--card-bg); border: 1px solid var(--border);
    border-radius: 20px; padding: 20px; margin-bottom: 16px;
    backdrop-filter: blur(var(--blur));
    -webkit-backdrop-filter: blur(var(--blur));
  }}
  .section-title {{
    font-size: 13px; font-weight: 600; color: var(--text-sub);
    text-transform: uppercase; letter-spacing: 0.08em; margin-bottom: 14px;
  }}

  .meta-grid {{ display: grid; grid-template-columns: 1fr 1fr; gap: 12px; }}
  .meta-item {{ display: flex; align-items: center; gap: 8px; font-size: 14px; }}
  .meta-icon {{ font-size: 16px; }}
  .meta-label {{ color: var(--text-sub); font-size: 12px; margin-top: 2px; }}

  .notes-waterfall {{
    height: 120px; position: relative;
    overflow: hidden; border-radius: 12px; background: var(--wave-bg);
  }}
  .notes-canvas {{ width: 100%; height: 100%; }}

  .abc-section {{ margin-bottom: 16px; }}
  .abc-output {{
    background: var(--card-bg); border: 1px solid var(--border);
    border-radius: 20px; padding: 16px; overflow-x: auto;
    backdrop-filter: blur(var(--blur));
    -webkit-backdrop-filter: blur(var(--blur));
  }}
  .abc-output svg {{ max-width: 100%; filter: {abc_svg_filter}; }}

  .share-section {{ text-align: center; padding: 20px 0 0; }}
  .share-btn {{
    display: inline-flex; align-items: center; gap: 8px;
    padding: 14px 32px; border-radius: 50px;
    background: var(--accent); color: #fff;
    font-size: 15px; font-weight: 600; border: none; cursor: pointer;
    letter-spacing: 0.02em;
    box-shadow: 0 8px 24px color-mix(in srgb, var(--accent) 35%, transparent);
    transition: transform 0.15s, box-shadow 0.15s;
    -webkit-tap-highlight-color: transparent;
  }}
  .share-btn:active {{ transform: scale(0.95); }}
  .brand-tag {{
    margin-top: 16px; font-size: 12px;
    color: var(--text-sub); letter-spacing: 0.04em;
  }}
  .extra-info {{
    font-size: 13px; color: var(--text-sub); line-height: 1.6; margin-top: 8px;
  }}

  .back-to-cover {{
    position: fixed;
    bottom: calc(var(--safe-bottom) + 24px); right: 20px;
    z-index: 200; width: 44px; height: 44px; border-radius: 50%;
    background: var(--card-bg); border: 1px solid var(--border);
    backdrop-filter: blur(var(--blur)); -webkit-backdrop-filter: blur(var(--blur));
    display: flex; align-items: center; justify-content: center;
    font-size: 18px; cursor: pointer;
    opacity: 0; transform: translateY(10px);
    transition: opacity 0.3s, transform 0.3s;
    -webkit-tap-highlight-color: transparent;
  }}
  .back-to-cover.visible {{ opacity: 1; transform: translateY(0); }}

  @media (min-width: 480px) {{
    .cover-disc {{ width: 200px; height: 200px; }}
  }}
</style>
</head>
<body>
<div class="page-wrapper" id="pageWrapper">

  <!-- ══ 封面层 ══ -->
  <div class="cover-layer" id="coverLayer">
    <canvas class="cover-bg-canvas" id="bgCanvas"></canvas>
    <div class="cover-content">
      <div class="cover-disc" id="coverDisc"></div>
      <div class="cover-title">{title}</div>
      {f'<div class="cover-composer">{composer}</div>' if composer else ''}
      <div class="pills-row">
        <span class="pill">{fmt_label}</span>
        <span class="pill secondary">♩={bpm} BPM</span>
        <span class="pill secondary">{key} 调</span>
        {f'<span class="pill secondary">{note_count} 音符</span>' if note_count else ''}
        {f'<span class="pill secondary">{duration_str}</span>' if duration_str != "--:--" else ''}
      </div>
      <div class="waveform-wrap" id="waveformWrap"></div>
      <div class="player-controls">
        <button class="ctrl-btn" id="btnRestart" title="重新开始">⏮</button>
        <button class="ctrl-btn primary" id="btnPlay" title="播放/暂停">▶</button>
        <button class="ctrl-btn" id="btnStop" title="停止">⏹</button>
      </div>
      <div class="pull-hint" id="pullHint">
        <div class="pull-hint-arrow"></div>
        <span>下拉查看详情</span>
      </div>
    </div>
  </div>

  <!-- ══ 详情层 ══ -->
  <div class="detail-layer">
    <div class="detail-inner">
      <div class="card">
        <div class="section-title">乐曲信息</div>
        <div class="meta-grid">
          <div class="meta-item">
            <span class="meta-icon">🎵</span>
            <div><div>{title}</div><div class="meta-label">曲名</div></div>
          </div>
          {composer_html}
          <div class="meta-item">
            <span class="meta-icon">🎼</span>
            <div><div>{key}</div><div class="meta-label">调号</div></div>
          </div>
          <div class="meta-item">
            <span class="meta-icon">🥁</span>
            <div><div>{bpm} BPM</div><div class="meta-label">速度</div></div>
          </div>
          <div class="meta-item">
            <span class="meta-icon">🎹</span>
            <div><div>{note_count}</div><div class="meta-label">音符数</div></div>
          </div>
          <div class="meta-item">
            <span class="meta-icon">⏱</span>
            <div><div>{duration_str}</div><div class="meta-label">时长</div></div>
          </div>
        </div>
        {extra_html}
      </div>

      <div class="card">
        <div class="section-title">音符可视化</div>
        <div class="notes-waterfall">
          <canvas class="notes-canvas" id="notesCanvas"></canvas>
        </div>
      </div>

      {abc_section}

      <div class="share-section">
        <button class="share-btn" id="shareBtn">
          <span>📤</span><span>分享这首乐曲</span>
        </button>
        <div class="brand-tag">由 EP-Agent 生成 · 乐谱海报</div>
      </div>
    </div>
  </div>

</div>
<button class="back-to-cover" id="backToCover" title="返回封面">↑</button>

<script>
(function() {{
  'use strict';

  const NOTES       = {notes_json_str};
  const WAVEFORM    = {waveform_json};
  const ABC_CONTENT = {abc_escaped};
  const BPM         = {bpm};
  const DURATION_MS = {duration_ms or (note_count * 300)};

  const coverLayer = document.getElementById('coverLayer');
  const coverDisc  = document.getElementById('coverDisc');
  const btnPlay    = document.getElementById('btnPlay');
  const btnStop    = document.getElementById('btnStop');
  const btnRestart = document.getElementById('btnRestart');
  const waveWrap   = document.getElementById('waveformWrap');
  const backBtn    = document.getElementById('backToCover');
  const shareBtn   = document.getElementById('shareBtn');

  // ── 波形渲染 ──
  WAVEFORM.forEach(function(h, i) {{
    const bar = document.createElement('div');
    bar.className = 'wave-bar';
    bar.style.height = Math.max(4, Math.round(h * 52)) + 'px';
    bar.dataset.idx = i;
    waveWrap.appendChild(bar);
  }});

  // ── Web Audio 播放器 ──
  let audioCtx = null, isPlaying = false, playStart = 0;
  let scheduledNodes = [], animFrame = null;

  const NOTE_FREQ = {{
    'C':261.63,'C#':277.18,'D':293.66,'D#':311.13,'E':329.63,
    'F':349.23,'F#':369.99,'G':392.00,'G#':415.30,'A':440.00,
    'A#':466.16,'B':493.88
  }};

  function noteNameToFreq(name) {{
    const m = name.match(/^([A-G]#?)(-?[0-9]+)$/);
    if (!m) return 440;
    return (NOTE_FREQ[m[1]] || 440) * Math.pow(2, parseInt(m[2]) - 4);
  }}

  function getAudioCtx() {{
    if (!audioCtx) audioCtx = new (window.AudioContext || window.webkitAudioContext)();
    return audioCtx;
  }}

  function stopAll() {{
    scheduledNodes.forEach(function(n) {{ try {{ n.stop(0); }} catch(e) {{}} }});
    scheduledNodes = [];
    if (animFrame) {{ cancelAnimationFrame(animFrame); animFrame = null; }}
    isPlaying = false;
    btnPlay.textContent = '▶';
    coverDisc.classList.remove('playing');
    highlightWave(-1);
  }}

  function playNotes(offsetMs) {{
    const ctx = getAudioCtx();
    if (ctx.state === 'suspended') ctx.resume();
    stopAll();
    const now = ctx.currentTime;
    playStart = now - offsetMs / 1000;
    isPlaying = true;
    btnPlay.textContent = '⏸';
    coverDisc.classList.add('playing');

    NOTES.forEach(function(note) {{
      const t = note.time_ms / 1000, d = (note.duration_ms || 200) / 1000;
      if (t < offsetMs / 1000) return;
      const osc = ctx.createOscillator(), gain = ctx.createGain();
      osc.connect(gain); gain.connect(ctx.destination);
      const freq = note.midi
        ? 440 * Math.pow(2, (note.midi - 69) / 12)
        : noteNameToFreq(note.pitch || 'A4');
      osc.type = 'sine';
      osc.frequency.setValueAtTime(freq, now + t);
      const st = now + t;
      gain.gain.setValueAtTime(0, st);
      gain.gain.linearRampToValueAtTime(0.18, st + 0.01);
      gain.gain.exponentialRampToValueAtTime(0.001, st + d);
      osc.start(st); osc.stop(st + d + 0.05);
      scheduledNodes.push(osc);
    }});

    setTimeout(function() {{
      if (isPlaying) stopAll();
    }}, (DURATION_MS / 1000 - offsetMs / 1000) * 1000 + 500);

    animateWave();
  }}

  function animateWave() {{
    const bars = waveWrap.querySelectorAll('.wave-bar');
    const total = bars.length;
    if (!total) return;
    function tick() {{
      if (!isPlaying) return;
      const elapsed = (getAudioCtx().currentTime - playStart) * 1000;
      const progress = Math.min(elapsed / (DURATION_MS || 1), 1);
      highlightWave(Math.floor(progress * total));
      animFrame = requestAnimationFrame(tick);
    }}
    animFrame = requestAnimationFrame(tick);
  }}

  function highlightWave(idx) {{
    waveWrap.querySelectorAll('.wave-bar').forEach(function(b, i) {{
      b.classList.toggle('active', i === idx);
    }});
  }}

  // ── 音符瀑布可视化 ──
  function drawNotesCanvas() {{
    const canvas = document.getElementById('notesCanvas');
    if (!canvas || !NOTES.length) return;
    const ctx2 = canvas.getContext('2d');
    const W = canvas.offsetWidth || 300, H = canvas.offsetHeight || 120;
    canvas.width = W * devicePixelRatio; canvas.height = H * devicePixelRatio;
    ctx2.scale(devicePixelRatio, devicePixelRatio);
    const maxT = NOTES.reduce(function(m, n) {{
      return Math.max(m, n.time_ms + (n.duration_ms || 200));
    }}, 1);
    const pitches = [...new Set(NOTES.map(function(n) {{ return n.midi || n.pitch; }}))].sort();
    const pitchIdx = {{}};
    pitches.forEach(function(p, i) {{ pitchIdx[p] = i; }});
    const rows = Math.max(pitches.length, 1);
    const accent = getComputedStyle(document.documentElement)
      .getPropertyValue('--accent').trim();
    ctx2.clearRect(0, 0, W, H);
    NOTES.forEach(function(note) {{
      const x  = (note.time_ms / maxT) * W;
      const pw = Math.max(2, ((note.duration_ms || 200) / maxT) * W);
      const pi = pitchIdx[note.midi || note.pitch] || 0;
      const y  = H - ((pi / rows) * H) - 4;
      ctx2.fillStyle = accent || '#FF375F';
      ctx2.globalAlpha = 0.75;
      ctx2.beginPath();
      ctx2.roundRect(x, y, pw, 4, 2);
      ctx2.fill();
    }});
    ctx2.globalAlpha = 1;
  }}

  // ── ABC 渲染 ──
  function renderAbc() {{
    if (!ABC_CONTENT || !window.ABCJS) return;
    try {{
      ABCJS.renderAbc('abcOutput', ABC_CONTENT, {{ responsive: 'resize', add_classes: true }});
    }} catch(e) {{ console.warn('ABC render failed:', e); }}
  }}

  // ── 下拉 / 滚动手势 ──
  let touchStartY = 0, coverPulled = false;

  document.addEventListener('touchstart', function(e) {{
    touchStartY = e.touches[0].clientY;
  }}, {{ passive: true }});

  document.addEventListener('touchmove', function(e) {{
    if (!coverPulled && e.touches[0].clientY - touchStartY < -60 && window.scrollY < 10)
      pullCover();
  }}, {{ passive: true }});

  document.addEventListener('wheel', function(e) {{
    if (!coverPulled && e.deltaY > 80) pullCover();
  }}, {{ passive: true }});

  function pullCover() {{
    if (coverPulled) return;
    coverPulled = true;
    coverLayer.classList.add('pulled');
    setTimeout(function() {{
      window.scrollTo({{ top: window.innerHeight * 0.5, behavior: 'smooth' }});
    }}, 200);
    backBtn.classList.add('visible');
  }}

  function restoreCover() {{
    coverPulled = false;
    coverLayer.classList.remove('pulled');
    window.scrollTo({{ top: 0, behavior: 'smooth' }});
    backBtn.classList.remove('visible');
  }}

  backBtn.addEventListener('click', restoreCover);

  window.addEventListener('scroll', function() {{
    if (window.scrollY > window.innerHeight * 0.3) {{
      if (!coverPulled) {{ coverPulled = true; coverLayer.classList.add('pulled'); }}
      backBtn.classList.add('visible');
    }} else {{
      backBtn.classList.remove('visible');
    }}
  }}, {{ passive: true }});

  // ── 播放按钮事件 ──
  btnPlay.addEventListener('click', function() {{
    if (isPlaying) stopAll(); else playNotes(0);
  }});
  btnStop.addEventListener('click', stopAll);
  btnRestart.addEventListener('click', function() {{
    stopAll(); setTimeout(function() {{ playNotes(0); }}, 50);
  }});

  // ── 分享 ──
  shareBtn.addEventListener('click', function() {{
    if (navigator.share) {{
      navigator.share({{ title: '{title}', text: '听听这首乐曲：{title}', url: location.href }})
        .catch(function() {{}});
    }} else {{
      navigator.clipboard.writeText(location.href).then(function() {{
        shareBtn.querySelector('span:last-child').textContent = '链接已复制！';
        setTimeout(function() {{
          shareBtn.querySelector('span:last-child').textContent = '分享这首乐曲';
        }}, 2000);
      }}).catch(function() {{}});
    }}
  }});

  // ── 背景粒子动画 ──
  function initBgCanvas() {{
    const canvas = document.getElementById('bgCanvas');
    if (!canvas) return;
    const ctx3 = canvas.getContext('2d');
    let W = canvas.offsetWidth, H = canvas.offsetHeight;
    canvas.width = W; canvas.height = H;
    const particles = Array.from({{length: 40}}, function() {{
      return {{
        x: Math.random() * W, y: Math.random() * H,
        r: Math.random() * 2 + 1,
        vx: (Math.random() - 0.5) * 0.4,
        vy: (Math.random() - 0.5) * 0.4,
        o: Math.random() * 0.5 + 0.2,
      }};
    }});
    const accent = getComputedStyle(document.documentElement)
      .getPropertyValue('--accent').trim() || '#FF375F';
    function draw() {{
      ctx3.clearRect(0, 0, W, H);
      particles.forEach(function(p) {{
        p.x += p.vx; p.y += p.vy;
        if (p.x < 0) p.x = W; if (p.x > W) p.x = 0;
        if (p.y < 0) p.y = H; if (p.y > H) p.y = 0;
        ctx3.beginPath();
        ctx3.arc(p.x, p.y, p.r, 0, Math.PI * 2);
        ctx3.fillStyle = accent;
        ctx3.globalAlpha = p.o;
        ctx3.fill();
      }});
      ctx3.globalAlpha = 1;
      requestAnimationFrame(draw);
    }}
    draw();
    window.addEventListener('resize', function() {{
      W = canvas.offsetWidth; H = canvas.offsetHeight;
      canvas.width = W; canvas.height = H;
    }});
  }}

  // ── 初始化 ──
  window.addEventListener('load', function() {{
    initBgCanvas();
    drawNotesCanvas();
    renderAbc();
  }});
  window.addEventListener('resize', drawNotesCanvas);

}})();
</script>
</body>
</html>"""
