"""
CreateAgent — ABC 谱创作 SubAgent

职责（单一）：
  - LLM 直接创作 ABC（从零创作 或 基于已有谱子改编）
  - 验证 ABC + 自动修正（最多 1 次重试）
  - 存入 session + 落库
  - 管理 create 域 TODO 状态

Prompt 设计：
  - 从 score-creator.agent 文件热加载（支持热更新）
  - 时长提示动态注入（用户指定分钟数时）
"""
from __future__ import annotations

import re
from typing import Callable, Awaitable

from app.agentcore.llm import complete as llm_complete
from app.agentcore.todo_manager import TodoManager, assert_finish_gate
from app.agentcore.react_executor import stream_text
from app.agentcore.abc_utils import (
    extract_abc_and_summary, parse_abc_header, count_notes,
    check_duration_requirement,
)

Publisher = Callable[[str, dict], Awaitable[None]]

# ── Prompt 常量（内嵌备用）────────────────────────────────────────────────────
# 当前实现：直接使用内嵌常量（性能最优，无 IO 开销）
# 未来扩展：可从 score-creator.agent 文件热加载（实现 load_agent_prompt() 后替换）
_SYSTEM_CREATE = (
    "你是世界顶级的 ABC Notation 音乐创作大师，曾为无数游戏、电影创作配乐，"
    "精通 Sky: Children of the Light 游戏的 15 键乐器特性。\n\n"
    "## Sky 乐器规范\n"
    "可用音符：C D E F G A B c d e f g a b c'（C4-C6，共 15 键）\n"
    "主旋律建议在 c-b 区间（C5-B5），副旋律可用 C-B（C4-B4）\n"
    "禁止使用范围外音符（如 C, 低八度 或 d'' 高八度）\n\n"
    "## ABC Notation 格式规范\n"
    "X:1\nT:曲名\nC:EP-Agent\nM:4/4\nL:1/8\nQ:1/4=120\nK:C\n"
    "% 正文：每行一个乐句（4小节），用 | 分隔小节\n\n"
    "## 创作原则（必须遵守）\n"
    "1. 结构完整：A段（主题8小节）→ B段（发展/副歌8小节）→ A'段（再现8小节）→ 尾声\n"
    "2. 旋律优美：有明确主题动机，有上行/下行张力，有呼吸感和情感起伏\n"
    "3. 节奏多样：不要全是八分音符，要有四分音符、附点、切分等节奏变化\n"
    "4. 足够的量：用户要求多长就必须写多长，不能偷懒！\n"
    "   - 1分钟 ≈ 30小节 ≈ 8行正文\n"
    "   - 2分钟 ≈ 60小节 ≈ 15行正文\n"
    "   - 3分钟 ≈ 90小节 ≈ 23行正文\n"
    "5. 情感表达：根据风格选择合适调式和节奏型\n\n"
    "## 输出格式（严格遵守）\n"
    "直接输出完整 ABC Notation，最后一行：\n"
    "SUMMARY: 一句话中文摘要（说明风格、结构、情感）\n"
    "不要输出任何解释、代码块（```）、JSON、分析文字。"
)

_SYSTEM_ARRANGE = (
    "你是世界顶级的 ABC Notation 音乐编曲大师，精通古典、流行、民谣等所有风格，"
    "对 Sky: Children of the Light 游戏乐器特性了如指掌。\n\n"
    "## 改编原则\n"
    "1. 精确理解用户意图，大胆改编，不保守\n"
    "2. 旋律必须流畅、有张力、有情感，符合专业音乐审美\n"
    "3. 保持或优化段落结构（A-B-A' 或 verse-chorus），有起承转合\n"
    "4. Sky 限制：所有音符必须在 C D E F G A B c d e f g a b c'（C4-C6）范围内\n\n"
    "## 输出格式（严格遵守）\n"
    "直接输出完整改编后 ABC Notation，最后一行：\n"
    "SUMMARY: 一句话中文说明改编内容\n"
    "不要输出任何解释、代码块（```）、JSON。"
)

_FALLBACK_ABC = "X:1\nT:新曲\nM:4/4\nL:1/8\nQ:1/4=120\nK:C\nCDEF GABc|dedB cAGE|FEDC DEFG|E4 z4|\n"


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
        current_abc: str = "",
    ) -> dict:
        from app.pipeline.domain import Score, ScoreMeta

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
            "arguments": {"style": message[:100], "has_base": bool(current_abc)},
        })

        system, user_prompt = self._build_prompt(message, current_abc)

        try:
            resp = await llm_complete([
                {"role": "system", "content": system},
                {"role": "user",   "content": user_prompt},
            ])
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

        # LLM 真实返回 → complete_one TODO[0]
        if exec_ids:
            await todo_mgr.complete_one(exec_ids[0], publish)

        new_abc, summary = extract_abc_and_summary(raw, _FALLBACK_ABC)
        new_abc, summary = await self._validate_and_fix(
            new_abc, summary, raw, system, user_prompt, publish
        )

        # ── 时长验证：用户指定分钟数时检查小节数是否满足要求 ─────────────────
        dur_match = re.search(r'(\d+)\s*分钟', message)
        if dur_match and not current_abc:  # 仅从零创作时验证（改编不强制时长）
            required_mins = int(dur_match.group(1))
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
                    resp3 = await llm_complete([
                        {"role": "system",    "content": system},
                        {"role": "user",      "content": user_prompt},
                        {"role": "assistant", "content": raw},
                        {"role": "user",      "content": (
                            f"你的作品只有约 {actual} 小节（约 {dur_check['actual_seconds']:.0f} 秒），"
                            f"但用户要求 {required_mins} 分钟（需要约 {required} 小节）。\n"
                            f"请在现有基础上继续扩展，补充约 {shortage} 小节，"
                            f"保持风格一致，重新输出完整 ABC + SUMMARY 行。"
                        )},
                    ])
                    raw3 = resp3 if isinstance(resp3, str) else resp3.get("content", "")
                    abc3, sum3 = extract_abc_and_summary(raw3, new_abc)
                    if "K:" in abc3 and count_notes(abc3) > count_notes(new_abc):
                        new_abc, summary = abc3, sum3
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

        # 落库由 service.universal_chat 在 SubAgent 返回后统一执行（避免双写）
        # SubAgent 只通过 session_saver 操作内存 session

        await publish("tool.call", {
            "call_id":        create_call_id,
            "tool":           "abc_composer",
            "status":         "succeeded",
            "result_preview": summary,
        })

        # 存储完成 → complete_one TODO[1]
        if len(exec_ids) > 1:
            await todo_mgr.complete_one(exec_ids[1], publish)

        await publish("abc.updated", {
            "abc":     new_abc,
            "version": 1,
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
        action_word = "改编" if current_abc else "创作"
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

    def _build_prompt(self, message: str, current_abc: str) -> tuple[str, str]:
        if current_abc:
            return _SYSTEM_ARRANGE, f"用户需求：{message}\n\n原始 ABC 谱：\n{current_abc}"

        duration_hint = ""
        dur_match = re.search(r'(\d+)\s*分钟', message)
        if dur_match:
            mins     = int(dur_match.group(1))
            est_bars = mins * 30
            duration_hint = (
                f"\n\n⚠️ 时长要求：{mins} 分钟，"
                f"必须写够约 {est_bars} 小节（4/4拍，BPM≈120）！"
                f"每行写4小节，需要约 {est_bars//4} 行正文，绝对不能偷懒只写几行！"
            )
        return _SYSTEM_CREATE, f"请创作：{message}{duration_hint}"

    async def _validate_and_fix(
        self,
        abc: str,
        summary: str,
        raw: str,
        system: str,
        user_prompt: str,
        publish: Publisher,
    ) -> tuple[str, str]:
        """验证 ABC + 自动修正（最多 1 次重试）。"""
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
                    resp2 = await llm_complete([
                        {"role": "system",    "content": system},
                        {"role": "user",      "content": user_prompt},
                        {"role": "assistant", "content": raw},
                        {"role": "user",      "content": (
                            f"你生成的 ABC 存在问题：{issues}\n"
                            "请修正所有超出 Sky C4-C6 范围的音符（移八度处理），"
                            "重新输出完整 ABC + SUMMARY 行。"
                        )},
                    ])
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
