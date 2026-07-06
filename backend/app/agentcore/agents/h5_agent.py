"""
H5Agent — H5 乐谱海报 SubAgent

职责（单一）：
  - 接收 MIDI / ABC / Sky JSON 附件或文本，解析后生成 H5 海报
  - 通过 ReactExecutor + h5 工具组执行生成逻辑
  - 统一管理 h5_create / h5_edit 域 TODO 状态
  - 异常路径 finish_all(failed) + assert_finish_gate

工作流（新架构 v2.0 — 大模型直接读写 HTML）：
  1. 判断附件格式（MIDI / ABC / Sky JSON / 无附件）
  2. MIDI → generate_h5_from_midi（二进制专属）
  3. ABC / Sky JSON / 纯描述 →
       list_h5_templates() 选模板
       → get_h5_template(name) 读取 HTML 源码
       → 直接修改 HTML 字符串（替换占位符 / 嵌视频 / 改样式）
       → save_h5_output(html=..., filename=..., template=...) 保存
  4. 推送 h5.ready 事件，前端可直接预览/下载
"""
from __future__ import annotations

import json
from typing import Callable, Awaitable

from app.agentcore.todo_manager import TodoManager, assert_finish_gate
from app.agentcore.agent_registry import register

if False:  # TYPE_CHECKING
    from app.agentcore.run_context import RunContext
from app.agentcore.react_executor import stream_text, ReactExecutor
from app.agentcore.tools import get_tool_schemas

Publisher = Callable[[str, dict], Awaitable[None]]

# H5 Agent 使用的工具组（只用 h5 组，workspace 组仅保留 list_workspace_files）
_H5_TOOL_GROUPS = ["h5"]

# ReactExecutor 系统提示（v5.0 — 大模型直接读写 HTML，像 Claude Code 一样操作文件）
_H5_SYSTEM_PROMPT = """你是 EP-Agent 的 H5 乐谱海报设计专家，将乐谱转化为精美的移动端 H5 分享页面。

## ⚡ 核心原则：大模型直接读写 HTML

你像 Claude Code 一样操作文件：
- **读模板 HTML** → **在内存里精准替换** → **写回保存**
- 不依赖任何"注入工具"，你就是那个改代码的人
- 可以修改任意内容：占位符替换、嵌视频 iframe、改 CSS、加歌词段落

**你永远不需要处理 base64 或二进制数据。**
MIDI 文件由 generate_h5_from_midi 工具处理，其余全部通过 get_h5_template 读取 HTML 后直接修改。

---

## 工作流程（按文件类型）

### MIDI 文件（.mid/.midi）→ 最多 3 步
```
list_h5_templates()          → 选模板 name
generate_h5_from_midi(
  midi_workspace_path=<路径>,
  title=<标题>,
  template=<模板名>,
  video_url=<视频链接（可选）>,
)                            → 返回 html（已渲染）+ workspace_path
# 若无需追加修改 → 直接 finish_task（工具已自动保存）
# 若需追加修改（嵌视频/加歌词）→ 直接编辑 html 字符串 → save_h5_output
```

### ABC / Sky JSON / 纯描述 → 最多 4 步
```
list_h5_templates()          → 根据 intent_keys 选模板 name
get_h5_template(name)        → 取 html 字段（完整 HTML 源码）
# 直接在 html 字符串中替换（精准按行修改）：
#   luoxiaohei v7：只改底部 ep-config JSON 块（约 15 行）
#   其他模板：逐一替换 {{VAR}} 散点占位符
save_h5_output(
  html=<修改后完整HTML>,
  filename=<曲名>,
  template=<模板名>,          ← 必填，自动复制 style.css/player.js/assets/
)
finish_task(summary=<含 url_path 的摘要>)
```

### 需要查找工作区文件时
```
list_workspace_files()       → 找到 MIDI / ABC / JSON 文件路径
```

---

## luoxiaohei v7 模板：ep-config JSON 结构

模板底部有唯一配置块，**只替换这一个 JSON 块**，player.js 自动读取并填充整个页面：
```json
{
  "TITLE":         "曲名",
  "COMPOSER":      "作曲者",
  "BPM":           "120",
  "KEY":           "C 大调",
  "FORMAT_LABEL":  "ABC Notation",
  "MIDI_URL":      "./曲名.mid",
  "ABC_CONTENT":   "X:1\\nT:...",
  "NIGHT_MOOD":    "深夜 · 月光 · 轻柔",
  "CAT_EMOJI":     "🐱",
  "VIDEO_URL":     "https://b23.tv/xxx",
  "VIDEO_TITLE":   "视频标题",
  "VIDEO_PLATFORM":"哔哩哔哩",
  "EXTRA_HTML":    "<p>歌词</p>",
  "NOTES_JSON":    []
}
```

## 视频嵌入（两种方式任选）

**方式 A（推荐）**：填 VIDEO_URL，player.js 自动转 embed：
- `https://www.bilibili.com/video/BVxxxxxx` → 自动转 bilibili embed
- `https://youtu.be/xxxxxx` → 自动转 YouTube embed

**方式 B（完全自由）**：直接在 HTML 中找 `<div id="videoWrap">` 插入 iframe：
```html
<iframe src="https://player.bilibili.com/player.html?bvid=BVxxxxxx&danmaku=0"
  width="100%" height="240" frameborder="0" allowfullscreen
  style="border-radius:12px;display:block"></iframe>
```

## 修改原则（节省 token）

- **只改需要改的行**，不重写整个文件
- luoxiaohei v7：只替换 ep-config JSON 块内的值，其余 HTML 原样保留
- 空值留空字符串 `""`，player.js 会自动隐藏对应区块
- ABC 内容换行用 `\\n` 转义（JSON 字符串内）
- finish_task 的 summary 必须包含访问路径（从 save_h5_output 返回的 url_path）
"""


@register("h5_create", "h5_edit")
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
                "message":        message[:80],
                "has_attachment": bool(attachment_workspace_path),
                "attachment":     attachment_name or "",
            },
        })

        # ── 记忆感知：若前端未传附件路径，从 Session 记忆中主动发现最新文件 ──
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

        # ── 记忆前缀注入 ──────────────────────────────────────────────────────
        system_prompt = _H5_SYSTEM_PROMPT
        try:
            from app.agentcore.memory_manager import build_memory_prefix
            _mem_prefix = build_memory_prefix(session_id)
            if _mem_prefix:
                system_prompt = _H5_SYSTEM_PROMPT + _mem_prefix
        except Exception:
            pass

        # ── 工具组装：h5 组（6个专属工具）+ workspace 文件操作工具 ───────────
        h5_tools = get_tool_schemas("h5")

        # 从 workspace 组取文件操作工具（list + read，H5 Agent 需要读 ABC 文件内容）
        _ws_needed = {"list_workspace_files", "read_workspace_files"}
        ws_tools_all = get_tool_schemas("workspace")
        ws_tools = [t for t in ws_tools_all
                    if t["function"]["name"] in _ws_needed]

        # finish_task 兜底查找
        finish_tools = [t for t in get_tool_schemas()
                        if t["function"]["name"] == "finish_task"][:1]

        all_tools = h5_tools + ws_tools + finish_tools

        executor = ReactExecutor()

        try:
            exec_result = await executor.run(
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user",   "content": user_content},
                ],
                tools=all_tools,
                publish=publish,
                todo_manager=todo_mgr,
                max_rounds=8,   # list→get→修改→save→finish 最多 5 步，留 3 步余量
                session_id=session_id,
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

        await publish("message.completed", {"message": summary})
        return {
            "domain":      domain,
            "message":     summary,
            "abc_updated": False,
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
        永远不传 base64，只传工作区路径，LLM 通过工具调用处理。
        """
        parts = [message]

        if attachment_workspace_path and attachment_name:
            name_lower = attachment_name.lower()

            if name_lower.endswith(".mid") or name_lower.endswith(".midi"):
                # MIDI：直接告知路径，调用 generate_h5_from_midi
                parts.append(
                    f"\n\n[MIDI 文件]\n"
                    f"workspace_path: {attachment_workspace_path}\n"
                    f"⚡ 先调用 list_h5_templates() 选模板，再调用：\n"
                    f"generate_h5_from_midi("
                    f"midi_workspace_path=\"{attachment_workspace_path}\", "
                    f"title=<标题>, template=<模板名>)"
                )

            elif name_lower.endswith(".abc") or name_lower.endswith(".txt"):
                # .txt 可能是 Sky JSON 谱子（songNotes），也可能是 ABC；需先读取内容判断
                try:
                    from app.agentcore.session_context import get_current_project_root
                    _root = get_current_project_root()
                    file_text = (
                        (_root / attachment_workspace_path)
                        .read_text(encoding="utf-8", errors="replace")
                        if _root else ""
                    )
                    if not file_text:
                        raise ValueError("无法读取")
                except Exception:
                    file_text = ""

                # 判断是否为 Sky JSON（含 songNotes 字段）
                _is_sky_json = False
                if file_text and name_lower.endswith(".txt"):
                    import json as _json_check
                    try:
                        _parsed = _json_check.loads(file_text.strip())
                        _arr = _parsed if isinstance(_parsed, list) else [_parsed]
                        if _arr and isinstance(_arr[0], dict) and _arr[0].get("songNotes"):
                            _is_sky_json = True
                    except Exception:
                        pass

                if _is_sky_json:
                    # Sky JSON（以 .txt 导出）→ 走 Sky JSON 流程
                    if file_text:
                        parts.append(
                            f"\n\n[Sky JSON 文件内容（.txt 格式导出）]\n```json\n{file_text[:4000]}\n```\n"
                            f"⚡ 先调用 parse_sky_json_to_json(sky_json_str=<上方JSON内容>) 提取元数据，\n"
                            f"再调用 list_h5_templates() 选模板，get_h5_template(name) 读取 HTML，\n"
                            f"修改后 save_h5_output 保存。"
                        )
                    else:
                        parts.append(
                            f"\n\n[Sky JSON 文件（.txt 格式）]\nworkspace_path: {attachment_workspace_path}\n"
                            f"⚡ 调用 list_workspace_files() 确认路径后按 Sky JSON 流程处理。"
                        )
                elif file_text:
                    # ABC 格式
                    parts.append(
                        f"\n\n[ABC 文件内容]\n```abc\n{file_text[:4000]}\n```\n"
                        f"⚡ 先调用 list_h5_templates() 选模板，再调用 get_h5_template(name) 读取 HTML，\n"
                        f"然后将上方 ABC 内容填入 ep-config JSON 的 ABC_CONTENT 字段，"
                        f"最后调用 save_h5_output 保存。"
                    )
                else:
                    parts.append(
                        f"\n\n[ABC 文件]\nworkspace_path: {attachment_workspace_path}\n"
                        f"⚡ 调用 list_workspace_files() 确认路径，"
                        f"再调用 list_h5_templates() 选模板，"
                        f"get_h5_template(name) 读取 HTML，修改后 save_h5_output 保存。"
                    )

            elif name_lower.endswith(".json"):
                # Sky JSON：读取内容，注入到消息中
                try:
                    from app.agentcore.session_context import get_current_project_root
                    _root = get_current_project_root()
                    json_text = (
                        (_root / attachment_workspace_path)
                        .read_text(encoding="utf-8", errors="replace")
                        if _root else ""
                    )
                    if json_text:
                        parts.append(
                            f"\n\n[Sky JSON 文件内容]\n```json\n{json_text[:4000]}\n```\n"
                            f"⚡ 先调用 parse_sky_json_to_json(sky_json_str=<上方JSON内容>) 提取元数据，\n"
                            f"再调用 list_h5_templates() 选模板，get_h5_template(name) 读取 HTML，\n"
                            f"修改后 save_h5_output 保存。"
                        )
                    else:
                        raise ValueError("无法读取")
                except Exception:
                    parts.append(
                        f"\n\n[JSON 文件]\nworkspace_path: {attachment_workspace_path}\n"
                        f"⚡ 调用 list_workspace_files() 确认路径后按 Sky JSON 流程处理。"
                    )
            else:
                parts.append(
                    f"\n\n[文件]\nworkspace_path: {attachment_workspace_path}\n"
                    f"filename: {attachment_name}"
                )

        else:
            # 无附件 → 提示从项目目录查找
            parts.append(
                "\n\n⚡ 请先调用 list_workspace_files() "
                "查找项目中的 MIDI（.sky/*.mid）/ ABC（.sky/*.abc）/ JSON 文件，"
                "找到后按对应格式的工作流处理。\n"
                "MIDI 文件直接调用 generate_h5_from_midi；"
                "ABC/JSON 文件调用 list_h5_templates → get_h5_template → 修改 → save_h5_output。"
            )

        return "".join(parts)

    async def run_with_ctx(self, ctx: "RunContext") -> dict:
        """v4.0 解耦接口：从 RunContext 解包参数，调用原 run()。
        AGENT-4 修复：补充 attachment_b64/attachment_content，H5 Agent 可处理内联附件。
        """
        todo_mgr = ctx.extra.get("todo_mgr")
        if todo_mgr is None:
            from app.agentcore.todo_manager import TodoManager as _TM
            todo_mgr = _TM()
            todo_mgr.session_id = ctx.session_id
        return await self.run(
            session_id=ctx.session_id,
            message=ctx.message,
            attachment_workspace_path=ctx.attachment_workspace_path,
            attachment_name=ctx.attachment_name,
            attachment_b64=ctx.attachment_b64,
            attachment_content=ctx.attachment_content,
            publish=ctx.publish,
            todo_mgr=todo_mgr,
            domain=ctx.domain or "h5_create",
            workspace_id=ctx.workspace_id,
        )

