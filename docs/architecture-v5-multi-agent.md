# EP-Agent v5 架构升级方案
# 从 Router+ReAct 迈向 LangGraph / Multi-Agent 范式

> 文档版本：v5.0 · 2026-07-05  
> 基于：EP-Agent 现有代码深度分析  
> 目标：让 Agent 具备**自主决策执行路径**的能力，对齐 LangGraph / OpenAI Swarm / Anthropic MCP 现代范式

---

## 目录

1. [现状诊断：v4 的五个核心短板](#1-现状诊断)
2. [核心范式转变：从路由到动态图](#2-核心范式转变)
3. [升级一：动态图引擎替代 if/elif 分发](#3-升级一动态图引擎)
4. [升级二：Agent-as-Tool 跨 Agent 调用](#4-升级二agent-as-tool)
5. [升级三：并行工具执行](#5-升级三并行工具执行)
6. [升级四：Reflection 反思节点](#6-升级四reflection-反思节点)
7. [升级五：持久化长期记忆（mem0 方案）](#7-升级五持久化长期记忆)
8. [迁移路径：最小改动量的渐进式升级](#8-迁移路径)
9. [改造后的节点图对比](#9-改造后节点图对比)

---

## 1. 现状诊断

### v4 架构的五个核心短板

| 编号 | 短板 | 现有代码位置 | 影响 |
|------|------|------------|------|
| S1 | 路由硬编码 if/elif | `universal_runner._dispatch` L301-485 | Agent 执行路径在路由时固定，无法运行中动态改变 |
| S2 | 无 Agent-as-Tool | 全局无跨 Agent 调用 | H5Agent 无法调用 ConvertAgent，协作靠关键词匹配 |
| S3 | 串行 ReAct Loop | `react_executor.py` 每轮单工具 | 可并行的工具被串行执行，延迟高 |
| S4 | 无 Reflection 节点 | 全局无 Critic/Reflection | 工具结果质量无自动评估，错误靠下一轮 LLM 自发现 |
| S5 | 会话级记忆（2h TTL） | `session_context.py` SESSION_TTL | 用户偏好、历史风格跨会话丢失 |

### 一句话总结当前问题

> EP-Agent v4 是一个**确定性状态机**伪装成 Agent：用户请求进来，`intent_router` 确定 domain，`universal_runner` 按 if/elif 分发，SubAgent 执行固定工具序列，输出结果。**整个过程没有任何节点由 LLM 自主决定下一步走向。**

---

## 2. 核心范式转变

### 2.1 现有模型 vs 目标模型

```
【v4 现有模型 — 确定性状态机】

用户输入
  → IntentRouter (LLM 识别 domain)
  → universal_runner._dispatch (if/elif 硬编码)
  → SubAgent.run() (固定执行序列)
  → 输出

执行路径：在 route_intent() 返回时就已完全固定。
```

```
【v5 目标模型 — LLM 驱动的动态图】

用户输入
  → OrchestratorAgent (LLM 决策：下一个节点是谁)
     ↓  可以调用任意 SubAgent 作为工具
  → SubAgent A (执行，返回结果 + next_action 建议)
     ↓  LLM 根据结果再决策
  → SubAgent B / 工具组 / Reflection节点 / END
     ↓
  → 输出

执行路径：每个节点执行完毕后，由 LLM 决定下一步。
可以循环、回溯、动态分叉。
```

### 2.2 LangGraph 核心概念映射到 EP-Agent

| LangGraph 概念 | EP-Agent v5 对应实现 |
|---------------|-------------------|
| `StateGraph` | `AgentGraph`（新增） |
| `State` | `GraphState` dataclass（新增） |
| `Node` | 每个 SubAgent 的 `run_node()` 方法 |
| `Edge` | LLM 返回的 `next_node` 字段 |
| `Conditional Edge` | `SupervisorAgent.decide_next()` |
| `Checkpointer` | 现有 `db.py` SQLite（复用） |
| `Human-in-the-loop` | 现有 SSE `pipeline.step` 事件（复用） |

---

## 3. 升级一：动态图引擎

### 3.1 新增文件：`app/agentcore/graph_engine.py`

```python
"""
AgentGraph — LangGraph 风格的动态图执行引擎

核心思想：
  - 每个节点（SubAgent）执行后返回 GraphState
  - GraphState 中的 next_node 字段由 LLM（SupervisorAgent）决定
  - 图引擎根据 next_node 调度下一个节点
  - 支持循环、回溯、并行分叉

对比 v4 universal_runner._dispatch：
  v4：if domain == "convert": ... elif domain == "edit": ...  (硬编码)
  v5：supervisor_agent.decide_next(state) -> next_node        (LLM 决策)
"""
from __future__ import annotations
from dataclasses import dataclass, field
from typing import Any, Callable, Awaitable, Literal
import asyncio

# ── GraphState：贯穿全图的共享状态 ──────────────────────────────────────────
@dataclass
class GraphState:
    """
    图执行过程中的共享状态。
    每个节点读取并更新此状态，图引擎根据 next_node 决定下一跳。
    """
    # 用户输入
    session_id:   str = ""
    message:      str = ""
    attachment_name: str = ""
    attachment_workspace_path: str = ""
    
    # 执行状态
    current_node: str = "supervisor"       # 当前节点名
    next_node:    str | None = None        # LLM 决策的下一节点（None = END）
    visited:      list[str] = field(default_factory=list)  # 已访问节点（防死循环）
    
    # 节点间传递的数据
    abc_notation: str = ""                 # 当前谱子 ABC
    score_meta:   dict = field(default_factory=dict)
    tool_results: list[dict] = field(default_factory=list)  # 所有工具执行记录
    
    # Reflection 状态
    reflection_score: float = 1.0         # 质量评分 0-1，低于阈值触发重试
    reflection_notes: str = ""            # 反思内容
    retry_count:      int = 0             # 当前节点重试次数
    
    # 最终输出
    final_output: dict = field(default_factory=dict)
    error:        str = ""
    
    # 发布函数（SSE 推流，不序列化）
    publish: Any = field(default=None, repr=False)


# ── 节点注册表 ────────────────────────────────────────────────────────────────
_NODE_REGISTRY: dict[str, Callable] = {}

def node(name: str):
    """装饰器：注册节点函数到图引擎。"""
    def decorator(fn: Callable) -> Callable:
        _NODE_REGISTRY[name] = fn
        return fn
    return decorator

def get_node(name: str) -> Callable | None:
    return _NODE_REGISTRY.get(name)


# ── AgentGraph：图执行引擎 ────────────────────────────────────────────────────
class AgentGraph:
    """
    动态图执行引擎。
    
    执行流程：
      1. 从 start_node 开始
      2. 执行当前节点 → 更新 state
      3. 调用 supervisor 决策 next_node
      4. 若 next_node == END 或超过 max_steps → 停止
      5. 否则跳转到 next_node，循环执行
    
    关键特性：
      - next_node 由 LLM（SupervisorAgent）决定，不是 if/elif
      - 支持节点间双向跳转（如 edit → reflect → edit）
      - 内置死循环保护（visited 计数 + max_steps）
    """
    
    MAX_STEPS = 12  # 最大执行步数（防止无限循环）
    
    async def run(
        self,
        state: GraphState,
        start_node: str = "supervisor",
    ) -> GraphState:
        state.current_node = start_node
        steps = 0
        
        while steps < self.MAX_STEPS:
            steps += 1
            node_name = state.current_node
            
            # 防死循环：同一节点访问超过 3 次强制终止
            node_visit_count = state.visited.count(node_name)
            if node_visit_count >= 3:
                state.error = f"节点 {node_name} 循环超限，强制终止"
                break
            
            state.visited.append(node_name)
            
            # 推送节点进入事件（SSE 可观测）
            if state.publish:
                await state.publish("graph.node_enter", {
                    "node": node_name,
                    "step": steps,
                    "visited": state.visited,
                })
            
            # 执行节点
            node_fn = get_node(node_name)
            if node_fn is None:
                state.error = f"未知节点: {node_name}"
                break
            
            try:
                state = await node_fn(state)
            except Exception as e:
                state.error = str(e)
                break
            
            # 检查是否结束
            if state.next_node is None or state.next_node == "END":
                break
            
            state.current_node = state.next_node
        
        return state


# 全局图实例
agent_graph = AgentGraph()
```

### 3.2 新增文件：`app/agentcore/supervisor_agent.py`

```python
"""
SupervisorAgent — 图的"大脑"，LLM 驱动的动态路由决策器

这是 v5 架构的核心创新：
  - v4 的 intent_router + universal_runner._dispatch 合并为一个 LLM 节点
  - 每次执行后，SupervisorAgent 观察当前 GraphState，决定下一步
  - 不再有硬编码的 if/elif，路由完全由 LLM 推理

对比 v4：
  v4: route_intent() → domain → if domain == "convert": ...
  v5: supervisor.decide_next(state) → next_node  (LLM 推理)
"""
from __future__ import annotations
import json
from app.agentcore.graph_engine import GraphState, node
from app.agentcore.llm import complete

# ── SupervisorAgent 的决策 Prompt ─────────────────────────────────────────────
_SUPERVISOR_SYSTEM = """你是 EP-Agent 的编排主管（Supervisor）。
你的唯一职责：观察当前执行状态，决定下一步调用哪个 Agent 节点。

可用节点：
- convert_node   : 解析 Sky JSON / ABC 文件 → 生成 ABC 谱
- edit_node      : 编辑已有 ABC 谱（转调/变速/加花等）
- create_node    : 从零创作 ABC 谱
- h5_node        : 生成 H5 乐谱海报页面
- audio_node     : 生成/迭代配乐音频
- sovits_node    : GPT-SoVITS 音色克隆
- reflect_node   : 质量反思（当工具结果质量不佳时调用）
- query_node     : 回答问题（无需修改谱子）
- END            : 任务完成，返回最终结果

决策规则：
1. 若 reflection_score < 0.6 且 retry_count < 2 → 调用 reflect_node 后重试原节点
2. 若用户请求包含多个意图（如"转换后生成H5"）→ 先完成第一个，再决策第二个
3. 若 abc_notation 为空且用户要编辑 → 先 convert_node 或 create_node
4. 已访问节点列表中同一节点超过 2 次 → 强制 END

输出严格 JSON，不要任何其他文字：
{
  "next_node": "节点名或END",
  "reasoning": "一句话说明决策理由",
  "confidence": 0.0-1.0
}
"""

@node("supervisor")
async def supervisor_node(state: GraphState) -> GraphState:
    """
    Supervisor 节点：LLM 决策下一个要执行的节点。
    这是整个动态图的调度中心。
    """
    context = _build_supervisor_context(state)
    
    resp = await complete([
        {"role": "system", "content": _SUPERVISOR_SYSTEM},
        {"role": "user",   "content": context},
    ], tier="lite")
    
    raw = resp if isinstance(resp, str) else resp.get("content", "{}")
    
    import re
    m = re.search(r'\{.*\}', raw, re.DOTALL)
    if m:
        try:
            decision = json.loads(m.group())
            state.next_node = decision.get("next_node", "END")
            
            if state.publish:
                await state.publish("graph.supervisor_decision", {
                    "next_node":  state.next_node,
                    "reasoning":  decision.get("reasoning", ""),
                    "confidence": decision.get("confidence", 1.0),
                    "visited":    state.visited,
                })
            return state
        except Exception:
            pass
    
    # 兜底：根据状态启发式决策
    state.next_node = _heuristic_fallback(state)
    return state


def _build_supervisor_context(state: GraphState) -> str:
    """构建 Supervisor 决策所需的上下文。"""
    parts = [
        f"用户消息：{state.message}",
        f"已访问节点：{' → '.join(state.visited) if state.visited else '无'}",
        f"当前 ABC 谱：{'有（' + str(len(state.abc_notation)) + '字符）' if state.abc_notation else '无'}",
        f"质量评分：{state.reflection_score:.2f}",
        f"重试次数：{state.retry_count}",
    ]
    if state.attachment_name:
        parts.append(f"附件：{state.attachment_name}")
    if state.tool_results:
        last = state.tool_results[-1]
        parts.append(f"上一步结果：{last.get('summary', '')[:100]}")
    if state.error:
        parts.append(f"上一步错误：{state.error}")
    return "\n".join(parts)


def _heuristic_fallback(state: GraphState) -> str:
    """Supervisor LLM 失败时的启发式兜底决策。"""
    if not state.visited:
        # 首次决策
        if state.attachment_name:
            ext = state.attachment_name.lower()
            if ext.endswith(('.mid', '.midi')):
                return "h5_node"
            if ext.endswith(('.txt', '.json', '.abc')):
                return "convert_node"
        return "create_node" if not state.abc_notation else "edit_node"
    
    last_node = state.visited[-1] if state.visited else ""
    if last_node == "convert_node" and state.abc_notation:
        # convert 完成后，检查是否有 h5 意图
        msg_lower = state.message.lower()
        if any(kw in msg_lower for kw in ["h5", "html", "网页", "页面", "播放"]):
            return "h5_node"
    return "END"
```

### 3.3 修改 `universal_runner.py`：替换 `_dispatch`

```python
# ── v5 改造：_dispatch 替换为图引擎调用 ──────────────────────────────────────

# 【删除】原有的 _dispatch 方法中所有 if/elif 分支（约 200 行）

# 【新增】统一走图引擎
async def _dispatch_v5(self, ctx: RunContext) -> dict:
    """
    v5 分发：不再有 if/elif，全部走 AgentGraph。
    Supervisor 节点（LLM）决定每一步调用哪个节点。
    """
    from app.agentcore.graph_engine import AgentGraph, GraphState
    
    # 构建初始状态
    sess = ctx.extra["session_getter"](ctx.session_id)
    state = GraphState(
        session_id=ctx.session_id,
        message=ctx.message,
        attachment_name=ctx.attachment_name,
        attachment_workspace_path=ctx.attachment_workspace_path,
        abc_notation=sess.score.abc_notation if sess.score else "",
        publish=ctx.publish,
    )
    
    # 执行动态图（Supervisor 决策每一步）
    graph = AgentGraph()
    final_state = await graph.run(state, start_node="supervisor")
    
    return final_state.final_output
```

---

## 4. 升级二：Agent-as-Tool

### 4.1 核心思想

让每个 SubAgent 既可以被图引擎作为**节点**调用，也可以被其他 Agent 通过**工具调用**的方式调用。

这实现了真正的多 Agent 协作：
- H5Agent 在执行中发现需要先转换文件 → 直接调用 `call_convert_agent` 工具
- EditAgent 在转调后发现用户想要 H5 → 直接调用 `call_h5_agent` 工具
- 不再需要 `universal_runner` 的链式意图检测（关键词匹配）

### 4.2 新增文件：`app/agentcore/tools/agent_tools.py`

```python
"""
Agent-as-Tool — 将 SubAgent 封装为工具，供其他 Agent 调用

这是 Multi-Agent 协作的核心机制：
  - 任何 Agent 都可以通过工具调用的方式调用其他 Agent
  - 调用方不需要知道被调用 Agent 的内部实现
  - 结果通过 GraphState 传递，保持状态一致性

使用场景：
  H5Agent 执行中：
    → call_convert_agent(sky_json=...) 先转换
    → 拿到 abc_notation 后继续生成 H5
  
  EditAgent 执行后：
    → call_h5_agent(abc=..., template="luoxiaohei") 生成 H5
"""
from __future__ import annotations
from app.agentcore.tools import tool
from app.agentcore.session_context import get_current_session_id


@tool(group="agent_call")
async def call_convert_agent(
    content: str,
    file_name: str = "score.txt",
) -> dict:
    """
    调用 ConvertAgent 将 Sky JSON 或 ABC 内容转换为标准 ABC 谱。
    当当前 Agent 需要先转换文件再继续工作时调用此工具。
    
    content: Sky JSON 字符串或 ABC 文本内容
    file_name: 原始文件名（用于格式推断）
    返回: {"abc_notation": str, "meta": {...}, "success": bool}
    """
    from app.agentcore.agents.convert_agent import ConvertAgent
    from app.agentcore.session_context import get_current_session_id
    from app.pipeline import service as _svc
    
    session_id = get_current_session_id()
    
    # 构造最小 publish（工具调用中不推送 SSE）
    async def _silent_publish(evt_type: str, payload: dict):
        pass
    
    # 复用现有 ConvertAgent 逻辑
    agent = ConvertAgent()
    # 简化调用：直接调用转换函数
    try:
        sess = _svc.get_session(session_id)
        result = await _svc.convert(
            session_id=session_id,
            json_content=content,
            file_name=file_name,
            publish=_silent_publish,
        )
        return {
            "success": True,
            "abc_notation": result.get("abc_notation", ""),
            "meta": result.get("meta", {}),
        }
    except Exception as e:
        return {"success": False, "error": str(e), "abc_notation": ""}


@tool(group="agent_call")
async def call_h5_agent(
    abc_content: str = "",
    midi_workspace_path: str = "",
    title: str = "",
    template: str = "luoxiaohei",
    video_url: str = "",
) -> dict:
    """
    调用 H5Agent 生成乐谱海报页面。
    当需要将当前谱子或 MIDI 转为 H5 页面时调用。
    
    abc_content: ABC 谱内容（与 midi_workspace_path 二选一）
    midi_workspace_path: MIDI 文件工作区路径（与 abc_content 二选一）
    title: 曲名
    template: 模板名（luoxiaohei/apple/miku/neon/ins）
    video_url: 可选视频链接
    返回: {"url_path": str, "workspace_path": str, "success": bool}
    """
    from app.agentcore.tools.h5_tools import (
        get_h5_template, save_h5_output, generate_h5_from_midi
    )
    
    if midi_workspace_path:
        result = generate_h5_from_midi(
            midi_workspace_path=midi_workspace_path,
            title=title,
            template=template,
            video_url=video_url,
        )
        return {
            "success": "error" not in result,
            "url_path": result.get("url_path", ""),
            "workspace_path": result.get("workspace_path", ""),
        }
    
    if abc_content:
        # 读取模板 → 替换 → 保存
        tpl = get_h5_template(template)
        if "error" in tpl:
            return {"success": False, "error": tpl["error"]}
        
        html = tpl["html"]
        # 简单替换 ep-config JSON 块
        import re, json as _json
        config = {
            "TITLE": title or "乐谱",
            "ABC_CONTENT": abc_content.replace("\n", "\\n"),
            "FORMAT_LABEL": "ABC Notation",
            "MIDI_URL": "", "VIDEO_URL": "", "NOTES_JSON": [],
        }
        # 找到 ep-config 块并替换
        pattern = r'(<script id="ep-config"[^>]*>)([\s\S]*?)(</script>)'
        replacement = r'\g<1>\n' + _json.dumps(config, ensure_ascii=False, indent=2) + r'\n\g<3>'
        html = re.sub(pattern, replacement, html)
        
        result = save_h5_output(html=html, filename=title or "score", template=template)
        return {
            "success": "error" not in result,
            "url_path": result.get("url_path", ""),
            "workspace_path": result.get("workspace_path", ""),
        }
    
    return {"success": False, "error": "需要提供 abc_content 或 midi_workspace_path"}


@tool(group="agent_call")
async def call_audio_agent(
    prompt: str,
    style: str = "",
    bpm: int = 0,
    key: str = "",
    provider: str = "auto",
) -> dict:
    """
    调用 AudioAgent 生成配乐音频。
    当需要为谱子生成背景音乐时调用。
    
    prompt: 音频描述（风格/情感/场景）
    style: 音乐风格（如 "轻柔钢琴", "电子流行"）
    bpm: 节拍（0=自动）
    key: 调号（如 "C大调"，可选）
    provider: 音频提供商（auto/suno/minimax）
    返回: {"audio_url": str, "success": bool}
    """
    from app.agentcore.tools.audio_tools import generate_audio_auto
    
    full_prompt = prompt
    if style:
        full_prompt = f"[{style}] {prompt}"
    if bpm:
        full_prompt += f"，BPM {bpm}"
    if key:
        full_prompt += f"，{key}"
    
    try:
        result = await generate_audio_auto(prompt=full_prompt, provider=provider)
        return {
            "success": True,
            "audio_url": result.get("audio_url", ""),
            "provider":  result.get("provider", ""),
        }
    except Exception as e:
        return {"success": False, "error": str(e)}
```

### 4.3 修改各 SubAgent：注入 agent_call 工具组

```python
# 在 H5Agent、EditAgent 等 SubAgent 的工具组装处添加：

# 原有代码（h5_agent.py L198-210）：
h5_tools    = get_tool_schemas("h5")
ws_tools    = [...]
finish_tools = [...]
all_tools   = h5_tools + ws_tools + finish_tools

# v5 新增：加入 agent_call 工具组
agent_call_tools = get_tool_schemas("agent_call")  # ← 新增
all_tools = h5_tools + ws_tools + agent_call_tools + finish_tools
```

---

## 5. 升级三：并行工具执行

### 5.1 修改 `react_executor.py`：支持多工具并行

现有 ReactExecutor 每轮只执行一个工具。现代 LLM（Claude 3.5+、GPT-4o）支持一次返回多个 `tool_calls`，可以并行执行。

```python
# ── 修改位置：react_executor.py 的工具执行部分 ──────────────────────────────

# 【v4 原有代码】（串行，每次只处理 tool_calls[0]）：
tool_call = response.tool_calls[0]
result = await call_tool(tool_call.name, tool_call.arguments)

# 【v5 新代码】（并行，一次处理所有 tool_calls）：
async def _execute_tools_parallel(
    tool_calls: list[dict],
    publish: Publisher,
    session_id: str = "",
) -> list[dict]:
    """
    并行执行多个工具调用。
    
    LLM 返回多个 tool_calls 时（如同时调用 parse_abc + list_h5_templates），
    并行执行，汇聚结果后统一返回，显著降低延迟。
    
    注意：有数据依赖的工具调用（B 依赖 A 的输出）LLM 会自动拆成两轮，
    无需开发者手动处理依赖关系。
    """
    from app.agentcore.tools import call_tool
    
    async def _run_one(tc: dict) -> dict:
        tool_name = tc.get("function", {}).get("name") or tc.get("name", "")
        arguments = tc.get("function", {}).get("arguments") or tc.get("arguments", {})
        if isinstance(arguments, str):
            import json
            arguments = json.loads(arguments)
        
        call_id = tc.get("id", "")
        
        # 推送工具开始事件
        await publish("tool.call", {
            "call_id":   call_id,
            "tool":      tool_name,
            "status":    "running",
            "arguments": arguments,
        })
        
        try:
            result = await call_tool(tool_name, arguments)
            await publish("tool.call", {
                "call_id":        call_id,
                "tool":           tool_name,
                "status":         "succeeded",
                "result_preview": str(result)[:100],
            })
            return {
                "tool_call_id": call_id,
                "name":         tool_name,
                "content":      str(result),
                "result":       result,
                "success":      True,
            }
        except Exception as e:
            await publish("tool.call", {
                "call_id": call_id,
                "tool":    tool_name,
                "status":  "failed",
                "error":   str(e),
            })
            return {
                "tool_call_id": call_id,
                "name":         tool_name,
                "content":      f"Error: {e}",
                "success":      False,
            }
    
    # asyncio.gather 并行执行所有工具
    results = await asyncio.gather(*[_run_one(tc) for tc in tool_calls])
    return list(results)
```

### 5.2 实际收益

以 H5Agent 为例，当前串行执行：
```
list_h5_templates()     → 等待 200ms
get_h5_template(name)   → 等待 300ms  
parse_abc_to_json(abc)  → 等待 150ms
                          合计 650ms
```

v5 并行执行（`list_h5_templates` 和 `parse_abc_to_json` 无依赖，可并行）：
```
list_h5_templates()  ─┐
parse_abc_to_json()  ─┘→ 并行等待 200ms
get_h5_template()    → 等待 300ms（依赖上一步结果，串行）
                         合计 500ms，节省 23%
```

---

## 6. 升级四：Reflection 反思节点

### 6.1 新增文件：`app/agentcore/agents/reflect_agent.py`

```python
"""
ReflectAgent — 质量反思节点

在关键步骤后插入，强制 LLM 对输出进行质量评估：
  - ABC 谱创作完成后：检查音符范围、节奏合理性
  - H5 页面生成后：检查模板变量是否全部替换
  - 音频生成后：检查是否符合用户风格要求

评分低于阈值时：
  - 将反思内容注入下一轮 LLM 上下文
  - 触发原节点重试（最多 2 次）

这解决了 v4 的核心问题：工具结果质量无自动评估。
"""
from __future__ import annotations
from app.agentcore.graph_engine import GraphState, node
from app.agentcore.llm import complete

_REFLECT_SYSTEM = """你是 EP-Agent 的质量评审员（Critic）。
评估上一步的执行结果，给出质量评分和改进建议。

评估维度：
1. 完整性：任务是否完整完成？
2. 正确性：输出格式是否正确？（ABC 语法、HTML 结构等）
3. 用户满意度：是否符合用户原始请求？

输出严格 JSON：
{
  "score": 0.0-1.0,
  "passed": true/false,
  "issues": ["问题1", "问题2"],
  "suggestion": "改进建议（传给下一轮的具体指令）"
}

评分标准：
  0.9-1.0：优秀，直接通过
  0.7-0.9：良好，可通过
  0.5-0.7：一般，建议重试
  < 0.5：较差，必须重试
"""

REFLECTION_THRESHOLD = 0.65  # 低于此分数触发重试

@node("reflect_node")
async def reflect_node(state: GraphState) -> GraphState:
    """
    反思节点：评估上一步输出质量，决定是否需要重试。
    """
    # 构建评估上下文
    context_parts = [f"用户原始请求：{state.message}"]
    
    if state.abc_notation:
        context_parts.append(
            f"生成的 ABC 谱（前500字）：\n{state.abc_notation[:500]}"
        )
    
    if state.tool_results:
        last_result = state.tool_results[-1]
        context_parts.append(
            f"上一步执行结果：{str(last_result)[:300]}"
        )
    
    if state.error:
        context_parts.append(f"执行错误：{state.error}")
        # 有错误时直接给低分
        state.reflection_score = 0.3
        state.reflection_notes = f"上一步发生错误：{state.error}"
    else:
        resp = await complete([
            {"role": "system", "content": _REFLECT_SYSTEM},
            {"role": "user",   "content": "\n".join(context_parts)},
        ], tier="lite")
        
        raw = resp if isinstance(resp, str) else resp.get("content", "{}")
        import re, json
        m = re.search(r'\{.*\}', raw, re.DOTALL)
        if m:
            try:
                review = json.loads(m.group())
                state.reflection_score = float(review.get("score", 0.5))
                state.reflection_notes = review.get("suggestion", "")
                issues = review.get("issues", [])
                
                if state.publish:
                    await state.publish("graph.reflection", {
                        "score":      state.reflection_score,
                        "passed":     review.get("passed", False),
                        "issues":     issues,
                        "suggestion": state.reflection_notes,
                    })
            except Exception:
                state.reflection_score = 0.5
    
    # 决策：通过 or 重试
    if state.reflection_score >= REFLECTION_THRESHOLD:
        # 质量通过 → 回到 supervisor 决定下一步
        state.next_node = "supervisor"
        state.retry_count = 0  # 重置重试计数
    else:
        # 质量不通过 → 重试上一个实质性节点
        last_real_node = _find_last_real_node(state.visited)
        if state.retry_count < 2 and last_real_node:
            state.retry_count += 1
            state.next_node = last_real_node
            # 将反思建议注入消息（下一轮 LLM 能看到）
            state.message = (
                f"{state.message}\n\n"
                f"[质量反思-第{state.retry_count}次重试] {state.reflection_notes}"
            )
        else:
            # 重试次数耗尽，强制通过
            state.next_node = "supervisor"
            state.retry_count = 0
    
    return state


def _find_last_real_node(visited: list[str]) -> str | None:
    """找到最近一个非 supervisor/reflect 的实质性节点。"""
    skip = {"supervisor", "reflect_node"}
    for node_name in reversed(visited):
        if node_name not in skip:
            return node_name
    return None
```

### 6.2 在关键 SubAgent 节点后自动插入 Reflection

```python
# 在 supervisor_agent.py 的决策逻辑中添加：
# 当某些高风险节点（create/edit）执行完毕后，自动插入 reflect_node

_NODES_REQUIRING_REFLECTION = {"create_node", "edit_node", "h5_node"}

# 在 supervisor_node 中：
if state.visited and state.visited[-1] in _NODES_REQUIRING_REFLECTION:
    if "reflect_node" not in state.visited[-3:]:  # 避免连续反思
        state.next_node = "reflect_node"
        return state
```

---

## 7. 升级五：持久化长期记忆

### 7.1 方案选择

| 方案 | 适合场景 | 改造成本 |
|------|---------|---------|
| **mem0（推荐）** | 用户偏好、风格记忆 | 低，pip install mem0ai |
| Zep | 长对话历史语义检索 | 中 |
| 自建 SQLite + embedding | 离线部署，无外部依赖 | 中高 |

EP-Agent 推荐方案：**mem0 + 现有 SQLite 双轨**
- mem0：存储用户风格偏好（"喜欢C大调"、"BPM偏快"）
- SQLite：存储完整对话历史（现有，继续复用）

### 7.2 新增文件：`app/agentcore/long_term_memory.py`

```python
"""
长期记忆模块 — 跨会话持久化用户偏好

解决 v4 的核心短板：session 过期（2h TTL）后用户偏好丢失。

存储内容：
  - 音乐风格偏好（"喜欢爵士风格"、"偏好慢节奏"）
  - 常用调号（"经常用C大调"）
  - 模板偏好（"喜欢 luoxiaohei 模板"）
  - 历史创作摘要（"上次创作了《晚安喵》，C大调，120BPM"）

使用方式：
  1. 每次对话结束时，提取关键信息存入长期记忆
  2. 每次对话开始时，检索相关记忆注入 system prompt
"""
from __future__ import annotations
import json
import sqlite3
from pathlib import Path
from datetime import datetime
from app.config import config

_DB_PATH = Path(config.DATA_DIR) / "long_term_memory.db"


class LongTermMemory:
    """
    基于 SQLite 的长期记忆（无外部依赖版本）。
    
    若要接入 mem0，将 add/search 方法替换为 mem0 client 调用：
      from mem0 import Memory
      self.mem0 = Memory()
      self.mem0.add(messages, user_id=user_id)
      results = self.mem0.search(query, user_id=user_id)
    """
    
    def __init__(self):
        self._init_db()
    
    def _init_db(self):
        with sqlite3.connect(_DB_PATH) as conn:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS memories (
                    id          TEXT PRIMARY KEY,
                    user_id     TEXT NOT NULL,
                    category    TEXT NOT NULL,  -- style/key/template/history
                    content     TEXT NOT NULL,
                    confidence  REAL DEFAULT 1.0,
                    created_at  TEXT NOT NULL,
                    accessed_at TEXT NOT NULL
                )
            """)
            conn.execute("CREATE INDEX IF NOT EXISTS idx_user ON memories(user_id)")
    
    def add(self, user_id: str, category: str, content: str, confidence: float = 1.0):
        """添加一条记忆。"""
        import uuid
        now = datetime.utcnow().isoformat()
        with sqlite3.connect(_DB_PATH) as conn:
            conn.execute(
                "INSERT OR REPLACE INTO memories VALUES (?,?,?,?,?,?,?)",
                (str(uuid.uuid4()), user_id, category, content, confidence, now, now)
            )
    
    def search(self, user_id: str, query: str = "", limit: int = 5) -> list[dict]:
        """检索相关记忆（简单关键词匹配，可替换为向量检索）。"""
        with sqlite3.connect(_DB_PATH) as conn:
            if query:
                rows = conn.execute(
                    "SELECT category, content, confidence FROM memories "
                    "WHERE user_id=? AND content LIKE ? "
                    "ORDER BY accessed_at DESC LIMIT ?",
                    (user_id, f"%{query}%", limit)
                ).fetchall()
            else:
                rows = conn.execute(
                    "SELECT category, content, confidence FROM memories "
                    "WHERE user_id=? ORDER BY accessed_at DESC LIMIT ?",
                    (user_id, limit)
                ).fetchall()
        return [{"category": r[0], "content": r[1], "confidence": r[2]} for r in rows]
    
    def build_memory_context(self, user_id: str) -> str:
        """
        构建注入 system prompt 的记忆前缀。
        每次对话开始时调用，让 Agent 了解用户历史偏好。
        """
        memories = self.search(user_id, limit=8)
        if not memories:
            return ""
        
        lines = ["【用户长期记忆（跨会话）】"]
        for m in memories:
            lines.append(f"  [{m['category']}] {m['content']}")
        return "\n".join(lines)


# ── 记忆提取：对话结束后自动提取关键信息 ──────────────────────────────────────

_EXTRACT_SYSTEM = """从对话中提取用户的音乐偏好信息。
输出 JSON 数组，每条记忆包含 category 和 content：
[
  {"category": "style", "content": "用户偏好爵士风格"},
  {"category": "key",   "content": "用户常用C大调"},
  {"category": "template", "content": "用户喜欢 luoxiaohei H5 模板"}
]
若无明显偏好信息，返回空数组 []。
category 只能是：style / key / template / bpm / history
"""

async def extract_and_save_memories(
    user_id: str,
    conversation: list[dict],
    ltm: LongTermMemory,
):
    """
    对话结束后，用 LLM 提取关键偏好信息存入长期记忆。
    在 service.universal_chat 的 finally 块中调用。
    """
    from app.agentcore.llm import complete
    
    # 只取最近 6 条消息（节省 token）
    recent = conversation[-6:] if len(conversation) > 6 else conversation
    conv_text = "\n".join([
        f"{m['role']}: {m['content'][:200]}" for m in recent
    ])
    
    try:
        resp = await complete([
            {"role": "system", "content": _EXTRACT_SYSTEM},
            {"role": "user",   "content": conv_text},
        ], tier="lite")
        
        raw = resp if isinstance(resp, str) else resp.get("content", "[]")
        import re
        m = re.search(r'\[.*\]', raw, re.DOTALL)
        if m:
            memories = json.loads(m.group())
            for mem in memories:
                ltm.add(
                    user_id=user_id,
                    category=mem.get("category", "general"),
                    content=mem.get("content", ""),
                )
    except Exception:
        pass  # 记忆提取失败不影响主流程


# 全局实例
long_term_memory = LongTermMemory()
```

### 7.3 在 `service.py` 中接入长期记忆

```python
# 在 universal_chat 函数中：

# ── 1. 对话开始前：注入长期记忆 ──────────────────────────────────────────────
from app.agentcore.long_term_memory import long_term_memory

# 获取 user_id（从 session 或 workspace 推断）
_user_id = workspace_id or session_id[:16]
_memory_context = long_term_memory.build_memory_context(_user_id)
# 将 _memory_context 注入到 universal_runner 的 system prompt

# ── 2. 对话结束后：提取并保存记忆 ────────────────────────────────────────────
# 在 finally 块中：
try:
    history = _db.get_session_messages(session_id)
    await extract_and_save_memories(
        user_id=_user_id,
        conversation=history,
        ltm=long_term_memory,
    )
except Exception:
    pass  # 记忆提取失败不影响主流程
```

---

## 8. 迁移路径

### 8.1 渐进式升级策略（不破坏现有功能）

v4 → v5 不是一次性重写，而是**渐进式替换**，每个阶段都可独立上线。

```
Phase 1（1周）：并行工具执行
  - 修改 react_executor.py，支持多 tool_call 并行
  - 不改变任何 Agent 逻辑
  - 风险：极低
  - 收益：延迟降低 20-30%

Phase 2（1周）：Agent-as-Tool
  - 新增 agent_tools.py
  - 各 SubAgent 工具列表加入 agent_call 组
  - 不改变图执行逻辑
  - 风险：低
  - 收益：跨 Agent 协作能力

Phase 3（2周）：Reflection 节点
  - 新增 reflect_agent.py
  - 在 create/edit/h5 节点后插入反思
  - 修改 universal_runner 支持节点回溯
  - 风险：中（需要测试重试逻辑）
  - 收益：输出质量提升

Phase 4（2周）：长期记忆
  - 新增 long_term_memory.py
  - 在 service.py 中接入
  - 风险：低（独立模块，失败不影响主流程）
  - 收益：用户体验大幅提升

Phase 5（3周）：动态图引擎（核心升级）
  - 新增 graph_engine.py + supervisor_agent.py
  - universal_runner._dispatch 替换为图引擎
  - 建议：先灰度 10% 流量，A/B 测试
  - 风险：中高（核心路径改变）
  - 收益：真正的自主决策能力
```

### 8.2 兼容性保障

```python
# universal_runner.py 中的双轨切换开关

USE_GRAPH_ENGINE = os.getenv("EP_AGENT_USE_GRAPH_ENGINE", "false").lower() == "true"

async def _dispatch(self, ...):
    if USE_GRAPH_ENGINE:
        return await self._dispatch_v5(ctx)   # 新：动态图引擎
    else:
        return await self._dispatch_v4(ctx)   # 旧：if/elif 分发（保留）
```

---

## 9. 改造后的节点图对比

### v4 架构（现状）
```
START → UniversalRunner → IntentRouter → [if/elif] → SubAgent → ReactExecutor → 工具 → END
                                              ↑
                                        路由在此固定，后续无法改变
```

### v5 架构（目标）
```
START
  ↓
SupervisorAgent ←──────────────────────────────────────────────┐
  ↓ LLM 决策 next_node                                          │
  ├─→ convert_node ──→ [结果] ──→ SupervisorAgent（再决策）      │
  ├─→ edit_node    ──→ [结果] ──→ reflect_node ──→ SupervisorAgent
  ├─→ create_node  ──→ [结果] ──→ reflect_node ──→ SupervisorAgent
  ├─→ h5_node      ──→ [结果] ──→ reflect_node ──→ SupervisorAgent
  ├─→ audio_node   ──→ [结果] ──→ SupervisorAgent              │
  └─→ END                                                       │
                                                                │
  每个节点内部：                                                  │
    ReactExecutor（并行工具执行）                                 │
      ├─ 工具A ─┐                                               │
      ├─ 工具B ─┼→ asyncio.gather → 汇聚结果                    │
      └─ 工具C ─┘                                               │
    节点可以调用 agent_tools（call_convert_agent 等）──────────────┘
```

### 关键差异

| 特性 | v4 | v5 |
|------|----|----|
| 路由决策者 | 代码（if/elif） | LLM（SupervisorAgent） |
| 执行路径 | 固定（路由时确定） | 动态（每步决策） |
| 节点间协作 | 链式意图关键词匹配 | Agent-as-Tool 直接调用 |
| 工具执行 | 串行 | 并行（asyncio.gather） |
| 质量保障 | 无 | Reflection 节点自动评估 |
| 用户记忆 | 会话级（2h TTL） | 持久化跨会话 |
| 可扩展性 | 加 domain 需改 if/elif | 加节点只需注册 @node |

---

## 附录：新增文件清单

```
EP-Agent/backend/app/agentcore/
├── graph_engine.py          ← 新增：动态图执行引擎（AgentGraph + GraphState）
├── supervisor_agent.py      ← 新增：LLM 驱动的调度决策器
├── long_term_memory.py      ← 新增：跨会话持久化记忆
├── agents/
│   └── reflect_agent.py     ← 新增：质量反思节点
└── tools/
    └── agent_tools.py       ← 新增：Agent-as-Tool 工具组

修改文件：
├── react_executor.py        ← 改：支持并行 tool_call
├── universal_runner.py      ← 改：_dispatch 接入图引擎（双轨切换）
└── pipeline/service.py      ← 改：接入长期记忆
```

---

*文档由超级麦吉基于 EP-Agent 代码深度分析生成 · 2026-07-05*
