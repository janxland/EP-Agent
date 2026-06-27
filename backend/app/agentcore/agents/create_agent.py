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

import re
from typing import Callable, Awaitable

from app.agentcore.llm import complete as llm_complete
from app.agentcore.todo_manager import TodoManager, assert_finish_gate
from app.agentcore.react_executor import stream_text
from app.agentcore.abc_utils import (
    extract_abc_and_summary, parse_abc_header, count_notes,
    check_duration_requirement, detect_duplicate_lines, check_rhythm_variety,
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


def _calc_duration_hint(mins: float, bpm: float, time_sig_num: int = 4) -> str:
    """
    根据目标时长、BPM、拍号精确计算目标行数，返回注入 prompt 的说明文本。
    每行 = 4小节，每小节 = (60/BPM) × time_sig_num 拍。
    """
    seconds_per_bar  = (60.0 / bpm) * time_sig_num
    seconds_per_line = seconds_per_bar * 4          # 每行 4 小节
    target_seconds   = mins * 60.0
    target_lines     = round(target_seconds / seconds_per_line)
    target_lines     = max(6, min(32, target_lines))  # 限制在合理范围
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
    """从用户消息中提取 BPM，未找到返回 None。"""
    m = re.search(r'(\d+)\s*(?:bpm|BPM|拍)', message)
    if m:
        return float(m.group(1))
    return None


def _infer_bpm_from_style(message: str) -> float:
    """根据风格关键词推断 BPM，无匹配返回 120。"""
    if any(kw in message for kw in ['慢', '抒情', '轻柔', '安静', '悠扬']):
        return 90.0
    if any(kw in message for kw in ['快', '欢快', '活泼', '节拍', '激烈']):
        return 140.0
    return 120.0


class CreateAgent:
    """ABC 谱创作 SubAgent（从零创作 或 基于已有谱子改编）。"""

    async def run(
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

        # ── 从 session 读取已有 ABC（改编/延伸时的数据基础）──────────────────────
        # current_abc 优先级：参数传入 > session.score.abc_notation
        # 这保证了「改编已有谱子」时 LLM 能看到完整的原谱数据（含 BPM/调号/旋律）
        session_abc = ""
        session_bpm = 120.0
        session_time_sig_num = 4
        try:
            sess = session_getter(session_id)
            if sess.score and sess.score.abc_notation:
                session_abc = sess.score.abc_notation
                session_bpm = float(sess.score.meta.bpm or 120.0)
                session_time_sig_num = int(getattr(sess.score.meta, 'time_sig_num', 4) or 4)
        except Exception:
            pass

        # 确定本次创作的「基础 ABC」：参数优先，其次 session 中已有的谱子
        base_abc = current_abc or session_abc

        ids      = todo_mgr.get_ids()
        pending  = todo_mgr.get_pending_ids()
        exec_ids = pending if pending else ids

        if exec_ids:
            await todo_mgr.tick(exec_ids[0], "running", publish)

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

        # 创作场景必须用高 temperature，否则 LLM 输出极度保守、旋律平淡
        # 改编模式（有 base_abc）用 0.85，从零创作用 0.92
        create_temperature = 0.85 if base_abc else 0.92
        try:
            resp = await llm_complete(
                [
                    {"role": "system", "content": system},
                    {"role": "user",   "content": user_prompt},
                ],
                temperature=create_temperature,
            )
            raw = resp if isinstance(resp, str) else resp.get("content", "")
        except Exception as e:
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

        # ── 验证 ABC 音符范围 + 自动修正 ─────────────────────────────────────
        new_abc, summary = await self._validate_and_fix(
            new_abc, summary, raw, system, user_prompt, publish
        )

        # ── 重复行检测 + 自动修正（防止主旋律循环）──────────────────────────────
        new_abc, summary = await self._fix_duplicate_lines(
            new_abc, summary, raw, system, user_prompt, message, publish
        )

        # ── 节奏多样性检测 + 自动修正（防止全八分音符单调输出）──────────────────
        new_abc, summary = await self._fix_rhythm_monotone(
            new_abc, summary, system, user_prompt, publish
        )

        # ── 时长验证：用户指定分钟数时检查小节数是否满足要求 ─────────────────────
        dur_match = re.search(r'(\d+(?:\.\d+)?)\s*分钟', message)
        if dur_match:
            required_mins = float(dur_match.group(1))
            dur_check = check_duration_requirement(new_abc, required_mins)
            if not dur_check["satisfied"]:
                shortage = dur_check["shortage_bars"]
                actual   = dur_check["actual_bars"]
                required = dur_check["required_bars"]
                await publish("pipeline.step", {
                    "step": "create_duration_check", "status": "running",
                    "text": f"时长不足（{actual}/{required} 小节），正在补充 {shortage} 小节...",
                })
                try:
                    # 从已生成的 ABC 解析实际 BPM（LLM 可能改变了 BPM）
                    actual_header = parse_abc_header(new_abc)
                    actual_bpm = actual_header["bpm"] or session_bpm
                    extend_hint = _calc_duration_hint(
                        required_mins, actual_bpm, actual_header["time_sig_num"]
                    )
                    resp3 = await llm_complete(
                        [
                            {"role": "system",    "content": system},
                            {"role": "user",      "content": user_prompt},
                            {"role": "assistant", "content": raw},
                            {"role": "user",      "content": (
                                f"你的作品只有约 {actual} 小节（约 {dur_check['actual_seconds']:.0f} 秒），"
                                f"但用户要求 {required_mins} 分钟（需要约 {required} 小节）。\n"
                                f"{extend_hint}\n"
                                f"请在现有旋律基础上继续扩展，补充约 {shortage} 小节。\n"
                                f"⚠️ 新增的每一行旋律必须与已有行不同，禁止重复已有旋律行。\n"
                                f"重新输出完整 ABC + SUMMARY 行。"
                            )},
                        ],
                        temperature=0.88,
                    )
                    raw3 = resp3 if isinstance(resp3, str) else resp3.get("content", "")
                    abc3, sum3 = extract_abc_and_summary(raw3, new_abc)
                    if "K:" in abc3 and count_notes(abc3) > count_notes(new_abc):
                        # 再次做重复行检测
                        dup3 = detect_duplicate_lines(abc3)
                        if not dup3["has_duplicates"]:
                            new_abc, summary = abc3, sum3
                        else:
                            # 扩展版有重复，保留原版
                            pass
                        await publish("pipeline.step", {
                            "step": "create_duration_check", "status": "succeeded",
                            "text": f"已扩展至 {check_duration_requirement(new_abc, required_mins)['actual_bars']} 小节",
                        })
                except Exception:
                    pass

        header   = parse_abc_header(new_abc)
        note_cnt = count_notes(new_abc)

        # 存入 session + 落库
        score = None
        try:
            sess = session_getter(session_id)
            meta  = ScoreMeta(
                title=header["title"], key=header["key"],
                bpm=header["bpm"], note_count=note_cnt,
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
            from app.agentcore.tools.workspace_tools import save_score_to_workspace_impl
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

        if len(exec_ids) > 1:
            await todo_mgr.complete_one(exec_ids[1], publish)

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

        # ── 确定 BPM（优先级：消息中指定 > 原谱 BPM > 风格推断）────────────────
        bpm = (
            _extract_bpm_from_message(message)
            or (parse_abc_header(base_abc)["bpm"] if base_abc else None)
            or session_bpm
            or _infer_bpm_from_style(message)
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
            # 无时长要求：默认写 10-14 行（约 1.5-2 分钟），给出参考
            seconds_per_line = (60.0 / bpm) * time_sig_num * 4
            default_lines_min = max(6, round(90 / seconds_per_line))
            default_lines_max = max(default_lines_min + 2, round(120 / seconds_per_line))
            duration_hint = (
                f"\n\n【时长参考】BPM={bpm:.0f}，每行（4小节）{seconds_per_line:.1f} 秒。"
                f"无时长要求时写 {default_lines_min}-{default_lines_max} 行（约1.5-2分钟），精炼优于冗长。"
            )

        # ── 改编模式：注入完整原谱 ────────────────────────────────────────────
        if base_abc:
            base_header = parse_abc_header(base_abc)
            arrange_note = (
                f"\n\n## 改编/延伸模式\n"
                f"原谱信息：调号={base_header['key']}，BPM={base_header['bpm']:.0f}，"
                f"拍号={base_header['time_sig_num']}/{base_header['time_sig_den']}\n"
                f"原谱是你的音乐素材库——提取其核心动机、调式、情感色彩，"
                f"结合用户需求创作全新旋律。\n"
                f"⚠️ 不是复制原谱，不是重复原谱的旋律行，而是基于原谱的音乐语言写出新的音乐叙事。\n"
                f"⚠️ 每一行旋律都必须是新的，任意两行不能完全相同。"
            )
            user = (
                f"用户需求：{message}"
                f"{duration_hint}"
                f"{arrange_note}"
                f"\n\n原始 ABC 谱（完整数据，用于理解音乐结构）：\n{base_abc}"
            )
        else:
            # 从零创作
            user = f"请创作：{message}{duration_hint}"

        return system, user

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
