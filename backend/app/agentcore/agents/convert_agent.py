"""
ConvertAgent — Sky JSON → ABC 转换 SubAgent（v3.1）

职责（单一）：
  - 从附件/消息中提取合法 Sky JSON（_extract_sky_json）
  - 调用 convert_fn 执行转换
  - 管理 convert 域 TODO 状态（pending→running→done）
  - 返回 {"valid": False} 表示不是合法 Sky JSON（由调用方降级处理）

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


class ConvertAgent:
    """
    Sky JSON → ABC 转换 SubAgent。

    run() 返回值：
      - 成功：{"domain": "convert", "abc_updated": True, ...}
      - 非法 JSON：{"domain": "convert", "valid": False}  ← 调用方据此降级
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
        file_name    = attachment_name or "score.json"

        # ── 提取合法 Sky JSON ─────────────────────────────────────────────────
        sky_json = _extract_sky_json(json_content)
        if not sky_json:
            # 不是合法 Sky JSON → 通知调用方降级（不在此处理降级逻辑）
            # 向用户解释为什么切换到创作模式，避免"静默降级"的困惑
            await todo_mgr.finish_all(publish, "failed")
            await publish("pipeline.step", {
                "step":   "convert_not_sky_json",
                "status": "warning",
                "text":   (
                    f"附件「{file_name}」不是合法的 Sky 谱子格式（未找到 songNotes 字段），"
                    "已自动切换到「创作模式」，将根据你的描述创作一首新谱子。"
                ),
            })
            # 推送一条简短的用户可见消息，让用户知道发生了什么
            await stream_text(
                f"⚠️ 附件「{file_name}」不是 Sky 谱子格式，已切换为创作模式。",
                publish,
            )
            return {"domain": "convert", "valid": False}

        # ── TODO 纪律：开始执行时 tick running ───────────────────────────────
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

        # ── 调用 convert_fn（真实执行）───────────────────────────────────────
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

        # ── TODO 纪律：convert_fn 真实返回后 complete_one ────────────────────
        if ids:
            await todo_mgr.complete_one(ids[0], publish)

        await publish("abc.updated", {
            "abc":     result.get("abc_notation", ""),
            "version": 1,
            "meta":    meta,
        })

        # ── 落盘到工作区 .sky/ 目录 + 写入重要记忆 ──────────────────────────
        # convert 成功后 ABC 只在 sess.score（内存），需落盘才能被 H5Agent 等跨轮次感知
        _ws_abc_path = ""
        try:
            from app.pipeline import db as _db_ref
            _si = _db_ref.get_session_info(session_id)
            _ws_id = (_si or {}).get("workspace_id") or ""
            if _ws_id and result.get("abc_notation"):
                from app.agentcore.tools.workspace_tools import save_score_to_workspace_impl
                _save_r = save_score_to_workspace_impl(
                    workspace_id=_ws_id,
                    abc_notation=result["abc_notation"],
                    title=meta.get("title") or "score",
                    overwrite=True,
                )
                _ws_abc_path = _save_r["path"]
                # 写入重要记忆：ABC 路径（供 H5Agent 等跨轮次感知）
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
