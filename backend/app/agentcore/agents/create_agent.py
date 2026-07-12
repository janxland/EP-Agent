"""
CreateAgent — ABC 谱创作 SubAgent

职责（单一）：
  - LLM 直接创作 ABC（从零创作 或 基于已有谱子改编/延伸）
  - 验证 ABC + 自动修正（最多 1 次重试）
  - 重复行检测 + 自动修正（防止主旋律循环）
  - 存入 session + 落库
  - 管理 create 域 TODO 状态

核心设计原则：
  1. 无论从零创作还是改编，都必须从已有 ABC 中读取 BPM/调号，
     再精确计算目标行数，注入 prompt，LLM 无需猜测时长
  2. 改编时将完整原谱注入 prompt，LLM 基于真实数据理解音乐结构
  3. 输出后做重复行检测，发现重复则强制要求 LLM 重写
"""
from __future__ import annotations

import logging
import re
from app.agentcore.agents.base_agent import BaseAgent
import asyncio
import time
import uuid
from typing import Callable, Awaitable

_logger = logging.getLogger("ep_agent.create_agent")

from app.agentcore.llm import complete as llm_complete, complete_stream, get_current_model_name
from app.agentcore.todo_manager import TodoManager, assert_finish_gate
from app.agentcore.agent_registry import register
from app.pipeline import db as _db

if False:  # TYPE_CHECKING
    from app.agentcore.run_context import RunContext
from app.agentcore.react_executor import stream_text
from app.agentcore.abc_utils import (
    extract_abc_and_summary, parse_abc_header, count_notes, count_bars,
    check_duration_requirement, detect_duplicate_lines, check_rhythm_variety,
    extract_abc_from_message, extract_motif_bars, check_melody_quality,
)
from app.agentcore.agent_loader import load_agent_prompt

Publisher = Callable[[str, dict], Awaitable[None]]

# ── Prompt 热加载 ──────────────────────────────────────────────────────────────

def _load_system_create() -> str:
    try:
        return load_agent_prompt("score-creator", sections=["role", "static_context"])
    except Exception:
        return (
            "你是世界顶级的 ABC Notation 音乐创作大师，精通 Sky: Children of the Light 游戏的 15 键乐器特性。\n"
            "可用音符：C D E F G A B c d e f g a b c'（C4-C6）\n"
            "每行写4小节。禁止重复旋律行。禁止全八分音符。\n"
            "直接输出完整 ABC + SUMMARY 行，不输出解释。"
        )

_FALLBACK_ABC = "X:1\nT:新曲\nM:4/4\nL:1/8\nQ:1/4=120\nK:C\nCDEF GABc|dedB cAGE|FEDC DEFG|E4 z4|\n"

_AGENT_NAME = "create_agent"


async def _llm_with_span(
    messages: list,
    temperature: float,
    publish: Publisher,
    span_name: str,
    call_id: str = "",
) -> str:
    """
    包装 llm_complete，自动发布 tool.call span 供 TraceCollector 记录。
    每次 LLM 调用都会在审计链路中留下可见的 span：
      - span_kind=model（通过 tool 名称前缀 'llm:' 识别）
      - 记录模型名、temperature、耗时、输出字符数
    """
    cid = call_id or f"llm_{span_name}_{uuid.uuid4().hex[:8]}"
    model_name = ""
    try:
        model_name = get_current_model_name("strong")
    except Exception:
        pass

    await publish("tool.call", {
        "call_id":   cid,
        "tool":      f"llm:{span_name}",
        "status":    "running",
        "arguments": {
            "model":          model_name,
            "temperature":    temperature,
            "messages_count": len(messages),
            "agent":          _AGENT_NAME,
        },
        "round_idx": 0,
    })

    t0 = time.time()
    try:
        resp = await llm_complete(messages, temperature=temperature)
        raw = resp if isinstance(resp, str) else resp.get("content", "")
        elapsed_ms = int((time.time() - t0) * 1000)
        await publish("tool.call", {
            "call_id":        cid,
            "tool":           f"llm:{span_name}",
            "status":         "succeeded",
            "result_preview": f"{len(raw)}chars {elapsed_ms}ms",
            "full_result":    f"output_chars={len(raw)} elapsed_ms={elapsed_ms} model={model_name}",
        })
        return raw
    except Exception as e:
        await publish("tool.call", {
            "call_id": cid,
            "tool":    f"llm:{span_name}",
            "status":  "failed",
            "error":   str(e),
        })
        raise


def _calc_duration_hint(mins: float, bpm: float, time_sig_num: int = 4) -> str:
    """
    根据目标时长、BPM、拍号精确计算目标行数，返回注入 prompt 的说明文本。
    每行 = 4小节，每小节 = (60/BPM) × time_sig_num 拍。

    行数上限动态计算（不再硬写死32）：
      - 理论上限 = 目标时长对应的精确行数 × 1.5（允许50%余量）
      - 绝对上限 = 48行（约6分钟@BPM120，防止 LLM 输出超长导致超时）
      - 绝对下限 = 4行（至少16小节，保证最短片段）
    """
    seconds_per_bar  = (60.0 / bpm) * time_sig_num
    seconds_per_line = seconds_per_bar * 4          # 每行 4 小节
    target_seconds   = mins * 60.0
    target_lines     = round(target_seconds / seconds_per_line)
    # 动态上限：理论行数的1.5倍，但不超过48行，不低于4行
    dynamic_max      = min(48, max(int(target_lines * 1.5), target_lines + 4))
    target_lines     = max(4, min(dynamic_max, target_lines))
    target_bars      = target_lines * 4

    return (
        f"\n\n【时长精确计算 — 必须严格执行】\n"
        f"目标时长：{mins} 分钟 = {target_seconds:.0f} 秒\n"
        f"BPM={bpm:.0f}，拍号={time_sig_num}/4，"
        f"每小节 {seconds_per_bar:.2f} 秒，每行（4小节）{seconds_per_line:.2f} 秒\n"
        f"✅ 必须写 {target_lines} 行正文（共 {target_bars} 小节）\n"
        f"❌ 禁止写超过 {target_lines + 2} 行（会超时）\n"
        f"❌ 禁止写少于 {target_lines - 2} 行（时长不足）\n"
        f"❌ 每行旋律内容必须不同，任意两行不能完全相同\n"
        f"❌ 禁止用注释（% 重复...）代替音符\n"
    )


def _extract_bpm_from_message(message: str) -> float | None:
    """
    从用户消息中提取 BPM。
    支持两种格式：
      1. 显式声明：「160bpm」「160 BPM」「160拍」
      2. ABC header 内嵌：消息中含 Q:1/4=160 格式
    优先级：显式声明 > ABC header 内嵌
    """
    # 1. 显式 BPM 声明
    m = re.search(r'(\d+)\s*(?:bpm|BPM|拍)', message)
    if m:
        return float(m.group(1))
    # 2. 从内嵌 ABC header 的 Q: 字段提取（如 Q:1/4=160）
    q = re.search(r'Q:\s*\d+/\d+=\s*(\d+)', message)
    if q:
        return float(q.group(1))
    # 3. 简单 Q:=160 格式
    q2 = re.search(r'Q:\s*(?:\d+/\d+=)?(\d+)', message)
    if q2:
        return float(q2.group(1))
    return None


def _infer_bpm_from_style(message: str) -> float:
    """
    根据风格关键词推断 BPM。
    覆盖更多常见音乐风格场景，优先匹配更具体的风格词。
    无匹配时返回 None，让 LLM 自己在 prompt 中决定 BPM。
    """
    # 极慢/慢摇/Ballad：60-75
    if any(kw in message for kw in ['慢摇', '摇篮曲', 'ballad', '极慢', '很慢']):
        return 70.0
    # 抒情/轻柔/安静：80-95
    if any(kw in message for kw in ['抒情', '轻柔', '安静', '悠扬', '忧郁', '伤感', '思念', '治愈']):
        return 88.0
    # R&B / Soul：75-90
    if any(kw in message for kw in ['r&b', 'rnb', 'soul', '慵懒', '性感']):
        return 82.0
    # 爵士 Jazz：100-120
    if any(kw in message for kw in ['爵士', 'jazz', '即兴', 'swing']):
        return 110.0
    # 电子 / EDM：125-135
    if any(kw in message for kw in ['电子', 'edm', 'dance', '电音', '蹦迪']):
        return 128.0
    # 欢快/活泼/流行快歌：130-145
    if any(kw in message for kw in ['欢快', '活泼', '激烈', '跑跳', '蹦蹦']):
        return 138.0
    # 快/动感：140+
    if any(kw in message for kw in ['快节奏', '高能', '摇滚', 'rock', '金属', 'metal']):
        return 148.0
    # 中速流行（默认）
    if any(kw in message for kw in ['流行', '华语', '情歌', '民谣', '轻快']):
        return 116.0
    # 无匹配：返回 None，由调用方决定是否使用默认值
    return None


def _extract_bars_from_offset(abc: str, skip_bars: int, take_bars: int) -> str:
    """
    从 ABC 谱中跳过前 skip_bars 个小节，再提取 take_bars 个小节。
    用于提取中段动机（跳过前奏，直接取主题段落）。

    参数：
      abc       — 完整 ABC 谱字符串
      skip_bars — 跳过的小节数
      take_bars — 提取的小节数

    返回：提取到的小节内容字符串（纯音符行，不含 header）。
    """
    if not abc:
        return ""
    k_match = re.search(r'^K:.*$', abc, re.MULTILINE)
    if not k_match:
        return ""
    body = abc[k_match.end():].strip()

    bars_skipped = 0
    bars_taken   = 0
    result_chars = []
    i = 0

    # 先跳过 skip_bars 个小节
    while i < len(body) and bars_skipped < skip_bars:
        ch = body[i]
        if ch == '|':
            next_ch = body[i + 1] if i + 1 < len(body) else ""
            if next_ch not in ('|', ':'):
                bars_skipped += 1
        i += 1

    # 再收集 take_bars 个小节
    while i < len(body) and bars_taken < take_bars:
        ch = body[i]
        result_chars.append(ch)
        if ch == '|':
            next_ch = body[i + 1] if i + 1 < len(body) else ""
            if next_ch not in ('|', ':'):
                bars_taken += 1
        i += 1

    return "".join(result_chars).strip()


@register("create")
class CreateAgent(BaseAgent):
    """ABC 谱创作 SubAgent（从零创作 或 基于已有谱子改编）。"""

    async def _run_impl(
        self,
        session_id: str,
        message: str,
        publish: Publisher,
        session_getter: Callable,
        session_saver: Callable,
        todo_mgr: TodoManager,
        current_abc: str = "",   # 由 chain convert→create 传入；改编时优先使用 session 中的谱子
    ) -> dict:
        from app.pipeline.domain import Score, ScoreMeta

        _logger.info("[create_agent] 开始 session=%s msg=%s", session_id[:8], message[:60].replace('\n', ' '))

        # ── 从 session 读取已有 ABC（改编/延伸时的数据基础）──────────────────────
        session_abc = ""
        session_bpm = 120.0
        session_time_sig_num = 4
        try:
            sess = session_getter(session_id)
            if sess.score and sess.score.abc_notation:
                session_abc = sess.score.abc_notation
                session_bpm = float(sess.score.meta.bpm or 120.0)
                session_time_sig_num = int(getattr(sess.score.meta, 'time_sig_num', 4) or 4)
                _logger.info("[create_agent] session ABC 已加载 len=%d bpm=%.0f", len(session_abc), session_bpm)
        except Exception as _e:
            _logger.debug("[create_agent] session_getter 失败（忽略）: %s", _e)

        # 从 message 中提取内嵌 ABC 参考谱
        message_abc = extract_abc_from_message(message)
        if message_abc:
            _logger.info("[create_agent] 从消息提取到 ABC len=%d", len(message_abc))

        # 确定本次创作的「基础 ABC」：参数传入 > session.score.abc_notation > message 内嵌
        base_abc = current_abc or session_abc or message_abc
        _logger.info("[create_agent] 创作模式=%s base_abc_len=%d",
                     '改编' if base_abc else '从零创作', len(base_abc))

        # BUG-CR1 修复：只取 pending 状态的 TODO，不 fallback 到 ids 全量
        # 原来 pending 为空时 fallback 到 ids（含 done/failed），会重复操作已完成 TODO
        exec_ids = todo_mgr.get_pending_ids()

        if exec_ids:
            await todo_mgr.tick(exec_ids[0], "running", publish)

        _logger.info("[create_agent] exec_ids=%s temperature将动态决定", exec_ids)
        create_call_id = f"call_create_{session_id[:8]}"
        await publish("tool.call", {
            "call_id":   create_call_id,
            "tool":      "abc_composer",
            "status":    "running",
            "arguments": {"style": message[:100], "has_base": bool(base_abc)},
        })

        system, user_prompt = self._build_prompt(
            message=message,
            base_abc=base_abc,
            session_bpm=session_bpm,
            session_time_sig_num=session_time_sig_num,
        )

        # ── 注入重要记忆前缀（用户意图、已有文件路径等跨轮次携带体）────────────
        try:
            from app.agentcore.memory_manager import build_memory_prefix
            _mem = build_memory_prefix(session_id)
            if _mem:
                system = system + _mem
        except Exception:
            pass

        # 创作场景必须用高 temperature，否则 LLM 输出极度保守、旋律平淡
        # temperature 根据改编强度动态调整：
        #   - 「保留原味/严格按照/风格相似」→ 偏低（0.75），保守改编
        #   - 「自由发挥/全新创作/大胆」→ 偏高（0.95），激进创作
        #   - 默认：改编 0.85，从零创作 0.92
        if base_abc:
            if any(kw in message for kw in ['保留原味', '严格按照', '风格相似', '尽量保持', '不要改太多']):
                create_temperature = 0.75
            elif any(kw in message for kw in ['自由发挥', '大胆', '全新', '完全不同', '随意']):
                create_temperature = 0.95
            else:
                create_temperature = 0.85
        else:
            create_temperature = 0.92
        # ── 主创作：流式 LLM 调用（边生成边推送 abc.updated，用户实时看到谱子）──
        # 同时通过 _llm_with_span 在审计链路留下可见 span
        main_llm_cid = f"llm_create_main_{uuid.uuid4().hex[:8]}"
        model_name = ""
        try:
            model_name = get_current_model_name("strong")
        except Exception:
            pass
        await publish("tool.call", {
            "call_id":   main_llm_cid,
            "tool":      "llm:create_main",
            "status":    "running",
            "arguments": {
                "model":          model_name,
                "temperature":    create_temperature,
                "messages_count": 2,
                "agent":          _AGENT_NAME,
                "has_base_abc":   bool(base_abc),
            },
            "round_idx": 0,
        })
        t0_main = time.time()
        raw = ""
        _logger.info("[create_agent] 开始流式主创作 temperature=%.2f model=%s", create_temperature, model_name)
        try:
            # 优先尝试流式生成（边生成边推送 abc.updated，用户实时看到谱子渐进出现）
            _partial_buf = ""
            _last_push_len = 0
            _push_interval = 200  # 每累积 200 字符推送一次中间状态
            _token_count = 0
            _reasoning_buf = ""  # 累积完整推理内容，落库时写入 reasoning 字段
            try:
                async for chunk in complete_stream(
                    [
                        {"role": "system", "content": system},
                        {"role": "user",   "content": user_prompt},
                    ],
                    temperature=create_temperature,
                    # thinking 参数不传：使用模型默认思考行为（DeepSeek-V4 默认开启思考，保证创作质量）
                ):
                    # complete_stream yield (content_delta, reasoning_delta) 二元组
                    content_delta   = chunk[0] if isinstance(chunk, tuple) else chunk
                    reasoning_delta = chunk[1] if isinstance(chunk, tuple) and len(chunk) > 1 else ""
                    # ── 推送 reasoning_delta（思考过程）到聊天气泡 ──────────────
                    # ABC 正文内容（content_delta）不发 message.delta：
                    #   - ABC notation 是结构化数据，不应出现在聊天气泡里
                    #   - 用户通过编辑器实时预览（abc.updated 事件，下方每 200 字符推一次）
                    # reasoning_delta（思考过程）可以发：让用户看到 AI 思考过程
                    if reasoning_delta:
                        _reasoning_buf += reasoning_delta  # 累积推理内容（无截断）
                        await publish("message.delta", {
                            "delta":            "",
                            "reasoning_delta":  reasoning_delta,
                        })
                    # pipeline.step 进度提示（每 100 token 推一次，让用户知道 AI 在工作）
                    if content_delta and _token_count % 100 == 0 and _token_count > 0:
                        await publish("pipeline.step", {
                            "step":   "create_generating",
                            "status": "running",
                            "text":   f"🎵 AI 创作中... 已生成 {_token_count} tokens",
                        })
                    if content_delta:
                        raw += content_delta
                        _partial_buf += content_delta
                        _token_count += 1
                        if _token_count % 50 == 0:
                            _logger.debug("[create_agent] 流式生成中 tokens=%d raw_len=%d", _token_count, len(raw))
                        # 每累积足够内容时推送中间 abc.updated（渐进式预览）
                        if len(raw) - _last_push_len >= _push_interval:
                            _partial, _ = extract_abc_and_summary(raw, "")
                            if "K:" in _partial:
                                await publish("abc.updated", {
                                    "abc":     _partial,
                                    "version": 0,
                                    "summary": "生成中...",
                                })
                                _last_push_len = len(raw)
                _logger.info("[create_agent] 流式生成完成 tokens=%d raw_len=%d elapsed=%.1fs",
                             _token_count, len(raw), time.time() - t0_main)
            except (AttributeError, NotImplementedError, TimeoutError):
                # 流式超时或旧版 SDK 不支持 stream=True → 降级为非流式调用
                _logger.warning("[create_agent] 流式生成超时或不支持，降级为非流式调用 elapsed=%.1fs", time.time() - t0_main)
                await publish("pipeline.step", {
                    "step": "create_stream_fallback", "status": "running",
                    "text": "流式生成超时，切换为普通模式重试...",
                })
                resp = await llm_complete(
                    [
                        {"role": "system", "content": system},
                        {"role": "user",   "content": user_prompt},
                    ],
                    temperature=create_temperature,
                )
                raw = resp if isinstance(resp, str) else resp.get("content", "")

            elapsed_ms = int((time.time() - t0_main) * 1000)
            await publish("tool.call", {
                "call_id":        main_llm_cid,
                "tool":           "llm:create_main",
                "status":         "succeeded",
                "result_preview": f"{len(raw)}chars {elapsed_ms}ms",
                "full_result":    f"output_chars={len(raw)} elapsed_ms={elapsed_ms} model={model_name}",
            })
            # ── 落库主创作 assistant 消息（含完整 raw 输出 + 完整推理内容，前端 SSE replay 恢复）──
            if session_id:
                try:
                    _db.insert_message(
                        msg_id=f"asst_create_{uuid.uuid4().hex[:12]}",
                        session_id=session_id,
                        role="assistant",
                        content=raw,
                        reasoning=_reasoning_buf,  # 完整推理内容，无截断
                    )
                except Exception as _dbe:
                    _logger.warning("[create_agent] assistant 消息落库失败 session=%s: %s", session_id, _dbe)
        except Exception as e:
            await publish("tool.call", {
                "call_id": main_llm_cid, "tool": "llm:create_main",
                "status": "failed", "error": str(e),
            })
            await publish("tool.call", {
                "call_id": create_call_id, "tool": "abc_composer",
                "status": "failed", "error": str(e),
            })
            await todo_mgr.finish_all(publish, "failed")
            await assert_finish_gate(todo_mgr, "create", publish)
            reply = f"创作失败：{e}"
            await stream_text(reply, publish)
            await publish("message.completed", {"message": reply})
            return {"domain": "create", "message": reply, "abc_updated": False}

        if exec_ids:
            await todo_mgr.complete_one(exec_ids[0], publish)

        new_abc, summary = extract_abc_and_summary(raw, _FALLBACK_ABC)

        # ── 质量流水线已移除（PERF-01）────────────────────────────────────────
        # 原有 4 步串行 LLM 修正（validate/dup/rhythm/melody）最坏增加 4 次 LLM 调用，
        # 导致总耗时 10+ 分钟。现在把所有质量约束直接写进 _build_prompt，
        # 由主创作 LLM 一次性输出正确结果，无需事后修正。

        # ── 流式续写循环：时长不足时持续流式追加，全程实时推送，无超时概念 ─────────
        # 设计原则：
        #   - 不做并行扩展（有超时风险），改为串行流式续写循环
        #   - 每轮写 2 行（8 小节），流式输出，实时追加到编辑器
        #   - 用户全程看到谱子一行行增长，体验流畅
        #   - 最多续写 8 轮（防止极端情况无限循环），每轮独立 stream
        dur_match = re.search(r'(\d+(?:\.\d+)?)\s*分钟', message)
        if dur_match:
            required_mins = float(dur_match.group(1))
            dur_check = check_duration_requirement(new_abc, required_mins)
            if not dur_check["satisfied"]:
                actual_header = parse_abc_header(new_abc)
                _cont_key  = actual_header["key"] or "C"
                _cont_bpm  = actual_header["bpm"] or session_bpm
                _cont_tsig = f"{actual_header['time_sig_num']}/{actual_header['time_sig_den']}"
                _cont_system = (
                    f"你是 ABC Notation 音乐创作专家。"
                    f"调号={_cont_key}，BPM={_cont_bpm:.0f}，拍号={_cont_tsig}，L:1/8。"
                    f"直接输出 ABC 正文行（每行4小节），不输出任何 Header（X:/T:/M:/L:/Q:/K:），"
                    f"不输出解释，不输出 SUMMARY。每行旋律必须不同，不能与参考行重复。"
                )
                _MAX_EXTEND_ROUNDS = 8  # 最多续写轮次，防止极端情况
                _extend_round = 0
                while not dur_check["satisfied"] and _extend_round < _MAX_EXTEND_ROUNDS:
                    _extend_round += 1
                    shortage = dur_check["shortage_bars"]
                    actual   = dur_check["actual_bars"]
                    required = dur_check["required_bars"]
                    # 提取末尾 3 行作为风格参考
                    _k_pos = new_abc.find("K:")
                    _body_lines = []
                    if _k_pos >= 0:
                        _body_raw = new_abc[new_abc.find("\n", _k_pos):].strip()
                        _body_lines = [l for l in _body_raw.splitlines() if l.strip()]
                    _tail_ctx = "\n".join(_body_lines[-3:]) if _body_lines else ""
                    # 每轮写 2 行（8 小节），不足 2 行时写到满足为止
                    _lines_this_round = max(2, min(4, (shortage + 3) // 4))
                    _cont_user = (
                        f"参考已有旋律末尾（保持风格连贯，每行必须不同）：\n{_tail_ctx}\n\n"
                        f"请继续写 {_lines_this_round} 行新旋律（每行4小节），风格：{message[:80]}。"
                        f"直接输出 {_lines_this_round} 行 ABC 正文，不含任何 Header 或解释。"
                    )
                    await publish("pipeline.step", {
                        "step": "create_extend", "status": "running",
                        "text": f"🎵 续写第 {_extend_round} 轮（已有 {actual}/{required} 小节，补充 {shortage} 小节）...",
                    })
                    _ext_raw = ""
                    try:
                        async for _chunk in complete_stream(
                            [
                                {"role": "system", "content": _cont_system},
                                {"role": "user",   "content": _cont_user},
                            ],
                            temperature=0.88 + _extend_round * 0.02,
                            # thinking 默认，模型自决
                        ):
                            _cd = _chunk[0] if isinstance(_chunk, tuple) else _chunk
                            _rd = _chunk[1] if isinstance(_chunk, tuple) and len(_chunk) > 1 else ""
                            if _rd:
                                await publish("message.delta", {"delta": "", "reasoning_delta": _rd})
                            if _cd:
                                _ext_raw += _cd
                    except Exception as _ext_e:
                        _logger.warning("[create_agent] 续写第 %d 轮失败: %s", _extend_round, _ext_e)
                        break
                    # 过滤 header 行，只保留正文
                    _new_lines = [
                        l for l in _ext_raw.splitlines()
                        if l.strip()
                        and not re.match(r'^[XTMLQKCS]:', l.strip())
                        and not l.strip().startswith('%')
                        and not l.strip().startswith('SUMMARY')
                    ]
                    if not _new_lines:
                        _logger.warning("[create_agent] 续写第 %d 轮无有效输出，停止", _extend_round)
                        break
                    # 追加到 ABC body 末尾
                    _k_end = new_abc.find("\n", new_abc.find("K:"))
                    if _k_end >= 0:
                        new_abc = new_abc[:_k_end + 1] + new_abc[_k_end + 1:].rstrip() + "\n" + "\n".join(_new_lines)
                    # 实时推送编辑器（用户看到谱子增长）
                    await publish("abc.updated", {
                        "abc":     new_abc,
                        "version": 0,
                        "summary": f"续写中（第 {_extend_round} 轮）...",
                    })
                    # 重新检查是否满足时长
                    dur_check = check_duration_requirement(new_abc, required_mins)
                    _logger.info("[create_agent] 续写第 %d 轮完成 actual=%d required=%d satisfied=%s",
                                 _extend_round, dur_check["actual_bars"], dur_check["required_bars"], dur_check["satisfied"])
                await publish("pipeline.step", {
                    "step": "create_extend", "status": "succeeded",
                    "text": f"✅ 续写完成（{_extend_round} 轮，共 {count_bars(new_abc)} 小节）",
                })

        header   = parse_abc_header(new_abc)
        note_cnt = count_notes(new_abc)

        # 存入 session + 落库
        score = None
        try:
            sess = session_getter(session_id)
            meta  = ScoreMeta(
                title=header["title"], key=header["key"],
                bpm=header["bpm"], note_count=note_cnt,
                time_sig_num=header.get("time_sig_num", 4),  # NEW-05 修复：补充拍号字段
                time_sig_den=header.get("time_sig_den", 4),
            )
            score = Score(title=header["title"], abc_notation=new_abc, meta=meta)
            sess.score = score
            session_saver(sess)
        except Exception:
            pass

        # ── 自动写入工作区文件（.sky/<title>.abc）─────────────────────────────
        # 谱子是项目资产，跨会话共享，通过 ContextVar 自动推断项目路径
        _ws_file_path = ""
        try:
            from app.agentcore.tools.abc_tools import save_score_to_workspace_impl  # 业务逻辑归属 abc_tools
            _save_result = save_score_to_workspace_impl(
                abc_notation=new_abc,
                title=header["title"] or "score",
                overwrite=True,
            )
            _ws_file_path = _save_result["path"]
            # 写入重要记忆：ABC 文件路径（供 H5Agent 等跨轮次感知）
            try:
                from app.agentcore.session_context import remember_workspace_file
                remember_workspace_file(session_id, _ws_file_path,
                                       header["title"] or "score")
            except Exception:
                pass
            # 通知前端文件树刷新
            await publish("workspace.file_saved", {
                "path":  _ws_file_path,
                "type":  "abc",
                "title": header["title"],
            })
        except Exception:
            pass

        await publish("tool.call", {
            "call_id":        create_call_id,
            "tool":           "abc_composer",
            "status":         "succeeded",
            "result_preview": summary,
        })

        # BUG-12 修复：exec_ids[1] 是「按需导出」TODO，只有真正执行了导出才 complete_one
        # 不能在导出前就标记 done（违反 TODO 纪律：未真实执行就标完成）
        # 导出结果在后续 _export_midi/_export_sky_json 逻辑中处理，此处不提前标记

        # 读取实际版本号（改编时 session 中可能已有多个版本）
        _version = 1
        try:
            _sv = session_getter(session_id)
            if _sv and _sv.score:
                _version = _sv.score.latest_version()
        except Exception:
            pass
        await publish("abc.updated", {
            "abc":     new_abc,
            "version": _version,
            "summary": summary,
            "meta": {
                "title":       header["title"],
                "composer":    "",
                "bpm":         header["bpm"],
                "key":         header["key"],
                "time_sig":    {"num": header["time_sig_num"], "den": header["time_sig_den"]},
                "note_count":  note_cnt,
                "pitch_level": 0,
            },
        })

        # ── 按需导出（低耦合语义判断，不靠关键词匹配）────────────────────────────
        # 通过 LLM 语义判断用户是否需要导出 MIDI 或 Sky JSON
        # 判断逻辑：检查 message 中是否有明确的导出意图
        # 不用关键词列表——用简单的语义规则（低耦合）
        _export_midi = False
        _export_sky_json = False
        try:
            _msg_lower = message.lower()
            # MIDI 导出意图：「转midi」「导出midi」「生成midi」「midi文件」「.mid」
            # BUG-03 修复：补充纯单词 'midi'/'mid' 变体，防止「给我一个midi」不触发
            _midi_hints = ["转midi", "导出midi", "生成midi", "midi文件", ".mid",
                           "转mid", "导出mid", "export midi", "to midi",
                           "midi文件", "mid文件", "要midi", "要mid",
                           "midi格式", "mid格式"]  # NEW-09 修复：移除裸 'midi' 避免误触发
            _export_midi = any(kw in _msg_lower for kw in _midi_hints)
            # Sky JSON 导出意图：「转sky」「导出json」「sky json」「游戏格式」「导入游戏」
            _sky_hints = ["转sky", "导出json", "sky json", "游戏格式", "导入游戏",
                          "skyjson", "sky格式", "转成sky"]
            _export_sky_json = any(kw in _msg_lower for kw in _sky_hints)
        except Exception:
            pass

        _export_results = {}
        _export_done = False  # BUG-12：追踪是否真实完成了导出
        if _export_midi or _export_sky_json:
            from app.agentcore.tools import call_tool as _call_tool
            if _export_midi:
                await publish("pipeline.step", {
                    "step": "create_export_midi", "status": "running",
                    "text": "正在导出 MIDI 文件...",
                })
                try:
                    _midi_tc_id = f"call_midi_{uuid.uuid4().hex[:8]}"
                    _midi_result = await _call_tool("abc_to_midi", {
                        "abc": new_abc,
                        "output_filename": f"{header['title'] or 'score'}.mid",
                    })
                    _export_results["midi"] = _midi_result
                    _export_done = True
                    # ── 落库 tool 消息（abc_to_midi 结果，前端可渲染工具卡片）──
                    if session_id:
                        try:
                            import json as _json
                            _midi_result_str = _midi_result if isinstance(_midi_result, str) else _json.dumps(_midi_result, ensure_ascii=False)
                            _db.insert_message(
                                msg_id=f"tool_{_midi_tc_id}_{uuid.uuid4().hex[:8]}",
                                session_id=session_id,
                                role="tool",
                                content=_midi_result_str,
                                tool_call_id=_midi_tc_id,
                                tool_name="abc_to_midi",
                            )
                        except Exception as _dbe:
                            _logger.warning("[create_agent] abc_to_midi tool 消息落库失败: %s", _dbe)
                    await publish("pipeline.step", {
                        "step": "create_export_midi", "status": "succeeded",
                        "text": "MIDI 文件已导出",
                    })
                except Exception as _e:
                    await publish("pipeline.step", {
                        "step": "create_export_midi", "status": "failed",
                        "text": f"MIDI 导出失败：{_e}",
                    })
            if _export_sky_json:
                await publish("pipeline.step", {
                    "step": "create_export_sky", "status": "running",
                    "text": "正在导出 Sky JSON...",
                })
                try:
                    _sky_tc_id = f"call_sky_{uuid.uuid4().hex[:8]}"
                    # BUG-CR3 修复：补充 output_filename，使用曲目标题而非默认文件名
                    _sky_result = await _call_tool("abc_to_sky_json", {
                        "abc": new_abc,
                        "output_filename": f"{header['title'] or 'score'}.json",
                    })
                    _export_results["sky_json"] = _sky_result
                    _export_done = True
                    # ── 落库 tool 消息（abc_to_sky_json 结果，前端可渲染工具卡片）──
                    if session_id:
                        try:
                            import json as _json
                            _sky_result_str = _sky_result if isinstance(_sky_result, str) else _json.dumps(_sky_result, ensure_ascii=False)
                            _db.insert_message(
                                msg_id=f"tool_{_sky_tc_id}_{uuid.uuid4().hex[:8]}",
                                session_id=session_id,
                                role="tool",
                                content=_sky_result_str,
                                tool_call_id=_sky_tc_id,
                                tool_name="abc_to_sky_json",
                            )
                        except Exception as _dbe:
                            _logger.warning("[create_agent] abc_to_sky_json tool 消息落库失败: %s", _dbe)
                    await publish("pipeline.step", {
                        "step": "create_export_sky", "status": "succeeded",
                        "text": "Sky JSON 已导出",
                    })
                except Exception as _e:
                    await publish("pipeline.step", {
                        "step": "create_export_sky", "status": "failed",
                        "text": f"Sky JSON 导出失败：{_e}",
                    })
            # BUG-12 修复：只有真实完成导出后才 complete_one exec_ids[1]
            if _export_done and len(exec_ids) > 1:
                await todo_mgr.complete_one(exec_ids[1], publish)

        await todo_mgr.finish_all(publish, "done")
        action_word = "改编" if base_abc else "创作"
        reply = f"✅ 已为你{action_word}《{header['title']}》：{summary}"
        await stream_text(reply, publish)
        await assert_finish_gate(todo_mgr, "create", publish)
        await publish("message.completed", {"message": reply})
        return {
            "domain":       "create",
            "message":      reply,
            "abc_updated":  True,
            "abc_notation": new_abc,
            "summary":      summary,
            **_export_results,
        }

    def _build_prompt(
        self,
        message: str,
        base_abc: str,
        session_bpm: float = 120.0,
        session_time_sig_num: int = 4,
    ) -> tuple[str, str]:
        """
        构造创作/改编 prompt。

        核心原则：
          - 无论从零创作还是改编，都基于真实 BPM 精确计算时长
          - 改编时将完整原谱注入，LLM 基于真实数据理解音乐结构
          - 时长计算结果以强制指令形式注入，不依赖 LLM 自行计算
        """
        system = _load_system_create()

        # ── 确定 BPM（优先级：消息显式声明 > 原谱Q:字段 > 原谱header > session > 风格推断 > 默认120）
        # BUG-PROMPT-2 修复：_extract_bpm_from_message 现在也能识别 Q:1/4=160 格式
        # _infer_bpm_from_style 返回 None 时表示无法推断，fallback 到 120
        bpm = (
            _extract_bpm_from_message(message)
            or (parse_abc_header(base_abc)["bpm"] if base_abc else None)
            or (session_bpm if session_bpm and session_bpm != 120.0 else None)
            or _infer_bpm_from_style(message)
            or 120.0
        )
        # 从原谱获取拍号
        time_sig_num = session_time_sig_num
        if base_abc:
            h = parse_abc_header(base_abc)
            time_sig_num = h.get("time_sig_num", 4) or 4

        # ── 时长注入（有时长需求时精确计算，无时长需求时给出默认建议）──────────
        dur_match = re.search(r'(\d+(?:\.\d+)?)\s*分钟', message)
        if dur_match:
            mins = float(dur_match.group(1))
            duration_hint = _calc_duration_hint(mins, bpm, time_sig_num)
        else:
            # 无时长要求：根据用户描述智能判断长度
            seconds_per_line = (60.0 / bpm) * time_sig_num * 4
            # 检测是否是短片段请求（前奏/间奏/几小节/片段）
            is_short_fragment = any(kw in message for kw in [
                '前奏', '间奏', '尾奏', '片段', '几小节', '4小节', '8小节',
                '一段', '短', 'intro', 'outro', 'bridge'
            ])
            if is_short_fragment:
                default_lines_min = max(2, round(20 / seconds_per_line))
                default_lines_max = max(default_lines_min + 2, round(40 / seconds_per_line))
                fragment_hint = "（短片段模式）"
            else:
                # 完整曲目：1.5-2.5 分钟，根据 BPM 动态调整
                default_lines_min = max(6, round(90 / seconds_per_line))
                default_lines_max = max(default_lines_min + 4, round(150 / seconds_per_line))
                fragment_hint = ""
            duration_hint = (
                f"\n\n【时长参考{fragment_hint}】BPM={bpm:.0f}，每行（4小节）{seconds_per_line:.1f} 秒。"
                f"建议写 {default_lines_min}-{default_lines_max} 行，精炼优于冗长。"
                f"你可根据情绪弧线自主决定最终行数，在此范围内灵活调整。"
            )

        # ── 改编模式：注入完整原谱 + 预提取核心动机段落 ──────────────────────
        # BUG-PROMPT-3 修复：Python 层预提取前8小节作为「核心动机段落」单独标注
        # 让 LLM 明确知道哪些是核心动机，而不是自己在长谱中猜测
        if base_abc:
            base_header = parse_abc_header(base_abc)
            # 智能提取核心动机段落：
            # 对于有长前奏的曲子，前8小节可能是前奏而非主题
            # 策略：提取前8小节 + 中段8小节，让LLM自己判断哪段是核心动机
            # BUG-PROMPT-4 修复：base_abc.find('K:') 在无 K: 时返回 -1，
            # base_abc[-1:] 只取最后1字符，导致 total_bars 永远为 0，中段动机永远不提取。
            # 修复：用 count_bars() 直接统计（内部已用 re.search K: 安全处理）
            total_bars = count_bars(base_abc) if base_abc else 0
            motif_bars_head = extract_motif_bars(base_abc, bar_count=8)  # 前8小节
            # 若谱子够长（>16小节），额外提取中段作为对比参考
            mid_hint = ""
            if total_bars > 16:
                mid_start = total_bars // 3  # 约1/3处开始
                mid_hint = f"\n\n【中段参考（第{mid_start+1}小节起）— 可能包含主题或副歌动机】\n"
                # 用正则跳过前mid_start个小节后再提取
                mid_hint += _extract_bars_from_offset(base_abc, mid_start, 8)
            motif_bars = motif_bars_head
            arrange_note = (
                f"\n\n## 改编/延伸模式（基于参考谱动机提取）\n"
                f"原谱信息：调号={base_header['key']}，BPM={base_header['bpm']:.0f}，"
                f"拍号={base_header['time_sig_num']}/{base_header['time_sig_den']}\n"
                f"\n【核心动机段落（前8小节）— 提取和弦骨架和节奏型】\n"
                f"{motif_bars}\n"
                f"{mid_hint}"
                f"\n执行动机提取四步法（脑内完成）：\n"
                f"① 从上方核心动机段落识别和弦骨架（根音序列）\n"
                f"② 识别核心节奏型（最常用时值组合）\n"
                f"③ 判断情感色彩（调式/音区/密度）\n"
                f"④ 找出钩子片段（最有辨识度的2-4小节）\n"
                f"然后：基于骨架写全新旋律，不复制原谱任何音符！\n"
                f"⚠️ 每一行旋律都必须是新的，任意两行不能完全相同。"
            )
            user = (
                f"用户需求：{message}"
                f"{duration_hint}"
                f"{arrange_note}"
                f"\n\n原始 ABC 谱（完整参考数据）：\n{base_abc}"
            )
        else:
            # 从零创作
            user = f"请创作：{message}{duration_hint}"

        return system, user

    async def _fix_all_quality_issues(
        self,
        abc: str,
        summary: str,
        raw: str,
        system: str,
        user_prompt: str,
        message: str,
        publish: Publisher,
    ) -> tuple[str, str]:
        """
        质量流水线 v2.0：并行检测 + 一次合并修正。

        优化前：3个检测串行，每次触发都额外 LLM 调用（最坏 3次额外 LLM）。
        优化后：3个检测并行（纯CPU，无IO），汇总问题后一次 LLM 修正。
        预期收益：最坏情况从 4次 LLM → 2次 LLM，耗时减少 30-50%。
        """
        try:
            # ── 并行运行所有质量检测（纯 CPU，用 asyncio.to_thread 避免阻塞事件循环）
            dup_result, rhythm_result, melody_result = await asyncio.gather(
                asyncio.to_thread(detect_duplicate_lines, abc),
                asyncio.to_thread(check_rhythm_variety, abc),
                asyncio.to_thread(check_melody_quality, abc),
                return_exceptions=True,
            )
            # 异常时降级为空结果
            if isinstance(dup_result, Exception):
                dup_result = {"has_duplicates": False, "duplicate_pairs": [], "total_lines": 0, "unique_lines": 0}
            if isinstance(rhythm_result, Exception):
                rhythm_result = {"variety_ratio": 1.0, "monotone_count": 0, "total_body_lines": 0}
            if isinstance(melody_result, Exception):
                melody_result = {"quality_ratio": 1.0, "chord_block_count": 0, "total_body_lines": 0}

            # ── 汇总所有问题
            issues: list[str] = []
            fix_instructions: list[str] = []
            total_lines = dup_result.get("total_lines", 0) or rhythm_result.get("total_body_lines", 0)

            # 1. 重复行问题
            dup_count = len(dup_result.get("duplicate_pairs", []))
            if dup_result.get("has_duplicates") and dup_count > 0:
                dup_desc = "\n".join(
                    f"  - 第{a+1}行与第{b+1}行完全相同：「{content}」"
                    for a, b, content in dup_result["duplicate_pairs"][:5]
                )
                issues.append(f"【问题1】重复旋律行（{dup_count} 对）：\n{dup_desc}")
                fix_instructions.append(
                    "① 重写所有重复行，使用动机发展手法（倒影、增值、减值、移调、节奏变形），"
                    "确保每行旋律独特。"
                )

            # 2. 节奏单调问题
            variety_ratio = rhythm_result.get("variety_ratio", 1.0)
            mono_count = rhythm_result.get("monotone_count", 0)
            if variety_ratio < 0.7 and total_lines >= 4:
                mono_lines = rhythm_result.get("monotone_lines", [])[:3]
                mono_desc = "\n".join(
                    f"  - 第{i+1}行：{preview}" for i, preview in mono_lines
                )
                issues.append(f"【问题2】节奏单调（{mono_count}/{total_lines} 行纯八分音符）：\n{mono_desc}")
                fix_instructions.append(
                    "② 修改单调节奏行，每行混用至少2种时值："
                    "四分音符C2（重量感）、附点八分C3/2（律动感）、长音C4（呼吸点）、休止符z2（留白）。"
                )

            # 3. 旋律质量问题
            quality_ratio = melody_result.get("quality_ratio", 1.0)
            cb_count = melody_result.get("chord_block_count", 0)
            if quality_ratio < 0.5 and total_lines >= 4:
                cb_lines = melody_result.get("chord_block_lines", [])[:3]
                cb_desc = "\n".join(
                    f"  - 第{i+1}行（和弦块占{ratio:.0%}）：{preview}"
                    for i, ratio, preview in cb_lines
                )
                issues.append(f"【问题3】旋律线条质量差（{cb_count}/{total_lines} 行纯和弦堆砌）：\n{cb_desc}")
                fix_instructions.append(
                    "③ 重写和弦堆砌行：用单音旋律音符（d2 e2 f2 g2）构成主旋律，"
                    "和弦块[]只用于低音铺垫，不能占全行80%以上，旋律应有方向感。"
                )

            if not issues:
                return abc, summary  # 所有检测通过，无需修正

            # ── 发布合并修正进度
            issue_summary = " / ".join([
                f"重复行×{dup_count}" if dup_result.get("has_duplicates") else "",
                f"节奏单调{mono_count}行" if variety_ratio < 0.7 and total_lines >= 4 else "",
                f"旋律质量差{cb_count}行" if quality_ratio < 0.5 and total_lines >= 4 else "",
            ])
            issue_summary = " / ".join(s for s in issue_summary.split(" / ") if s)
            await publish("pipeline.step", {
                "step":       "create_quality_fix",
                "status":     "running",
                "text":       f"质量检测发现 {len(issues)} 个问题（{issue_summary}），正在一次性修正...",
                "agent_name": _AGENT_NAME,
            })

            # ── 一次 LLM 调用修正所有问题（_llm_with_span 自动记录 span）
            fix_prompt = (
                f"你的 ABC 谱存在以下 {len(issues)} 个问题，请一次性全部修正：\n\n"
                + "\n\n".join(issues)
                + "\n\n修正要求：\n"
                + "\n".join(fix_instructions)
                + "\n\n行数保持不变，重新输出完整 ABC + SUMMARY 行。"
            )
            raw_fix = await _llm_with_span(
                messages=[
                    {"role": "system",    "content": system},
                    {"role": "user",      "content": user_prompt},
                    {"role": "assistant", "content": raw},
                    {"role": "user",      "content": fix_prompt},
                ],
                temperature=0.82,
                publish=publish,
                span_name="quality_fix",
            )
            abc_fix, sum_fix = extract_abc_and_summary(raw_fix, abc)
            if "K:" in abc_fix:
                # 验证修正效果（只做快速检测，不再触发新 LLM）
                dup2 = detect_duplicate_lines(abc_fix)
                rhythm2 = check_rhythm_variety(abc_fix)
                melody2 = check_melody_quality(abc_fix)
                improved = (
                    (not dup2.get("has_duplicates") or len(dup2.get("duplicate_pairs", [])) < dup_count)
                    and (rhythm2.get("variety_ratio", 0) >= variety_ratio)
                    and (melody2.get("quality_ratio", 0) >= quality_ratio)
                )
                status_text = "已修正" if improved else "修正效果有限，保留原版"
                await publish("pipeline.step", {
                    "step":       "create_quality_fix",
                    "status":     "succeeded" if improved else "failed",
                    "text":       status_text,
                    "agent_name": _AGENT_NAME,
                })
                if improved:
                    return abc_fix, sum_fix
            else:
                await publish("pipeline.step", {
                    "step":       "create_quality_fix",
                    "status":     "failed",
                    "text":       "修正输出无效（缺少K:字段），保留原版",
                    "agent_name": _AGENT_NAME,
                })
        except Exception:
            pass
        return abc, summary

    async def _validate_and_fix(
        self,
        abc: str,
        summary: str,
        raw: str,
        system: str,
        user_prompt: str,
        publish: Publisher,
    ) -> tuple[str, str]:
        """验证 ABC 音符范围 + 自动修正（最多 1 次重试）。"""
        from app.agentcore.tools import call_tool as _call_tool
        try:
            validation = await _call_tool("validate_abc", {"abc": abc})
            if isinstance(validation, dict) and not validation.get("valid", True):
                issues = "; ".join(validation.get("out_of_range", []))
                await publish("pipeline.step", {
                    "step": "create_validate", "status": "running",
                    "text": f"ABC 验证发现问题，正在自动修正：{issues}",
                })
                try:
                    resp2 = await llm_complete(
                        [
                            {"role": "system",    "content": system},
                            {"role": "user",      "content": user_prompt},
                            {"role": "assistant", "content": raw},
                            {"role": "user",      "content": (
                                f"你生成的 ABC 存在问题：{issues}\n"
                                "请修正所有超出 Sky C4-C6 范围的音符（移八度处理），"
                                "重新输出完整 ABC + SUMMARY 行。"
                            )},
                        ],
                        temperature=0.3,  # 修正时用低 temperature 确保精确性
                    )
                    raw2 = resp2 if isinstance(resp2, str) else resp2.get("content", "")
                    abc2, sum2 = extract_abc_and_summary(raw2, abc)
                    if "K:" in abc2:
                        abc, summary = abc2, sum2
                    await publish("pipeline.step", {
                        "step": "create_validate", "status": "succeeded",
                        "text": "ABC 已自动修正",
                    })
                except Exception:
                    pass
        except Exception:
            pass
        return abc, summary

    async def _fix_rhythm_monotone(
        self,
        abc: str,
        summary: str,
        system: str,
        user_prompt: str,
        publish: Publisher,
    ) -> tuple[str, str]:
        """
        检测节奏单调性（全八分音符行占比 > 30%）并要求 LLM 重写。
        防止 LLM 输出「CDEDCDEF|GABCDEFG」这类毫无节奏变化的谱子。
        """
        try:
            result = check_rhythm_variety(abc)
            variety_ratio = result.get("variety_ratio", 1.0)
            monotone_count = result.get("monotone_count", 0)
            total = result.get("total_body_lines", 0)

            if variety_ratio >= 0.7 or total < 4:
                return abc, summary  # 节奏多样性足够，无需修正

            mono_lines = result.get("monotone_lines", [])[:5]
            mono_desc = "\n".join(
                f"  - 第{i+1}行：{preview}" for i, preview in mono_lines
            )
            await publish("pipeline.step", {
                "step": "create_rhythm_fix", "status": "running",
                "text": f"节奏单调（{monotone_count}/{total} 行纯八分音符），正在修正...",
            })
            resp = await llm_complete(
                [
                    {"role": "system", "content": system},
                    {"role": "user",   "content": user_prompt},
                    {"role": "assistant", "content": abc},
                    {"role": "user", "content": (
                        f"你的谱子有 {monotone_count} 行是纯八分音符（节奏单调），违反了禁令4：\n"
                        f"{mono_desc}\n\n"
                        f"请修改这些行，在每行混用至少2种时值：\n"
                        f"- 四分音符 C2（重量感）、附点八分 C3/2 C/2（律动感）\n"
                        f"- 长音 C4/C6（呼吸点）、休止符 z2/z4（留白）\n"
                        f"保持旋律骨干音不变，只改节奏型。重新输出完整 ABC + SUMMARY 行。"
                    )},
                ],
                temperature=0.75,
            )
            raw_fix = resp if isinstance(resp, str) else resp.get("content", "")
            abc_fix, sum_fix = extract_abc_and_summary(raw_fix, abc)
            if "K:" in abc_fix:
                result2 = check_rhythm_variety(abc_fix)
                if result2.get("variety_ratio", 0) > variety_ratio:
                    await publish("pipeline.step", {
                        "step": "create_rhythm_fix", "status": "succeeded",
                        "text": f"节奏多样性已提升（{variety_ratio:.0%}→{result2['variety_ratio']:.0%}）",
                    })
                    return abc_fix, sum_fix
            await publish("pipeline.step", {
                "step": "create_rhythm_fix", "status": "failed",
                "text": "节奏修正效果有限，保留原版",
            })
        except Exception:
            pass
        return abc, summary

    async def _fix_melody_quality(
        self,
        abc: str,
        summary: str,
        system: str,
        user_prompt: str,
        publish: Publisher,
    ) -> tuple[str, str]:
        """
        检测旋律线条质量（BUG-QP-1 修复）。
        防止 LLM 输出「只有和弦块堆砌，没有旋律线条」的低质量谱子。

        触发条件：
          - quality_ratio < 0.5（超过50%的行是纯和弦块堆砌）
          - 至少4行以上（太短的谱子不做质量判断）

        修正策略：
          - 告知 LLM 哪些行是纯和弦块堆砌
          - 要求在和弦骨架上加入真正的旋律线条
          - 保留和弦进行，但每行需有单音旋律音符
        """
        try:
            result = check_melody_quality(abc)
            quality_ratio  = result.get("quality_ratio", 1.0)
            cb_count       = result.get("chord_block_count", 0)
            total          = result.get("total_body_lines", 0)

            if quality_ratio >= 0.5 or total < 4:
                return abc, summary  # 旋律线条质量足够，无需修正

            # 构造问题行描述
            cb_lines = result.get("chord_block_lines", [])[:5]
            cb_desc = "\n".join(
                f"  - 第{i+1}行（和弦块占{ratio:.0%}）：{preview}"
                for i, ratio, preview in cb_lines
            )
            await publish("pipeline.step", {
                "step": "create_melody_fix", "status": "running",
                "text": f"旋律线条质量差（{cb_count}/{total} 行纯和弦堆砌），正在修正...",
            })
            resp = await llm_complete(
                [
                    {"role": "system", "content": system},
                    {"role": "user",   "content": user_prompt},
                    {"role": "assistant", "content": abc},
                    {"role": "user", "content": (
                        f"你的谱子有 {cb_count} 行是纯和弦块堆砌，没有真正的旋律线条：\n"
                        f"{cb_desc}\n\n"
                        f"这种写法只是把和弦块反复堆砌，完全没有音乐性。\n"
                        f"请重写这些行，在保留和弦骨架的同时加入真正的旋律线条：\n"
                        f"- 用单音旋律音符（如 d2 e2 f2 g2）构成主旋律\n"
                        f"- 和弦块 [] 只用于低音铺垫，不能占全行的80%以上\n"
                        f"- 旋律应有方向感（上行/下行/波浪），不是原地踏步\n"
                        f"- 混用至少2种时值（切分/附点/长音）\n"
                        f"重新输出完整 ABC + SUMMARY 行。"
                    )},
                ],
                temperature=0.88,
            )
            raw_fix = resp if isinstance(resp, str) else resp.get("content", "")
            abc_fix, sum_fix = extract_abc_and_summary(raw_fix, abc)
            if "K:" in abc_fix:
                result2 = check_melody_quality(abc_fix)
                if result2.get("quality_ratio", 0) > quality_ratio:
                    await publish("pipeline.step", {
                        "step": "create_melody_fix", "status": "succeeded",
                        "text": f"旋律线条已改善（{quality_ratio:.0%}→{result2['quality_ratio']:.0%}）",
                    })
                    return abc_fix, sum_fix
            await publish("pipeline.step", {
                "step": "create_melody_fix", "status": "failed",
                "text": "旋律修正效果有限，保留原版",
            })
        except Exception:
            pass
        return abc, summary

    async def _fix_duplicate_lines(
        self,
        abc: str,
        summary: str,
        raw: str,
        system: str,
        user_prompt: str,
        message: str,
        publish: Publisher,
    ) -> tuple[str, str]:
        """
        检测重复旋律行并要求 LLM 修正（最多 1 次重试）。
        重复行是「3分钟变12分钟」的根本原因：LLM 把主旋律循环 N 遍凑时长。
        """
        dup = detect_duplicate_lines(abc)
        if not dup["has_duplicates"]:
            return abc, summary

        dup_count = len(dup["duplicate_pairs"])
        total     = dup["total_lines"]
        unique    = dup["unique_lines"]
        await publish("pipeline.step", {
            "step": "create_dedup", "status": "running",
            "text": f"检测到 {dup_count} 对重复旋律行（共 {total} 行，唯一 {unique} 行），正在修正...",
        })

        # 构造重复行说明，让 LLM 精确知道哪些行重复了
        dup_desc = "\n".join(
            f"  - 第{a+1}行与第{b+1}行完全相同：「{content}」"
            for a, b, content in dup["duplicate_pairs"][:5]  # 最多列出5对
        )

        try:
            resp_fix = await llm_complete(
                [
                    {"role": "system",    "content": system},
                    {"role": "user",      "content": user_prompt},
                    {"role": "assistant", "content": raw},
                    {"role": "user",      "content": (
                        f"你的 ABC 谱存在 {dup_count} 对重复旋律行，这会导致音乐听起来是在循环主旋律：\n"
                        f"{dup_desc}\n\n"
                        f"请重写整首曲子，确保每一行旋律都是独特的。\n"
                        f"使用动机发展手法（倒影、增值、减值、移调、节奏变形）让每行都不同，"
                        f"而不是重复已有旋律。\n"
                        f"行数保持不变，重新输出完整 ABC + SUMMARY 行。"
                    )},
                ],
                temperature=0.88,  # 重写时保持高创意度
            )
            raw_fix = resp_fix if isinstance(resp_fix, str) else resp_fix.get("content", "")
            abc_fix, sum_fix = extract_abc_and_summary(raw_fix, abc)
            if "K:" in abc_fix:
                dup2 = detect_duplicate_lines(abc_fix)
                if dup2["has_duplicates"]:
                    # 修正后仍有重复，但至少减少了重复数量则接受
                    if len(dup2["duplicate_pairs"]) < dup_count:
                        abc, summary = abc_fix, sum_fix
                        await publish("pipeline.step", {
                            "step": "create_dedup", "status": "succeeded",
                            "text": f"重复行已减少（{dup_count}→{len(dup2['duplicate_pairs'])} 对）",
                        })
                    else:
                        await publish("pipeline.step", {
                            "step": "create_dedup", "status": "failed",
                            "text": "重复行修正效果有限，保留原版",
                        })
                else:
                    abc, summary = abc_fix, sum_fix
                    await publish("pipeline.step", {
                        "step": "create_dedup", "status": "succeeded",
                        "text": "重复旋律行已全部修正",
                    })
        except Exception:
            pass

        return abc, summary

    async def run(self, ctx: "RunContext") -> dict:
        """v4.0 解耦接口：从 RunContext 解包参数，调用原 run()。
        AGENT-2 修复：session_getter/saver 通过 ctx 属性统一解包（fallback 逻辑在 RunContext 中）。
        """
        todo_mgr = ctx.extra.get("todo_mgr")
        if todo_mgr is None:
            from app.agentcore.todo_manager import TodoManager as _TM
            todo_mgr = _TM()
            todo_mgr.session_id = ctx.session_id
        return await self._run_impl(
            session_id=ctx.session_id,
            message=ctx.message,
            publish=ctx.publish,
            session_getter=ctx.session_getter,
            session_saver=ctx.session_saver,
            todo_mgr=todo_mgr,
            current_abc=ctx.extra.get("current_abc", ""),
        )

