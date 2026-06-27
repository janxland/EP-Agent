"""
H5Agent — H5 乐谱海报 SubAgent

职责（单一）：
  - 接收 MIDI / ABC / Sky JSON 附件或文本，解析后生成 H5 海报
  - 通过 ReactExecutor + h5 工具组执行生成逻辑
  - 统一管理 h5_create / h5_edit 域 TODO 状态
  - 异常路径 finish_all(failed) + assert_finish_gate

工作流：
  1. 判断附件格式（MIDI / ABC / Sky JSON / 无附件）
  2. 调用对应解析工具（parse_abc_to_json / parse_sky_json_to_json；MIDI 直接走 generate_h5_from_midi）
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
_H5_TOOL_GROUPS = ["h5", "workspace"]

# ReactExecutor 系统提示（v4.0 — 文件路径驱动，CDN 库处理 MIDI，LLM 不碰二进制）
_H5_SYSTEM_PROMPT = """你是 EP-Agent 的 H5 乐谱海报设计专家，将乐谱转化为精美的移动端 H5 分享页面。

## ⚡ 核心原则：文件路径驱动，CDN 库处理音频

**你永远不需要处理 base64 或二进制数据。**
MIDI 文件由工具直接从工作区读取，H5 模板内置 CDN 播放库（MidiPlayerJS + Soundfont），
浏览器端自动加载播放，你只需传文件路径。

---

## 工作流程（按文件类型）

### MIDI 文件（.mid/.midi）→ 3 步完成
```
list_h5_templates()
↓ 选模板
generate_h5_from_midi(midi_workspace_path=<工作区路径>, title=<标题>, template=<模板名>)
↓ 得到 html + midi_url
save_h5_file(html=<html>, filename=<安全文件名>)
↓
finish_task(summary="H5 海报已生成：{title}，模板：{template}，访问路径：{url_path}")
```

### ABC / 文本文件 → 3 步完成
```
list_h5_templates()
↓
generate_h5_from_abc(abc=<内容>, template=<模板名>, ...)
↓
save_h5_file(...) → finish_task(...)
```

### Sky JSON 文件 → 4 步
```
list_h5_templates()
↓
parse_sky_json_to_json(sky_json_str=<json文本>)
↓
generate_h5_poster(title=<标题>, notes_json=<JSON字符串>, template=<模板名>)
↓
save_h5_file(...) → finish_task(...)
```

### 需要查找工作区文件时
```
list_workspace_files()
↓ 找到文件路径后按对应流程
```

---

## 模板选择（调用 list_h5_templates 后决定）
- 用户明确指定 → 直接使用
- 用户说"苹果风格"/"简洁"/"白色" → apple
- 用户未指定 → 默认 apple

---

## 关键约束
- **禁止**读取或传递任何 base64 / 二进制内容给 LLM，只传文件路径
- finish_task 的 summary 必须包含访问路径（从 save_h5_file 返回的 url_path）
- notes_json 参数必须是 JSON 字符串
"""


class H5Agent:
    """H5 乐谱海报 SubAgent，通过 ReactExecutor 执行工具链。"""

    async def run(
        self,
        session_id: str,
        message: str,
        attachment_workspace_path: str,   # 工作区内文件路径（如 .sky/song.mid），不再传 base64
        attachment_name: str,
        publish: Publisher,
        todo_mgr: TodoManager,
        domain: str = "h5_create",
        workspace_id: str = "",
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
                "has_attachment": bool(attachment_workspace_path),
                "attachment":   attachment_name or "",
            },
        })

        # ── 重要记忆感知：若前端未传附件路径，从 Session 记忆中主动发现最新文件 ──
        # 查找优先级：midi → abc → json（MIDI 可直接生成 H5；ABC/JSON 需额外解析步骤）
        # 从 Session 记忆中主动发现最新文件（无需 workspace_id，ContextVar 自动推断）
        if not attachment_workspace_path:
            try:
                from app.agentcore.session_context import recall_latest_file
                import os
                _remembered_path = (
                    recall_latest_file(session_id, "midi")
                    or recall_latest_file(session_id, "abc")
                    or recall_latest_file(session_id, "json")
                )
                if _remembered_path:
                    attachment_workspace_path = _remembered_path
                    attachment_name = attachment_name or os.path.basename(_remembered_path)
            except Exception:
                pass

        # 构建用户消息（附件信息注入）
        user_content = self._build_user_message(
            message, attachment_workspace_path, attachment_name,
        )

        # ── 记忆前缀注入：将 Session 携带体追加到 system prompt ──────────────
        # 让 Agent 在新对话开始时就携带上轮的重要上下文（文件路径、用户意图等）
        system_prompt = _H5_SYSTEM_PROMPT
        try:
            from app.agentcore.memory_manager import build_memory_prefix
            _mem_prefix = build_memory_prefix(session_id)
            if _mem_prefix:
                system_prompt = _H5_SYSTEM_PROMPT + _mem_prefix
        except Exception:
            pass

        # 获取 H5 工具 schema（含 list_h5_templates / generate_h5_poster 等）
        h5_tools = get_tool_schemas("h5")
        # 工作区工具：list_workspace_files / read_workspace_file
        workspace_tools = get_tool_schemas("workspace")

        # finish_task 直接从全量工具中查找（各组均不含，兜底最可靠）
        finish_tools = [t for t in get_tool_schemas() if t["function"]["name"] == "finish_task"][:1]

        all_tools = h5_tools + workspace_tools + finish_tools

        executor = ReactExecutor()

        try:
            exec_result = await executor.run(
                messages=[
                    {"role": "system", "content": system_prompt},  # 含记忆前缀的版本
                    {"role": "user",   "content": user_content},
                ],
                tools=all_tools,
                publish=publish,
                todo_manager=todo_mgr,
                max_rounds=12,  # H5 工作流：list_templates→解析→生成→保存→finish，需要更多轮次
                session_id=session_id,  # 传入 session_id，让 ReactExecutor 落库 tool message
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
                "title":          extra.get("title", "乐谱海报"),
                "url_path":       extra.get("url_path", ""),
                "file_path":      extra.get("file_path", ""),
                "size_kb":        extra.get("size_kb", 0),
                "template":       extra.get("template", "apple"),
                "workspace_path": extra.get("workspace_path", ""),
            })
            # H5 文件已写入工作区 h5/ 目录，触发文件树刷新
            if extra.get("workspace_path"):
                await publish("workspace.file_saved", {
                    "path":  extra["workspace_path"],
                    "type":  "h5",
                    "title": extra.get("title", ""),
                })

        if ids:
            await todo_mgr.complete_one(ids[0], publish)
        await todo_mgr.finish_all(publish, "done")
        await assert_finish_gate(todo_mgr, domain, publish)

        # ReactExecutor 流式 Tool Calling 已将 LLM 最终回复实时推送为 message.delta
        # 此处只推送 completed 事件，不再重复 stream_text，避免消息重复显示
        await publish("message.completed", {"message": summary})
        return {
            "domain":      domain,
            "message":     summary,
            "abc_updated": False,
            # ReactExecutor 已落库所有 assistant/tool 消息，service.py 无需重复写入
            "_persisted":  True,
            **extra,
        }

    def _build_user_message(
        self,
        message: str,
        attachment_workspace_path: str,
        attachment_name: str,
    ) -> str:
        """
        构建用户消息，注入文件路径信息。
        永远不传 base64，只传工作区路径，LLM 通过路径调用工具。
        工具通过 ContextVar 自动推断项目根目录，无需 workspace_id。
        """
        parts = [message]

        if attachment_workspace_path and attachment_name:
            name_lower = attachment_name.lower()
            if name_lower.endswith(".mid") or name_lower.endswith(".midi"):
                parts.append(
                    f"\n\n[MIDI 文件]\n"
                    f"workspace_path: {attachment_workspace_path}\n"
                    f"⚡ 直接调用：generate_h5_from_midi("
                    f"midi_workspace_path=\"{attachment_workspace_path}\", title=<标题>, template=<模板名>)"
                )
            elif name_lower.endswith(".abc") or name_lower.endswith(".txt"):
                try:
                    from app.agentcore.session_context import get_current_project_root
                    _root = get_current_project_root()
                    abc_text = (_root / attachment_workspace_path).read_text(encoding="utf-8", errors="replace") if _root else ""
                    if abc_text:
                        parts.append(
                            f"\n\n[ABC 文件内容]\n```abc\n{abc_text[:3000]}\n```\n"
                            f"⚡ 直接调用：generate_h5_from_abc(abc=<上方 ABC 内容>, template=<模板名>)"
                        )
                    else:
                        raise ValueError("无法读取")
                except Exception:
                    parts.append(
                        f"\n\n[ABC 文件]\nworkspace_path: {attachment_workspace_path}\n"
                        f"请调用 read_workspace_file(file_path=\"{attachment_workspace_path}\") 读取内容，"
                        f"再调用 generate_h5_from_abc(abc=<读取的内容>)"
                    )
            elif name_lower.endswith(".json"):
                try:
                    from app.agentcore.session_context import get_current_project_root
                    _root = get_current_project_root()
                    json_text = (_root / attachment_workspace_path).read_text(encoding="utf-8", errors="replace") if _root else ""
                    if json_text:
                        parts.append(
                            f"\n\n[Sky JSON 文件内容]\n```json\n{json_text[:3000]}\n```\n"
                            f"⚡ 直接调用：parse_sky_json_to_json(sky_json_str=<上方 JSON 内容>)"
                        )
                    else:
                        raise ValueError("无法读取")
                except Exception:
                    parts.append(
                        f"\n\n[JSON 文件]\nworkspace_path: {attachment_workspace_path}\n"
                        f"请调用 read_workspace_file 读取"
                    )
            else:
                parts.append(
                    f"\n\n[文件]\nworkspace_path: {attachment_workspace_path}\n"
                    f"filename: {attachment_name}"
                )

        else:
            # 无指定附件 → 提示从项目目录查找（abc_to_midi 生成的 MIDI 已自动记入记忆）
            parts.append(
                "\n\n⚡ 请先调用 list_workspace_files() "
                "查找项目中的 MIDI（.sky/*.mid）/ ABC / JSON 文件，"
                "找到 MIDI 文件后直接调用 generate_h5_from_midi 生成 H5，无需任何额外解析步骤。"
            )

        return "".join(parts)
