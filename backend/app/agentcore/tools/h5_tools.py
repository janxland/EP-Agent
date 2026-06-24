"""
H5 工具组 — H5 乐谱海报生成工具集

@tool(group="h5") 注册所有工具，H5Agent 通过 get_tool_schemas("h5") 按需加载。

工具清单：
  parse_midi_to_json      — MIDI base64 → JSON 音符数据
  parse_abc_to_json       — ABC Notation → JSON 音符数据
  generate_h5_poster      — 乐谱数据 + 样式 → 完整 H5 HTML（苹果风格海报）
  generate_h5_from_abc    — ABC 字符串直接生成 H5 海报（快捷入口）
  save_h5_file            — 将 HTML 字符串保存为文件并返回访问路径
  list_h5_templates       — 列出可用 H5 模板

设计原则：
  - 每个工具职责单一，可独立调用
  - 工具不依赖 session 状态，纯函数风格
  - generate_h5_poster 返回完整可运行的 HTML 字符串
  - 所有工具通过 @tool(group="h5") 注册，可被发现
"""
from __future__ import annotations

import base64
import json
import os
import re
import struct
import uuid
from pathlib import Path
from typing import Literal, Optional

from app.agentcore.tools import tool
from app.config import config

# ── 输出目录配置（统一从 config 读取，避免与 main.py 重复定义）────────────────
_H5_OUTPUT_DIR = Path(config.H5_OUTPUT_DIR)
_H5_OUTPUT_DIR.mkdir(parents=True, exist_ok=True)


# ═══════════════════════════════════════════════════════════════════════════════
# 1. 解析工具
# ═══════════════════════════════════════════════════════════════════════════════

@tool(group="h5")
def parse_midi_to_json(midi_b64: str, title: str = "") -> dict:
    """
    将 MIDI 文件（base64 编码）解析为 JSON 音符数据。
    支持标准 MIDI Type 0 / Type 1 格式。

    midi_b64: MIDI 文件的 base64 字符串（可含 data:audio/midi;base64, 前缀）
    title: 乐曲标题（可选，未提供时尝试从 MIDI meta 事件读取）
    返回: {"title": str, "bpm": int, "notes": [...], "duration_ms": int, "track_count": int}
    """
    # 剥离 DataURL 前缀
    if "," in midi_b64:
        midi_b64 = midi_b64.split(",", 1)[1]

    try:
        raw = base64.b64decode(midi_b64)
    except Exception as e:
        return {"error": f"base64 解码失败: {e}", "notes": []}

    return _parse_midi_bytes(raw, title)


@tool(group="h5")
def parse_abc_to_json(abc: str, title: str = "") -> dict:
    """
    将 ABC Notation 字符串解析为 JSON 音符数据。

    abc: ABC Notation 字符串
    title: 乐曲标题（可选，未提供时从 ABC T: 字段读取）
    返回: {"title": str, "bpm": int, "key": str, "notes": [...], "abc_clean": str}
    """
    if not abc or not abc.strip():
        return {"error": "ABC 内容为空", "notes": []}

    result: dict = {"notes": [], "abc_clean": abc.strip()}

    # 提取元数据
    for line in abc.splitlines():
        line = line.strip()
        if line.startswith("T:") and not title:
            title = line[2:].strip()
        elif line.startswith("Q:"):
            try:
                bpm_str = re.search(r"(\d+)", line[2:])
                if bpm_str:
                    result["bpm"] = int(bpm_str.group(1))
            except Exception:
                pass
        elif line.startswith("K:"):
            result["key"] = line[2:].strip()

    result["title"] = title or "未命名乐曲"
    result.setdefault("bpm", 120)
    result.setdefault("key", "C")

    # 尝试调用 sky-music-tools 进行深度解析
    try:
        import sys
        sky_tools_path = str(Path(__file__).parent.parent.parent.parent / "sky-music-tools")
        if sky_tools_path not in sys.path:
            sys.path.insert(0, sky_tools_path)
        from tools.abc_to_json import abc_to_note_events
        events = abc_to_note_events(abc)
        result["notes"] = [
            {
                "pitch": e.pitch,
                "time_ms": int(e.time_ms),
                "duration_ms": int(e.duration_ms),
            }
            for e in events
        ]
    except Exception:
        # 降级：简单提取音符字母（不含时值）
        note_pattern = re.compile(r"[A-Ga-g][',]?")
        raw_notes = note_pattern.findall(abc)
        result["notes"] = [{"pitch": n, "time_ms": i * 300, "duration_ms": 250}
                           for i, n in enumerate(raw_notes[:128])]

    return result


@tool(group="h5")
def parse_sky_json_to_json(sky_json_str: str, title: str = "") -> dict:
    """
    将 Sky: Children of the Light 游戏导出的 JSON 谱子解析为通用音符数据。

    sky_json_str: Sky 游戏谱子 JSON 字符串
    title: 乐曲标题（可选，未提供时从 JSON name 字段读取）
    返回: {"title": str, "bpm": int, "notes": [...], "key_count": int}
    """
    try:
        data = json.loads(sky_json_str)
    except Exception as e:
        return {"error": f"JSON 解析失败: {e}", "notes": []}

    # Sky JSON 格式：可能是列表或对象
    if isinstance(data, list):
        song_data = data[0] if data else {}
    else:
        song_data = data

    result: dict = {
        "title": title or song_data.get("name", "未命名乐曲"),
        "bpm": song_data.get("bpm", 120),
        "key_count": song_data.get("pitchLevel", 15),
        "notes": [],
    }

    song_notes = song_data.get("songNotes", [])
    for note in song_notes:
        result["notes"].append({
            "pitch": note.get("key", ""),
            "time_ms": int(float(note.get("time", 0)) * 1000),
            "duration_ms": 200,
        })

    return result


# ═══════════════════════════════════════════════════════════════════════════════
# 2. H5 生成工具
# ═══════════════════════════════════════════════════════════════════════════════

@tool(group="h5")
def generate_h5_poster(
    title: str,
    notes_json: str,
    template: Literal["apple_dark", "apple_light", "neon", "minimal"] = "apple_dark",
    source_format: Literal["midi", "abc", "sky_json"] = "abc",
    abc_content: str = "",
    bpm: int = 120,
    key: str = "C",
    composer: str = "",
    extra_info: str = "",
) -> dict:
    """
    生成支持 MIDI/JSON/ABC 播放的 H5 乐谱海报页面。
    苹果风格下拉式高级海报，支持移动端分享。

    title: 乐曲标题
    notes_json: 音符数据 JSON 字符串（parse_*_to_json 的输出）
    template: 视觉模板 apple_dark/apple_light/neon/minimal
    source_format: 原始格式 midi/abc/sky_json
    abc_content: ABC Notation 原文（用于 abcjs 渲染，可选）
    bpm: 节拍速度
    key: 调号
    composer: 作曲者（可选）
    extra_info: 额外说明（可选）
    返回: {"html": str, "file_saved": false, "title": str}
    """
    try:
        notes_data = json.loads(notes_json) if isinstance(notes_json, str) else notes_json
    except Exception:
        notes_data = {"notes": []}

    notes_list = notes_data.get("notes", []) if isinstance(notes_data, dict) else []

    html = _build_h5_html(
        title=title,
        notes=notes_list,
        template=template,
        source_format=source_format,
        abc_content=abc_content,
        bpm=bpm,
        key=key,
        composer=composer,
        extra_info=extra_info,
    )

    return {
        "html": html,
        "file_saved": False,
        "title": title,
        "note_count": len(notes_list),
        "template": template,
    }


@tool(group="h5")
def generate_h5_from_abc(
    abc: str,
    template: Literal["apple_dark", "apple_light", "neon", "minimal"] = "apple_dark",
    composer: str = "",
    extra_info: str = "",
) -> dict:
    """
    从 ABC Notation 字符串直接生成 H5 乐谱海报（快捷入口，自动解析标题/BPM/调号）。

    abc: ABC Notation 字符串
    template: 视觉模板 apple_dark/apple_light/neon/minimal
    composer: 作曲者（可选）
    extra_info: 额外说明（可选）
    返回: {"html": str, "file_saved": false, "title": str, "note_count": int}
    """
    parsed = parse_abc_to_json(abc)
    if "error" in parsed:
        return {"error": parsed["error"], "html": ""}

    title = parsed.get("title", "未命名乐曲")
    bpm   = parsed.get("bpm", 120)
    key   = parsed.get("key", "C")
    notes = parsed.get("notes", [])

    html = _build_h5_html(
        title=title,
        notes=notes,
        template=template,
        source_format="abc",
        abc_content=abc,
        bpm=bpm,
        key=key,
        composer=composer,
        extra_info=extra_info,
    )

    return {
        "html": html,
        "file_saved": False,
        "title": title,
        "note_count": len(notes),
        "template": template,
    }


@tool(group="h5")
def save_h5_file(html: str, filename: str = "") -> dict:
    """
    将 H5 HTML 字符串保存为文件，返回文件路径和访问 URL。

    html: 完整的 HTML 字符串
    filename: 文件名（不含扩展名，可选，默认自动生成）
    返回: {"file_path": str, "url_path": str, "size_kb": float}
    """
    if not filename:
        filename = f"h5_poster_{uuid.uuid4().hex[:8]}"

    # 清理文件名
    safe_name = re.sub(r"[^\w\-]", "_", filename)
    out_path = _H5_OUTPUT_DIR / f"{safe_name}.html"

    try:
        out_path.write_text(html, encoding="utf-8")
    except Exception as e:
        return {"error": f"文件保存失败: {e}"}

    size_kb = round(len(html.encode("utf-8")) / 1024, 1)

    return {
        "file_path": str(out_path),
        "url_path":  f"/h5/{safe_name}.html",
        "filename":  f"{safe_name}.html",
        "size_kb":   size_kb,
    }


@tool(group="h5")
def list_h5_templates() -> dict:
    """
    列出所有可用的 H5 海报模板及其特点描述。
    返回: {"templates": [...]}
    """
    return {
        "templates": [
            {
                "id":          "apple_dark",
                "name":        "苹果暗色",
                "description": "深色毛玻璃背景，白色文字，苹果 Music 风格，适合夜晚分享",
                "primary_color": "#1C1C1E",
                "accent_color":  "#FF375F",
            },
            {
                "id":          "apple_light",
                "name":        "苹果亮色",
                "description": "白色磨砂背景，深色文字，清新简约，适合日间分享",
                "primary_color": "#F2F2F7",
                "accent_color":  "#007AFF",
            },
            {
                "id":          "neon",
                "name":        "霓虹电子",
                "description": "深黑背景 + 霓虹渐变，电子感十足，适合现代音乐",
                "primary_color": "#0A0A0F",
                "accent_color":  "#00F5FF",
            },
            {
                "id":          "minimal",
                "name":        "极简白",
                "description": "纯白背景，极简排版，专注于乐谱内容本身",
                "primary_color": "#FFFFFF",
                "accent_color":  "#333333",
            },
        ]
    }


# ═══════════════════════════════════════════════════════════════════════════════
# 内部实现：MIDI 解析
# ═══════════════════════════════════════════════════════════════════════════════

def _parse_midi_bytes(raw: bytes, title: str = "") -> dict:
    """轻量级 MIDI 解析器（不依赖第三方库）。"""
    result = {"title": title or "未命名乐曲", "bpm": 120, "notes": [],
              "duration_ms": 0, "track_count": 0}

    if len(raw) < 14 or raw[:4] != b"MThd":
        return {**result, "error": "不是有效的 MIDI 文件"}

    try:
        fmt    = struct.unpack(">H", raw[8:10])[0]
        n_trks = struct.unpack(">H", raw[10:12])[0]
        ticks  = struct.unpack(">H", raw[12:14])[0]
        result["track_count"] = n_trks

        pos    = 14
        tempo  = 500000  # 默认 120 BPM
        notes  = []

        for _ in range(n_trks):
            if pos + 8 > len(raw):
                break
            if raw[pos:pos+4] != b"MTrk":
                break
            trk_len = struct.unpack(">I", raw[pos+4:pos+8])[0]
            trk_end = pos + 8 + trk_len
            pos += 8

            abs_tick   = 0
            active: dict[int, int] = {}  # pitch → start_tick
            last_status = 0

            while pos < trk_end:
                # 读取 delta time（可变长编码）
                delta = 0
                while pos < trk_end:
                    b = raw[pos]; pos += 1
                    delta = (delta << 7) | (b & 0x7F)
                    if not (b & 0x80):
                        break
                abs_tick += delta

                if pos >= trk_end:
                    break

                status = raw[pos]
                if status & 0x80:
                    last_status = status; pos += 1
                else:
                    status = last_status  # running status

                msg_type = status & 0xF0

                if msg_type == 0x90:  # Note On
                    if pos + 1 >= trk_end:
                        break
                    pitch = raw[pos]; vel = raw[pos+1]; pos += 2
                    if vel > 0:
                        active[pitch] = abs_tick
                    else:
                        if pitch in active:
                            dur = abs_tick - active.pop(pitch)
                            ms_start = int(active.get("_start_ms_" + str(pitch), 0))
                            notes.append({"pitch": pitch, "tick": active.get("_t_" + str(pitch), abs_tick - dur), "dur_tick": dur})

                elif msg_type == 0x80:  # Note Off
                    if pos + 1 >= trk_end:
                        break
                    pitch = raw[pos]; pos += 2
                    if pitch in active:
                        dur = abs_tick - active.pop(pitch)
                        notes.append({"pitch": pitch, "tick": abs_tick - dur, "dur_tick": dur})

                elif msg_type == 0xFF:  # Meta
                    if pos >= trk_end:
                        break
                    meta_type = raw[pos]; pos += 1
                    meta_len  = 0
                    while pos < trk_end:
                        b = raw[pos]; pos += 1
                        meta_len = (meta_len << 7) | (b & 0x7F)
                        if not (b & 0x80):
                            break
                    meta_data = raw[pos:pos+meta_len]; pos += meta_len

                    if meta_type == 0x51 and len(meta_data) >= 3:
                        tempo = struct.unpack(">I", b"\x00" + meta_data[:3])[0]
                        result["bpm"] = round(60_000_000 / tempo)
                    elif meta_type == 0x03 and not title:
                        try:
                            result["title"] = meta_data.decode("utf-8", errors="replace").strip()
                        except Exception:
                            pass

                elif msg_type in (0xA0, 0xB0, 0xE0):
                    pos += 2
                elif msg_type in (0xC0, 0xD0):
                    pos += 1
                elif status == 0xF0 or status == 0xF7:
                    sysex_len = 0
                    while pos < trk_end:
                        b = raw[pos]; pos += 1
                        sysex_len = (sysex_len << 7) | (b & 0x7F)
                        if not (b & 0x80):
                            break
                    pos += sysex_len
                else:
                    pos += 1

            pos = trk_end

        # 将 tick 转换为毫秒
        tick_ms = (tempo / 1000) / ticks  # ms per tick
        out_notes = []
        for n in notes[:256]:  # 最多 256 个音符
            t_ms  = int(n["tick"] * tick_ms)
            d_ms  = max(50, int(n["dur_tick"] * tick_ms))
            pitch = n["pitch"]
            # MIDI 音高 → 音名
            names = ["C","C#","D","D#","E","F","F#","G","G#","A","A#","B"]
            note_name = f"{names[pitch % 12]}{pitch // 12 - 1}"
            out_notes.append({"pitch": note_name, "midi": pitch, "time_ms": t_ms, "duration_ms": d_ms})

        result["notes"] = sorted(out_notes, key=lambda x: x["time_ms"])
        if result["notes"]:
            last = result["notes"][-1]
            result["duration_ms"] = last["time_ms"] + last["duration_ms"]

    except Exception as e:
        result["error"] = f"MIDI 解析异常: {e}"

    return result


# ═══════════════════════════════════════════════════════════════════════════════
# 内部实现：H5 HTML 生成
# ═══════════════════════════════════════════════════════════════════════════════

# 模板配置
_TEMPLATES: dict[str, dict] = {
    "apple_dark": {
        "bg":           "#0A0A0F",
        "card_bg":      "rgba(28,28,30,0.85)",
        "text":         "#FFFFFF",
        "text_sub":     "rgba(255,255,255,0.55)",
        "accent":       "#FF375F",
        "accent2":      "#FF9500",
        "bar_color":    "#FF375F",
        "border":       "rgba(255,255,255,0.08)",
        "pill_bg":      "rgba(255,255,255,0.10)",
        "waveform_bg":  "rgba(255,55,95,0.15)",
        "blur":         "20px",
        "gradient":     "linear-gradient(135deg, #1a0010 0%, #0A0A0F 50%, #001020 100%)",
    },
    "apple_light": {
        "bg":           "#F2F2F7",
        "card_bg":      "rgba(255,255,255,0.80)",
        "text":         "#1C1C1E",
        "text_sub":     "rgba(0,0,0,0.45)",
        "accent":       "#007AFF",
        "accent2":      "#34C759",
        "bar_color":    "#007AFF",
        "border":       "rgba(0,0,0,0.06)",
        "pill_bg":      "rgba(0,122,255,0.10)",
        "waveform_bg":  "rgba(0,122,255,0.08)",
        "blur":         "20px",
        "gradient":     "linear-gradient(135deg, #E8F4FF 0%, #F2F2F7 50%, #E8FFE8 100%)",
    },
    "neon": {
        "bg":           "#050508",
        "card_bg":      "rgba(10,10,20,0.90)",
        "text":         "#E0F7FF",
        "text_sub":     "rgba(0,245,255,0.55)",
        "accent":       "#00F5FF",
        "accent2":      "#FF00AA",
        "bar_color":    "#00F5FF",
        "border":       "rgba(0,245,255,0.15)",
        "pill_bg":      "rgba(0,245,255,0.10)",
        "waveform_bg":  "rgba(0,245,255,0.08)",
        "blur":         "16px",
        "gradient":     "linear-gradient(135deg, #050508 0%, #0A0020 50%, #000A0A 100%)",
    },
    "minimal": {
        "bg":           "#FFFFFF",
        "card_bg":      "rgba(248,248,248,0.95)",
        "text":         "#1A1A1A",
        "text_sub":     "rgba(0,0,0,0.40)",
        "accent":       "#222222",
        "accent2":      "#666666",
        "bar_color":    "#333333",
        "border":       "rgba(0,0,0,0.08)",
        "pill_bg":      "rgba(0,0,0,0.05)",
        "waveform_bg":  "rgba(0,0,0,0.04)",
        "blur":         "0px",
        "gradient":     "linear-gradient(135deg, #FAFAFA 0%, #FFFFFF 100%)",
    },
}


def _build_h5_html(
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
    """构建完整的 H5 海报 HTML。"""
    t = _TEMPLATES.get(template, _TEMPLATES["apple_dark"])

    notes_json_str = json.dumps(notes[:256], ensure_ascii=False)
    abc_escaped    = json.dumps(abc_content or "", ensure_ascii=False)
    note_count     = len(notes)
    duration_ms    = (notes[-1]["time_ms"] + notes[-1].get("duration_ms", 200)) if notes else 0
    duration_str   = f"{duration_ms // 60000}:{(duration_ms % 60000) // 1000:02d}" if duration_ms else "--:--"

    # 格式徽章
    fmt_badges = {"midi": "MIDI", "abc": "ABC", "sky_json": "Sky JSON"}
    fmt_label  = fmt_badges.get(source_format, source_format.upper())

    # 波形条数据（基于音符密度生成可视化波形）
    waveform_bars = _gen_waveform_data(notes, bars=40)
    waveform_json = json.dumps(waveform_bars)

    composer_html = f'<div class="meta-item"><span class="meta-icon">👤</span><span>{composer}</span></div>' if composer else ""
    extra_html    = f'<div class="extra-info">{extra_info}</div>' if extra_info else ""

    # ABC 渲染区（仅当有 abc_content 时显示）
    abc_section = ""
    if abc_content:
        abc_section = """
        <div class="abc-section" id="abcSection">
            <div class="section-title">乐谱预览</div>
            <div id="abcOutput" class="abc-output"></div>
        </div>"""

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

  /* ── 页面结构：固定封面 + 可滚动详情 ── */
  .page-wrapper {{
    position: relative;
    min-height: 100dvh;
  }}

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

  /* 封面背景粒子 */
  .cover-bg-canvas {{
    position: absolute;
    inset: 0;
    width: 100%; height: 100%;
    pointer-events: none;
    opacity: 0.35;
  }}

  /* 封面内容 */
  .cover-content {{
    position: relative;
    z-index: 2;
    width: 100%;
    max-width: 420px;
    text-align: center;
  }}

  .cover-disc {{
    width: 160px; height: 160px;
    border-radius: 50%;
    background: conic-gradient(
      var(--accent) 0deg,
      var(--accent2) 120deg,
      var(--accent) 240deg,
      var(--accent2) 360deg
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
    content: "";
    position: absolute;
    width: 48px; height: 48px;
    border-radius: 50%;
    background: var(--bg);
    box-shadow: inset 0 2px 8px rgba(0,0,0,0.4);
  }}
  @keyframes discSpin {{
    from {{ transform: rotate(0deg); }}
    to   {{ transform: rotate(360deg); }}
  }}

  .cover-title {{
    font-size: clamp(22px, 6vw, 32px);
    font-weight: 700;
    letter-spacing: -0.02em;
    line-height: 1.15;
    margin-bottom: 8px;
  }}
  .cover-composer {{
    font-size: 15px;
    color: var(--text-sub);
    margin-bottom: 24px;
    letter-spacing: 0.01em;
  }}

  /* 格式 + 元数据 pills */
  .pills-row {{
    display: flex;
    gap: 8px;
    justify-content: center;
    flex-wrap: wrap;
    margin-bottom: 32px;
  }}
  .pill {{
    padding: 5px 14px;
    border-radius: 20px;
    background: var(--pill-bg);
    border: 1px solid var(--border);
    font-size: 12px;
    font-weight: 600;
    color: var(--accent);
    letter-spacing: 0.04em;
    backdrop-filter: blur(var(--blur));
    -webkit-backdrop-filter: blur(var(--blur));
  }}
  .pill.secondary {{ color: var(--text-sub); font-weight: 500; }}

  /* 波形可视化 */
  .waveform-wrap {{
    width: 100%;
    height: 56px;
    display: flex;
    align-items: flex-end;
    gap: 2px;
    margin-bottom: 28px;
    padding: 0 4px;
    background: var(--wave-bg);
    border-radius: 14px;
    overflow: hidden;
  }}
  .wave-bar {{
    flex: 1;
    background: var(--bar-color);
    border-radius: 2px 2px 0 0;
    transition: height 0.3s ease;
    opacity: 0.75;
    min-height: 3px;
  }}
  .wave-bar.active {{ opacity: 1; }}

  /* 播放控件 */
  .player-controls {{
    display: flex;
    align-items: center;
    justify-content: center;
    gap: 20px;
    margin-bottom: 32px;
  }}
  .ctrl-btn {{
    width: 48px; height: 48px;
    border-radius: 50%;
    border: none;
    cursor: pointer;
    display: flex; align-items: center; justify-content: center;
    font-size: 20px;
    transition: transform 0.15s, opacity 0.15s;
    background: var(--pill-bg);
    color: var(--text);
    -webkit-tap-highlight-color: transparent;
  }}
  .ctrl-btn:active {{ transform: scale(0.88); opacity: 0.7; }}
  .ctrl-btn.primary {{
    width: 64px; height: 64px;
    font-size: 26px;
    background: var(--accent);
    color: #fff;
    box-shadow: 0 8px 24px color-mix(in srgb, var(--accent) 40%, transparent);
  }}

  /* 下拉提示 */
  .pull-hint {{
    display: flex;
    flex-direction: column;
    align-items: center;
    gap: 6px;
    color: var(--text-sub);
    font-size: 12px;
    letter-spacing: 0.04em;
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

  /* ── 详情层（在封面下方，封面拉走后露出） ── */
  .detail-layer {{
    position: relative;
    z-index: 1;
    padding-top: 100dvh;
    min-height: 200dvh;
    background: var(--bg);
  }}

  .detail-inner {{
    padding: 40px 20px calc(var(--safe-bottom) + 60px);
    max-width: 480px;
    margin: 0 auto;
  }}

  /* 卡片 */
  .card {{
    background: var(--card-bg);
    border: 1px solid var(--border);
    border-radius: 20px;
    padding: 20px;
    margin-bottom: 16px;
    backdrop-filter: blur(var(--blur));
    -webkit-backdrop-filter: blur(var(--blur));
  }}

  .section-title {{
    font-size: 13px;
    font-weight: 600;
    color: var(--text-sub);
    text-transform: uppercase;
    letter-spacing: 0.08em;
    margin-bottom: 14px;
  }}

  /* 元数据网格 */
  .meta-grid {{
    display: grid;
    grid-template-columns: 1fr 1fr;
    gap: 12px;
  }}
  .meta-item {{
    display: flex;
    align-items: center;
    gap: 8px;
    font-size: 14px;
  }}
  .meta-icon {{ font-size: 16px; }}
  .meta-label {{ color: var(--text-sub); font-size: 12px; margin-top: 2px; }}

  /* 音符瀑布 */
  .notes-waterfall {{
    height: 120px;
    position: relative;
    overflow: hidden;
    border-radius: 12px;
    background: var(--wave-bg);
  }}
  .notes-canvas {{
    width: 100%; height: 100%;
  }}

  /* ABC 乐谱 */
  .abc-section {{ margin-bottom: 16px; }}
  .abc-output {{
    background: var(--card-bg);
    border: 1px solid var(--border);
    border-radius: 20px;
    padding: 16px;
    overflow-x: auto;
    backdrop-filter: blur(var(--blur));
    -webkit-backdrop-filter: blur(var(--blur));
  }}
  .abc-output svg {{
    max-width: 100%;
    filter: {("invert(1) hue-rotate(180deg)" if template in ("apple_dark","neon") else "none")};
  }}

  /* 底部分享区 */
  .share-section {{
    text-align: center;
    padding: 20px 0 0;
  }}
  .share-btn {{
    display: inline-flex;
    align-items: center;
    gap: 8px;
    padding: 14px 32px;
    border-radius: 50px;
    background: var(--accent);
    color: #fff;
    font-size: 15px;
    font-weight: 600;
    border: none;
    cursor: pointer;
    letter-spacing: 0.02em;
    box-shadow: 0 8px 24px color-mix(in srgb, var(--accent) 35%, transparent);
    transition: transform 0.15s, box-shadow 0.15s;
    -webkit-tap-highlight-color: transparent;
  }}
  .share-btn:active {{ transform: scale(0.95); }}
  .brand-tag {{
    margin-top: 16px;
    font-size: 12px;
    color: var(--text-sub);
    letter-spacing: 0.04em;
  }}

  .extra-info {{
    font-size: 13px;
    color: var(--text-sub);
    line-height: 1.6;
    margin-top: 8px;
  }}

  /* 返回顶部按钮 */
  .back-to-cover {{
    position: fixed;
    bottom: calc(var(--safe-bottom) + 24px);
    right: 20px;
    z-index: 200;
    width: 44px; height: 44px;
    border-radius: 50%;
    background: var(--card-bg);
    border: 1px solid var(--border);
    backdrop-filter: blur(var(--blur));
    -webkit-backdrop-filter: blur(var(--blur));
    display: flex; align-items: center; justify-content: center;
    font-size: 18px;
    cursor: pointer;
    opacity: 0;
    transform: translateY(10px);
    transition: opacity 0.3s, transform 0.3s;
    -webkit-tap-highlight-color: transparent;
  }}
  .back-to-cover.visible {{
    opacity: 1;
    transform: translateY(0);
  }}

  /* 响应式 */
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
      <!-- 旋转唱片 -->
      <div class="cover-disc" id="coverDisc"></div>

      <!-- 标题 -->
      <div class="cover-title">{title}</div>
      {f'<div class="cover-composer">{composer}</div>' if composer else ''}

      <!-- 格式 + 元数据 pills -->
      <div class="pills-row">
        <span class="pill">{fmt_label}</span>
        <span class="pill secondary">♩={bpm} BPM</span>
        <span class="pill secondary">{key} 调</span>
        {f'<span class="pill secondary">{note_count} 音符</span>' if note_count else ''}
        {f'<span class="pill secondary">{duration_str}</span>' if duration_str != "--:--" else ''}
      </div>

      <!-- 波形 -->
      <div class="waveform-wrap" id="waveformWrap">
        <!-- JS 动态插入 wave-bar -->
      </div>

      <!-- 播放控件 -->
      <div class="player-controls">
        <button class="ctrl-btn" id="btnRestart" title="重新开始">⏮</button>
        <button class="ctrl-btn primary" id="btnPlay" title="播放/暂停">▶</button>
        <button class="ctrl-btn" id="btnStop" title="停止">⏹</button>
      </div>

      <!-- 下拉提示 -->
      <div class="pull-hint" id="pullHint">
        <div class="pull-hint-arrow"></div>
        <span>下拉查看详情</span>
      </div>
    </div>
  </div>

  <!-- ══ 详情层 ══ -->
  <div class="detail-layer">
    <div class="detail-inner">

      <!-- 元数据卡片 -->
      <div class="card">
        <div class="section-title">乐曲信息</div>
        <div class="meta-grid">
          <div class="meta-item">
            <span class="meta-icon">🎵</span>
            <div>
              <div>{title}</div>
              <div class="meta-label">曲名</div>
            </div>
          </div>
          {composer_html}
          <div class="meta-item">
            <span class="meta-icon">🎼</span>
            <div>
              <div>{key}</div>
              <div class="meta-label">调号</div>
            </div>
          </div>
          <div class="meta-item">
            <span class="meta-icon">🥁</span>
            <div>
              <div>{bpm} BPM</div>
              <div class="meta-label">速度</div>
            </div>
          </div>
          <div class="meta-item">
            <span class="meta-icon">🎹</span>
            <div>
              <div>{note_count}</div>
              <div class="meta-label">音符数</div>
            </div>
          </div>
          <div class="meta-item">
            <span class="meta-icon">⏱</span>
            <div>
              <div>{duration_str}</div>
              <div class="meta-label">时长</div>
            </div>
          </div>
        </div>
        {extra_html}
      </div>

      <!-- 音符瀑布可视化 -->
      <div class="card">
        <div class="section-title">音符可视化</div>
        <div class="notes-waterfall">
          <canvas class="notes-canvas" id="notesCanvas"></canvas>
        </div>
      </div>

      {abc_section}

      <!-- 分享 -->
      <div class="share-section">
        <button class="share-btn" id="shareBtn">
          <span>📤</span>
          <span>分享这首乐曲</span>
        </button>
        <div class="brand-tag">由 EP-Agent 生成 · 乐谱海报</div>
      </div>

    </div>
  </div>

</div>

<!-- 返回封面按钮 -->
<button class="back-to-cover" id="backToCover" title="返回封面">↑</button>

<script>
(function() {{
  'use strict';

  // ── 数据 ──
  const NOTES       = {notes_json_str};
  const WAVEFORM    = {waveform_json};
  const ABC_CONTENT = {abc_escaped};
  const BPM         = {bpm};
  const DURATION_MS = {duration_ms or (note_count * 300)};

  // ── DOM ──
  const coverLayer  = document.getElementById('coverLayer');
  const coverDisc   = document.getElementById('coverDisc');
  const btnPlay     = document.getElementById('btnPlay');
  const btnStop     = document.getElementById('btnStop');
  const btnRestart  = document.getElementById('btnRestart');
  const waveWrap    = document.getElementById('waveformWrap');
  const backBtn     = document.getElementById('backToCover');
  const shareBtn    = document.getElementById('shareBtn');

  // ── 波形渲染 ──
  WAVEFORM.forEach(function(h, i) {{
    const bar = document.createElement('div');
    bar.className = 'wave-bar';
    bar.style.height = Math.max(4, Math.round(h * 52)) + 'px';
    bar.dataset.idx = i;
    waveWrap.appendChild(bar);
  }});

  // ── 简易音频合成（Web Audio API）──
  let audioCtx = null;
  let isPlaying = false;
  let playStart = 0;
  let scheduledNodes = [];
  let animFrame = null;

  const NOTE_FREQ = {{
    'C':261.63,'C#':277.18,'D':293.66,'D#':311.13,'E':329.63,
    'F':349.23,'F#':369.99,'G':392.00,'G#':415.30,'A':440.00,
    'A#':466.16,'B':493.88
  }};

  function noteNameToFreq(name) {{
    const m = name.match(/^([A-G]#?)(-?[0-9]+)$/);
    if (!m) return 440;
    const base = NOTE_FREQ[m[1]] || 440;
    const oct  = parseInt(m[2]) - 4;
    return base * Math.pow(2, oct);
  }}

  function getAudioCtx() {{
    if (!audioCtx) {{
      audioCtx = new (window.AudioContext || window.webkitAudioContext)();
    }}
    return audioCtx;
  }}

  function stopAll() {{
    scheduledNodes.forEach(function(n) {{
      try {{ n.stop(0); }} catch(e) {{}}
    }});
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
      const t  = note.time_ms / 1000;
      const d  = (note.duration_ms || 200) / 1000;
      if (t < offsetMs / 1000) return;

      const osc  = ctx.createOscillator();
      const gain = ctx.createGain();
      osc.connect(gain);
      gain.connect(ctx.destination);

      const freq = note.midi
        ? 440 * Math.pow(2, (note.midi - 69) / 12)
        : noteNameToFreq(note.pitch || 'A4');

      osc.type = 'sine';
      osc.frequency.setValueAtTime(freq, now + t);

      const startT = now + t;
      gain.gain.setValueAtTime(0, startT);
      gain.gain.linearRampToValueAtTime(0.18, startT + 0.01);
      gain.gain.exponentialRampToValueAtTime(0.001, startT + d);

      osc.start(startT);
      osc.stop(startT + d + 0.05);
      scheduledNodes.push(osc);
    }});

    // 播放结束检测
    const totalSec = DURATION_MS / 1000 || 10;
    setTimeout(function() {{
      if (isPlaying) stopAll();
    }}, (totalSec - offsetMs / 1000) * 1000 + 500);

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
      const activeIdx = Math.floor(progress * total);
      highlightWave(activeIdx);
      animFrame = requestAnimationFrame(tick);
    }}
    animFrame = requestAnimationFrame(tick);
  }}

  function highlightWave(idx) {{
    const bars = waveWrap.querySelectorAll('.wave-bar');
    bars.forEach(function(b, i) {{
      b.classList.toggle('active', i === idx);
    }});
  }}

  // ── 音符瀑布可视化 ──
  function drawNotesCanvas() {{
    const canvas = document.getElementById('notesCanvas');
    if (!canvas || !NOTES.length) return;
    const ctx = canvas.getContext('2d');
    const W = canvas.offsetWidth || 300;
    const H = canvas.offsetHeight || 120;
    canvas.width  = W * window.devicePixelRatio;
    canvas.height = H * window.devicePixelRatio;
    ctx.scale(window.devicePixelRatio, window.devicePixelRatio);

    const maxT = NOTES.reduce(function(m, n) {{ return Math.max(m, n.time_ms + (n.duration_ms||200)); }}, 1);
    const pitches = [...new Set(NOTES.map(function(n) {{ return n.midi || n.pitch; }}))].sort();
    const pitchIdx = {{}};
    pitches.forEach(function(p, i) {{ pitchIdx[p] = i; }});
    const rows = Math.max(pitches.length, 1);

    const accent = getComputedStyle(document.documentElement).getPropertyValue('--accent').trim();
    ctx.clearRect(0, 0, W, H);

    NOTES.forEach(function(note) {{
      const x  = (note.time_ms / maxT) * W;
      const pw = Math.max(2, ((note.duration_ms || 200) / maxT) * W);
      const pi = pitchIdx[note.midi || note.pitch] || 0;
      const y  = H - ((pi / rows) * H) - 4;
      ctx.fillStyle = accent || '#FF375F';
      ctx.globalAlpha = 0.75;
      ctx.beginPath();
      ctx.roundRect(x, y, pw, 4, 2);
      ctx.fill();
    }});
    ctx.globalAlpha = 1;
  }}

  // ── ABC 渲染（abcjs）──
  function renderAbc() {{
    if (!ABC_CONTENT || !window.ABCJS) return;
    try {{
      ABCJS.renderAbc('abcOutput', ABC_CONTENT, {{
        responsive: 'resize',
        add_classes: true,
      }});
    }} catch(e) {{
      console.warn('ABC render failed:', e);
    }}
  }}

  // ── 下拉手势 ──
  let touchStartY = 0;
  let coverPulled = false;

  document.addEventListener('touchstart', function(e) {{
    touchStartY = e.touches[0].clientY;
  }}, {{ passive: true }});

  document.addEventListener('touchmove', function(e) {{
    if (coverPulled) return;
    const dy = e.touches[0].clientY - touchStartY;
    if (dy < -60 && window.scrollY < 10) {{
      pullCover();
    }}
  }}, {{ passive: true }});

  // 鼠标滚轮支持（桌面端）
  document.addEventListener('wheel', function(e) {{
    if (!coverPulled && e.deltaY > 80) {{
      pullCover();
    }}
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

  // 滚动监听：显示/隐藏返回按钮
  window.addEventListener('scroll', function() {{
    if (window.scrollY > window.innerHeight * 0.3) {{
      if (!coverPulled) {{ coverPulled = true; coverLayer.classList.add('pulled'); }}
      backBtn.classList.add('visible');
    }} else {{
      backBtn.classList.remove('visible');
    }}
  }}, {{ passive: true }});

  // ── 按钮事件 ──
  btnPlay.addEventListener('click', function() {{
    if (isPlaying) {{
      stopAll();
    }} else {{
      playNotes(0);
    }}
  }});

  btnStop.addEventListener('click', stopAll);

  btnRestart.addEventListener('click', function() {{
    stopAll();
    setTimeout(function() {{ playNotes(0); }}, 50);
  }});

  // ── 分享 ──
  shareBtn.addEventListener('click', function() {{
    if (navigator.share) {{
      navigator.share({{
        title: '{title}',
        text: '听听这首乐曲：{title}',
        url: location.href,
      }}).catch(function() {{}});
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
    const ctx = canvas.getContext('2d');
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

    const accent = getComputedStyle(document.documentElement).getPropertyValue('--accent').trim() || '#FF375F';

    function draw() {{
      ctx.clearRect(0, 0, W, H);
      particles.forEach(function(p) {{
        p.x += p.vx; p.y += p.vy;
        if (p.x < 0) p.x = W; if (p.x > W) p.x = 0;
        if (p.y < 0) p.y = H; if (p.y > H) p.y = 0;
        ctx.beginPath();
        ctx.arc(p.x, p.y, p.r, 0, Math.PI * 2);
        ctx.fillStyle = accent;
        ctx.globalAlpha = p.o;
        ctx.fill();
      }});
      ctx.globalAlpha = 1;
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

  window.addEventListener('resize', function() {{
    drawNotesCanvas();
  }});

}})();
</script>
</body>
</html>"""


def _gen_waveform_data(notes: list[dict], bars: int = 40) -> list[float]:
    """根据音符时间分布生成波形数据（0.0~1.0）。"""
    if not notes:
        import math
        return [0.3 + 0.4 * abs(math.sin(i * 0.5)) for i in range(bars)]

    max_t = max((n.get("time_ms", 0) + n.get("duration_ms", 200)) for n in notes)
    if max_t <= 0:
        max_t = bars * 300

    buckets = [0.0] * bars
    for note in notes:
        idx = min(int((note.get("time_ms", 0) / max_t) * bars), bars - 1)
        buckets[idx] += 1.0

    max_v = max(buckets) or 1.0
    # 平滑 + 归一化
    smooth = buckets[:]
    for i in range(1, bars - 1):
        smooth[i] = (buckets[i-1] + buckets[i] * 2 + buckets[i+1]) / 4
    return [min(1.0, max(0.05, v / max_v)) for v in smooth]
