"""
H5 工具组 — H5 乐谱海报生成工具集

@tool(group="h5") 注册所有工具，H5Agent 通过 get_tool_schemas("h5") 按需加载。

工具清单：
  parse_midi_to_json      — MIDI base64 → JSON 音符数据
  parse_abc_to_json       — ABC Notation → JSON 音符数据
  parse_sky_json_to_json  — Sky JSON → JSON 音符数据
  generate_h5_poster      — 乐谱数据 + 样式 → 完整 H5 HTML（苹果风格海报）
  generate_h5_from_abc    — ABC 字符串直接生成 H5 海报（快捷入口）
  save_h5_file            — 将 HTML 字符串保存为文件并返回访问路径
  list_h5_templates       — 列出可用 H5 模板

模块拆分说明：
  h5_parsers.py   — MIDI / ABC / Sky JSON 解析（parse_midi_bytes 等）
  h5_templates.py — 模板配置（TEMPLATES / TEMPLATE_META）+ build_h5_html()
  h5_utils.py     — 波形数据生成（gen_waveform_data）
"""
from __future__ import annotations

import base64
import json
import re
import uuid
from pathlib import Path
from typing import Literal

from app.agentcore.tools import tool
from app.config import config

from .h5_parsers import parse_abc_notes, parse_midi_bytes, parse_sky_json
from .h5_templates import TEMPLATE_META, build_h5_html

# ── 输出目录（统一从 config 读取）────────────────────────────────────────────
_H5_OUTPUT_DIR = Path(config.H5_OUTPUT_DIR)
_H5_OUTPUT_DIR.mkdir(parents=True, exist_ok=True)


# ═══════════════════════════════════════════════════════════════════════════════
# 解析工具
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
    if "," in midi_b64:
        midi_b64 = midi_b64.split(",", 1)[1]
    try:
        raw = base64.b64decode(midi_b64)
    except Exception as e:
        return {"error": f"base64 解码失败: {e}", "notes": []}
    return parse_midi_bytes(raw, title)


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
    result = parse_abc_notes(abc)
    if title:
        result["title"] = title
    return result


@tool(group="h5")
def parse_sky_json_to_json(sky_json_str: str, title: str = "") -> dict:
    """
    将 Sky: Children of the Light 游戏导出的 JSON 谱子解析为通用音符数据。

    sky_json_str: Sky 游戏谱子 JSON 字符串
    title: 乐曲标题（可选，未提供时从 JSON name 字段读取）
    返回: {"title": str, "bpm": int, "notes": [...], "key_count": int}
    """
    return parse_sky_json(sky_json_str, title)


# ═══════════════════════════════════════════════════════════════════════════════
# H5 生成工具
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

    html = build_h5_html(
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
    if not abc or not abc.strip():
        return {"error": "ABC 内容为空", "html": ""}

    parsed = parse_abc_notes(abc)
    if "error" in parsed:
        return {"error": parsed["error"], "html": ""}

    html = build_h5_html(
        title=parsed.get("title", "未命名乐曲"),
        notes=parsed.get("notes", []),
        template=template,
        source_format="abc",
        abc_content=abc,
        bpm=parsed.get("bpm", 120),
        key=parsed.get("key", "C"),
        composer=composer,
        extra_info=extra_info,
    )

    return {
        "html": html,
        "file_saved": False,
        "title": parsed.get("title", "未命名乐曲"),
        "note_count": len(parsed.get("notes", [])),
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

    safe_name = re.sub(r"[^\w\-]", "_", filename)
    out_path  = _H5_OUTPUT_DIR / f"{safe_name}.html"

    try:
        out_path.write_text(html, encoding="utf-8")
    except Exception as e:
        return {"error": f"文件保存失败: {e}"}

    return {
        "file_path": str(out_path),
        "url_path":  f"/h5/{safe_name}.html",
        "filename":  f"{safe_name}.html",
        "size_kb":   round(len(html.encode("utf-8")) / 1024, 1),
    }


@tool(group="h5")
def list_h5_templates() -> dict:
    """
    列出所有可用的 H5 海报模板及其特点描述。
    返回: {"templates": [...]}
    """
    return {"templates": TEMPLATE_META}
