"""
H5 工具组 — 最小化专属工具集

设计原则：
  文件读写 = workspace_tools（read_workspace_file / write_workspace_file / list_workspace_files）
  H5 专属工具只做 workspace_tools 无法完成的事：
    1. MIDI 二进制文件处理（复制 + 渲染初始 HTML）
    2. 模板注册表查询（列出模板 + 意图匹配）
    3. ABC / Sky JSON 解析（提取 title/bpm/key 元数据）

工具清单（共 6 个）：
  parse_abc_to_json       — ABC → 元数据（title/bpm/key/notes）
  parse_sky_json_to_json  — Sky JSON → 元数据
  list_h5_templates       — 列出可用模板 + 意图匹配指南
  get_h5_template         — 读取指定模板完整 HTML 源码（含 {{VAR}} 占位符）
  generate_h5_from_midi   — MIDI 辅助：复制文件 + 渲染初始 HTML（返回 html 供 Agent 继续编辑）
  save_h5_output          — 将 HTML 字符串同时写入全局输出目录 + 项目 h5/ 目录，返回访问路径
                            （write_workspace_file 只写项目目录，此工具额外写全局输出目录）
"""
from __future__ import annotations

import json
import re
import shutil
import uuid
from pathlib import Path

from app.agentcore.tools import tool
from app.config import config

from .h5_parsers import parse_abc_notes, parse_midi_bytes, parse_sky_json
from .h5_renderer import (
    TEMPLATE_REGISTRY,
    _TEMPLATE_DIR,
    read_h5_template,
    render_score_to_h5,
)

_H5_OUTPUT_DIR = Path(config.H5_OUTPUT_DIR)
_H5_OUTPUT_DIR.mkdir(parents=True, exist_ok=True)


def _copy_template_statics(tpl_dir: Path, dest_dir: Path) -> None:
    """复制模板根目录下的非 HTML 静态文件（style.css / player.js 等）到目标目录。"""
    if not tpl_dir.exists():
        return
    dest_dir.mkdir(parents=True, exist_ok=True)
    for f in tpl_dir.iterdir():
        if f.is_file() and f.name not in {"index.html", "meta.json"}:
            shutil.copy2(f, dest_dir / f.name)


# ═══════════════════════════════════════════════════════════════════════════════
# 解析工具（提取元数据，供 Agent 填入 HTML）
# ═══════════════════════════════════════════════════════════════════════════════

@tool(group="h5")
def parse_abc_to_json(abc: str, title: str = "") -> dict:
    """
    ABC Notation → 元数据（title / bpm / key / notes）。
    Agent 用返回值填写 HTML 模板中的对应占位符。

    abc: ABC Notation 字符串
    title: 覆盖标题（可选）
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
    Sky: Children of the Light JSON 谱子 → 元数据。

    sky_json_str: Sky 游戏谱子 JSON 字符串
    title: 覆盖标题（可选）
    返回: {"title": str, "bpm": int, "notes": [...], "key_count": int}
    """
    return parse_sky_json(sky_json_str, title)


# ═══════════════════════════════════════════════════════════════════════════════
# 模板注册表查询
# ═══════════════════════════════════════════════════════════════════════════════

@tool(group="h5")
def list_h5_templates() -> dict:
    """
    列出所有可用 H5 模板的元数据（轻量，不含 HTML 源码）。
    用于意图匹配选模板，选好后调用 get_h5_template(name) 读取 HTML 源码。

    返回: {
      "active": [{"name", "label", "intent_keys", "extra_vars", "size_kb"}],
      "intent_guide": str,
    }
    """
    active = [t for t in TEMPLATE_REGISTRY if not t.get("legacy")]

    result = []
    for t in active:
        result.append({
            "name":        t["name"],
            "label":       t["label"],
            "desc":        t.get("desc", ""),
            "mood":        t.get("mood", ""),
            "intent_keys": t["intent_keys"],
            "extra_vars":  t["extra_vars"],
            "size_kb":     t.get("size_kb", 0),
        })

    intent_lines = []
    for t in active:
        keys  = "、".join(t["intent_keys"][:6])
        extra = ""
        if t["extra_vars"]:
            extra = "  专属变量: " + ", ".join(f"{k}" for k in t["extra_vars"])
        intent_lines.append(f"  {t['name']:12s} → {t['label']} | {keys}{extra}")

    return {
        "active":       result,
        "intent_guide": "\n".join(intent_lines),
        "usage": (
            "1. 根据 intent_keys 选择模板 name\n"
            "2. 调用 get_h5_template(name) 读取该模板 HTML 源码\n"
            "3. 直接修改 HTML 字符串（替换占位符、嵌视频、改样式）\n"
            "4. 用 save_h5_output(html=修改后html, filename=曲名, template=模板名) 保存"
        ),
    }


# ═══════════════════════════════════════════════════════════════════════════════
# MIDI 辅助（唯一无法用 workspace_tools 替代的工具）
# ═══════════════════════════════════════════════════════════════════════════════

@tool(group="h5")
def get_h5_template(template_name: str) -> dict:
    """
    读取指定模板的完整 HTML 源码（含 {{VAR}} 占位符）。
    模板文件在服务器路径，workspace_tools 沙箱无法访问，必须用此工具读取。
    先调用 list_h5_templates() 获取可用模板名，再调用此工具。

    template_name: 模板名（如 apple / luoxiaohei / miku / neon / ins）
    返回: {
      "name": str,
      "html": str,        ← 完整 HTML 源码，直接修改后传给 save_h5_output
      "variables": [...], ← 所有 {{VAR}} 占位符名称列表
      "size_kb": float,
      "edit_hint": str,   ← 修改提示（ep-config JSON 位置等）
    }
    """
    try:
        html = read_h5_template(template_name)
    except FileNotFoundError as e:
        available = [t["name"] for t in TEMPLATE_REGISTRY if not t.get("legacy")]
        return {"error": str(e), "available": available}

    variables = sorted(set(re.findall(r"\{\{(\w+)\}\}", html)))

    if '"ep-config"' in html or "ep-config" in html:
        edit_hint = (
            "v7 模板：所有变量集中在底部 <script id=\"ep-config\"> JSON 块，"
            "只替换 JSON 值即可，其余 HTML 原样保留。"
            "VIDEO_URL 填普通链接（B站/YouTube），player.js 自动转 embed。"
        )
    else:
        edit_hint = (
            "v6 模板：{{VAR}} 散落在 HTML 各处，逐一字符串替换。"
            "视频嵌入：找到 {{VIDEO_SECTION}} 替换为 <iframe> 代码。"
        )

    return {
        "name":      template_name,
        "html":      html,
        "variables": variables,
        "size_kb":   round(len(html.encode()) / 1024, 1),
        "edit_hint": edit_hint,
    }


@tool(group="h5")
def generate_h5_from_midi(
    midi_workspace_path: str,
    title: str = "",
    template: str = "apple",
    composer: str = "",
    extra_info: str = "",
    video_url: str = "",
    extra_vars: str = "{}",
) -> dict:
    """
    【MIDI 专属】处理 MIDI 二进制文件（Agent 不能直接读取二进制），完成：
      ① 安全校验 MIDI 路径
      ② 复制 MIDI + 模板静态文件到项目 h5/ 目录
      ③ 渲染初始 HTML（基础占位符已替换）
      ④ 自动保存到项目 h5/ 目录 + 全局输出目录

    返回的 html 字段是完整 HTML 源码。
    若需追加修改（嵌视频/加歌词/改样式），直接编辑 html 字符串后调用 save_h5_output。
    若无需修改，工具已自动保存，直接 finish_task。

    midi_workspace_path: 项目内 MIDI 相对路径（如 .sky/晚安喵.mid）
    title: 标题（可选，默认从文件名推断）
    template: 模板名（调用 list_h5_templates 获取可用列表，默认 apple）
    composer: 作曲者（可选）
    extra_info: 额外说明（可选）
    video_url: 视频链接（B站/抖音/YouTube 普通链接，player.js 自动转 embed，可选）
    extra_vars: 模板专属变量 JSON 字符串（可选），如 '{"NIGHT_MOOD": "深夜·月光"}'
    返回: {
      "html": str,            ← 完整 HTML，可继续修改后 save_h5_output
      "file_saved": bool,
      "workspace_path": str,  ← 项目内路径，如 h5/晚安喵.html
      "url_path": str,
      "midi_url": str,        ← 相对路径，如 ./晚安喵.mid
      "title": str,
    }
    """
    try:
        from app.agentcore.session_context import get_current_project_root
        _project_root = get_current_project_root()
    except Exception:
        _project_root = ""

    if not _project_root:
        return {"error": "无法确定项目根目录，请确保在有效会话中调用此工具"}

    proj_root      = Path(_project_root)
    fallback_title = title or Path(midi_workspace_path).stem

    src_path = (proj_root / midi_workspace_path).resolve()
    if not str(src_path).startswith(str(proj_root.resolve())):
        return {"error": "路径越界拒绝"}
    if not src_path.exists():
        return {"error": f"MIDI 文件不存在: {midi_workspace_path}"}

    midi_filename = Path(midi_workspace_path).name
    midi_url      = f"./{midi_filename}"

    try:
        _extra_vars = json.loads(extra_vars) if isinstance(extra_vars, str) else (extra_vars or {})
    except Exception:
        _extra_vars = {}

    try:
        html = render_score_to_h5(
            title=fallback_title, notes=[], theme=template,
            midi_url=midi_url, composer=composer, extra_info=extra_info,
            video_url=video_url, source_format="midi", extra_vars=_extra_vars,
        )
    except Exception:
        html = render_score_to_h5(
            title=fallback_title, notes=[], theme="apple",
            midi_url=midi_url, composer=composer, extra_info=extra_info,
            source_format="midi",
        )

    safe_name = re.sub(r"[^\w\-]", "_", fallback_title) or "h5_midi"

    # 保存到全局输出目录
    out_path   = _H5_OUTPUT_DIR / f"{safe_name}.html"
    url_path   = ""
    file_saved = False
    try:
        out_path.write_text(html, encoding="utf-8")
        url_path   = f"/h5/{safe_name}.html"
        file_saved = True
        midi_out = _H5_OUTPUT_DIR / midi_filename
        if not midi_out.exists():
            shutil.copy2(src_path, midi_out)
        _copy_template_statics(_TEMPLATE_DIR / template, _H5_OUTPUT_DIR)
        tpl_assets = _TEMPLATE_DIR / template / "assets"
        if tpl_assets.exists():
            shutil.copytree(tpl_assets, _H5_OUTPUT_DIR / "assets", dirs_exist_ok=True)
    except Exception:
        pass

    # 保存到项目 h5/ 目录
    ws_path = ""
    try:
        proj_h5_dir = proj_root / "h5"
        proj_h5_dir.mkdir(parents=True, exist_ok=True)
        (proj_h5_dir / f"{safe_name}.html").write_text(html, encoding="utf-8")
        ws_path = f"h5/{safe_name}.html"
        _copy_template_statics(_TEMPLATE_DIR / template, proj_h5_dir)
        tpl_assets = _TEMPLATE_DIR / template / "assets"
        if tpl_assets.exists():
            shutil.copytree(tpl_assets, proj_h5_dir / "assets", dirs_exist_ok=True)
        midi_dest = proj_h5_dir / midi_filename
        if not midi_dest.exists():
            shutil.copy2(src_path, midi_dest)
    except Exception:
        pass

    return {
        "html":           html,
        "file_saved":     file_saved or bool(ws_path),
        "workspace_path": ws_path,
        "url_path":       url_path,
        "title":          fallback_title,
        "midi_url":       midi_url,
        "template":       template,
        "size_kb":        round(len(html.encode()) / 1024, 1),
        "_note": "已自动保存。如需修改 html 后调用 save_h5_output 覆盖；无需修改则直接 finish_task。",
    }


# ═══════════════════════════════════════════════════════════════════════════════
# 双写保存（项目目录 + 全局输出目录）
# workspace_tools.write_workspace_file 只写项目目录；
# 此工具额外写全局输出目录，使 /h5/xxx.html 路径可访问。
# ═══════════════════════════════════════════════════════════════════════════════

@tool(group="h5")
def save_h5_output(html: str, filename: str = "", template: str = "") -> dict:
    """
    将 Agent 编辑完成的 HTML 双写到：
      ① 全局输出目录（/h5/xxx.html 可访问）
      ② 当前项目 h5/ 目录（文件树可见）
    并自动复制模板静态文件（style.css / player.js / assets/）到两个目录，
    确保浏览器能正确加载样式和播放器脚本。

    ⚠️ 不需要传 workspace_id / project_id，系统自动推断。

    html: 完整 HTML 字符串（Agent 修改后的最终版本）
    filename: 文件名（不含扩展名，可选，默认自动生成）
    template: 模板名（如 luoxiaohei / apple，用于复制静态文件；可选，留空则跳过静态文件复制）
    返回: {"url_path": str, "workspace_path": str, "size_kb": float}
    """
    if not filename:
        filename = f"h5_{uuid.uuid4().hex[:8]}"

    safe_name = re.sub(r"[^\w\-]", "_", filename)
    out_path  = _H5_OUTPUT_DIR / f"{safe_name}.html"

    try:
        out_path.write_text(html, encoding="utf-8")
    except Exception as e:
        return {"error": f"全局输出目录写入失败: {e}"}

    # 复制模板静态文件到全局输出目录
    if template:
        _copy_template_statics(_TEMPLATE_DIR / template, _H5_OUTPUT_DIR)
        tpl_assets = _TEMPLATE_DIR / template / "assets"
        if tpl_assets.exists():
            shutil.copytree(tpl_assets, _H5_OUTPUT_DIR / "assets", dirs_exist_ok=True)

    ws_path = ""
    try:
        from app.agentcore.session_context import get_current_project_root
        _proj_root = get_current_project_root()
        if _proj_root:
            ws_h5_dir = Path(_proj_root) / "h5"
            ws_h5_dir.mkdir(parents=True, exist_ok=True)
            (ws_h5_dir / f"{safe_name}.html").write_text(html, encoding="utf-8")
            ws_path = f"h5/{safe_name}.html"
            # 复制模板静态文件到项目 h5/ 目录
            if template:
                _copy_template_statics(_TEMPLATE_DIR / template, ws_h5_dir)
                tpl_assets = _TEMPLATE_DIR / template / "assets"
                if tpl_assets.exists():
                    shutil.copytree(tpl_assets, ws_h5_dir / "assets", dirs_exist_ok=True)
    except Exception:
        pass

    return {
        "url_path":       f"/h5/{safe_name}.html",
        "workspace_path": ws_path,
        "filename":       f"{safe_name}.html",
        "size_kb":        round(len(html.encode()) / 1024, 1),
    }
