"""
TodoManager — TODO 生命周期管理 + finish_task 门控

职责（单一）：
  - 管理 TODO 列表的 pending/running/done/failed 状态流转
  - TodoCritic：LLM 异步评审 TODO 结构合理性
  - finish_gate：finish_task 前的门控检查

设计原则（对标 magic-coding-service）：
  1. complete_one()：真实落地后立刻调用，严禁在执行前调用
  2. finish_all()：只用于异常兜底（failed）或收尾确认
  3. _assert_finish_gate()：finish=true 前不得有 pending/running todo
  4. TodoCritic：异步执行，不阻塞主流程

TODO 状态流转（严格遵守）：
  pending → running（开始执行时）→ done（真实落地后立刻 complete_one）
  任何情况下不允许在未真实执行前标记 done
"""
from __future__ import annotations

import asyncio
import json
import logging
import re
from typing import Callable, Awaitable

from app.agentcore.llm import complete
from app.agentcore.domain_config import build_todo_prompt

Publisher = Callable[[str, dict], Awaitable[None]]

logger = logging.getLogger("ep_agent")

# ── TODO 规划 System Prompt（惰性缓存，避免每次 plan() 重建）────────────────────
_TODO_SYSTEM_CACHE: str = ""

def _build_todo_system() -> str:
    """构建并缓存 TODO 规划 system prompt（domain_config 动态读取）。"""
    global _TODO_SYSTEM_CACHE
    if _TODO_SYSTEM_CACHE:
        return _TODO_SYSTEM_CACHE
    _domain_section = build_todo_prompt()
    _TODO_SYSTEM_CACHE = f"""你是 EP-Agent 的任务规划器。根据用户意图输出结构化 TODO 列表。

输出严格 JSON，不要任何其他文字：
{{
  "todos": [
    {{"title": "任务标题（≤15字）", "detail": "详细说明（≤30字）"}}
  ],
  "summary": "一句话总结本次任务（≤20字）"
}}

规则：
- 每个意图通常 2-4 个 TODO，简洁清晰，每个 TODO 必须对应真实可执行的操作
- 不要包含 id 和 status 字段（系统自动分配）
- 各意图域的 TODO 模板（按此规划）：
{_domain_section}
- 每个 TODO 必须有明确的完成标志（工具返回/LLM输出/验证通过）"""
    return _TODO_SYSTEM_CACHE


class TodoManager:
    """
    统一管理 TODO 列表的生命周期。

    TODO 执行纪律（必须遵守）：
      1. pending → running：开始执行该 TODO 时立刻调用 tick(id, "running")
      2. running → done：真实落地后立刻调用 complete_one(id)
      3. 所有 TODO complete_one 后，才允许结束
      4. 异常时调用 tick(id, "failed") 或 finish_all(status="failed")
    """

    def __init__(self):
        self.todos: list[dict] = []
        self.summary: str = ""
        self.domain: str = ""
        self.session_id: str = ""   # 由 universal_runner 注入，用于状态变更时回写数据库

    # ── 查询方法 ──────────────────────────────────────────────────────────────

    def get_ids(self) -> list[str]:
        return [t["id"] for t in self.todos]

    def get_pending_ids(self) -> list[str]:
        return [t["id"] for t in self.todos if t.get("status") == "pending"]

    def get_running_ids(self) -> list[str]:
        return [t["id"] for t in self.todos if t.get("status") == "running"]

    def all_done(self) -> bool:
        return all(t.get("status") in ("done", "failed") for t in self.todos)

    def get_by_id(self, todo_id: str) -> dict | None:
        return next((t for t in self.todos if t["id"] == todo_id), None)

    # ── 规划 ─────────────────────────────────────────────────────────────────

    async def plan(
        self,
        message: str,
        domain: str,
        has_score: bool,
        publish: Publisher,
        session_id: str = "",
    ) -> list[dict]:
        """
        调用 LLM 规划 TODO 列表并推送 todo.list 事件。
        id 由此函数强制分配（"1","2","3"...），LLM 不控制 id。
        规划完成后异步触发 TodoCritic 评审（不阻塞主流程）。
        """
        self.domain = domain
        context = (
            f"用户意图：{message}\n"
            f"意图域：{domain}\n"
            f"已有谱子：{'是' if has_score else '否'}"
        )
        _TODO_SYSTEM = _build_todo_system()

        try:
            resp = await complete([
                {"role": "system", "content": _TODO_SYSTEM},
                {"role": "user",   "content": context},
            ])
            raw = resp if isinstance(resp, str) else resp.get("content", "{}")
            m = re.search(r'\{.*\}', raw, re.DOTALL)
            if m:
                data = json.loads(m.group())
                raw_todos = data.get("todos", [])
                self.todos = [
                    {
                        "id":     str(i + 1),
                        "title":  t.get("title", f"步骤 {i+1}"),
                        "detail": t.get("detail", ""),
                        "status": "pending",
                    }
                    for i, t in enumerate(raw_todos)
                    if isinstance(t, dict)
                ]
                self.summary = data.get("summary", "")
        except Exception:
            pass  # 规划失败时使用空列表，不影响主流程

        if self.todos:
            await publish("todo.list", {
                "todos":   self.todos,
                "summary": self.summary,
                "domain":  domain,
            })
            if session_id:
                try:
                    from app.pipeline import db as _db
                    _db.upsert_todos(
                        session_id=session_id,
                        todos=self.todos,
                        domain=domain,
                        summary=self.summary,
                    )
                except Exception:
                    pass

        # TodoCritic：异步评审，不阻塞主流程
        if self.todos and session_id:
            asyncio.create_task(
                self._run_critic_async(message, domain, session_id)
            )

        return self.todos

    # ── TodoCritic ───────────────────────────────────────────────────────────

    async def _run_critic_async(
        self,
        message: str,
        domain: str,
        session_id: str,
    ) -> None:
        """异步运行 TodoCritic，不阻塞主执行流程。"""
        try:
            result = await self.critic_check(message, domain)
            if not result.get("pass", True):
                issues = result.get("issues", [])
                fixes  = result.get("required_fixes", [])
                logger.warning(
                    "[TodoCritic] session=%s domain=%s issues=%s fixes=%s",
                    session_id, domain, issues, fixes,
                )
        except Exception:
            pass

    async def critic_check(self, message: str, domain: str) -> dict:
        """
        TodoCritic：LLM 评审 TODO 结构合理性（对标 magic-coding-service todo_critic.go）。
        只检查结构错误，不检查业务完整性/功能丰富度/实现顺序。
        返回：{"pass": True/False, "issues": [...], "required_fixes": [...]}
        """
        if not self.todos:
            return {"pass": True, "issues": [], "required_fixes": []}

        _CRITIC_SYSTEM = """你是 TODO 结构审稿工具。只检查 TODO 清单的结构是否合理。

只允许检查结构错误：
- TODO 缺少明显必需的步骤（如 convert 域缺少"解析文件"步骤）
- 同一条 TODO 描述与操作明显矛盾
- 把本应单一交付的内容拆成显然无法独立落地的碎片

不要检查：功能是否丰富、页面是否完整、实现顺序是否合理。
只要结构合理，就输出 pass=true。

输出严格 JSON：
{"pass": true|false, "issues": [...], "required_fixes": [...]}
禁止输出 Markdown、代码块、任何解释。"""

        payload = {
            "domain": domain,
            "user_message": message,
            "todos": self.todos,
        }

        try:
            resp = await complete([
                {"role": "system", "content": _CRITIC_SYSTEM},
                {"role": "user",   "content": json.dumps(payload, ensure_ascii=False)},
            ], temperature=0.1)
            raw = resp if isinstance(resp, str) else resp.get("content", "{}")
            raw = re.sub(r'```[a-z]*\n?', '', raw).strip()
            m = re.search(r'\{.*\}', raw, re.DOTALL)
            if m:
                return json.loads(m.group())
        except Exception:
            pass
        return {"pass": True, "issues": [], "required_fixes": []}

    # ── 状态流转 ─────────────────────────────────────────────────────────────

    async def tick(self, todo_id: str, status: str, publish: Publisher):
        """更新单个 TODO 状态，推送 SSE 并回写数据库（确保刷新后状态不丢失）。"""
        t = self.get_by_id(todo_id)
        if t:
            t["status"] = status
        await publish("todo.update", {"id": todo_id, "status": status})
        # ── 回写数据库：状态变更必须持久化，否则刷新后 TODO 状态丢失 ──
        if self.session_id:
            try:
                from app.pipeline import db as _db
                _db.upsert_todos(
                    session_id=self.session_id,
                    todos=self.todos,
                    domain=self.domain,
                    summary=self.summary,
                )
            except Exception:
                pass  # 持久化失败不影响主流程

    async def tick_next(self, publish: Publisher, status: str = "running") -> str | None:
        """将第一个 pending 的 TODO 设为 running，返回其 id。"""
        for t in self.todos:
            if t.get("status") == "pending":
                await self.tick(t["id"], status, publish)
                return t["id"]
        return None

    async def complete_one(self, todo_id: str, publish: Publisher):
        """
        【核心纪律】真实落地后立刻调用，标记单个 TODO 为 done。
        调用后自动将下一个 pending TODO 设为 running。
        禁止在执行前或执行中调用。
        """
        await self.tick(todo_id, "done", publish)
        await self.tick_next(publish, "running")

    async def finish_all(self, publish: Publisher, status: str = "done"):
        """
        将所有未完成的 TODO 标记为 done/failed/skipped。
        【重要】只应在以下场景调用：
          1. 异常兜底：status="failed"
          2. 所有工作真实完成后的收尾确认：status="done"
          3. 提前结束时未执行的 TODO：status="skipped"
        禁止用此方法绕过 complete_one 纪律进行批量标绿！
        """
        for t in self.todos:
            if t.get("status") not in ("done", "failed", "skipped"):
                await self.tick(t["id"], status, publish)

    async def append_subtasks(
        self,
        parent_id: str,
        subtasks: list[dict],
        publish: Publisher,
    ) -> list[dict]:
        """动态追加子任务，id 格式："{parent_id}.1", "{parent_id}.2"..."""
        new_todos: list[dict] = []
        for i, sub in enumerate(subtasks):
            sub_id = f"{parent_id}.{i + 1}"
            item = {
                "id":        sub_id,
                "title":     sub.get("title", f"子步骤 {i+1}"),
                "detail":    sub.get("detail", ""),
                "status":    "pending",
                "parent_id": parent_id,
            }
            new_todos.append(item)
            self.todos.append(item)

        if new_todos:
            await publish("todo.append", {
                "parent_id": parent_id,
                "todos":     new_todos,
            })
        return new_todos


# ── finish_task 门控（对标 magic-coding-service output_contract）─────────────

async def assert_finish_gate(
    todo_mgr: TodoManager,
    domain: str,
    publish: Publisher,
) -> None:
    """
    finish_task 前的门控检查（对标 magic-coding-service output_contract）。
    规则：finish=true 前不得有 pending/running todo。
    违反时：强制 finish_all(done) 并推送警告（不抛异常，确保前端状态一致）。
    """
    pending = todo_mgr.get_pending_ids()
    running = todo_mgr.get_running_ids()

    if pending or running:
        logger.error(
            "[finish_gate] domain=%s 违反 TODO 纪律: pending=%s running=%s — 强制收尾",
            domain, pending, running,
        )
        # running 的标记 done（已开始执行但未完成）
        for tid in running:
            await todo_mgr.tick(tid, "done", publish)
        # pending 的标记 skipped（未执行就结束，不能豎充为 done）
        for tid in pending:
            await todo_mgr.tick(tid, "skipped", publish)
        await publish("pipeline.step", {
            "step":   "finish_gate",
            "status": "warning",
            "text":   f"[{domain}] 检测到未执行的步骤，已标记为跳过（skipped={pending}）",
        })
