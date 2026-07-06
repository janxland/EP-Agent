"""
ConvertAgent — Sky JSON / ABC 转换 SubAgent（v3.2）

职责（单一）：
  - 从附件/消息中提取合法 Sky JSON（_extract_sky_json）或 ABC 谱（_extract_abc）
  - Sky JSON → ABC：调用 convert_fn 执行转换
  - ABC 直接加载：解析元数据写入 session，无需再转换
  - 管理 convert 域 TODO 状态（pending→running→done）
  - 返回 {"valid": False} 表示既不是 Sky JSON 也不是 ABC（由调用方降级处理）

设计原则（低耦合）：
  - 不持有 session 状态，通过参数接收
  - 不感知降级逻辑（降级由 universal_runner._dispatch 处理）
  - 不持有 todos_task（await 由调用方负责）
  - 异常路径：finish_all(failed) + assert_finish_gate
  - 成功路径：complete_one + finish_all(done) + assert_finish_gate
"""
from __future__ import annotations

import json
import re
from typing import Callable, Awaitable

from app.agentcore.todo_manager import TodoManager, assert_finish_gate
from app.agentcore.agent_registry import register

if False:  # TYPE_CHECKING
    from app.agentcore.run_context import RunContext
from app.agentcore.react_executor import stream_text

Publisher = Callable[[str, dict], Awaitable[None]]


def _extract_sky_json(raw: str) -> str | None:
    """从原始文本中提取合法的 Sky JSON（含 songNotes 字段）。"""
    raw = raw.strip()
    # 1. 直接尝试整体解析
    try:
        parsed = json.loads(raw)
        arr = parsed if isinstance(parsed, list) else [parsed]
        if arr and isinstance(arr[0], dict) and arr[0].get("songNotes"):
            return raw
    except Exception:
        pass
    # 2. 尝试提取第一个 JSON 数组 [...] 或对象 {...}
    for pattern in (r'\[.*\]', r'\{.*\}'):
        m = re.search(pattern, raw, re.DOTALL)
        if m:
            try:
                parsed = json.loads(m.group())
                arr = parsed if isinstance(parsed, list) else [parsed]
                if arr and isinstance(arr[0], dict) and arr[0].get("songNotes"):
                    return m.group()
            except Exception:
                pass
    return None


def _extract_abc(content: str, filename: str) -> str | None:
    """
    判断内容是否为合法 ABC 谱，返回清理后的 ABC 字符串，否则返回 None。
    判断依据：含有 X: 或 T: 且 K: 字段，或文件扩展名为 .abc。
    """
    # 文件名扩展名优先
    if filename.lower().endswith(".abc"):
        stripped = content.strip()
        if stripped:
            return stripped

    # 内容特征检测：必须有 K: 行（ABC 必填字段）
    content_stripped = content.strip()
    if not content_stripped:
        return None
    has_key = bool(re.search(r'^K:', content_stripped, re.MULTILINE))
    has_title_or_index = bool(re.search(r'^[XT]:', content_stripped, re.MULTILINE))
    if has_key and has_title_or_index:
        return content_stripped
    # 宽松匹配：只要有 K: 行且有音符行（含 CDEFGAB 的非 header 行）
    if has_key:
        for line in content_stripped.splitlines():
            if line and not re.match(r'^[A-Za-z]:', line) and re.search(r'[A-Ga-g]', line):
                return content_stripped
    return None


@register("convert")
class ConvertAgent:
    """
    Sky JSON / ABC → 加载 SubAgent。

    run() 返回值：
      - 成功（Sky JSON）：{"domain": "convert", "abc_updated": True, ...}
      - 成功（ABC 直接加载）：{"domain": "convert", "abc_updated": True, "source": "abc"}
      - 非法格式：{"domain": "convert", "valid": False}  ← 调用方据此降级
      - 失败：{"domain": "convert", "abc_updated": False, "message": "..."}
    """

    async def run(
        self,
        session_id: str,
        message: str,
        attachment_content: str,
        attachment_name: str,
        publish: Publisher,
        convert_fn: Callable,
        todo_mgr: TodoManager,
        session_getter: Callable,
        session_saver: Callable,
    ) -> dict:
        json_content = attachment_content or message
        file_name    = attachment_name or "score"

        # ── 优先尝试提取 Sky JSON ─────────────────────────────────────────────
        sky_json = _extract_sky_json(json_content)
        if sky_json:
            return await self._run_sky_json(
                sky_json, file_name, session_id, publish,
                convert_fn, todo_mgr, session_getter, session_saver,
            )

        # ── 其次尝试识别 ABC 谱 ───────────────────────────────────────────────
        abc_content = _extract_abc(json_content, file_name)
        if abc_content:
            return await self._run_abc_direct(
                abc_content, file_name, session_id, publish,
                todo_mgr, session_getter, session_saver,
            )

        # ── 两者都不是 → 降级 ─────────────────────────────────────────────────
        await todo_mgr.finish_all(publish, "failed")
        await publish("pipeline.step", {
            "step":   "convert_not_recognized",
            "status": "warning",
            "text":   (
                f"附件「{file_name}」不是 Sky JSON 也不是 ABC 谱格式，"
                "已自动切换到「创作模式」，将根据你的描述创作一首新谱子。"
            ),
        })
        await stream_text(
            f"⚠️ 附件「{file_name}」不是 Sky 谱子格式，已切换为创作模式。",
            publish,
        )
        return {"domain": "convert", "valid": False}

    # ── Sky JSON 转换路径 ──────────────────────────────────────────────────────

    async def _run_sky_json(
        self, sky_json: str, file_name: str, session_id: str,
        publish: Publisher, convert_fn: Callable, todo_mgr: TodoManager,
        session_getter: Callable, session_saver: Callable,
    ) -> dict:
        ids = todo_mgr.get_ids()
        if ids:
            await todo_mgr.tick(ids[0], "running", publish)

        convert_call_id = f"call_convert_{session_id[:8]}"
        await publish("tool.call", {
            "call_id":   convert_call_id,
            "tool":      "convert_sky_json",
            "status":    "running",
            "arguments": {"file_name": file_name, "size": len(sky_json)},
        })

        try:
            result = await convert_fn(session_id, sky_json, file_name, publish)
        except Exception as e:
            await publish("tool.call", {
                "call_id": convert_call_id, "tool": "convert_sky_json",
                "status": "failed", "error": str(e),
            })
            if ids:
                await todo_mgr.tick(ids[0], "failed", publish)
            await todo_mgr.finish_all(publish, "failed")
            await assert_finish_gate(todo_mgr, "convert", publish)
            reply = f"谱子转换失败：{e}"
            await stream_text(reply, publish)
            await publish("message.completed", {"message": reply})
            return {"domain": "convert", "message": reply, "abc_updated": False}

        meta = result.get("meta", {})
        await publish("tool.call", {
            "call_id":        convert_call_id,
            "tool":           "convert_sky_json",
            "status":         "succeeded",
            "result_preview": f"《{meta.get('title','')}》{meta.get('note_count',0)} 音符",
        })

        if ids:
            await todo_mgr.complete_one(ids[0], publish)

        await publish("abc.updated", {
            "abc":     result.get("abc_notation", ""),
            "version": 1,
            "meta":    meta,
        })

        # 落盘到项目 .sky/ 目录（通过 ContextVar 推断路径，无需查 DB）
        _ws_abc_path = ""
        try:
            if result.get("abc_notation"):
                from app.agentcore.tools.abc_tools import save_score_to_workspace_impl
                _save_r = save_score_to_workspace_impl(
                    abc_notation=result["abc_notation"],
                    title=meta.get("title") or "score",
                    overwrite=True,
                )
                _ws_abc_path = _save_r["path"]
                from app.agentcore.session_context import remember_workspace_file
                remember_workspace_file(session_id, _ws_abc_path,
                                        meta.get("title") or "score")
        except Exception:
            pass

        load_reply = (
            f"✅ 已成功加载谱子《{meta.get('title', '未命名')}》\n\n"
            f"- 调号：{meta.get('key', 'C')}\n"
            f"- BPM：{meta.get('bpm', 120):.0f}\n"
            f"- 音符数：{meta.get('note_count', 0)}\n\n"
            "你可以继续说「升高八度」「加快节奏」或「生成配乐」等。"
        )

        await stream_text(load_reply, publish)
        await todo_mgr.finish_all(publish, "done")
        await assert_finish_gate(todo_mgr, "convert", publish)
        await publish("message.completed", {"message": load_reply})

        return {
            "domain":      "convert",
            "valid":       True,
            "message":     load_reply,
            "abc_updated": True,
            **result,
        }

    # ── ABC 直接加载路径 ───────────────────────────────────────────────────────

    async def _run_abc_direct(
        self, abc: str, file_name: str, session_id: str,
        publish: Publisher, todo_mgr: TodoManager,
        session_getter: Callable, session_saver: Callable,
    ) -> dict:
        """ABC 文件直接加载到 session，解析元数据，推送 abc.updated 事件。"""
        ids = todo_mgr.get_ids()
        if ids:
            await todo_mgr.tick(ids[0], "running", publish)

        load_call_id = f"call_load_abc_{session_id[:8]}"
        await publish("tool.call", {
            "call_id":   load_call_id,
            "tool":      "load_abc_score",
            "status":    "running",
            "arguments": {"file_name": file_name, "size": len(abc)},
        })

        # BUG-02 修复：使用 parse_abc_header() 代替手写解析
        # 手写解析缺少 time_sig_num/time_sig_den，note_count 计数方式也不一致
        from app.agentcore.abc_utils import parse_abc_header, count_notes
        _header = parse_abc_header(abc)
        meta = {
            "title":        _header.get("title") or file_name.replace(".abc", ""),
            "key":          _header.get("key") or "C",
            "bpm":          _header.get("bpm") or 120,
            "note_count":   count_notes(abc),
            "time_sig":     f"{_header.get('time_sig_num', 4)}/{_header.get('time_sig_den', 4)}",
            "time_sig_num": _header.get("time_sig_num", 4),
            "time_sig_den": _header.get("time_sig_den", 4),
        }

        # 写入 session score（让后续 edit/create 可以感知）
        # BUG-01 修复：统一使用 Score + ScoreMeta，与 create_agent/edit_agent 一致
        try:
            sess = session_getter(session_id)
            if sess is not None:
                from app.pipeline.domain import Score, ScoreMeta
                # BUG-C1 修复：补充 time_sig_num/den，避免后续 edit 时拍号信息丢失
                sess.score = Score(
                    title=meta["title"],
                    abc_notation=abc,
                    meta=ScoreMeta(
                        title=meta["title"],
                        key=meta["key"],
                        bpm=float(meta["bpm"]),
                        note_count=meta["note_count"],
                        time_sig_num=meta.get("time_sig_num", 4),
                        time_sig_den=meta.get("time_sig_den", 4),
                    ),
                )
                session_saver(sess)
        except Exception:
            pass

        await publish("tool.call", {
            "call_id":        load_call_id,
            "tool":           "load_abc_score",
            "status":         "succeeded",
            "result_preview": f"《{meta['title']}》{meta['note_count']} 音符",
        })

        # 推送 abc.updated 让前端 ABC 编辑器同步
        await publish("abc.updated", {
            "abc":     abc,
            "version": 1,
            "meta":    meta,
        })

        # 落盘到项目 .sky/ 目录（通过 ContextVar 推断路径，无需查 DB）
        try:
            from app.agentcore.tools.abc_tools import save_score_to_workspace_impl
            _save_r = save_score_to_workspace_impl(
                abc_notation=abc,
                title=meta["title"],
                overwrite=True,
            )
            from app.agentcore.session_context import remember_workspace_file
            remember_workspace_file(session_id, _save_r["path"], meta["title"])
        except Exception:
            pass

        if ids:
            await todo_mgr.complete_one(ids[0], publish)

        # NEW-13 修复：BPM 可能是 float（如 120.0），格式化为整数显示更友好
        _bpm_display = f"{float(meta['bpm']):.0f}" if meta.get("bpm") is not None else "120"
        load_reply = (
            f"✅ 已成功加载谱子《{meta['title']}》\n\n"
            f"- 调号：{meta['key']}\n"
            f"- BPM：{_bpm_display}\n"
            f"- 音符数：{meta['note_count']}\n\n"
            "你可以继续说「转为 MIDI」「升高八度」「加快节奏」或「生成 H5」等。"
        )

        await stream_text(load_reply, publish)
        await todo_mgr.finish_all(publish, "done")
        await assert_finish_gate(todo_mgr, "convert", publish)
        await publish("message.completed", {"message": load_reply})

        return {
            "domain":      "convert",
            "valid":       True,
            "source":      "abc",
            "message":     load_reply,
            "abc_updated": True,
            "abc_notation": abc,
            "meta":        meta,
        }

    async def run_with_ctx(self, ctx: "RunContext") -> dict:
        """v4.0 解耦接口：从 RunContext 解包参数，调用原 run()。
        AGENT-2 修复：session_getter/saver 通过 ctx 属性统一解包（fallback 逻辑在 RunContext 中）。
        """
        convert_fn = ctx.extra.get("convert_fn") or (lambda *a, **kw: {})
        todo_mgr   = ctx.extra.get("todo_mgr")
        if todo_mgr is None:
            from app.agentcore.todo_manager import TodoManager as _TM
            todo_mgr = _TM()
            todo_mgr.session_id = ctx.session_id
        return await self.run(
            session_id=ctx.session_id,
            message=ctx.message,
            attachment_content=ctx.attachment_content,
            attachment_name=ctx.attachment_name,
            publish=ctx.publish,
            convert_fn=convert_fn,
            todo_mgr=todo_mgr,
            session_getter=ctx.session_getter,
            session_saver=ctx.session_saver,
        )

