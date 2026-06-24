"""
H5Agent — H5 乐谱海报 SubAgent

职责（单一）：
  - 接收 MIDI / ABC / Sky JSON 附件或文本，解析后生成 H5 海报
  - 通过 ReactExecutor + h5 工具组执行生成逻辑
  - 统一管理 h5_create / h5_edit 域 TODO 状态
  - 异常路径 finish_all(failed) + assert_finish_gate

工作流：
  1. 判断附件格式（MIDI / ABC / Sky JSON / 无附件）
  2. 调用对应解析工具（parse_midi_to_json / parse_abc_to_json / parse_sky_json_to_json）
  3. 调用 generate_h5_poster 或 generate_h5_from_abc 生成 HTML
  4. 调用 save_h5_file 持久化，返回访问路径
  5. 推送 h5.ready 事件，前端可直接预览/下载
"""
from __future__ import annotations

import json
from typing import Callable, Awaitable

from app.agentcore.todo_manager import TodoManager, assert_finish_gate
from app.agentcore.react_executor import stream_text, ReactExecutor
from app.agentcore.tools import get_tool_schemas

Publisher = Callable[[str, dict], Awaitable[None]]

# H5 Agent 使用的工具组
_H5_TOOL_GROUPS = ["h5"]

# ReactExecutor 系统提示
_H5_SYSTEM_PROMPT = """你是 EP-Agent 的 H5 页面生成专家，能生成两类 HTML 页面：

【模式A：MIDI 播放器页面】触发词：「播放」「播放器」「播放MIDI」「HTML播放」
→ 生成可在浏览器直接播放 MIDI 的 HTML（Tone.js/MIDI.js）
→ 包含：播放/暂停、进度条、音符可视化、曲目信息
→ 工具链：parse_midi_to_json（有MIDI附件时）→ generate_h5_poster(mode=player) → save_h5_file

【模式B：乐谱分享海报】触发词：「海报」「分享」「乐谱海报」
→ 精美苹果风格海报，展示乐谱信息和波形
→ 工具链：解析附件 → generate_h5_poster 或 generate_h5_from_abc → save_h5_file

工具使用策略（按附件类型）：
1. 附件 .mid/.midi → parse_midi_to_json → generate_h5_poster
2. 附件 .abc/.txt → generate_h5_from_abc（一步完成）
3. 附件 .json（Sky JSON）→ parse_sky_json_to_json → generate_h5_poster
4. 无附件但 session 有谱子 → 使用 session 中的 ABC 数据
5. 无附件无谱子 → generate_h5_poster(notes_json="[]")
6. 生成 HTML 后必须调用 save_h5_file 保存
7. 最后调用 finish_task

模板选择：播放器→player；默认→apple_dark；「清新/白色」→apple_light；「电子/霓虹」→neon；「简约」→minimal

输出契约：finish_task summary 格式：「H5页面已生成：{title}，访问路径：{url_path}」
"""


class H5Agent:
    """H5 乐谱海报 SubAgent，通过 ReactExecutor 执行工具链。"""

    async def run(
        self,
        session_id: str,
        message: str,
        attachment_b64: str,
        attachment_name: str,
        publish: Publisher,
        todo_mgr: TodoManager,
        domain: str = "h5_create",
    ) -> dict:
        ids = todo_mgr.get_ids()
        if ids:
            await todo_mgr.tick(ids[0], "running", publish)

        call_id = f"call_h5_{session_id[:8]}"
        await publish("tool.call", {
            "call_id":   call_id,
            "tool":      "h5_generator",
            "status":    "running",
            "arguments": {
                "message":      message[:80],
                "has_attachment": bool(attachment_b64),
                "attachment":   attachment_name or "",
            },
        })

        # 构建用户消息（附件信息注入）
        user_content = self._build_user_message(message, attachment_b64, attachment_name)

        # 获取 H5 工具 schema
        h5_tools = get_tool_schemas("h5")

        # 添加通用 finish_task 工具（从 abc_edit 组获取，顶层已 import get_tool_schemas）
        finish_tools = [t for t in get_tool_schemas("abc_edit") if t["function"]["name"] == "finish_task"]
        all_tools = h5_tools + finish_tools

        executor = ReactExecutor()

        try:
            exec_result = await executor.run(
                messages=[
                    {"role": "system", "content": _H5_SYSTEM_PROMPT},
                    {"role": "user",   "content": user_content},
                ],
                tools=all_tools,
                publish=publish,
                todo_manager=todo_mgr,
                max_rounds=8,
            )
        except Exception as e:
            await publish("tool.call", {
                "call_id": call_id, "tool": "h5_generator",
                "status": "failed", "error": str(e),
            })
            await todo_mgr.finish_all(publish, "failed")
            await assert_finish_gate(todo_mgr, domain, publish)
            reply = f"H5 海报生成失败：{e}"
            await stream_text(reply, publish)
            await publish("message.completed", {"message": reply})
            return {"domain": domain, "message": reply, "abc_updated": False}

        # 提取结果
        r       = exec_result if isinstance(exec_result, dict) else {}
        summary = r.get("content") or r.get("summary") or "H5 海报已生成"
        extra   = r.get("extra", {})

        await publish("tool.call", {
            "call_id":        call_id,
            "tool":           "h5_generator",
            "status":         "succeeded",
            "result_preview": summary[:120],
        })

        # 推送 H5 就绪事件（前端可据此展示预览/下载链接）
        if extra.get("url_path") or extra.get("file_path"):
            await publish("h5.ready", {
                "title":     extra.get("title", "乐谱海报"),
                "url_path":  extra.get("url_path", ""),
                "file_path": extra.get("file_path", ""),
                "size_kb":   extra.get("size_kb", 0),
                "template":  extra.get("template", "apple_dark"),
            })

        if ids:
            await todo_mgr.complete_one(ids[0], publish)
        await todo_mgr.finish_all(publish, "done")
        await assert_finish_gate(todo_mgr, domain, publish)

        await stream_text(summary, publish)
        await publish("message.completed", {"message": summary})
        return {
            "domain":      domain,
            "message":     summary,
            "abc_updated": False,
            **extra,
        }

    def _build_user_message(
        self,
        message: str,
        attachment_b64: str,
        attachment_name: str,
    ) -> str:
        """构建注入附件信息的用户消息。"""
        parts = [message]

        if attachment_b64 and attachment_name:
            name_lower = attachment_name.lower()

            if name_lower.endswith(".mid") or name_lower.endswith(".midi"):
                parts.append(
                    f"\n\n[附件信息]\n"
                    f"attachment_format: midi\n"
                    f"attachment_name: {attachment_name}\n"
                    f"attachment_b64: {attachment_b64[:64]}...（已截断，请用 parse_midi_to_json 工具解析）\n"
                    f"调用提示：parse_midi_to_json(midi_b64=<完整base64>)"
                )
                # 将完整 b64 附加在特殊标记后（ReactExecutor 可在工具调用时引用）
                parts.append(f"\n[MIDI_B64_FULL]{attachment_b64}[/MIDI_B64_FULL]")

            elif name_lower.endswith(".abc") or name_lower.endswith(".txt"):
                # ABC 文本：直接解码内容
                try:
                    import base64
                    abc_text = base64.b64decode(attachment_b64).decode("utf-8", errors="replace")
                    parts.append(
                        f"\n\n[附件内容 - ABC Notation]\n```abc\n{abc_text[:2000]}\n```\n"
                        f"调用提示：generate_h5_from_abc(abc=<上方ABC内容>)"
                    )
                except Exception:
                    parts.append(f"\n\n[附件] {attachment_name}（解码失败，请用工具处理）")

            elif name_lower.endswith(".json"):
                # Sky JSON
                try:
                    import base64
                    json_text = base64.b64decode(attachment_b64).decode("utf-8", errors="replace")
                    parts.append(
                        f"\n\n[附件内容 - Sky JSON]\n```json\n{json_text[:2000]}\n```\n"
                        f"调用提示：parse_sky_json_to_json(sky_json_str=<上方JSON内容>)"
                    )
                except Exception:
                    parts.append(f"\n\n[附件] {attachment_name}（解码失败，请用工具处理）")

            else:
                parts.append(f"\n\n[附件] {attachment_name}（未知格式，请根据内容判断处理方式）")

        return "".join(parts)
