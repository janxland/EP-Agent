# EP-Agent 解耦架构设计 v4.0

> 目标：职责单一、依赖单向、流程无断点、横向可扩展

---

## 一、当前架构的耦合问题诊断

### 1.1 耦合地图（现状）

```
router.py ──────────────────────────────────────────────────────────┐
  直接 import: config / domain_config / llm / role_config /         │
               session_context / tools / workspace_tools            │
  问题：路由层直接操作 LLM + 工具，越级调用                           │
                                                                     │
universal_runner._dispatch() ───────────────────────────────────────┤
  13 个参数透传（session_getter/saver/convert_fn/edit_fn/           │
               audio_chat_fn/todo_mgr/todos_task...）               │
  问题：参数爆炸，每新增 domain 都要改 _dispatch 签名                 │
                                                                     │
react_executor.py ──────────────────────────────────────────────────┤
  直接 import pipeline.db（落库 tool message）                       │
  问题：执行层感知了存储层，违反分层原则                               │
                                                                     │
session_context.py ─────────────────────────────────────────────────┤
  既管 ContextVar 注入，又管路径推断，又管文件记忆                    │
  问题：职责混杂，三种关注点合并在一个文件                             │
                                                                     │
SubAgent（各 agent.py）─────────────────────────────────────────────┘
  各自接收 session_getter/saver/convert_fn/edit_fn 回调
  问题：业务 Agent 感知了 pipeline 层的实现细节
```

### 1.2 耦合类型分类

| 类型 | 位置 | 严重度 | 影响 |
|------|------|--------|------|
| **参数爆炸** | `_dispatch()` 13参数 | 🔴 高 | 每加 domain 改签名，测试难 |
| **越级调用** | `router.py` 直接用 LLM/tools | 🔴 高 | 路由层变成业务层 |
| **跨层依赖** | `react_executor` → `pipeline.db` | 🟡 中 | 执行层依赖存储层 |
| **职责混杂** | `session_context` 三职合一 | 🟡 中 | 改一处影响全局 |
| **回调地狱** | `convert_fn/edit_fn/audio_chat_fn` | 🟡 中 | 隐式接口，难追踪 |
| **硬编码路由** | `if domain == "convert": ...` | 🟢 低 | 可用注册表替代 |

---

## 二、解耦目标架构 v4.0

### 2.1 分层原则

```
┌─────────────────────────────────────────────────────────────┐
│  接入层  router.py                                           │
│  职责：HTTP 解析、鉴权、SSE 推送、参数校验                    │
│  禁止：直接调用 LLM、直接操作工具、直接读写 session           │
├─────────────────────────────────────────────────────────────┤
│  编排层  universal_runner.py                                 │
│  职责：意图路由 → TODO 规划 → 分发 SubAgent → 门控           │
│  禁止：包含任何业务逻辑（创作/编辑/H5 生成等）               │
├─────────────────────────────────────────────────────────────┤
│  执行层  react_executor.py                                   │
│  职责：ReAct Loop（Think→Act→Observe）、流式推送             │
│  禁止：感知 domain、直接操作 DB、感知 session 结构            │
├─────────────────────────────────────────────────────────────┤
│  Agent层  agents/*.py                                        │
│  职责：构建 prompt、组装工具集、解析结果、落库                │
│  禁止：包含 ReAct Loop 实现、感知其他 Agent                   │
├─────────────────────────────────────────────────────────────┤
│  工具层  tools/*.py                                          │
│  职责：原子操作（文件读写/ABC转换/H5生成/音频生成）           │
│  禁止：感知 session、感知 domain、调用其他工具                │
├─────────────────────────────────────────────────────────────┤
│  基础层  llm.py / db.py / session_context.py                 │
│  职责：LLM 客户端、持久化、ContextVar 管理                    │
│  禁止：感知业务逻辑                                           │
└─────────────────────────────────────────────────────────────┘
```

### 2.2 核心解耦：RunContext 统一上下文对象

**问题根源**：参数爆炸来自"把所有信息都当参数传"。

**解法**：引入 `RunContext` 数据类，一次构建，全链路传递。

```python
# app/agentcore/run_context.py（新增）
from __future__ import annotations
from dataclasses import dataclass, field
from typing import Callable, Awaitable, Any

Publisher = Callable[[str, dict], Awaitable[None]]

@dataclass
class RunContext:
    """
    单次请求的全局上下文，贯穿 Runner → Agent → Executor。
    不可变字段在构造时确定，可变字段通过方法更新。
    """
    # ── 身份信息（不可变）────────────────────────────────────────
    session_id:   str
    workspace_id: str
    project_id:   str
    trace_id:     str
    role_id:      str

    # ── 请求内容（不可变）────────────────────────────────────────
    message:                   str
    attachment_content:        str = ""
    attachment_name:           str = ""
    attachment_workspace_path: str = ""
    attachment_b64:            str = ""

    # ── 运行时状态（可变）────────────────────────────────────────
    domain:       str = ""
    has_score:    bool = False

    # ── 基础设施（注入）──────────────────────────────────────────
    publish:      Publisher | None = None

    # ── 扩展字段（Agent 间传递结果）──────────────────────────────
    extra: dict = field(default_factory=dict)

    def with_domain(self, domain: str) -> "RunContext":
        """返回新 context（domain 已更新），保持不可变语义。"""
        from dataclasses import replace
        return replace(self, domain=domain)
```

**效果**：`_dispatch()` 从 13 个参数 → 1 个 `RunContext`。

---

### 2.3 核心解耦：Domain 注册表（消灭 if-elif 链）

**问题根源**：每新增 domain 都要在 `_dispatch` 里加一个 `if domain == "xxx"` 分支。

**解法**：Agent 注册表 + 自动发现。

```python
# app/agentcore/agent_registry.py（新增）
from __future__ import annotations
from typing import Type, TYPE_CHECKING

if TYPE_CHECKING:
    from app.agentcore.agents.base_agent import BaseAgent

_REGISTRY: dict[str, Type["BaseAgent"]] = {}

def register(*domains: str):
    """装饰器：将 Agent 注册到指定 domain。"""
    def decorator(cls):
        for d in domains:
            _REGISTRY[d] = cls
        return cls
    return decorator

def get_agent(domain: str) -> Type["BaseAgent"] | None:
    return _REGISTRY.get(domain)

def list_domains() -> list[str]:
    return sorted(_REGISTRY.keys())
```

```python
# app/agentcore/agents/base_agent.py（新增）
from __future__ import annotations
from abc import ABC, abstractmethod
from app.agentcore.run_context import RunContext

class BaseAgent(ABC):
    """所有 SubAgent 的基类，统一接口。"""

    @abstractmethod
    async def run(self, ctx: RunContext) -> dict:
        """执行 Agent 逻辑，返回结果 dict。"""
        ...
```

```python
# app/agentcore/agents/edit_agent.py（改造后）
from app.agentcore.agent_registry import register
from app.agentcore.agents.base_agent import BaseAgent
from app.agentcore.run_context import RunContext

@register("edit")                         # ← 一行注册，无需改 _dispatch
class EditAgent(BaseAgent):
    async def run(self, ctx: RunContext) -> dict:
        # 从 ctx 取所有需要的字段，不再接收 13 个参数
        session_id = ctx.session_id
        message    = ctx.message
        publish    = ctx.publish
        ...
```

**新的 `_dispatch`（简化后）**：

```python
async def _dispatch(self, ctx: RunContext, todo_mgr: TodoManager) -> dict:
    from app.agentcore.agent_registry import get_agent
    
    AgentClass = get_agent(ctx.domain)
    if AgentClass is None:
        # 兜底：QueryAgent
        from app.agentcore.agents.query_agent import QueryAgent
        AgentClass = QueryAgent
    
    return await AgentClass().run(ctx)
```

---

### 2.4 核心解耦：ReactExecutor 去除 DB 依赖

**问题根源**：`react_executor.py` 直接 `import pipeline.db` 落库 tool message，违反分层。

**解法**：通过回调接口（`on_message_saved`）将落库职责还给调用方。

```python
# react_executor.py 改造
class ReactExecutor:
    async def run(
        self,
        messages:        list[dict],
        tools:           list[dict],
        publish:         Publisher,
        todo_manager:    TodoManager,
        # 新增：落库回调（可选，由 Agent 层注入，Executor 不感知 DB）
        on_message_saved: Callable[[dict], Awaitable[None]] | None = None,
        ...
    ) -> dict:
        ...
        # 原来直接调用 _db.save_message(...)
        # 改为：
        if on_message_saved:
            await on_message_saved(tool_message)
```

---

### 2.5 核心解耦：session_context 职责拆分

**当前职责混杂**：

```
session_context.py
  ├── ContextVar 注入/读取（基础设施）
  ├── 路径推断 _get_project_root()（文件系统）
  └── 文件记忆 remember/recall（业务状态）
```

**拆分后**：

```
context_vars.py          # 纯 ContextVar：session_id / trace_id / project_root
  set_current_session_id()
  get_current_session_id()
  set_current_trace_id()
  get_current_trace_id()

workspace_path.py        # 路径推断（依赖 context_vars + DB）
  get_project_root() -> Path | None
  resolve_safe(rel_path) -> Path

session_memory.py        # 文件记忆（依赖 DB）
  remember_workspace_file()
  recall_latest_file()
  get_ep_logger()
```

---

### 2.6 核心解耦：router.py 去除越级调用

**当前问题**：`router.py` 直接 import `llm` / `tools` / `workspace_tools`。

**解法**：路由层只调用 `service.py`，所有业务逻辑下沉到 service。

```python
# router.py（改造后）
@router.get("/models")
async def list_models():
    return await service.list_available_models()   # ← 委托给 service

@router.get("/health/tools")
async def health_tools():
    return await service.check_tools_health()      # ← 委托给 service

# router.py 禁止出现：
# ❌ from app.agentcore.llm import ...
# ❌ from app.agentcore.tools import ...
# ❌ from app.agentcore.tools.workspace_tools import ...
```

---

## 三、流程无断点保障机制

### 3.1 当前断点风险点

```
风险1：ContextVar 注入时机
  universal_runner.run() 注入 → 但 list_workspace_scores_impl()
  在 ReactExecutor 启动前调用 → fix38 已修复，但依赖注入顺序脆弱

风险2：TodoManager 并发竞态
  todos_task = asyncio.create_task(todo_mgr.plan(...))
  → SubAgent 在 plan() 完成前就开始执行
  → finish_gate 可能在 plan 还未写库时就触发

风险3：降级时 todos_task.cancel() 竞态
  edit→create 降级时 cancel 旧 task
  → cancel 不保证立即生效，可能旧 TODO 仍在写库

风险4：链式意图中 project_id 丢失
  _dispatch 中链式意图路径用了 project_id
  但 project_id 是 run() 的局部变量，_dispatch 无法访问
  → RunContext 解决此问题
```

### 3.2 无断点流程设计

#### 方案：事件驱动的流程状态机

```python
# app/agentcore/pipeline_fsm.py（新增）
"""
流程状态机：确保每个步骤都有明确的状态转移，
任何异常都能被捕获并推送给前端，不会静默失败。
"""
from enum import Enum

class PipelineState(Enum):
    IDLE        = "idle"
    ROUTING     = "routing"       # 意图识别中
    PLANNING    = "planning"      # TODO 规划中
    EXECUTING   = "executing"     # Agent 执行中
    FINISHING   = "finishing"     # finish_gate 检查中
    SUCCEEDED   = "succeeded"
    FAILED      = "failed"

class PipelineFSM:
    def __init__(self, ctx: RunContext):
        self.ctx   = ctx
        self.state = PipelineState.IDLE

    async def transition(self, new_state: PipelineState, text: str = ""):
        self.state = new_state
        await self.ctx.publish("pipeline.step", {
            "step":   new_state.value,
            "status": "running" if new_state not in (
                PipelineState.SUCCEEDED, PipelineState.FAILED
            ) else new_state.value,
            "text":   text,
        })

    async def run(self) -> dict:
        try:
            # Step 1: 路由
            await self.transition(PipelineState.ROUTING, "意图识别中...")
            domain = await route_intent(self.ctx)
            self.ctx = self.ctx.with_domain(domain)

            # Step 2: 规划（同步等待，消灭并发竞态）
            await self.transition(PipelineState.PLANNING, "规划任务步骤...")
            todo_mgr = TodoManager(self.ctx.session_id)
            await todo_mgr.plan(self.ctx)          # ← 同步等待，不再 create_task

            # Step 3: 执行
            await self.transition(PipelineState.EXECUTING, f"执行 {domain}...")
            result = await self._execute(todo_mgr)

            # Step 4: 门控
            await self.transition(PipelineState.FINISHING, "完成检查...")
            await assert_finish_gate(todo_mgr)

            await self.transition(PipelineState.SUCCEEDED, "完成")
            return result

        except Exception as e:
            await self.ctx.publish("pipeline.step", {
                "step":   "error",
                "status": "failed",
                "text":   str(e),
            })
            raise
```

**关键改动**：`todo_mgr.plan()` 改为**同步等待**，消灭并发竞态。

性能影响：plan() 用 lite 模型，约 1-2s，用户几乎感知不到（原来也有路由耗时）。

---

### 3.3 ContextVar 注入的根本解法

**当前**：依赖 `universal_runner.run()` 手动注入，顺序脆弱。

**改法**：在 `RunContext` 构造时自动注入，生命周期与请求绑定。

```python
# run_context.py
@dataclass
class RunContext:
    ...
    def __post_init__(self):
        """构造完成后自动注入 ContextVar，无需手动调用。"""
        from app.agentcore.context_vars import (
            set_current_session_id, set_current_trace_id
        )
        if self.session_id:
            set_current_session_id(self.session_id)
        if self.trace_id:
            set_current_trace_id(self.trace_id)
```

---

## 四、改造优先级与实施路线

### Phase 1（1-2天，立即可做，零破坏性）

| 改动 | 文件 | 收益 |
|------|------|------|
| 新增 `RunContext` 数据类 | `run_context.py`（新增） | 为后续重构铺路 |
| `_dispatch` 改用 RunContext | `universal_runner.py` | 13参数→1参数 |
| `session_context` 拆分职责 | 拆为3个文件 | 职责清晰 |
| `todos_task` 改同步等待 | `universal_runner.py` | 消灭竞态断点 |

### Phase 2（2-3天，中优先级）

| 改动 | 文件 | 收益 |
|------|------|------|
| 新增 `BaseAgent` + `agent_registry` | 2个新文件 | 消灭 if-elif 链 |
| 各 Agent 改用 `@register` | 7个 agent 文件 | 零侵入扩展 |
| `ReactExecutor` 去除 DB 依赖 | `react_executor.py` | 分层纯净 |

### Phase 3（3-5天，中低优先级）

| 改动 | 文件 | 收益 |
|------|------|------|
| 引入 `PipelineFSM` | `pipeline_fsm.py`（新增） | 流程可观测 |
| `router.py` 去除越级调用 | `router.py` | 分层纯净 |
| `service.py` 承接 router 下沉的业务 | `service.py` | 单一入口 |

---

## 五、解耦前后对比

### 参数传递

```python
# 现在：参数爆炸
async def _dispatch(self, domain, chain_intents, session_id, message,
                    attachment_content, attachment_name,
                    attachment_workspace_path, attachment_b64,
                    publish, session_getter, session_saver,
                    convert_fn, edit_fn, audio_chat_fn,
                    todo_mgr, todos_task, has_score,
                    role_id, workspace_id):  # 19个参数

# 改后：统一上下文
async def _dispatch(self, ctx: RunContext, todo_mgr: TodoManager):  # 2个参数
```

### 新增 Domain

```python
# 现在：必须修改 _dispatch（侵入式）
if domain == "new_domain":
    from app.agentcore.agents.new_agent import NewAgent
    return await NewAgent().run(session_id=..., message=..., ...)

# 改后：只需新建文件（零侵入）
# new_agent.py
@register("new_domain")
class NewAgent(BaseAgent):
    async def run(self, ctx: RunContext) -> dict:
        ...
```

### 流程断点

```python
# 现在：并发竞态风险
todos_task = asyncio.create_task(todo_mgr.plan(...))  # 后台并发
# ... SubAgent 可能在 plan 完成前就执行 ...
await todos_task  # 有时忘记 await

# 改后：FSM 保证顺序
await self.transition(PLANNING, "规划中...")
await todo_mgr.plan(ctx)          # 同步等待，无竞态
await self.transition(EXECUTING)
result = await agent.run(ctx)     # plan 100% 完成后才执行
```

---

## 六、不变的核心设计（保留）

以下设计已经足够好，**不需要改动**：

| 设计 | 理由 |
|------|------|
| ContextVar 路径推断 | 工具层无需参数，自动感知项目根目录 |
| `@tool(group=...)` 注册机制 | 工具分组清晰，按需组装 |
| `finish_task` 门控 | ReAct Loop 终止信号统一 |
| `_FILE_WRITE_TOOLS` 文件树刷新 | 工具执行后自动通知前端 |
| M7 熔断重试 | LLM 调用稳定性保障 |
| Trace ID 全链路 | 可观测性基础 |
| lite/strong 模型分层 | 成本优化 |
