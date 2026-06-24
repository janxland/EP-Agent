# EP-Agent 前端对齐专家系统 & 前后端检查专家系统
## 设计与实现方案 v1.0

> 核心理念：**Agent 就是如此** — 工具可被发现、意图可被路由、状态可被追踪、组件可被复用

---

## 一、现状诊断

### 1.1 当前架构全景

```
前端（Next.js + Zustand）
  ChatPanel.tsx          ← 对话面板（输入/展示/SSE 驱动）
  TodoListCard.tsx       ← TODO 进度可视化
  ChatMessageList.tsx    ← 消息渲染（含工具调用卡片）
  chat.store.ts          ← 对话状态（SSE 事件驱动）
  session/store.ts       ← Score/Session 状态

后端（FastAPI + asyncio）
  universal_runner.py    ← 纯编排层（路由→规划→调度→门控）
  intent_router.py       ← 意图路由（LLM + 关键词）
  todo_manager.py        ← TODO 生命周期（plan/tick/complete/gate）
  react_executor.py      ← 通用 ReAct Loop（所有 SubAgent 共用）
  agents/
    convert_agent.py     ← Sky JSON → ABC
    edit_agent.py        ← ABC 编辑
    create_agent.py      ← ABC 创作
    audio_agent.py       ← 音频/音色
    query_agent.py       ← 查询问答
```

### 1.2 现存问题矩阵

| # | 问题 | 位置 | 严重度 |
|---|------|------|--------|
| F1 | 前端没有「对齐检查」机制：后端推送的 SSE 事件类型与前端处理分支是否一一对应，无法自动验证 | chat.store.ts | 🔴 |
| F2 | 工具调用卡片（ToolCard）硬编码工具名映射，新增工具时需手动修改前端 | ToolCard 组件 | 🟡 |
| F3 | SSE 事件序号（sequence）前端完全忽略，乱序/丢包无感知 | chat.store.ts | 🟡 |
| F4 | 前端无「后端健康检查」入口，后端异常时用户只看到超时 | ChatPanel.tsx | 🟡 |
| B1 | `session_getter/session_saver` 透传 26 次，深度耦合 | 所有 agents | 🟡 |
| B2 | `tools/` 目录工具无统一 Schema 注册表，扩展新工具需手动维护多处 | tools/ | 🔴 |
| B3 | `intent_router.py` 中意图域定义与 `todo_manager.py` 中 TODO 模板各自维护，易漂移 | 两个文件 | 🟡 |
| B4 | `audio_runner.py` 仍有独立 ReAct Loop（468 行），绕过 TodoManager | audio_runner.py | 🔴 |
| B5 | 没有「工具健康检查」端点，新部署后无法快速验证工具链是否可用 | pipeline/router.py | 🟡 |
| A1 | 前后端 SSE 事件类型定义各自维护（后端 Python 枚举 vs 前端 TypeScript 枚举），易漂移 | 两端 | 🔴 |

---

## 二、专家系统设计

### 2.1 整体架构

```
EP-Agent 专家系统
├── [FE] 前端对齐专家系统
│   ├── SSE 事件对齐检查器（EventAlignmentChecker）
│   ├── 工具注册表驱动 ToolCard（ToolRegistry → ToolCard）
│   ├── 序号守卫（SequenceGuard）
│   └── 系统健康面板（HealthPanel）
│
├── [BE] 后端检查专家系统
│   ├── 工具注册表（ToolRegistry）
│   ├── 工具健康检查端点（GET /health/tools）
│   ├── Session 上下文管理器（SessionContext）
│   └── 意图域配置中心（DomainConfig）
│
└── [共享] 前后端对齐层
    ├── SSE 事件契约（SSEContract）— 单一来源，自动生成两端类型
    └── 工具 Schema 契约（ToolSchema）— 后端定义，前端消费
```

---

## 三、前端对齐专家系统

### 3.1 SSE 事件对齐检查器

**问题**：后端新增 SSE 事件类型时，前端 `chat.store.ts` 的 `handleSSEEvent` switch 可能漏掉处理分支，导致静默丢失。

**方案**：在 `chat.store.ts` 的 default 分支加入「未知事件警告」，并在开发模式下记录所有未处理事件。

```
EP-Agent/frontend/src/shared/lib/sse-alignment.ts   ← 新增
EP-Agent/frontend/src/shared/lib/api.ts             ← 修改（注入对齐检查）
EP-Agent/frontend/src/features/chat/store/chat.store.ts  ← 修改（default 分支）
```

**实现要点**：
```typescript
// sse-alignment.ts
export const SSE_HANDLED_EVENTS = new Set([
  'connected', 'pipeline.step', 'abc.updated', 'activity.update',
  'message.delta', 'message.completed', 'message.history',
  'tool.call', 'todo.list', 'todo.update', 'todo.append', 'error',
] as const)

export function checkSSEAlignment(eventType: string): void {
  if (!SSE_HANDLED_EVENTS.has(eventType as SSEEventType)) {
    if (process.env.NODE_ENV === 'development') {
      console.warn(`[SSE对齐] 未处理的事件类型: "${eventType}"，请在 chat.store.ts 补充处理分支`)
    }
  }
}
```

**在 handleSSEEvent default 分支调用**：
```typescript
default:
  checkSSEAlignment(event.type)
  break
```

---

### 3.2 工具注册表驱动 ToolCard（可被发现化）

**问题**：`ToolCard` 组件硬编码了工具名 → 图标/描述映射，每次新增工具都要改前端代码。

**方案**：工具注册表从后端 `/health/tools` 拉取，前端 ToolCard 动态渲染。

```
EP-Agent/frontend/src/shared/lib/tool-registry.ts  ← 新增（工具注册表客户端）
EP-Agent/frontend/src/widgets/chat-panel/tool-call/ToolCard.tsx  ← 修改（注册表驱动）
EP-Agent/frontend/src/widgets/chat-panel/tool-call/ToolRegistry.tsx ← 新增（注册表 Provider）
```

**工具注册表结构**（与后端 `/health/tools` 响应对齐）：
```typescript
// tool-registry.ts
export interface ToolMeta {
  name: string           // 工具唯一标识（与后端 function name 一致）
  label: string          // 用户可读名称（中文）
  icon: string           // emoji 图标
  description: string    // 工具描述
  domain: string[]       // 适用意图域（convert/edit/create/audio/query）
  dangerous?: boolean    // 是否为破坏性操作
}

// 本地默认注册表（兜底，后端拉取失败时使用）
export const DEFAULT_TOOL_REGISTRY: Record<string, ToolMeta> = {
  convert_sky_json:  { name: 'convert_sky_json',  label: '解析 Sky 谱子', icon: '🎮', description: '将 Sky 游戏谱子 JSON 转换为 ABC 记谱', domain: ['convert'] },
  abc_transpose:     { name: 'abc_transpose',      label: '转调',         icon: '🎵', description: '升高/降低指定半音数', domain: ['edit'] },
  abc_set_tempo:     { name: 'abc_set_tempo',      label: '调整速度',     icon: '⏱️', description: '修改 BPM', domain: ['edit'] },
  abc_to_sky_json:   { name: 'abc_to_sky_json',    label: '导出 Sky JSON', icon: '📤', description: '将 ABC 谱导出为 Sky 游戏格式', domain: ['edit', 'create'] },
  abc_to_midi_b64:   { name: 'abc_to_midi_b64',    label: '导出 MIDI',    icon: '🎹', description: '将 ABC 谱导出为 MIDI 文件', domain: ['edit', 'create'] },
  intent_router:     { name: 'intent_router',      label: '意图识别',     icon: '🧭', description: '分析用户意图，路由到对应处理域', domain: ['*'] },
  abc_editor:        { name: 'abc_editor',         label: 'ABC 编辑器',   icon: '✏️', description: '执行 ABC 谱子编辑操作', domain: ['edit'] },
}
```

**ToolCard 改造**（注册表驱动，不再硬编码）：
```typescript
// ToolCard.tsx 改造后
import { useToolRegistry } from '@/shared/lib/tool-registry'

export const ToolCard = memo(function ToolCard({ toolName, ... }) {
  const meta = useToolRegistry(toolName)  // O(1) 查找
  const icon  = meta?.icon  ?? '🔧'
  const label = meta?.label ?? toolName
  // ... 渲染逻辑不变，只是图标/标签从注册表取
})
```

---

### 3.3 SSE 序号守卫（SequenceGuard）

**问题**：后端每个 SSE 事件携带 `sequence` 字段，前端完全忽略，乱序/重复事件无感知。

**方案**：在 SSE 事件处理入口加序号守卫，检测并记录乱序/重复。

```typescript
// sse-alignment.ts 补充
export class SequenceGuard {
  private lastSeq = -1
  private seenIds = new Set<string>()

  check(event: SSEEvent): 'ok' | 'duplicate' | 'out-of-order' {
    // 去重：同一 id 不处理两次
    if (event.id && this.seenIds.has(event.id)) return 'duplicate'
    if (event.id) this.seenIds.add(event.id)

    // 序号检查（sequence 字段存在时）
    if (event.sequence !== undefined && event.sequence > 0) {
      if (event.sequence <= this.lastSeq) return 'out-of-order'
      this.lastSeq = event.sequence
    }
    return 'ok'
  }

  reset() { this.lastSeq = -1; this.seenIds.clear() }
}
```

---

### 3.4 系统健康面板（HealthPanel）

**问题**：后端异常时用户只看到「请求超时」，无法判断是网络问题还是服务宕机。

**方案**：在 ChatPanel 顶栏添加健康状态指示器，定期 ping `/health`。

```
EP-Agent/frontend/src/shared/hooks/useBackendHealth.ts  ← 新增
EP-Agent/frontend/src/widgets/chat-panel/ChatPanel.tsx   ← 修改（顶栏注入）
```

```typescript
// useBackendHealth.ts
export function useBackendHealth(intervalMs = 30_000) {
  const [status, setStatus] = useState<'ok' | 'degraded' | 'down' | 'unknown'>('unknown')

  useEffect(() => {
    const check = async () => {
      try {
        const r = await fetch('/api/health', { signal: AbortSignal.timeout(3000) })
        const data = await r.json()
        setStatus(data.tools_ok ? 'ok' : 'degraded')
      } catch {
        setStatus('down')
      }
    }
    check()
    const t = setInterval(check, intervalMs)
    return () => clearInterval(t)
  }, [intervalMs])

  return status
}
```

**顶栏健康指示器**：
```tsx
// ChatPanel.tsx 顶栏补充
const healthStatus = useBackendHealth()
const HEALTH_CONFIG = {
  ok:       { dot: 'bg-green-400',  tip: '服务正常' },
  degraded: { dot: 'bg-yellow-400', tip: '部分工具不可用' },
  down:     { dot: 'bg-red-400 animate-pulse', tip: '服务不可用' },
  unknown:  { dot: 'bg-gray-300',  tip: '检查中...' },
}
// 渲染：右上角一个小圆点 + hover tooltip
```

---

## 四、后端检查专家系统

### 4.1 工具注册表（ToolRegistry）— 可被发现化核心

**问题**：工具分散在 `tools/` 目录，无统一元数据，前端无法动态发现，健康检查无依据。

**方案**：每个工具模块在注册时声明自己的元数据，`ToolRegistry` 汇总后对外暴露。

```
EP-Agent/backend/app/agentcore/tool_registry.py  ← 新增
EP-Agent/backend/app/agentcore/tools/            ← 每个工具补充 @register_tool 装饰器
EP-Agent/backend/app/pipeline/router.py          ← 新增 GET /health/tools 端点
```

**工具注册表实现**：
```python
# tool_registry.py
from dataclasses import dataclass, field
from typing import Callable, Any

@dataclass
class ToolMeta:
    name: str
    label: str          # 用户可读名称（中文）
    icon: str           # emoji
    description: str    # 功能描述
    domain: list[str]   # 适用意图域
    parameters: dict    # JSON Schema（与 LLM function calling 格式一致）
    dangerous: bool = False
    fn: Callable | None = None  # 实际执行函数（call_tool 使用）

class ToolRegistry:
    _tools: dict[str, ToolMeta] = {}

    @classmethod
    def register(cls, meta: ToolMeta) -> Callable:
        """注册工具，返回装饰器。"""
        def decorator(fn: Callable) -> Callable:
            meta.fn = fn
            cls._tools[meta.name] = meta
            return fn
        return decorator

    @classmethod
    def get(cls, name: str) -> ToolMeta | None:
        return cls._tools.get(name)

    @classmethod
    def list_all(cls) -> list[ToolMeta]:
        return list(cls._tools.values())

    @classmethod
    def to_llm_tools(cls, domain: str | None = None) -> list[dict]:
        """生成 LLM function calling 格式的工具列表（可按 domain 过滤）。"""
        metas = cls._tools.values()
        if domain:
            metas = [m for m in metas if domain in m.domain or '*' in m.domain]
        return [
            {
                "type": "function",
                "function": {
                    "name":        m.name,
                    "description": m.description,
                    "parameters":  m.parameters,
                }
            }
            for m in metas
        ]

_registry = ToolRegistry()
```

**工具注册示例**（`tools/abc_tools.py` 改造）：
```python
# tools/abc_tools.py
from app.agentcore.tool_registry import ToolRegistry, ToolMeta

@ToolRegistry.register(ToolMeta(
    name="abc_transpose",
    label="转调",
    icon="🎵",
    description="将 ABC 谱子升高或降低指定半音数",
    domain=["edit"],
    parameters={
        "type": "object",
        "properties": {
            "abc":       {"type": "string", "description": "ABC 谱子原文"},
            "semitones": {"type": "integer", "description": "半音数（正数升调，负数降调）"},
        },
        "required": ["abc", "semitones"],
    },
))
async def abc_transpose(abc: str, semitones: int) -> str:
    ...
```

**新增 GPT-SoVITS 工具只需一步**：
```python
# tools/sovits_tools.py（新建，无需改其他任何文件）
from app.agentcore.tool_registry import ToolRegistry, ToolMeta

@ToolRegistry.register(ToolMeta(
    name="sovits_clone_voice",
    label="音色克隆",
    icon="🎤",
    description="使用 GPT-SoVITS 克隆音色并合成语音",
    domain=["voice"],
    parameters={
        "type": "object",
        "properties": {
            "text":          {"type": "string",  "description": "要合成的文本"},
            "ref_audio_b64": {"type": "string",  "description": "参考音频 base64"},
            "ref_text":      {"type": "string",  "description": "参考音频对应文本（提升克隆质量）"},
        },
        "required": ["text", "ref_audio_b64"],
    },
))
async def sovits_clone_voice(text: str, ref_audio_b64: str, ref_text: str = "") -> dict:
    """调用 SOVITS_BASE_URL 服务执行音色克隆。"""
    import os, httpx, base64
    base_url = os.getenv("SOVITS_BASE_URL", "http://localhost:9880")
    # ... httpx 调用 GPT-SoVITS API
```

---

### 4.2 工具健康检查端点

**方案**：`GET /health/tools` 返回所有已注册工具的状态，前端健康面板消费。

```python
# pipeline/router.py 新增
@router.get("/health/tools")
async def health_tools():
    """工具健康检查：列出所有已注册工具，并检测关键工具是否可调用。"""
    from app.agentcore.tool_registry import ToolRegistry
    tools = ToolRegistry.list_all()
    
    # 检测关键工具（只检测无副作用的工具）
    critical_checks = {}
    for name in ["abc_transpose", "abc_to_sky_json"]:
        try:
            meta = ToolRegistry.get(name)
            critical_checks[name] = "ok" if meta and meta.fn else "not_registered"
        except Exception as e:
            critical_checks[name] = f"error: {e}"
    
    tools_ok = all(v == "ok" for v in critical_checks.values())
    return {
        "tools_ok":       tools_ok,
        "tool_count":     len(tools),
        "tools":          [
            {"name": m.name, "label": m.label, "icon": m.icon,
             "description": m.description, "domain": m.domain}
            for m in tools
        ],
        "critical_checks": critical_checks,
    }

@router.get("/health")
async def health():
    """快速健康检查（前端 30s 轮询）。"""
    from app.agentcore.tool_registry import ToolRegistry
    tool_count = len(ToolRegistry.list_all())
    return {
        "status":      "ok",
        "tools_ok":    tool_count > 0,
        "tool_count":  tool_count,
    }
```

---

### 4.3 Session 上下文管理器（消除透传）

**问题**：`session_getter/session_saver` 在所有 SubAgent 中透传 26 次，耦合度高。

**方案**：上下文变量（`contextvars`）在请求入口注入，SubAgent 直接调用 `get_session()` / `save_session()`。

```
EP-Agent/backend/app/agentcore/session_context.py  ← 新增
EP-Agent/backend/app/pipeline/router.py             ← 修改（请求入口注入）
EP-Agent/backend/app/agentcore/agents/*.py          ← 逐步移除 session_getter/saver 参数
```

```python
# session_context.py
from contextvars import ContextVar
from typing import Callable

_session_getter_var: ContextVar[Callable] = ContextVar('session_getter')
_session_saver_var:  ContextVar[Callable] = ContextVar('session_saver')

def set_session_context(getter: Callable, saver: Callable):
    """在请求入口调用，注入 session 操作函数。"""
    _session_getter_var.set(getter)
    _session_saver_var.set(saver)

def get_session(session_id: str):
    """SubAgent 直接调用，无需透传。"""
    return _session_getter_var.get()(session_id)

def save_session(sess):
    """SubAgent 直接调用，无需透传。"""
    return _session_saver_var.get()(sess)
```

**请求入口注入**（`router.py` 的 `/chat` 处理函数）：
```python
# pipeline/router.py
from app.agentcore.session_context import set_session_context
from app.pipeline.service import get_session, save_session

async def handle_chat(session_id: str, ...):
    # 请求入口注入，后续所有 SubAgent 无需透传
    set_session_context(get_session, save_session)
    await universal_runner.run(...)
```

**SubAgent 改造后**（以 EditAgent 为例）：
```python
# edit_agent.py 改造后（移除 session_getter/saver 参数）
from app.agentcore.session_context import get_session, save_session

class EditAgent:
    async def run(self, session_id, message, publish, edit_fn, todo_mgr) -> dict:
        sess = get_session(session_id)   # 直接调用，不透传
        ...
        save_session(sess)               # 直接调用，不透传
```

---

### 4.4 意图域配置中心（DomainConfig）

**问题**：意图域定义分散在 `intent_router.py`（_ROUTER_SYSTEM）和 `todo_manager.py`（_TODO_SYSTEM）两处，新增意图域需同步修改两个文件。

**方案**：抽取 `domain_config.py`，单一来源定义所有意图域元数据。

```
EP-Agent/backend/app/agentcore/domain_config.py  ← 新增
```

```python
# domain_config.py
from dataclasses import dataclass

@dataclass
class DomainMeta:
    name: str           # 意图域标识
    label: str          # 中文名称
    icon: str           # emoji
    description: str    # 路由器判断依据（注入 _ROUTER_SYSTEM）
    todo_template: str  # TODO 规划模板（注入 _TODO_SYSTEM）
    agent_class: str    # 对应 SubAgent 类名

DOMAIN_CONFIG: dict[str, DomainMeta] = {
    "convert": DomainMeta(
        name="convert",
        label="解析谱子",
        icon="🎮",
        description="用户提供了 Sky 游戏谱子文件（含 songNotes 字段）",
        todo_template="解析谱子文件 → 转换为 ABC → 加载完成",
        agent_class="ConvertAgent",
    ),
    "edit": DomainMeta(
        name="edit",
        label="编辑谱子",
        icon="✏️",
        description="修改已有谱子（转调/变速/风格/加花等），需已有谱子",
        todo_template="理解编辑意图 → 调用编辑工具 → 验证结果",
        agent_class="EditAgent",
    ),
    "create": DomainMeta(
        name="create",
        label="创作谱子",
        icon="🎵",
        description="从零创作 ABC 谱子（用户描述音乐风格/旋律/情感等）",
        todo_template="理解风格需求 → 创作 ABC 谱子 → 验证存储",
        agent_class="CreateAgent",
    ),
    "audio": DomainMeta(
        name="audio",
        label="生成音频",
        icon="🎧",
        description="生成/迭代音频（生成配乐/再欢快一点/翻唱等）",
        todo_template="分析音频需求 → 生成音频",
        agent_class="AudioAgent",
    ),
    "voice": DomainMeta(
        name="voice",
        label="音色克隆",
        icon="🎤",
        description="音色克隆/TTS（克隆声音/用我的声音/查看音色等）",
        todo_template="分析音色需求 → 克隆/合成音频",
        agent_class="AudioAgent",
    ),
    "query": DomainMeta(
        name="query",
        label="查询分析",
        icon="🔍",
        description="查询/分析谱子信息（这首是什么调/有多少音符等）",
        todo_template="分析用户问题 → 查询谱子信息 → 回答",
        agent_class="QueryAgent",
    ),
    "sovits": DomainMeta(
        name="sovits",
        label="SoVITS 音色",
        icon="🎙️",
        description="GPT-SoVITS 高质量音色克隆/TTS（需配置 SOVITS_BASE_URL）",
        todo_template="上传参考音频 → 克隆音色 → 合成语音",
        agent_class="SoVITSAgent",
    ),
}
```

**`intent_router.py` 改造**（从 DomainConfig 动态生成 prompt）：
```python
# intent_router.py 改造后
from app.agentcore.domain_config import DOMAIN_CONFIG

def _build_router_system() -> str:
    domain_lines = "\n".join(
        f"- {k:8s}: {v.description}"
        for k, v in DOMAIN_CONFIG.items()
    )
    return f"""你是 EP-Agent 的意图路由器。分析用户消息，输出 JSON 路由决策。

意图域（按优先级判断）：
{domain_lines}

输出严格 JSON：
{{"domain": "...", "confidence": 0.0-1.0, "chain_intents": [], "summary": "..."}}
"""
```

---

## 五、GPT-SoVITS 接入方案（预留）

### 5.1 接入架构

```
用户消息："用我的声音唱这首歌"
    ↓ intent_router → domain=voice/sovits
    ↓ AudioAgent / SoVITSAgent
    ↓ ToolRegistry.get("sovits_clone_voice")
    ↓ HTTP → SOVITS_BASE_URL（GPT-SoVITS 服务）
    ↓ 返回 audio_url / audio_b64
    ↓ SSE: tool.call(succeeded) + abc.updated(audio)
```

### 5.2 环境变量

```bash
SOVITS_BASE_URL=http://your-sovits-server:9880  # GPT-SoVITS 服务地址
SOVITS_API_KEY=                                  # 可选鉴权
```

### 5.3 新增步骤（仅需 3 步）

1. **新建** `EP-Agent/backend/app/agentcore/tools/sovits_tools.py`（使用 `@ToolRegistry.register` 注册工具）
2. **新建** `EP-Agent/backend/app/agentcore/agents/sovits_agent.py`（SubAgent，调用注册的工具）
3. **在** `domain_config.py` 的 `DOMAIN_CONFIG` 中补充 `sovits` 域（已预置，取消注释即可）

**无需修改** `universal_runner.py`、`intent_router.py`、`todo_manager.py`、`react_executor.py`。

---

## 六、实施优先级与文件拆分计划

### Phase 1：立即可做（1-2天，不破坏现有功能）

| 任务 | 文件 | 说明 |
|------|------|------|
| 新增 `tool_registry.py` | backend | 空注册表，不影响现有 call_tool |
| 新增 `domain_config.py` | backend | 纯数据，不影响现有逻辑 |
| 新增 `GET /health/tools` | backend | 新端点，无破坏 |
| 新增 `GET /health` | backend | 新端点，无破坏 |
| 新增 `sse-alignment.ts` | frontend | 纯工具函数，不影响现有逻辑 |
| 新增 `tool-registry.ts` | frontend | 纯数据，ToolCard 渐进接入 |
| 新增 `useBackendHealth.ts` | frontend | 独立 hook |
| `chat.store.ts` default 分支补充对齐检查 | frontend | 1行改动 |

### Phase 2：中期优化（3-5天）

| 任务 | 文件 | 说明 |
|------|------|------|
| 现有工具迁移到 `@ToolRegistry.register` | backend/tools/* | 逐个迁移，向后兼容 |
| `intent_router.py` 从 DomainConfig 生成 prompt | backend | 消除漂移 |
| `todo_manager.py` 从 DomainConfig 取 TODO 模板 | backend | 消除漂移 |
| ToolCard 接入 ToolRegistry | frontend | 渐进替换 |
| ChatPanel 顶栏注入健康指示器 | frontend | UI 增强 |
| SequenceGuard 接入 handleSSEEvent | frontend | 健壮性 |

### Phase 3：长期重构（1周+）

| 任务 | 文件 | 说明 |
|------|------|------|
| `session_context.py` 消除透传 | backend | 需同步修改所有 SubAgent |
| `audio_runner.py` 整合到 ReactExecutor | backend | 468行 → ReactExecutor 统一 |
| 前后端 SSE 契约代码生成 | 工具链 | Python → TypeScript 自动生成 |
| SoVITS Agent 实现 | backend | 新建，不改现有文件 |

---

## 七、「可被发现化」原则总结

### Agent 的三个可被发现化层次

```
层次 1：工具可被发现（ToolRegistry）
  - 每个工具自描述（name/label/icon/description/domain/parameters）
  - 注册表统一汇总，LLM 和前端都从注册表取
  - 新增工具：只需新建文件 + @register 装饰器

层次 2：意图可被发现（DomainConfig）
  - 每个意图域自描述（路由判断依据 + TODO 模板）
  - 路由器和规划器都从 DomainConfig 取
  - 新增意图域：只需在 DomainConfig 补一条记录

层次 3：SubAgent 可被发现（_DOMAIN_AGENT_MAP）
  - universal_runner 的 _dispatch 从 DomainConfig 动态加载 SubAgent
  - 新增 SubAgent：只需新建文件，在 DomainConfig 声明 agent_class
  - 无需修改 _dispatch 的任何 if/elif 分支
```

### 最终目标：新增一个完整意图域（如 SoVITS）只需

```
1. tools/sovits_tools.py      ← 工具实现 + @ToolRegistry.register
2. agents/sovits_agent.py     ← SubAgent 实现
3. domain_config.py           ← 补一条 DomainMeta 记录
```

**其他所有文件零改动。**

---

## 八、文件结构全景（目标态）

```
EP-Agent/backend/app/agentcore/
├── domain_config.py      ← 【新增】意图域配置中心（单一来源）
├── tool_registry.py      ← 【新增】工具注册表（可被发现化核心）
├── session_context.py    ← 【新增】Session 上下文管理器（消除透传）
├── todo_manager.py       ← 【现有】TODO 生命周期（从 DomainConfig 取模板）
├── react_executor.py     ← 【现有】通用 ReAct Loop（不变）
├── intent_router.py      ← 【修改】从 DomainConfig 动态生成 prompt
├── abc_utils.py          ← 【现有】ABC 工具函数（不变）
├── llm.py                ← 【现有】LLM 调用封装（不变）
├── agent_loader.py       ← 【现有】.agent 文件热加载（不变）
├── universal_runner.py   ← 【修改】_dispatch 从 DomainConfig 动态加载 SubAgent
├── edit_runner.py        ← 【现有】ABC 编辑专用逻辑（不变）
├── audio_runner.py       ← 【待整合】整合到 ReactExecutor（Phase 3）
├── agents/
│   ├── convert_agent.py  ← 【现有】（移除 session_getter/saver 参数，Phase 3）
│   ├── edit_agent.py     ← 【现有】（同上）
│   ├── create_agent.py   ← 【现有】（同上）
│   ├── audio_agent.py    ← 【现有】（同上）
│   ├── query_agent.py    ← 【现有】（同上）
│   └── sovits_agent.py   ← 【新增，Phase 3】GPT-SoVITS SubAgent
└── tools/
    ├── __init__.py        ← 【修改】从 ToolRegistry 取 call_tool
    ├── abc_tools.py       ← 【修改】迁移到 @ToolRegistry.register
    ├── sky_tools.py       ← 【修改】同上
    └── sovits_tools.py    ← 【新增，Phase 3】GPT-SoVITS 工具

EP-Agent/frontend/src/
├── shared/
│   ├── lib/
│   │   ├── sse-alignment.ts    ← 【新增】SSE 对齐检查 + SequenceGuard
│   │   └── tool-registry.ts   ← 【新增】工具注册表客户端（从 /health/tools 拉取）
│   └── hooks/
│       └── useBackendHealth.ts ← 【新增】后端健康检查 hook
└── widgets/chat-panel/
    ├── ChatPanel.tsx           ← 【修改】顶栏注入健康指示器
    ├── TodoListCard.tsx        ← 【已优化 ✅】
    ├── ChatMessageList.tsx     ← 【现有】
    └── tool-call/
        ├── ToolCard.tsx        ← 【修改】注册表驱动（Phase 2）
        └── ToolRegistry.tsx   ← 【新增】注册表 Provider（Phase 2）
```
