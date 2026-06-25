"""
H5 工具组 — H5 乐谱海报生成工具集

@tool(group="h5") 注册所有工具，H5Agent 通过 get_tool_schemas("h5") 按需加载。

工具清单：
  _parse_midi_file        — MIDI 文件路径 → JSON 音符数据（内部函数，不对 LLM 暴露）
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

import json
import re
import shutil
import uuid
from pathlib import Path
from typing import Literal

from app.agentcore.tools import tool
from app.config import config

from .h5_parsers import parse_abc_notes, parse_midi_bytes, parse_sky_json
from .h5_renderer import (
    TEMPLATE_REGISTRY,
    _TEMPLATE_DIR,
    list_template_files,
    read_h5_template,
    render_score_to_h5,
    write_h5_template as _write_h5_template,
)

# ── 输出目录（统一从 config 读取）────────────────────────────────────────────
_H5_OUTPUT_DIR = Path(config.H5_OUTPUT_DIR)
_H5_OUTPUT_DIR.mkdir(parents=True, exist_ok=True)


# ═══════════════════════════════════════════════════════════════════════════════
# 解析工具
# ═══════════════════════════════════════════════════════════════════════════════

# ═══════════════════════════════════════════════════════════════════════════════
# MIDI 解析（内部，仅供 generate_h5_from_midi 工具使用，不暴露给 LLM）
# MIDI 场景统一走文件路径：generate_h5_from_midi(midi_workspace_path=...) 一步完成
# LLM 永远不接触 MIDI 二进制或 base64 内容
# ═══════════════════════════════════════════════════════════════════════════════

def _parse_midi_file(midi_path: str, title: str = "") -> dict:
    """
    从文件路径读取 MIDI 并解析为 JSON 音符数据（内部函数，不对 LLM 暴露）。
    MIDI 场景请使用 generate_h5_from_midi(midi_workspace_path=...) 工具。

    midi_path: MIDI 文件的本地绝对路径
    title: 乐曲标题（可选，未提供时尝试从 MIDI meta 事件读取）
    返回: {"title": str, "bpm": int, "notes": [...], "duration_ms": int, "track_count": int}
    """
    fallback_title = title or "乐曲"
    try:
        with open(midi_path, "rb") as f:
            raw = f.read()
    except Exception as e:
        return {
            "title": fallback_title, "bpm": 120, "notes": [],
            "duration_ms": 0, "track_count": 0,
            "_warn": f"MIDI 文件读取失败({e})，已降级为空音符海报",
        }

    result = parse_midi_bytes(raw, fallback_title)

    if "error" in result:
        result["_warn"] = result.pop("error") + "，已降级为空音符海报"
        result.setdefault("notes", [])
        result.setdefault("bpm", 120)
        result.setdefault("title", fallback_title)

    return result


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
    template: str = "apple",
    source_format: Literal["midi", "abc", "sky_json"] = "abc",
    abc_content: str = "",
    bpm: int = 120,
    key: str = "C",
    composer: str = "",
    extra_info: str = "",
    midi_url: str = "",
    video_url: str = "",
    extra_vars: str = "{}",
) -> dict:
    """
    生成支持 MIDI/JSON/ABC 播放的 H5 乐谱海报页面。
    自动发现 h5-templates/ 下所有模板，无需硬编码模板名。

    title: 乐曲标题
    notes_json: 音符数据 JSON 字符串（parse_*_to_json 的输出）
    template: 模板名（调用 list_h5_templates 获取可用列表，默认 apple）
    source_format: 原始格式 midi/abc/sky_json
    abc_content: ABC Notation 原文（用于 abcjs 渲染，可选）
    bpm: 节拍速度
    key: 调号
    composer: 作曲者（可选）
    extra_info: 额外说明（可选）
    midi_url: MIDI 文件相对路径（CDN 库直接加载播放）
    video_url: 视频链接（可选）
    extra_vars: 模板专属变量 JSON 字符串，如 '{"LYRIC_LINE": "歌词"}'
                可用变量由 list_h5_templates 的 extra_vars 字段声明
    返回: {"html": str, "file_saved": false, "title": str, "note_count": int, "template": str}
    """
    try:
        notes_data = json.loads(notes_json) if isinstance(notes_json, str) else notes_json
    except Exception:
        notes_data = {"notes": []}
    notes_list = notes_data.get("notes", []) if isinstance(notes_data, dict) else []

    try:
        _extra_vars = json.loads(extra_vars) if isinstance(extra_vars, str) else (extra_vars or {})
    except Exception:
        _extra_vars = {}

    html = render_score_to_h5(
        title=title, notes=notes_list, theme=template,
        abc_content=abc_content, bpm=bpm, key=key,
        composer=composer, extra_info=extra_info,
        midi_url=midi_url, video_url=video_url,
        source_format=source_format, extra_vars=_extra_vars,
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
    template: str = "apple",
    composer: str = "",
    extra_info: str = "",
    video_url: str = "",
    extra_vars: str = "{}",
) -> dict:
    """
    从 ABC Notation 字符串直接生成 H5 乐谱海报（快捷入口，自动解析标题/BPM/调号）。
    自动发现 h5-templates/ 下所有模板，无需硬编码模板名。

    abc: ABC Notation 字符串
    template: 模板名（调用 list_h5_templates 获取可用列表，默认 apple）
    composer: 作曲者（可选）
    extra_info: 额外说明（可选）
    video_url: 视频链接（可选）
    extra_vars: 模板专属变量 JSON 字符串，如 '{"LYRIC_LINE": "歌词"}'
                可用变量由 list_h5_templates 的 extra_vars 字段声明
    返回: {"html": str, "file_saved": false, "title": str, "note_count": int}
    """
    if not abc or not abc.strip():
        return {"error": "ABC 内容为空", "html": ""}

    parsed = parse_abc_notes(abc)
    if "error" in parsed:
        return {"error": parsed["error"], "html": ""}

    notes = parsed.get("notes", [])
    title = parsed.get("title", "未命名乐曲")
    bpm   = parsed.get("bpm", 120)
    key   = parsed.get("key", "C")

    try:
        _extra_vars = json.loads(extra_vars) if isinstance(extra_vars, str) else (extra_vars or {})
    except Exception:
        _extra_vars = {}

    html = render_score_to_h5(
        title=title, notes=notes, theme=template,
        abc_content=abc, bpm=bpm, key=key,
        composer=composer, extra_info=extra_info,
        video_url=video_url, source_format="abc",
        extra_vars=_extra_vars,
    )

    return {
        "html": html,
        "file_saved": False,
        "title": title,
        "note_count": len(notes),
        "template": template,
    }


@tool(group="h5")
def save_h5_file(html: str, filename: str = "", workspace_id: str = "") -> dict:
    """
    将 H5 HTML 字符串保存为文件，返回文件路径和访问 URL。
    同时将文件复制到工作区 h5/ 目录（若提供 workspace_id），使其出现在文件树中。

    html: 完整的 HTML 字符串
    filename: 文件名（不含扩展名，可选，默认自动生成）
    workspace_id: 工作区 ID（可选，提供时同步写入工作区 h5/ 目录）
    返回: {"file_path": str, "url_path": str, "size_kb": float, "workspace_path": str}
    """
    if not filename:
        filename = f"h5_poster_{uuid.uuid4().hex[:8]}"

    safe_name = re.sub(r"[^\w\-]", "_", filename)
    out_path  = _H5_OUTPUT_DIR / f"{safe_name}.html"

    try:
        out_path.write_text(html, encoding="utf-8")
    except Exception as e:
        return {"error": f"文件保存失败: {e}"}

    # 同步写入工作区 h5/ 目录，使文件树可见
    ws_path = ""
    if workspace_id:
        try:
            from app.agentcore.tools.workspace_tools import _WS_ROOT
            ws_h5_dir = _WS_ROOT / workspace_id / "h5"
            ws_h5_dir.mkdir(parents=True, exist_ok=True)
            ws_file = ws_h5_dir / f"{safe_name}.html"
            ws_file.write_text(html, encoding="utf-8")
            ws_path = f"h5/{safe_name}.html"
        except Exception:
            pass

    return {
        "file_path":      str(out_path),
        "url_path":       f"/h5/{safe_name}.html",
        "filename":       f"{safe_name}.html",
        "size_kb":        round(len(html.encode("utf-8")) / 1024, 1),
        "workspace_path": ws_path,
    }


@tool(group="h5")
def list_h5_templates() -> dict:
    """
    列出所有可用的 H5 海报模板，包含意图关键词、专属变量、风格描述。
    Agent 应调用此工具动态获取模板信息，而非依赖提示词中的硬编码列表。
    返回: {"active": [...], "legacy": [...], "intent_guide": str}
    """
    # 从注册表动态构建，无需手动维护
    active  = [t for t in TEMPLATE_REGISTRY if not t.get("legacy")]
    legacy  = [t for t in TEMPLATE_REGISTRY if t.get("legacy")]

    # 意图匹配指南（供 Agent 推理用）
    intent_lines = []
    for t in active:
        keys = "、".join(t["intent_keys"][:6])
        extra = ""
        if t["extra_vars"]:
            extra = "  专属参数: " + ", ".join(
                f"{k}（{v}）" for k, v in t["extra_vars"].items()
            )
        intent_lines.append(f"  {t['name']:12s} → {t['label']} | 触发词: {keys}{extra}")

    return {
        "active":  active,
        "legacy":  legacy,
        "intent_guide": "模板意图匹配参考:\n" + "\n".join(intent_lines),
        "usage_hint": (
            "根据用户描述的风格/情绪/关键词匹配 intent_keys，"
            "选择最合适的 name 传入 generate_h5_poster/generate_h5_from_abc 的 template 参数。"
            "若用户未指定，默认使用 apple。"
        ),
    }


@tool(group="h5")
def get_h5_template(template_name: str) -> dict:
    """
    读取指定 H5 模板文件的原始 HTML 内容，供 LLM 查看和定制。

    template_name: 模板名称（apple / miku / luoxiaohei / neon / ins）
    返回: {"name": str, "html": str, "variables": list[str], "size_kb": float}
    """
    try:
        html = read_h5_template(template_name)
        variables = sorted(set(__import__("re").findall(r"\{\{(\w+)\}\}", html)))
        return {
            "name":      template_name,
            "html":      html,
            "variables": variables,
            "size_kb":   round(len(html.encode("utf-8")) / 1024, 1),
        }
    except FileNotFoundError as e:
        return {"error": str(e)}


@tool(group="h5")
def save_h5_template(template_name: str, html_content: str) -> dict:
    """
    保存（新建或覆盖）一个 H5 模板文件。
    LLM 可调用此工具定制专属主题模板，写入后立即可用。

    template_name: 模板名称（不含 .html 后缀，如 "cyberpunk" "genshin"）
    html_content:  完整 HTML 字符串（建议包含 {{TITLE}} {{NOTES_JSON}} 等占位符）
    返回: {"path": str, "size_kb": float, "variables": list[str]}
    """
    try:
        result = _write_h5_template(template_name, html_content)
        return result
    except Exception as e:
        return {"error": f"模板保存失败: {e}"}



@tool(group="h5")
def generate_h5_from_midi(
    midi_workspace_path: str,
    workspace_id: str,
    title: str = "",
    template: str = "apple",
    composer: str = "",
    extra_info: str = "",
) -> dict:
    """
    【快捷工具】从工作区 MIDI 文件路径一步生成 H5 海报并保存到工作区。
    智能相对路径：H5 保存在 workspace/h5/xxx.html，MIDI 在 workspace/.sky/xxx.mid，
    两者同属工作区，H5 直接用相对路径 ../{midi_workspace_path} 加载 MIDI，
    无需复制文件，路径始终正确。

    midi_workspace_path: 工作区内 MIDI 文件的相对路径（如 .sky/song.mid）
    workspace_id: 工作区 ID
    title: 乐曲标题（可选，未提供时从文件名推断）
    template: 视觉模板 apple/miku/luoxiaohei/neon/ins（默认 apple）
    composer: 作曲者（可选）
    extra_info: 额外说明（可选）
    返回: {"file_saved": true, "url_path": str, "workspace_path": str, "title": str, "midi_url": str}
    """
    import shutil
    from app.agentcore.tools.workspace_tools import _WS_ROOT

    fallback_title = title or Path(midi_workspace_path).stem

    # 工作区 MIDI 文件的真实路径（安全校验）
    ws_dir   = _WS_ROOT / workspace_id
    src_path = (ws_dir / midi_workspace_path).resolve()
    if not str(src_path).startswith(str(ws_dir.resolve())):
        return {"error": "路径越界拒绝"}
    if not src_path.exists():
        return {"error": f"MIDI 文件不存在: {midi_workspace_path}"}

    # ── 智能相对路径：H5 在 h5/xxx.html，MIDI 在 .sky/xxx.mid
    # 从 h5/ 目录出发，相对路径为 ../{midi_workspace_path}
    # 例：midi_workspace_path=".sky/song.mid" → midi_url="../.sky/song.mid"
    midi_url = f"../{midi_workspace_path}"

    # 生成 H5（只传 URL，CDN 库自行加载，LLM 不碰二进制）
    try:
        html = render_score_to_h5(
            title=fallback_title,
            notes=[],
            theme=template,
            midi_url=midi_url,
            composer=composer,
            extra_info=extra_info,
            source_format="midi",
        )
    except Exception:
        html = render_score_to_h5(
            title=fallback_title, notes=[], theme="apple",
            midi_url=midi_url, composer=composer,
            extra_info=extra_info, source_format="midi",
        )

    # ── 自动保存 1：H5 输出目录（静态服务访问）─────────────────────────────
    safe_name  = re.sub(r"[^\w\-]", "_", fallback_title) or "h5_midi"
    out_path   = _H5_OUTPUT_DIR / f"{safe_name}.html"
    url_path   = ""
    file_saved = False
    try:
        out_path.write_text(html, encoding="utf-8")
        url_path   = f"/h5/{safe_name}.html"
        file_saved = True
        # 复制模板 assets/ 目录（图片等静态资源，供相对路径 assets/xxx.png 访问）
        tpl_assets = _TEMPLATE_DIR / template / "assets"
        if tpl_assets.exists():
            out_assets = _H5_OUTPUT_DIR / "assets"
            shutil.copytree(tpl_assets, out_assets, dirs_exist_ok=True)
    except Exception:
        pass

    # ── 自动保存 2：工作区 h5/ 目录（文件树可见）─────────────────────────────
    ws_path = ""
    try:
        ws_h5_dir = _WS_ROOT / workspace_id / "h5"
        ws_h5_dir.mkdir(parents=True, exist_ok=True)
        ws_file = ws_h5_dir / f"{safe_name}.html"
        ws_file.write_text(html, encoding="utf-8")
        ws_path = f"h5/{safe_name}.html"
        # 工作区同样复制 assets/
        tpl_assets = _TEMPLATE_DIR / template / "assets"
        if tpl_assets.exists():
            ws_assets = ws_h5_dir / "assets"
            shutil.copytree(tpl_assets, ws_assets, dirs_exist_ok=True)
        # MIDI 无需复制：H5 用相对路径 ../{midi_workspace_path} 直接引用工作区原文件
    except Exception:
        pass

    # ── URL 优先级：工作区路径 > H5 输出目录路径 ──────────────────────────────
    # 工作区 H5（/workspace/ws_xxx/h5/xxx.html）中的相对路径 ../.sky/song.mid
    # 浏览器解析为 /workspace/ws_xxx/.sky/song.mid → 命中工作区静态服务 ✅
    # /h5/xxx.html 中的 ../.sky/song.mid → 解析为 /.sky/song.mid → 404 ❌
    # 因此前端应优先使用工作区访问路径
    ws_url_path = f"/workspace/{workspace_id}/h5/{safe_name}.html" if ws_path else ""
    final_url   = ws_url_path or url_path  # 工作区路径优先

    return {
        "file_saved":      file_saved or bool(ws_path),
        "url_path":        final_url,
        "h5_output_path":  url_path,        # /h5/ 静态服务路径（备用，MIDI 相对路径在此无效）
        "workspace_url":   ws_url_path,     # /workspace/ 路径（MIDI 相对路径正确）
        "file_path":       str(out_path) if file_saved else "",
        "workspace_path":  ws_path,
        "title":           fallback_title,
        "midi_url":        midi_url,
        "template":        template,
        "size_kb":         round(len(html.encode("utf-8")) / 1024, 1),
        "_note":           "H5 已自动保存到工作区 h5/ 目录。url_path 为工作区访问路径，MIDI 相对路径正确。无需再调 save_h5_file。",
    }
