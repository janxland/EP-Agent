"""
IntentRouter — 意图路由（轻量 LLM，快速冷启动）

职责（单一）：
  - 调用轻量 LLM 识别用户意图域（domain）
  - 注入对话历史上下文（context_summary）
  - 检测链式意图（chain_intents）
  - 关键词快速匹配（_detect_chain_intent，无需额外 LLM 调用）

意图域（单一来源：domain_config.py）：
  convert  — Sky JSON → ABC 转换
  edit     — 修改已有谱子
  create   — 从零创作 ABC
  audio    — 生成/迭代音频
  voice    — 音色克隆/TTS
  query    — 查询/分析谱子信息
  sovits   — GPT-SoVITS 音色克隆/TTS（需配置 SOVITS_BASE_URL）

⚠️ 修改意图域定义请到 domain_config.py，此文件通过 build_router_prompt() 动态读取，
   无需在此处手动同步。
"""
from __future__ import annotations

import json
import re
from typing import Callable, Awaitable

from app.agentcore.llm import complete
from app.agentcore.domain_config import build_router_prompt
from app.agentcore.role_config import get_role_domains, build_role_system_prompt

Publisher = Callable[[str, dict], Awaitable[None]]

# ── 路由 LLM Prompt（意图域描述由 domain_config 动态生成）────────────────────

def _build_router_system(role_id: str | None = None) -> str:
    """
    动态构建路由器 system prompt。
    - 意图域描述从 domain_config.build_router_prompt() 读取
    - 角色专属 prompt 从 role_config.build_role_system_prompt() 注入
    - 角色域过滤：只列出该角色擅长的意图域，减少路由歧义
    """
    from app.agentcore.domain_config import DOMAIN_CONFIG
    # 获取该角色允许的域（过滤后只列出角色擅长的域）
    allowed_domains = get_role_domains(role_id)
    role_prompt     = build_role_system_prompt(role_id)

    # 只为该角色的域生成描述段落
    lines = []
    for name, d in DOMAIN_CONFIG.items():
        if not d.enabled:
            continue
        if name not in allowed_domains:
            continue
        desc_lines = d.description.strip().splitlines()
        first = desc_lines[0]
        rest  = "\n".join(f"    {l}" for l in desc_lines[1:])
        entry = f"- {d.name:<10}: {first}"
        if rest:
            entry += "\n" + rest
        lines.append(entry)
    domain_section  = "\n".join(lines)
    allowed_str     = "|".join(allowed_domains)

    return f"""{role_prompt}

你是 EP-Agent 的意图路由器。分析用户消息，输出 JSON 路由决策。

当前角色可处理的意图域（按优先级判断）：
{domain_section}

⚠️ 特别注意 convert 域（若在上方列出）：
  - Sky 谱子常以 .txt 格式导出，不要因为扩展名是 .txt 就误判为 create！
  - 只要附件内容含有 songNotes 字段，无论扩展名，必须路由到 convert！

输出严格 JSON，不要任何其他文字：
{{
  "domain": "{allowed_str}",
  "confidence": 0.0-1.0,
  "has_attachment": true/false,
  "attachment_type": "sky_json|text|midi|audio|none",
  "chain_intents": [],
  "summary": "一句话说明路由决策"
}}

chain_intents：若用户一句话包含多个意图，按执行顺序列出，如 ["convert","edit"]。
"""

# 链式意图关键词（确定性匹配，不调 LLM）
# ⚠️ 时长关键词 & 创作型关键词优先级高于 edit，匹配到则强制 create
_DURATION_KWS = ["分钟", "秒钟", "秒长", "1分钟", "2分钟", "3分钟", "5分钟",
                 "一分钟", "两分钟", "三分钟", "五分钟", "x分钟"]
_CREATIVE_KWS = ["写一个", "写一首", "创作", "重写", "改编成", "变成", "写成",
                 "流行歌旋律", "流行旋律", "新旋律", "新谱子", "全新",
                 "做一段", "生成旋律", "写旋律"]
_EDIT_KWS     = ["转调", "升调", "降调", "加快", "放慢", "加花", "简化",
                 "改成X调", "移到", "BPM", "速度", "拍号"]
_CREATE_KWS   = ["写", "创作", "生成", "做一段", "3分钟", "一分钟",
                 "流行", "爵士", "古典", "中国风", "新旋律"]


async def route_intent(
    message: str,
    attachment_name: str,
    attachment_preview: str,
    has_score: bool,
    context_summary: str = "",
    role_id: str | None = None,
) -> dict:
    """
    调用轻量 LLM 识别意图域，注入对话历史上下文。

    返回：
      {
        "domain": str,
        "confidence": float,
        "chain_intents": [...],
        "summary": str,
        ...
      }
    """
    context_parts = [f"用户消息：{message}"]
    if attachment_name:
        context_parts.append(f"附件名称：{attachment_name}")
    if attachment_preview:
        context_parts.append(f"附件内容预览（前500字）：\n{attachment_preview[:500]}")
    context_parts.append(f"当前 session 是否已有谱子：{'是' if has_score else '否'}")
    if context_summary:
        context_parts.append(f"历史操作摘要：{context_summary}")

    resp = await complete([
        {"role": "system", "content": _build_router_system(role_id)},
        {"role": "user",   "content": "\n".join(context_parts)},
    ])
    raw = resp if isinstance(resp, str) else resp.get("content", "{}")
    m = re.search(r'\{.*\}', raw, re.DOTALL)
    if m:
        try:
            return json.loads(m.group())
        except Exception:
            pass
    # 兜底路由（优先选角色允许的域）
    allowed = get_role_domains(role_id)
    fallback = "edit" if (has_score and "edit" in allowed) else (
        "create" if "create" in allowed else (allowed[0] if allowed else "query")
    )
    return {
        "domain":        fallback,
        "confidence":    0.5,
        "chain_intents": [],
        "summary":       "兜底路由",
    }


def detect_chain_intent(message: str, chain_intents: list[str]) -> str:
    """
    检测 convert 后是否有额外意图。
    优先使用路由 LLM 已返回的 chain_intents，
    无则用关键词快速匹配，避免额外 LLM 调用。
    返回 "edit" | "create" | "none"

    优先级：时长词 > 创作词 > edit词 > create词
    时长词和创作词命中 → 强制 create（不被 edit 词干扰）
    """
    if len(chain_intents) > 1:
        second = chain_intents[1]
        if second in ("edit", "create"):
            return second

    # 时长词 / 创作型词 → 强制 create（优先级最高）
    if any(kw in message for kw in _DURATION_KWS):
        return "create"
    if any(kw in message for kw in _CREATIVE_KWS):
        return "create"
    # 纯参数修改词 → edit
    if any(kw in message for kw in _EDIT_KWS):
        return "edit"
    # 宽泛创作词 → create
    if any(kw in message for kw in _CREATE_KWS):
        return "create"
    return "none"
