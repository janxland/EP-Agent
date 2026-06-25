"""
H5 模板渲染器 — 文件夹驱动的模板注册 + 变量注入系统

架构说明（参考 PPT 专家的模板管理模式）：
  模板目录：backend/agent/h5-templates/{name}/
  每个模板文件夹包含：
    index.html   — 模板 HTML，内含 {{VARIABLE}} 占位符
    meta.json    — 模板元数据（自描述，无需修改 Python 代码即可注册新模板）
    assets/      — 模板专属静态资源（图片等）

  新增模板只需：
    1. 创建 h5-templates/{name}/ 目录
    2. 放入 index.html + meta.json（+ 可选 assets/）
    3. 重启服务（或调用 reload_template_registry()）
    → Agent / 工具层 / 提示词全部动态感知，零硬编码

meta.json 格式：
  {
    "name":         "apple",
    "label":        "苹果风格",
    "desc":         "模板描述",
    "mood":         "dark premium",
    "intent_keys":  ["苹果", "apple", ...],
    "extra_vars":   {"LYRIC_LINE": "说明"},
    "theme_preset": {
      "ACCENT_COLOR": "#FF375F",
      "BG_COLOR": "#0A0A0F",
      "CARD_BG": "rgba(...)",
      "TEXT_COLOR": "#FFFFFF",
      "TEXT_SUB": "rgba(...)",
      "GRADIENT": "linear-gradient(...)",
      "ABC_SVG_FILTER": "invert(1) brightness(0.9)"
    }
  }

提供的函数：
  reload_template_registry() — 重新扫描目录，刷新注册表（热重载）
  read_h5_template()         — 按名称读取模板 HTML
  write_h5_template()        — 写入/更新模板 HTML（支持子目录结构）
  render_h5_template()       — 将 {{VAR}} 占位符替换为实际值
  list_template_files()      — 返回所有已注册模板列表（含元数据）
  build_template_vars()      — 从乐谱数据构建变量字典
  render_score_to_h5()       — 一步完成：读模板→构建变量→渲染
"""
from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

# ── 模板目录 ──────────────────────────────────────────────────────────────────
_BACKEND_DIR  = Path(__file__).resolve().parent.parent.parent.parent  # backend/
_TEMPLATE_DIR = _BACKEND_DIR / "agent" / "h5-templates"

# ── 兜底主题预设（当模板 meta.json 缺少 theme_preset 时使用）─────────────────
_FALLBACK_PRESET: dict[str, str] = {
    "ACCENT_COLOR":   "#FF375F",
    "BG_COLOR":       "#0A0A0F",
    "CARD_BG":        "rgba(28,28,30,0.85)",
    "TEXT_COLOR":     "#FFFFFF",
    "TEXT_SUB":       "rgba(255,255,255,0.55)",
    "GRADIENT":       "linear-gradient(135deg, #1a0010 0%, #0A0A0F 50%, #001020 100%)",
    "ABC_SVG_FILTER": "invert(1) brightness(0.9)",
}

# ── legacy 别名（向后兼容旧模板名，不需要独立文件夹）────────────────────────
_LEGACY_ALIASES: dict[str, str] = {
    "apple_dark":  "apple",
    "apple_light": "apple",
    "minimal":     "apple",
}


# ═══════════════════════════════════════════════════════════════════════════════
# 模板注册表 — 运行时从文件夹动态构建，零硬编码
# ═══════════════════════════════════════════════════════════════════════════════

def _scan_template_dir() -> tuple[list[dict], dict[str, dict], dict[str, str]]:
    """
    扫描 h5-templates/ 目录，构建三个数据结构：
      registry     — 完整模板列表（含元数据）
      by_name      — name → 元数据字典
      preset_map   — name → theme_preset 字典
    """
    registry: list[dict] = []
    by_name:  dict[str, dict] = {}
    presets:  dict[str, str]  = {}

    if not _TEMPLATE_DIR.exists():
        return registry, by_name, presets

    for subdir in sorted(_TEMPLATE_DIR.iterdir()):
        if not subdir.is_dir():
            continue
        index_file = subdir / "index.html"
        if not index_file.exists():
            continue

        name = subdir.name
        meta_file = subdir / "meta.json"

        # 读取 meta.json（若缺失则生成最小元数据）
        if meta_file.exists():
            try:
                meta = json.loads(meta_file.read_text(encoding="utf-8"))
            except Exception:
                meta = {}
        else:
            meta = {}

        # 规范化元数据字段
        entry = {
            "name":        meta.get("name", name),
            "label":       meta.get("label", name),
            "desc":        meta.get("desc", ""),
            "mood":        meta.get("mood", ""),
            "intent_keys": meta.get("intent_keys", [name]),
            "extra_vars":  meta.get("extra_vars", {}),
            "legacy":      False,
            # 文件系统信息
            "file":        f"{name}/index.html",
            "path":        str(index_file),
            "size_kb":     round(index_file.stat().st_size / 1024, 1),
            # assets 列表
            "assets":      _list_assets(subdir),
        }

        registry.append(entry)
        by_name[name] = entry

        # 主题预设：优先 meta.json，缺失则用兜底
        preset = meta.get("theme_preset", {})
        presets[name] = {**_FALLBACK_PRESET, **preset}

    # 注入 legacy 别名（指向已有模板，不重复出现在列表中）
    for alias, target in _LEGACY_ALIASES.items():
        if alias not in by_name and target in by_name:
            alias_entry = {**by_name[target], "name": alias, "legacy": True}
            by_name[alias] = alias_entry
            presets[alias] = presets.get(target, _FALLBACK_PRESET)

    return registry, by_name, presets


def _list_assets(subdir: Path) -> list[str]:
    assets_dir = subdir / "assets"
    if not assets_dir.exists():
        return []
    return [f.name for f in sorted(assets_dir.iterdir()) if f.is_file()]


# 首次启动时扫描
TEMPLATE_REGISTRY, _REGISTRY_BY_NAME, _PRESET_MAP = _scan_template_dir()


def reload_template_registry() -> int:
    """
    热重载：重新扫描模板目录，刷新全局注册表。
    新增/删除模板文件夹后调用此函数即可生效，无需重启服务。
    返回：发现的模板数量
    """
    global TEMPLATE_REGISTRY, _REGISTRY_BY_NAME, _PRESET_MAP
    TEMPLATE_REGISTRY, _REGISTRY_BY_NAME, _PRESET_MAP = _scan_template_dir()
    return len(TEMPLATE_REGISTRY)


# ═══════════════════════════════════════════════════════════════════════════════
# 核心读写函数
# ═══════════════════════════════════════════════════════════════════════════════

def read_h5_template(template_name: str) -> str:
    """
    读取模板 HTML。

    查找顺序：
      1. {name}/index.html（标准子目录结构）
      2. legacy 别名 → 指向目标模板的子目录
      3. {name}.html（根目录平铺，向后兼容）
    """
    # legacy 别名解析
    resolved = _LEGACY_ALIASES.get(template_name, template_name)

    # 1. 子目录结构（首选）
    subdir_path = _TEMPLATE_DIR / resolved / "index.html"
    if subdir_path.exists():
        return subdir_path.read_text(encoding="utf-8")

    # 2. 根目录平铺（向后兼容）
    flat_path = _TEMPLATE_DIR / f"{resolved}.html"
    if flat_path.exists():
        return flat_path.read_text(encoding="utf-8")

    # 找不到：给出有用的错误信息
    available = [t["name"] for t in TEMPLATE_REGISTRY]
    raise FileNotFoundError(
        f"模板不存在：{template_name!r}\n"
        f"可用模板：{available}\n"
        f"模板目录：{_TEMPLATE_DIR}\n"
        f"新增模板：在 {_TEMPLATE_DIR}/<name>/ 下放 index.html + meta.json 即可"
    )


def write_h5_template(template_name: str, html_content: str) -> dict:
    """
    将 HTML 内容写入模板文件（新建或覆盖）。
    统一写到子目录结构：{name}/index.html
    用于 LLM 定制模板、保存新主题。

    template_name: 模板名称（不含 .html 后缀）
    html_content:  完整 HTML 字符串（应含 {{VAR}} 占位符）
    返回: {"path": str, "name": str, "size_kb": float, "variables": list[str]}
    """
    safe_name = re.sub(r"[^\w\-]", "_", template_name)
    tpl_dir   = _TEMPLATE_DIR / safe_name
    tpl_dir.mkdir(parents=True, exist_ok=True)

    index_path = tpl_dir / "index.html"
    index_path.write_text(html_content, encoding="utf-8")

    variables = sorted(set(re.findall(r"\{\{(\w+)\}\}", html_content)))

    # 若无 meta.json，自动生成最小版本
    meta_path = tpl_dir / "meta.json"
    if not meta_path.exists():
        meta_path.write_text(
            json.dumps({
                "name":        safe_name,
                "label":       safe_name,
                "desc":        "自定义模板",
                "mood":        "",
                "intent_keys": [safe_name],
                "extra_vars":  {},
                "theme_preset": {}
            }, ensure_ascii=False, indent=2),
            encoding="utf-8"
        )

    # 热重载注册表
    reload_template_registry()

    return {
        "path":      str(index_path),
        "name":      safe_name,
        "size_kb":   round(len(html_content.encode("utf-8")) / 1024, 1),
        "variables": variables,
    }


def render_h5_template(template_html: str, variables: dict[str, Any]) -> str:
    """
    将模板 HTML 中的 {{VAR}} 占位符替换为实际值。
    未提供的变量保留原始 {{VAR}} 形式，方便调试。
    渲染完成后自动剥除模板头部的 EP-Agent 内部注释（对用户无意义）。
    """
    result = template_html
    for key, value in variables.items():
        result = result.replace(f"{{{{{key}}}}}", str(value) if value is not None else "")
    # 剥除 <!-- EP-Agent ... --> 内部注释块，避免暴露模板变量名等内部信息
    result = re.sub(r'<!--\s*EP-Agent.*?-->', '', result, flags=re.DOTALL)
    return result


def list_template_files() -> list[dict]:
    """
    返回所有已注册模板列表（含元数据、占位符变量列表、assets 列表）。
    结果直接来自内存注册表，调用极快。
    若需感知最新文件系统变化，先调用 reload_template_registry()。
    """
    result = []
    for t in TEMPLATE_REGISTRY:
        entry = {k: v for k, v in t.items() if k != "path"}  # 不暴露服务器绝对路径
        # 按需扫描占位符（读文件一次，结果缓存在 entry 中）
        if "variables" not in entry:
            try:
                html = read_h5_template(t["name"])
                entry["variables"] = sorted(set(re.findall(r"\{\{(\w+)\}\}", html)))
            except Exception:
                entry["variables"] = []
        result.append(entry)
    return result


# ═══════════════════════════════════════════════════════════════════════════════
# 变量构建器 — 轻量注入，只替换展示变量，不注入大数据
# ═══════════════════════════════════════════════════════════════════════════════

def build_template_vars(
    title:         str,
    notes:         list[dict],   # 音符数据，注入 NOTES_JSON 供可视化使用
    theme:         str  = "apple",
    abc_content:   str  = "",
    bpm:           int  = 120,
    key:           str  = "C",
    composer:      str  = "",
    extra_info:    str  = "",
    midi_url:      str  = "",
    video_url:     str  = "",
    source_format: str  = "abc",
    extra_vars:    dict | None = None,  # 模板专属变量（从 meta.json extra_vars 声明）
) -> dict[str, str]:
    """
    构建模板变量字典——只注入轻量展示变量。

    主题预设从 meta.json 的 theme_preset 读取（动态，无硬编码）。
    模板专属变量通过 extra_vars 字典传入，key 对应模板 {{VAR}} 占位符。
    meta.json extra_vars 声明了该模板支持哪些专属变量及其默认值描述，
    实际默认值在此处按变量名推断（空字符串兜底，模板 HTML 自行处理缺省显示）。
    注入 NOTES_JSON（最多 800 条，供前端音符可视化使用）。
    """
    extra_vars = extra_vars or {}

    # 格式标签
    format_labels  = {"midi": "MIDI", "abc": "ABC Notation", "sky_json": "Sky JSON"}
    format_label   = format_labels.get(source_format, source_format.upper())

    # 主题预设（优先从注册表读，fallback 到兜底）
    resolved_theme = _LEGACY_ALIASES.get(theme, theme)
    preset         = _PRESET_MAP.get(resolved_theme, _FALLBACK_PRESET)

    _title    = title    or "未命名乐曲"
    _composer = composer or "EP-Agent"

    # ABC 内容（JS 字符串安全转义）
    abc_escaped = json.dumps(abc_content)[1:-1] if abc_content else ""

    # ABC 区块（有内容才渲染）
    abc_section = (
        f'<div class="abc-section card" data-card>'
        f'<div class="sec-label">🎼 乐谱</div>'
        f'<div class="abc-wrap"><div id="abcContainer"></div></div>'
        f'</div>'
        if abc_content and abc_content.strip() else ""
    )

    # 视频区块
    video_section = (
        f'<div class="card" data-card>'
        f'<div class="sec-label">🎬 视频</div>'
        f'<div style="border-radius:14px;overflow:hidden;margin-top:12px;">'
        f'<video controls playsinline style="width:100%;border-radius:14px;" src="{video_url}"></video>'
        f'</div></div>'
        if video_url and video_url.strip() else ""
    )

    # 额外信息区块
    extra_html = (
        f'<div class="card" data-card>'
        f'<p style="opacity:0.75;font-size:0.9em;line-height:1.6;">{extra_info}</p>'
        f'</div>'
        if extra_info and extra_info.strip() else ""
    )

    # ── 模板专属变量：从注册表读取该模板声明的 extra_vars 键名，
    # 按调用方传入的 extra_vars dict 填充，未传则用空字符串（模板自行处理缺省）
    template_entry  = _REGISTRY_BY_NAME.get(resolved_theme, {})
    declared_extras = template_entry.get("extra_vars", {})  # {"LYRIC_LINE": "说明", ...}
    resolved_extras = {k: str(extra_vars.get(k, "")) for k in declared_extras}

    base_vars = {
        # 内容展示
        "TITLE":        _title,
        "COMPOSER":     _composer,
        "BPM":          str(bpm),
        "KEY":          key,
        "FORMAT_LABEL": format_label,
        "THEME":        theme,

        # MIDI 相对路径
        "MIDI_URL":     midi_url or "",

        # ABC
        "ABC_CONTENT":  abc_escaped,
        "ABC_SECTION":  abc_section,

        # 样式（来自 meta.json theme_preset，动态读取）
        "ACCENT_COLOR":   preset["ACCENT_COLOR"],
        "BG_COLOR":       preset["BG_COLOR"],
        "CARD_BG":        preset["CARD_BG"],
        "TEXT_COLOR":     preset["TEXT_COLOR"],
        "TEXT_SUB":       preset["TEXT_SUB"],
        "GRADIENT":       preset["GRADIENT"],
        "ABC_SVG_FILTER": preset["ABC_SVG_FILTER"],

        # 扩展
        "VIDEO_URL":     video_url    or "",
        "VIDEO_SECTION": video_section,
        "EXTRA_INFO":    extra_info   or "",
        "EXTRA_HTML":    extra_html,

        # 音符数据（最多 800 条，供前端可视化使用）
        "NOTES_JSON": json.dumps(notes[:800], ensure_ascii=False) if notes else "[]",

        # 文档占位（渲染时清空）
        "VAR": "",
    }

    # 模板专属变量覆盖（动态，无硬编码）
    base_vars.update(resolved_extras)
    # 调用方直接传入的任意额外变量（优先级最高）
    for k, v in extra_vars.items():
        base_vars[k] = str(v)

    return base_vars


# ═══════════════════════════════════════════════════════════════════════════════
# 一体化便捷函数
# ═══════════════════════════════════════════════════════════════════════════════

def render_score_to_h5(
    title:         str,
    notes:         list[dict],
    theme:         str        = "apple",
    abc_content:   str        = "",
    bpm:           int        = 120,
    key:           str        = "C",
    composer:      str        = "",
    extra_info:    str        = "",
    midi_url:      str        = "",
    video_url:     str        = "",
    source_format: str        = "abc",
    extra_vars:    dict | None = None,  # 模板专属变量，如 {"LYRIC_LINE": "..."}
) -> str:
    """
    一步完成：读取模板 → 构建变量 → 渲染 → 返回完整 HTML。
    theme 对应 h5-templates/{theme}/index.html。
    模板专属变量通过 extra_vars 传入，key 由 meta.json extra_vars 字段声明。
    新增模板无需修改此函数。
    """
    template_html = read_h5_template(theme)
    variables     = build_template_vars(
        title=title, notes=notes, theme=theme,
        abc_content=abc_content, bpm=bpm, key=key,
        composer=composer, extra_info=extra_info,
        midi_url=midi_url, video_url=video_url,
        source_format=source_format,
        extra_vars=extra_vars,
    )
    return render_h5_template(template_html, variables)
