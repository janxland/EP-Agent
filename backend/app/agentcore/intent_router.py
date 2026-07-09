"""
IntentRouter — 意图路由（轻量 LLM，快速冷启动）

职责（单一）：
  - 调用轻量 LLM 识别用户意图域（domain）
  - 注入对话历史上下文（context_summary）
  - 检测链式意图（chain_intents）
  - 关键词快速匹配（_detect_chain_intent，无需额外 LLM 调用）

意图域（单一来源：domain_config.py）：
意图域定义见 domain_config.py，此文件通过 build_router_prompt() 动态读取。
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
  - 附件是 .abc 文件（ABC Notation 格式）时，也必须路由到 convert！
  - ABC 文件含有 X:/T:/K: 等字段，是乐谱格式，不是普通文本！

⚠️ 特别注意 audio 域（若在上方列出）：
  - 用户说「生成一首歌」「帮我生成音乐」「用 MiniMax/Suno 生成」→ 必须路由到 audio！
  - 消息里含有 ABC 旋律内容（如 z8|[DA_g]4 等符号）且用户要求「生成音乐/歌曲」→ 仍是 audio！
  - ABC 内容是「旋律参考」，不是「附件」，不要因为有 ABC 符号就误判为 voice 或 convert！
  - 只有用户明确说「克隆音色」「用XX的声音」「参考音频合成语音」才是 voice！

⚠️ 特别注意「转 MIDI」请求：
  - 若 session 已有谱子（has_score=true）且用户说「转 MIDI/导出 MIDI」→ 路由到 edit！
  - edit 域的 Agent 有 abc_to_midi 工具可直接完成转换！
  - 若附件是 ABC 文件且用户说「转 MIDI」→ chain_intents=["convert","edit"]！

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
⚠️ 特别注意：用户上传了 .txt 附件并要求创作/改编时，必须返回 chain_intents=["convert","create"]！
  例：「晚安喵.txt 转化成ABC然后改写成1分钟抒情流行」→ 先 convert，再 create
  例：「上传谱子，转成MIDI」→ 先 convert，再 edit
"""

# 链式意图关键词（确定性匹配，不调 LLM）
# 优先级：H5词 > MIDI词 > 时长词 > 创作词 > edit词 > create词
_H5_KWS       = ["h5", "H5", "html", "HTML", "网页", "页面", "播放器",
                 "播放midi", "播放MIDI", "midi播放", "MIDI播放",
                 "海报", "分享页", "乐谱页面", "生成页面"]
_MIDI_KWS     = ["转midi", "转MIDI", "导出midi", "导出MIDI",
                 "转成midi", "转成MIDI", "转为midi", "转为MIDI",
                 "export midi", "to midi", "转mid", "导出mid"]
# 注意：「生成MIDI」不在此列——「生成钢琴曲ABC(2) ABC(2)转到MIDI」里的「生成」是创作意图
# 只有「转MIDI」「导出MIDI」等明确转换词才触发 edit 链式意图
_DURATION_KWS = ["分钟", "秒钟", "秒长", "一分钟", "两分钟", "三分钟", "五分钟"]
_CREATIVE_KWS = ["写一首", "创作", "重写", "改编成", "新旋律", "新谱子", "写旋律"]
_EDIT_KWS     = ["转调", "升调", "降调", "加快", "放慢", "加花", "简化", "BPM", "拍号"]
_CREATE_KWS   = ["写", "创作", "生成", "流行", "爵士", "古典", "中国风"]
# FIX-BUG-4-2: 兜底：当 router 返回空 chain_intents，但用户有 .txt 附件+创作词时，补充 convert
_SKY_ATTACH_WITH_CREATE_KWS = ["转化成", "改写", "改编成", "新旋律", "新谱子", "生成ABC", "创作旋律"]


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
    ], tier="lite")  # M1: 意图路由用轻量模型，降低成本
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


def detect_chain_intent(message: str, chain_intents: list[str], attachment_name: str = "") -> str:
    """
    检测 convert 后是否有额外意图。
    优先使用路由 LLM 已返回的 chain_intents，
    无则用关键词快速匹配，避免额外 LLM 调用。
    返回 "h5_create" | "edit" | "create" | "none"

    优先级：H5词 > 时长词 > 创作词 > edit词 > create词
    """
    # FIX-BUG-4: 当 router 已返回多个 chain_intents 时，取第一个（主要意图）。
    # 例：router 返回 ["convert","edit"] → 应从 "convert" 开始链路，而不是 "edit"。
    # 第一个意图是用户话语的核心操作（"转化成ABC"），后续是附加请求。
    if len(chain_intents) > 1:
        return chain_intents[0]

    # FIX-BUG-4-2: 兜底：当 router 返回空 chain_intents，但用户有 .txt 附件+创作词时，
    # 说明 router LLM 没有正确识别出 convert 意图，必须强制走 convert。
    # 关键：这里必须返回 "convert"，不能返回 "create"！
    # - 返回 "convert" → _dispatch 进入 convert 分支 → convert 步骤执行 → abc_notation 存入 session
    # - 返回 "create"  → _dispatch 直接 return，convert 不执行，session 里没有 abc_notation，链路彻底断裂
    #
    # 流程：Step 1 (convert) → detect_chain_intent 再调用 → 命中创作词 → 返回 "create" → Step 2 (create)
    # Step 1 时 message 仍含创作词，会再次命中兜底 → 返回 "convert"（不在合法链式意图中）→ 不进入 if，return result
    # Step 2 时 session 已有 abc_notation，CreateAgent 用它作为 base_abc → 正确注入原谱数据
    if attachment_name and attachment_name.lower().endswith(".txt"):
        # 检查用户是否在要求创作/改写操作
        msg_lower = message.lower()
        has_create_word = any(kw in msg_lower for kw in
            _SKY_ATTACH_WITH_CREATE_KWS + _CREATIVE_KWS + _DURATION_KWS)
        if has_create_word:
            return "convert"

    msg_lower = message.lower()
    # H5/HTML/播放器词 → h5_create（优先级最高）
    if any(kw.lower() in msg_lower for kw in _H5_KWS):
        return "h5_create"
    # MIDI 转换词 → edit（edit 域有 abc_to_midi 工具）
    if any(kw.lower() in msg_lower for kw in _MIDI_KWS):
        return "edit"
    # 时长词 / 创作型词 → 强制 create
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
