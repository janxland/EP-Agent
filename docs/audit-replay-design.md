# EP-Agent 工具调用审计 & 一键重播系统设计文档

> 版本：v1.3 | 日期：2026-07-02（v1.3 第三轮自检修复版）
> 参考：Braintrust Agent Observability Guide 2026、AI Agent Debugging Playbook 2026、Tech Bytes Observability Checklist  
> 设计原则：**零侵入现有功能** · **最小数据结构** · **一键重播可落地**

---

## 〇、v1.1 自检修复记录

> 通过大语言模型边界条件自问自答，发现并修复以下问题：

| 编号 | 严重度 | 问题描述 | 修复方案 | 状态 |
|------|--------|---------|---------|------|
| BUG-1 | 🔴 严重 | `tool_result` 始终为 `"{}"` — SSE 不传完整 JSON，fixture 无法存储真实返回值 | 改为将 `result_preview` 字符串存入 `tool_result`；Phase 2 重播匹配基于 `tool_args_hash`，result 用于展示 | ✅ 已修复 |
| BUG-2 | 🔴 严重 | 同一 `call_id` 重复收到 `running` 事件（SSE 重连/重传）会创建重复 span | 新增 `_seen_running: set[str]`，同一 call_id 只创建一次 span | ✅ 已修复 |
| ISSUE-3 | 🟡 中 | domain 提取依赖固定文本格式 `"意图：sovits — ..."`，格式变化时提取失败 | 改用 `_DOMAIN_PATTERNS` 多正则策略（优先 `payload.domain` 字段，其次多正则兜底） | ✅ 已修复 |
| ISSUE-4 | 🟡 中 | `end_trace` 是 async 函数，但直接调用同步 DB 写入，阻塞 asyncio 事件循环 | 改用 `loop.run_in_executor(None, _write_to_db)` 非阻塞写入 | ✅ 已修复 |
| ISSUE-5 | 🟡 中 | 文档伪代码 `wrap_publish` 执行顺序写反（先记录再推送），与实现不符 | 文档更正为「先原始推送，再审计记录」 | ✅ 已修正 |
| ISSUE-6 | 🟢 低 | `_calc_duration_ms` fallback 用 `datetime.now()` 可能产生误导性数值 | 改为解析失败时返回 `None`，最终 `None` 时返回 0 | ✅ 已修复 |
| DOC-1 | 🟢 低 | 文档 §3.1 Trace 结构含 `run_id` 字段（注释为「与 trace_id 相同」），实现中已省略 | 文档删除 `run_id` 字段，说明省略原因 | ✅ 已修正（见下） |

### v1.2 第二轮自检修复（更深层边界条件）

| 编号 | 严重度 | 问题描述 | 修复方案 | 状态 |
|------|--------|---------|---------|------|
| BUG-3 | 🔴 严重 | `run_in_executor` 执行期间 asyncio 可调度其他协程，`_handle_event` 可能并发修改 `self.spans` 列表，导致线程池读到不一致数据（竞态） | 提交线程池前先做快照：`spans_snapshot = [dict(s) for s in self.spans]`，线程池只读快照 | ✅ 已修复 |
| BUG-4 | 🟡 中 | `call_id` 为空字符串时，BUG-2 的去重逻辑会把所有空 call_id 的工具调用都当作重复而跳过（只记录第一个） | 去重逻辑加 `if call_id:` 前置判断，空 call_id 不参与去重，每次都正常创建 span | ✅ 已修复 |
| SCOPE-1 | 🟢 低 | `service.audio_chat()` 没有挂载 TraceCollector，音频生成路径的工具调用不被审计 | Phase 1 已知范围限制，Phase 2 补充；文档明确说明 | 📝 已记录 |
| DEBT-1 | 🟢 低 | `new_id()` 只有 8 位 hex（32 bits），高并发下 `INSERT OR IGNORE` 静默跳过碰撞导致 trace 丢失 | EP-Agent 为单用户本地工具，实际并发极低，可接受；Phase 3 改为 UUID4 全长 | 📝 已记录 |

**SCOPE-1 说明（audio_chat 审计范围）：**
- Phase 1 仅审计 `universal_chat` 路径（覆盖 sovits/voice/edit/convert/query 所有域）
- `audio_chat` 是独立路径，Phase 2 扩展时在 `service.audio_chat()` 同样挂载 TraceCollector 即可
- 两条路径共用同一套 `db.py` / `router.py`，扩展成本极低

### v1.3 第三轮自检修复（跨组件一致性 + 极端边界条件）

| 编号 | 严重度 | 问题描述 | 修复方案 | 状态 |
|------|--------|---------|---------|------|
| BUG-5 | 🟡 中 | `get_db()` 在线程池新线程首次调用时执行 `_migrate()`，多线程并发初始化可能触发 SQLite 写锁冲突（`database is locked`） | EP-Agent 单用户本地工具，实际并发极低，风险可接受；记录为已知风险，Phase 3 可加 `threading.Lock()` 保护 | 📝 已记录 |
| BUG-6 | 🟡 中 | `timeline.store.ts` 的 `selectTrace` 缺乏竞态保护：用户快速切换 trace 时，慢请求结果覆盖快请求，导致显示错误的 spans | 请求返回后检查 `get().selectedTraceId !== traceId`，不一致则丢弃结果 | ✅ 已修复 |
| ISSUE-7 | 🟢 低 | `PipelineTimelineButton.handleClick` 中重复调用 `loadTraces`，与 `PipelineTimeline` 的 `useEffect` 双重触发，面板打开时连发两次 GET 请求 | 删除 `handleClick` 中的 `loadTraces` 调用，完全依赖 `PipelineTimeline` 的 `useEffect` 统一管理加载时机 | ✅ 已修复 |
| ISSUE-8 | 🟢 低 | `service.py` 调用 `end_trace()` 时未传 `input_tokens`/`output_tokens`，所有 trace 记录的 token 消耗始终为 0 | Phase 1 已知范围限制，Phase 2 从 runner 结果提取 token 数后传入 `end_trace` | 📝 已记录 |

**BUG-6 修复详情（竞态保护模式）：**
```typescript
// timeline.store.ts — selectTrace
selectTrace: async (traceId: string) => {
  if (get().selectedTraceId === traceId) return
  set({ loading: true, selectedTraceId: traceId, spans: [] })
  try {
    const res = await getTraceDetail(traceId)
    if (get().selectedTraceId !== traceId) return  // ← 竞态保护：请求期间已切换
    set({ spans: res.spans, loading: false })
  } catch (e) {
    if (get().selectedTraceId !== traceId) return  // ← 同样保护错误路径
    set({ error: String(e), loading: false })
  }
}
```

**ISSUE-7 修复详情（消除双重 loadTraces）：**
- 修复前：`PipelineTimelineButton.handleClick` 调用 `loadTraces` + `PipelineTimeline.useEffect` 也调用 `loadTraces` = 2次请求
- 修复后：`handleClick` 只调用 `togglePanel()`，数据加载统一由 `PipelineTimeline.useEffect` 负责（`isOpen && sessionId` 时触发）
- 职责更清晰：Button 只管开关，Timeline 自己管数据

### 关键边界条件说明

**关于 `tool_result` 的设计决策（BUG-1 的根本原因）：**

EP-Agent 的 SSE `tool.call` 事件 payload 设计上只传 `result_preview`（截断字符串），不传完整 JSON 对象（避免大结果膨胀 SSE 流）。因此 TraceCollector 通过 `wrap_publish` 拦截时，**无法获得完整工具返回值**。

解决思路有三种：
1. **当前方案（v1.1）**：存储 `result_preview` 字符串，fixture 匹配仅依赖 `tool_args_hash`（参数 hash），重播时用 result_preview 作为展示值。适合 Phase 1 审计场景。
2. **Phase 2 方案**：在 `react_executor.py` 中新增 `on_tool_result` 回调钩子，将完整 result 传给 TraceCollector（需要微量侵入）。
3. **备选方案**：在工具函数层面注入装饰器，自动记录完整 result（侵入更大）。

**Phase 2 推荐采用方案 2**，在 `ReactExecutor` 中追加一个可选回调参数，保持向后兼容。

---

---

## 一、背景与目标

### 1.1 现状

EP-Agent 已有基础的工具调用追踪（`react_executor.py` 中的 `tool_call_records`），并通过 SSE 推送 `tool.call` 事件到前端。但目前缺少：

- **结构化 Trace 持久化**：工具调用记录只存在内存，Session 过期后丢失
- **链路可视化**：前端只有工具卡片，没有完整的执行时间线
- **重播能力**：无法对历史会话"一键重跑"，调试只能靠日志猜测

### 1.2 目标

| 目标 | 说明 |
|------|------|
| **审计链路** | 每次 Agent 执行的完整 Trace 持久化到 SQLite，包含所有工具调用、参数、返回值、耗时 |
| **前端时间线** | 新增 Pipeline Timeline 面板，展示完整调用链路图 |
| **一键重播** | 固定按钮触发：用历史 Trace 的 fixtures 替代真实工具调用，确定性重跑 |
| **零侵入** | 不修改任何现有 SubAgent、工具函数、SSE 协议，纯追加设计 |

---

## 二、2026 年业界方案对比

| 方案 | 核心机制 | EP-Agent 适配性 |
|------|---------|----------------|
| **OpenTelemetry GenAI** | Span/Trace 标准协议，工具调用为独立 Span | ✅ 结构完全对齐，但引入 OTEL SDK 有依赖成本 |
| **LangSmith / Langfuse** | 托管 Trace 存储 + 重播 UI | ❌ 需要外部服务，离线场景不可用 |
| **Braintrust** | Trace + 在线评估 + CI 门控 | ❌ 商业 SaaS，数据不在本地 |
| **自建 SQLite Trace Store** | 本地持久化，无外部依赖 | ✅ 最适合 EP-Agent 当前阶段 |

**结论**：采用**自建 SQLite Trace Store + OpenTelemetry 数据结构**方案，本地优先，未来可无缝对接 OTEL 导出器。

---

## 三、核心数据结构设计

### 3.1 Trace（一次完整执行）

```python
# 对应一次 /api/sessions/:id/chat 请求
{
  "trace_id":    "trace_01J...",   # 全局唯一，贯穿所有 Span
  "session_id":  "sess_xxx",
  # run_id 省略：单 Agent 场景下与 trace_id 相同，无独立价值
  "role_id":     "voice_cloner",
  "domain":      "sovits",
  "user_message": "帮我克隆这段声音",
  "attachment_name": "furina.wav",
  "started_at":  "2026-07-02T00:32:17Z",
  "ended_at":    "2026-07-02T00:32:45Z",
  "duration_ms": 28000,
  "status":      "succeeded",     # succeeded / failed / aborted
  "total_steps": 4,
  "input_tokens": 1240,
  "output_tokens": 380,
  "spans":       [...]             # 见 3.2
}
```

### 3.2 Span（单个工具调用 / 推理步骤）

```python
# span_kind: model | tool | routing | todo_plan | memory | chain
{
  "span_id":       "span_01J...",
  "trace_id":      "trace_01J...",
  "parent_span_id": "span_00J...",  # 父 Span（routing Span 是所有子 Span 的父）
  "agent_name":    "VoiceCloneAgent",
  "span_kind":     "tool",          # model | tool | routing | todo_plan | memory | chain
  "round_idx":     1,               # ReAct 第几轮（0-based）
  "step_idx":      2,               # 本 trace 第几步（全局递增）

  # 工具调用专属（span_kind=tool）
  "tool_name":     "sovits_clone_and_save",
  "tool_args":     {"target_text": "欢迎使用", "ref_audio_workspace_path": "xxx.wav"},
  "tool_args_hash": "sha256:...",   # 用于重播匹配
  "tool_result":   {"workspace_path": "output/cloned.wav", "size_bytes": 102400},
  "tool_result_preview": "workspace_path=output/cloned.wav",
  "attempt":       1,               # 重试次数

  # 模型调用专属（span_kind=model）
  "model":         "gpt-4o-mini",
  "temperature":   0.2,
  "input_tokens":  640,
  "output_tokens": 120,
  "finish_reason": "tool_calls",    # stop | tool_calls | length

  # 通用
  "started_at":    "2026-07-02T00:32:20Z",
  "ended_at":      "2026-07-02T00:32:23Z",
  "duration_ms":   3000,
  "status":        "ok",            # ok | error | timeout | skipped
  "error_msg":     "",
}
```

### 3.3 ReplayFixture（重播用固定数据）

```python
# 每个 tool Span 自动生成一个 fixture
{
  "fixture_id":     "fix_01J...",
  "trace_id":       "trace_01J...",
  "span_id":        "span_01J...",
  "tool_name":      "sovits_clone_and_save",
  "tool_args_hash": "sha256:...",   # 重播时用 hash 匹配，不用原始参数（防 PII）
  "tool_result":    {"workspace_path": "output/cloned.wav"},  # 冻结的返回值
  "created_at":     "2026-07-02T00:32:23Z",
}
```

### 3.4 ReplaySession（重播执行记录）

```python
{
  "replay_id":      "replay_01J...",
  "source_trace_id": "trace_01J...",  # 原始 trace
  "session_id":     "sess_yyy",       # 重播创建的新 session
  "mode":           "fixture",        # fixture（用冻结数据）| live（真实调用）
  "started_at":     "...",
  "status":         "succeeded",
  "diff_summary":   "步骤3工具参数不同：...",  # 与原始 trace 的差异
}
```

---

## 四、系统架构

```
┌─────────────────────────────────────────────────────────────┐
│                     EP-Agent 现有链路（不变）                   │
│                                                             │
│  ChatPanel → /api/sessions/:id/chat                         │
│    → service.universal_chat                                 │
│      → universal_runner.run                                 │
│        → intent_router → TodoManager → SubAgent             │
│          → ReactExecutor → tool.call → SSE publish          │
└──────────────────────────┬──────────────────────────────────┘
                           │ 追加（不修改现有逻辑）
                           ▼
┌─────────────────────────────────────────────────────────────┐
│                   审计层（Audit Layer）                        │
│                                                             │
│  TraceCollector（收集器）                                     │
│    ├── 挂载到 ReactExecutor.on_tool_result 回调              │
│    ├── 挂载到 publish("tool.call") 事件                       │
│    └── 追加写入 SQLite traces / spans / fixtures 表           │
│                                                             │
│  TraceStore（SQLite）                                        │
│    ├── traces 表（Trace 级别）                                │
│    ├── spans 表（Span 级别，含工具调用）                        │
│    └── replay_fixtures 表（冻结工具返回值）                    │
│                                                             │
│  ReplayEngine（重播引擎）                                     │
│    ├── 加载指定 trace_id 的所有 fixtures                      │
│    ├── 创建新 session，注入 FixtureMockToolRegistry           │
│    └── 执行 universal_runner.run（工具调用被 mock 拦截）        │
└──────────────────────────┬──────────────────────────────────┘
                           │
                           ▼
┌─────────────────────────────────────────────────────────────┐
│                   前端新增组件（不影响现有）                     │
│                                                             │
│  PipelineTimeline（时间线面板）                               │
│    ├── 展示 Span 树（routing → todo_plan → tool calls）      │
│    ├── 每个 Span 显示：工具名、耗时、状态、参数预览              │
│    └── 点击 Span 展开详情（完整参数 + 返回值）                  │
│                                                             │
│  ReplayButton（一键重播按钮）                                  │
│    ├── 位置：PipelineTimeline 面板右上角                      │
│    ├── 点击 → POST /api/sessions/:id/replay                  │
│    ├── mode 选择：fixture（快速，无 API 消耗）/ live（真实重跑） │
│    └── 重播结果在新 session 中展示，与原始 trace 对比差异        │
└─────────────────────────────────────────────────────────────┘
```

---

## 五、后端实现方案

### 5.1 新增文件清单

```
EP-Agent/backend/app/
├── agentcore/
│   ├── trace_collector.py    # 新增：TraceCollector（收集 + 写入）
│   └── replay_engine.py      # 新增：ReplayEngine（重播执行）
└── pipeline/
    ├── db.py                  # 修改：新增 traces/spans/fixtures 表 DDL
    └── router.py              # 修改：新增 /traces /replay 端点
```

### 5.2 `trace_collector.py` 设计

```python
"""
TraceCollector — 审计链路收集器

挂载方式（零侵入）：
  1. ReactExecutor.run() 调用时，通过 on_tool_result 回调注入
  2. publish 函数被包装：拦截 tool.call 事件，同步写入 SQLite
  3. 不修改任何现有工具函数或 SubAgent 逻辑

生命周期：
  trace_collector.begin_trace(session_id, message, domain, role_id)
    → 每个工具调用自动追加 span
  trace_collector.end_trace(status)
    → 写入 Trace 汇总行 + 生成所有 fixtures
"""

class TraceCollector:
    def __init__(self, session_id: str, message: str, domain: str, role_id: str):
        self.trace_id = new_id("trace")
        self.session_id = session_id
        self.spans: list[dict] = []
        self.step_idx = 0
        self.started_at = datetime.now(timezone.utc)
        # ... 初始化字段

    def wrap_publish(self, publish: Publisher) -> Publisher:
        """
        包装 publish 函数：拦截 tool.call 事件，自动记录 Span。
        执行顺序（重要）：① 先原始推送（确保 SSE 不受影响）→ ② 再审计记录（失败完全隔离）。
        注意：不能先记录再推送，否则审计崩溃会阻断 SSE 推送。
        """
        async def wrapped(evt_type: str, payload: dict, **kwargs):
            # ① 先执行原始推送，确保 SSE 不受影响
            await publish(evt_type, payload, **kwargs)
            # ② 再做审计记录（异常完全隔离）
            if evt_type == "tool.call":
                self._record_tool_span(payload)
        return wrapped

    def _record_tool_span(self, payload: dict):
        """从 tool.call SSE payload 提取 Span 信息"""
        status = payload.get("status", "running")
        if status == "running":
            # 开始记录：创建 pending span
            span = {
                "span_id":   new_id("span"),
                "trace_id":  self.trace_id,
                "span_kind": "tool",
                "tool_name": payload.get("tool", ""),
                "tool_args": payload.get("arguments", {}),
                "tool_args_hash": _hash_args(payload.get("arguments", {})),
                "step_idx":  self.step_idx,
                "round_idx": payload.get("round_idx", 0),
                "started_at": datetime.now(timezone.utc).isoformat(),
                "status":    "running",
                "call_id":   payload.get("call_id", ""),  # 用于匹配 succeeded/failed
            }
            self.spans.append(span)
            self.step_idx += 1

        elif status in ("succeeded", "failed"):
            # 完成记录：找到对应 span，填写结果
            call_id = payload.get("call_id", "")
            for span in reversed(self.spans):
                if span.get("call_id") == call_id and span["status"] == "running":
                    span["status"] = "ok" if status == "succeeded" else "error"
                    span["tool_result_preview"] = payload.get("result_preview", "")
                    span["ended_at"] = datetime.now(timezone.utc).isoformat()
                    span["duration_ms"] = _calc_duration(span["started_at"], span["ended_at"])
                    if status == "failed":
                        span["error_msg"] = payload.get("error", "")
                    break

    async def end_trace(self, status: str, input_tokens: int = 0, output_tokens: int = 0):
        """
        写入 Trace 汇总行 + 持久化所有 Spans + 生成 Fixtures。
        使用 run_in_executor 避免同步 DB 写入阻塞 asyncio 事件循环。
        """
        ended_at = datetime.now(timezone.utc)
        trace_row = {
            "trace_id":      self.trace_id,
            "session_id":    self.session_id,
            "domain":        self.domain,
            "role_id":       self.role_id,
            "user_message":  self.message[:500],
            "started_at":    self.started_at.isoformat(),
            "ended_at":      ended_at.isoformat(),
            "duration_ms":   int((ended_at - self.started_at).total_seconds() * 1000),
            "status":        status,
            "total_steps":   self.step_idx,
            "input_tokens":  input_tokens,
            "output_tokens": output_tokens,
        }
        # 写入 DB（db.py 新增方法）
        _db.insert_trace(trace_row)
        for span in self.spans:
            _db.insert_span(span)
        # 生成 fixtures（仅 succeeded 的 tool span）
        for span in self.spans:
            if span["span_kind"] == "tool" and span["status"] == "ok":
                _db.insert_fixture({
                    "fixture_id":     new_id("fix"),
                    "trace_id":       self.trace_id,
                    "span_id":        span["span_id"],
                    "tool_name":      span["tool_name"],
                    "tool_args_hash": span["tool_args_hash"],
                    "tool_result":    span.get("tool_result", {}),
                })
```

### 5.3 `replay_engine.py` 设计

```python
"""
ReplayEngine — 一键重播引擎

重播模式：
  1. fixture 模式（默认）：用冻结的工具返回值替代真实调用
     - 速度快（无 API 调用）
     - 用于调试：修改 prompt/逻辑后，验证工具调用链路是否变化
  2. live 模式：真实调用所有工具
     - 用于验证：确认修复后的行为与预期一致

核心机制：FixtureMockToolRegistry
  - 拦截 call_tool(name, args) 调用
  - 用 hash(args) 查找 fixtures 表中的冻结返回值
  - hash 匹配：返回 fixture；hash 不匹配：fallback 到真实调用（并记录差异）
"""

class FixtureMockToolRegistry:
    """Mock 工具注册表：用 fixture 替代真实工具调用"""

    def __init__(self, fixtures: list[dict], fallback_to_live: bool = False):
        # 按 tool_name + args_hash 建立索引
        self._index: dict[str, dict] = {}
        for f in fixtures:
            key = f"{f['tool_name']}:{f['tool_args_hash']}"
            self._index[key] = f
        self.fallback_to_live = fallback_to_live
        self.misses: list[dict] = []  # 记录未命中的工具调用（用于差异分析）

    async def call(self, tool_name: str, arguments: dict) -> any:
        args_hash = _hash_args(arguments)
        key = f"{tool_name}:{args_hash}"
        if key in self._index:
            return self._index[key]["tool_result"]
        # 未命中：记录差异
        self.misses.append({"tool_name": tool_name, "args_hash": args_hash})
        if self.fallback_to_live:
            from app.agentcore.tools import call_tool as _real_call
            return await _real_call(tool_name, arguments)
        return {"error": f"[REPLAY] fixture not found for {tool_name}", "replay_miss": True}


class ReplayEngine:
    async def replay(
        self,
        source_trace_id: str,
        mode: str = "fixture",           # fixture | live
        session_id: str | None = None,   # 若 None，自动创建新 session
        publish: Publisher | None = None,
    ) -> dict:
        """
        重播指定 trace_id 的执行。
        返回：{replay_id, new_session_id, status, diff_summary, spans}
        """
        # 1. 加载原始 trace 信息
        source_trace = _db.get_trace(source_trace_id)
        if not source_trace:
            return {"error": f"trace {source_trace_id} not found"}

        # 2. 加载 fixtures
        fixtures = _db.get_fixtures_by_trace(source_trace_id)

        # 3. 创建新 session（或复用传入的）
        from app.pipeline import service as _svc
        if not session_id:
            new_sess = _svc.create_session(title=f"[重播] {source_trace['user_message'][:20]}")
            session_id = new_sess.id

        # 4. 注入 FixtureMock（fixture 模式）
        if mode == "fixture":
            mock_registry = FixtureMockToolRegistry(fixtures, fallback_to_live=False)
            # 将 mock 注入 ContextVar，ReactExecutor 调用 call_tool 时自动使用
            _set_mock_registry(mock_registry)

        # 5. 执行重播（调用 universal_runner，复用原始 message + domain）
        replay_trace = TraceCollector(session_id, source_trace["user_message"],
                                      source_trace["domain"], source_trace["role_id"])
        wrapped_publish = replay_trace.wrap_publish(publish or _noop_publish)

        result = await universal_runner.run(
            session_id=session_id,
            message=source_trace["user_message"],
            attachment_content="",
            attachment_name=source_trace.get("attachment_name", ""),
            publish=wrapped_publish,
            role_id=source_trace["role_id"],
        )

        await replay_trace.end_trace(status="succeeded" if not result.get("error") else "failed")

        # 6. 清除 mock
        _clear_mock_registry()

        # 7. 对比差异
        diff = _diff_traces(source_trace_id, replay_trace.trace_id)

        # 8. 记录重播记录
        replay_id = new_id("replay")
        _db.insert_replay({
            "replay_id":        replay_id,
            "source_trace_id":  source_trace_id,
            "replay_trace_id":  replay_trace.trace_id,
            "session_id":       session_id,
            "mode":             mode,
            "status":           "succeeded",
            "diff_summary":     diff["summary"],
        })

        return {
            "replay_id":     replay_id,
            "session_id":    session_id,
            "trace_id":      replay_trace.trace_id,
            "diff_summary":  diff["summary"],
            "diff_details":  diff["details"],
            "fixture_misses": mock_registry.misses if mode == "fixture" else [],
        }
```

### 5.4 数据库 DDL（追加到 `db.py`）

```sql
-- Trace 表（每次 chat 请求一行）
CREATE TABLE IF NOT EXISTS traces (
    trace_id      TEXT PRIMARY KEY,
    session_id    TEXT NOT NULL,
    domain        TEXT NOT NULL DEFAULT '',
    role_id       TEXT NOT NULL DEFAULT '',
    user_message  TEXT NOT NULL DEFAULT '',
    attachment_name TEXT DEFAULT '',
    started_at    TEXT NOT NULL,
    ended_at      TEXT,
    duration_ms   INTEGER DEFAULT 0,
    status        TEXT NOT NULL DEFAULT 'running',  -- running/succeeded/failed/aborted
    total_steps   INTEGER DEFAULT 0,
    input_tokens  INTEGER DEFAULT 0,
    output_tokens INTEGER DEFAULT 0,
    created_at    TEXT DEFAULT (datetime('now'))
);

-- Span 表（每个工具调用/推理步骤一行）
CREATE TABLE IF NOT EXISTS spans (
    span_id        TEXT PRIMARY KEY,
    trace_id       TEXT NOT NULL REFERENCES traces(trace_id),
    parent_span_id TEXT DEFAULT '',
    agent_name     TEXT DEFAULT '',
    span_kind      TEXT NOT NULL DEFAULT 'tool',  -- model/tool/routing/todo_plan/memory/chain
    round_idx      INTEGER DEFAULT 0,
    step_idx       INTEGER DEFAULT 0,
    tool_name      TEXT DEFAULT '',
    tool_args      TEXT DEFAULT '{}',   -- JSON
    tool_args_hash TEXT DEFAULT '',
    tool_result    TEXT DEFAULT '{}',   -- JSON（落盘时填写）
    tool_result_preview TEXT DEFAULT '',
    attempt        INTEGER DEFAULT 1,
    model          TEXT DEFAULT '',
    temperature    REAL DEFAULT 0.2,
    input_tokens   INTEGER DEFAULT 0,
    output_tokens  INTEGER DEFAULT 0,
    finish_reason  TEXT DEFAULT '',
    started_at     TEXT NOT NULL,
    ended_at       TEXT,
    duration_ms    INTEGER DEFAULT 0,
    status         TEXT NOT NULL DEFAULT 'running',  -- running/ok/error/timeout/skipped
    error_msg      TEXT DEFAULT '',
    call_id        TEXT DEFAULT '',   -- 与 SSE tool.call_id 对应，用于状态更新匹配
    created_at     TEXT DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS idx_spans_trace_id ON spans(trace_id);
CREATE INDEX IF NOT EXISTS idx_spans_tool_name ON spans(tool_name);

-- Fixture 表（重播用冻结工具返回值）
CREATE TABLE IF NOT EXISTS replay_fixtures (
    fixture_id     TEXT PRIMARY KEY,
    trace_id       TEXT NOT NULL REFERENCES traces(trace_id),
    span_id        TEXT NOT NULL REFERENCES spans(span_id),
    tool_name      TEXT NOT NULL,
    tool_args_hash TEXT NOT NULL,
    tool_result    TEXT NOT NULL DEFAULT '{}',  -- JSON 冻结值
    created_at     TEXT DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS idx_fixtures_trace_id ON replay_fixtures(trace_id);
CREATE INDEX IF NOT EXISTS idx_fixtures_tool_hash ON replay_fixtures(tool_name, tool_args_hash);

-- Replay 记录表
CREATE TABLE IF NOT EXISTS replays (
    replay_id       TEXT PRIMARY KEY,
    source_trace_id TEXT NOT NULL REFERENCES traces(trace_id),
    replay_trace_id TEXT REFERENCES traces(trace_id),
    session_id      TEXT NOT NULL,
    mode            TEXT NOT NULL DEFAULT 'fixture',  -- fixture/live
    status          TEXT NOT NULL DEFAULT 'running',
    diff_summary    TEXT DEFAULT '',
    created_at      TEXT DEFAULT (datetime('now'))
);
```

### 5.5 新增 API 端点（追加到 `router.py`）

```
GET  /api/sessions/:id/traces          # 获取该 session 的所有 trace 列表
GET  /api/traces/:trace_id             # 获取单个 trace 详情（含所有 spans）
GET  /api/traces/:trace_id/spans       # 获取 trace 的所有 spans（时间线数据）
GET  /api/traces/:trace_id/fixtures    # 获取 trace 的所有 fixtures
POST /api/traces/:trace_id/replay      # 触发重播
     body: { mode: "fixture" | "live", session_id?: string }
GET  /api/replays/:replay_id           # 获取重播结果（含 diff）
```

### 5.6 TraceCollector 挂载方式（零侵入）

**挂载点：`service.universal_chat`**，在调用 `universal_runner.run` 前后包装：

```python
# service.py 中 universal_chat 函数追加（仅在此处改动）
async def universal_chat(...) -> dict:
    # ... 现有代码不变 ...

    # ── 新增：创建 TraceCollector，包装 publish ──────────────────────────────
    from app.agentcore.trace_collector import TraceCollector
    _tracer = TraceCollector(
        session_id=session_id,
        message=message,
        domain="",          # domain 在 route_intent 后才知道，tracer 内部延迟填写
        role_id=role_id or "",
        attachment_name=attachment_name,
    )
    _traced_publish = _tracer.wrap_publish(publish)
    # ────────────────────────────────────────────────────────────────────────

    result = await universal_runner.run(
        ...
        publish=_traced_publish,   # 用包装后的 publish 替代原始 publish
        ...
    )

    # ── 新增：结束 trace ──────────────────────────────────────────────────────
    _status = "failed" if result.get("error") else "succeeded"
    await _tracer.end_trace(status=_status)
    # ────────────────────────────────────────────────────────────────────────

    return result
```

**改动量极小**：`service.py` 仅增加 4 行（创建 tracer + 替换 publish + 结束 trace），其余文件零修改。

---

## 六、前端实现方案

### 6.1 新增组件

```
frontend/src/
├── widgets/
│   ├── pipeline-timeline/          # 新增：执行时间线面板
│   │   ├── PipelineTimeline.tsx    # 主组件
│   │   ├── SpanRow.tsx             # 单个 Span 行（工具名/耗时/状态/展开详情）
│   │   └── timeline.store.ts       # Zustand store（加载 trace/spans）
│   └── replay-panel/               # 新增：重播控制面板
│       ├── ReplayButton.tsx        # 一键重播按钮（含模式选择）
│       └── ReplayDiff.tsx          # 重播结果 vs 原始 trace 差异展示
└── shared/
    └── types/
        └── trace.types.ts          # 新增：Trace/Span/Fixture/Replay 类型定义
```

### 6.2 PipelineTimeline 布局

```
┌─────────────────────────────────────────────────────┐
│  执行时间线  [trace_id: trace_01J...]  耗时: 28.3s   │
│  ─────────────────────────────────── [🔄 一键重播 ▼] │
│                                                     │
│  ● routing       intent_router      0.8s  ✅        │
│  ● todo_plan     TodoManager        1.2s  ✅        │
│  ├─● tool  [1]  sovits_list_audio_files  0.3s  ✅   │
│  │   ▶ 参数：{}                                     │
│  │   ▶ 返回：[{workspace_path: "furina.wav", ...}]  │
│  ├─● model [1]  gpt-4o-mini  1.8s  ✅  640→120tok  │
│  ├─● tool  [2]  sovits_clone_and_save  24.1s  ✅   │
│  │   ▶ 参数：{target_text: "欢迎使用", ref_audio:…} │
│  │   ▶ 返回：{workspace_path: "cloned.wav"}         │
│  └─● tool  [3]  finish_task  0.1s  ✅              │
│                                                     │
│  Token 用量：1240 in / 380 out  │  总耗时：28.3s    │
└─────────────────────────────────────────────────────┘
```

### 6.3 一键重播按钮交互设计

```
点击 [🔄 一键重播 ▼]
  ↓
弹出下拉菜单：
  ├── 🚀 快速重播（fixture 模式）
  │     说明：用历史工具返回值重跑，不消耗 API，速度极快
  └── 🔴 真实重播（live 模式）
        说明：重新调用所有工具，验证修复效果

选择后：
  → 显示进度条（SSE 驱动，与正常对话相同）
  → 完成后展示差异面板：
    ┌──────────────────────────────────────────────┐
    │  重播结果对比                                  │
    │  ✅ 工具调用链路相同（3/3 步骤匹配）            │
    │  ⚠️  步骤2参数差异：target_text 不同           │
    │  原始：「欢迎使用 EP-Agent」                   │
    │  重播：「欢迎使用」                             │
    └──────────────────────────────────────────────┘
```

### 6.4 新增 SSE 事件（不影响现有）

```typescript
// trace.types.ts
export type TraceSSEEvent =
  | { type: 'trace.begin';  payload: { trace_id: string; domain: string } }
  | { type: 'trace.span';   payload: SpanPayload }
  | { type: 'trace.end';    payload: { trace_id: string; status: string; duration_ms: number } }
  | { type: 'replay.begin'; payload: { replay_id: string; source_trace_id: string } }
  | { type: 'replay.end';   payload: { replay_id: string; diff_summary: string } }
```

---

## 七、实施路线图

### Phase 1：审计链路（1 周）

**后端**
- [ ] `db.py`：新增 4 张表 DDL + CRUD 方法（`insert_trace / insert_span / insert_fixture`）
- [ ] `trace_collector.py`：实现 `TraceCollector`（`wrap_publish` + `end_trace`）
- [ ] `service.py`：4 行挂载代码
- [ ] `router.py`：新增 `GET /api/sessions/:id/traces` + `GET /api/traces/:trace_id/spans`

**前端**
- [ ] `trace.types.ts`：类型定义
- [ ] `timeline.store.ts`：加载 trace/spans 的 Zustand store
- [ ] `PipelineTimeline.tsx`：时间线面板（只读展示）
- [ ] 挂载到现有工作区侧边栏底部（折叠面板，不影响现有布局）

**验收标准**：每次对话后，`/api/sessions/:id/traces` 能返回完整的工具调用链路，前端时间线正确展示。

### Phase 2：一键重播（1 周）

**后端**
- [ ] `replay_engine.py`：`FixtureMockToolRegistry` + `ReplayEngine`
- [ ] `router.py`：新增 `POST /api/traces/:trace_id/replay`
- [ ] ContextVar 注入机制：`_set_mock_registry` / `_clear_mock_registry`

**前端**
- [ ] `ReplayButton.tsx`：下拉按钮（fixture/live 模式选择）
- [ ] `ReplayDiff.tsx`：差异展示面板
- [ ] SSE 处理：新增 `replay.begin` / `replay.end` 事件处理

**验收标准**：点击「快速重播」后，3 秒内完成，前端展示与原始 trace 的差异对比。

### Phase 3：高级功能（可选，后续迭代）

- [ ] Trace 搜索与过滤（按 domain / 工具名 / 状态 / 时间范围）
- [ ] Trace 导出（JSON / OpenTelemetry OTLP 格式）
- [ ] 自动回归测试：将失败 trace 加入测试套件，CI 时重播验证
- [ ] 成本统计：按 trace / session / 时间段统计 token 消耗

---

## 八、关键设计决策

### 8.1 为什么用 `wrap_publish` 而不是修改 `ReactExecutor`？

`wrap_publish` 是**最小侵入**方案：
- `ReactExecutor` 已经在推送 `tool.call` 事件，信息完整
- 只需在 `publish` 函数层面拦截，不需要修改任何执行逻辑
- 未来即使 `ReactExecutor` 重构，`TraceCollector` 仍然有效

### 8.2 为什么用 `tool_args_hash` 而不是原始参数匹配？

- 参数可能含路径、时间戳等动态值，hash 匹配更鲁棒
- 保护 PII（参考音频路径等敏感信息不直接存储）
- 与 2026 年业界最佳实践（Tech Bytes Checklist）一致

### 8.3 为什么 fixture 模式下 hash 不匹配时返回错误而不是真实调用？

- fixture 模式的价值在于**确定性**：如果 fallback 到真实调用，重播结果就不确定了
- 不匹配说明参数发生了变化（prompt 修改导致工具调用参数不同），这正是需要关注的差异
- `misses` 列表会记录所有未命中，用于差异分析

### 8.4 重播 session 与原始 session 的关系

- 重播创建**全新 session**，不污染原始 session
- 新 session 标题自动加 `[重播]` 前缀
- 用户可在侧边栏看到重播 session，与普通 session 并列

---

## 九、与现有功能的兼容性矩阵

| 现有功能 | 影响 | 说明 |
|---------|------|------|
| SSE 事件推送 | ✅ 无影响 | `wrap_publish` 只追加行为，原始推送照常执行 |
| ReactExecutor ReAct Loop | ✅ 无影响 | 不修改任何执行逻辑 |
| TodoManager | ✅ 无影响 | TODO 状态流转不变 |
| 所有 SubAgent | ✅ 无影响 | 不修改任何 Agent 文件 |
| 所有工具函数 | ✅ 无影响（fixture 模式下被 mock） | 真实工具函数代码不变 |
| 前端现有面板 | ✅ 无影响 | 新增面板独立挂载，不修改现有组件 |
| SQLite 数据库 | ⚠️ 新增 4 张表 | 向后兼容，现有表结构不变 |
| `service.py` | ⚠️ 新增 4 行代码 | 最小侵入，不改变任何现有逻辑 |

---

*设计文档版本：v1.0 | 参考：Braintrust 2026 · AI Agent Debugging Playbook 2026 · Tech Bytes Observability Checklist 2026*
