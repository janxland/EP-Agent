# EP-Agent Agent 架构深度文档 v3.1

> 更新时间：2026-06-24  
> 对标参考：`coding/magic-coding-service`（router.agent / fullstack.agent / todo_critic.go）

---

## 1. 架构全景图

```
用户消息
  │
  ▼
service.universal_chat()          ← 用例层（落库 + session 管理）
  │
  ▼
UniversalChatRunner.run()          ← 编排层（v3.1：纯编排，~274行）
  ├── route_intent()               ← 意图路由（intent_router.py）
  ├── TodoManager.plan()           ← TODO 规划 + TodoCritic（todo_manager.py）
  └── _dispatch()                  ← SubAgent 调度
        ├── ConvertAgent           ← Sky JSON → ABC
        ├── EditAgent              ← ABC 编辑（→ edit_runner.run_edit()）
        ├── CreateAgent            ← ABC 创作
        ├── AudioAgent             ← 音频/音色（→ audio_runner）
        └── QueryAgent             ← 谱子问答

ReactExecutor.run()                ← 通用 ReAct Loop（react_executor.py）
  └── complete_one() 纪律          ← 工具成功后自动 complete TODO
```

---

## 2. 模块职责一览（v3.1 最终状态）

| 文件 | 行数 | 职责 | 依赖 |
|------|------|------|------|
| `universal_runner.py` | 274 | **纯编排**：路由→规划→调度 SubAgent | intent_router, todo_manager |
| `todo_manager.py` | 313 | TODO 生命周期 + TodoCritic + finish_gate | llm |
| `react_executor.py` | 203 | 通用 ReAct Loop + stream_text/stream_llm | llm, todo_manager, tools |
| `intent_router.py` | 130 | 意图路由 LLM + 链式意图关键词检测 | llm |
| `abc_utils.py` | 83 | ABC 提取/解析/计数（共享工具函数） | — |
| `edit_runner.py` | 261 | **ABC 编辑逻辑**：prompt构造 + run_edit() | react_executor, abc_utils |
| `agents/convert_agent.py` | 152 | Sky JSON → ABC 转换 SubAgent | todo_manager, react_executor |
| `agents/edit_agent.py` | 173 | ABC 编辑 SubAgent（调用 run_edit()） | edit_runner, todo_manager |
| `agents/create_agent.py` | 250 | ABC 创作 SubAgent | llm, abc_utils, todo_manager |
| `agents/audio_agent.py` | 144 | 音频/音色 SubAgent（含 SoVITS 路由） | todo_manager, react_executor |
| `agents/query_agent.py` | 64 | 谱子问答 SubAgent | todo_manager, react_executor |

---

## 3. v3.0 → v3.1 核心变更

### 3.1 universal_runner.py 大幅精简

| 指标 | v3.0 | v3.1 |
|------|------|------|
| 行数 | 1516 行 | 274 行 |
| 缩减 | — | **81%（-1242行）** |
| 职责 | TodoManager + ReactExecutor + UniversalChatRunner + 5个函数 | 仅 UniversalChatRunner（纯编排） |
| 类数量 | 3 个类 | 1 个类 |

**拆分去向：**
- `TodoManager` → `todo_manager.py`
- `ReactExecutor` → `react_executor.py`
- `_route_intent()` → `intent_router.py`
- `_assert_finish_gate()` → `todo_manager.py`（`assert_finish_gate`）
- `_extract_abc_and_summary()` → `abc_utils.py`（`extract_abc_and_summary`）
- `_do_convert/edit/create/audio/query` → `agents/` 目录各 SubAgent

### 3.2 edit_runner.py 架构整合

**v3.0 问题：** `DirectEditRunner` 内嵌独立 ReAct Loop，绕过了 `TodoManager` 和 `finish_gate`。

**v3.1 修复：**
```
v3.0：EditAgent → edit_fn(session_id, message, publish)
                    └── DirectEditRunner.run()（独立 ReAct Loop，绕过 TodoManager）

v3.1：EditAgent → run_edit(current_abc, intent, meta, todo_mgr, ...)
                    └── ReactExecutor.run()（统一 ReAct Loop，todo_mgr 由外层传入）
```

- `edit_runner.py` 保留：`run_edit()`（纯逻辑）+ `_build_system_prompt()`
- `edit_runner.py` 移除：`DirectEditRunner`（内嵌 ReAct Loop）
- 向后兼容：`edit_agent_runner`（`_LegacyEditRunner`）保留旧接口，`service.edit()` 无需修改

### 3.3 链式意图状态污染修复

**v3.0 问题：** convert 完成后检测 chain_intent，复用已 `finish_all` 的 `todo_mgr`，导致状态污染。

**v3.1 修复：**
```python
# universal_runner.py _dispatch()
chain_todo_mgr = TodoManager()   # ← 新实例，避免状态污染
await chain_todo_mgr.plan(message, extra_domain, True, publish, session_id)
```

### 3.4 EditAgent 完全自治

**v3.1 新增：** `EditAgent` 直接从 session 取谱子信息，不再依赖外部透传：
- 从 `session_getter(session_id)` 取 `current_abc` 和 `meta`
- 调用 `run_edit()` 传入完整参数
- 编辑结果直接落库（`upsert_session`）+ 推送 `abc.updated`
- `edit_fn` 参数保留但不使用（签名兼容性）

---

## 4. TODO 执行纪律（v3.1 验证通过）

```
pending → running（tick，开始执行前）
running → done  （complete_one，真实落地后立刻调用）
任何情况下不允许在未真实执行前标记 done
finish_all 只用于：异常兜底(failed) 或 所有工作真实完成后的收尾
```

### 关键机制覆盖（全量扫描结果）

| 机制 | 出现次数 | 状态 |
|------|----------|------|
| `assert_finish_gate` 调用 | 25 处 | ✅ |
| `finish_all("failed")` 调用 | 5 处 | ✅ |
| `complete_one` 调用 | 25 处 | ✅ |
| `message.completed` 推送 | 10 处 | ✅ |
| `TodoManager()` 新实例 | 3 处 | ✅ |
| `chain_todo_mgr = TodoManager()` | 1 处 | ✅ |
| `run_edit()` 调用 | 11 处（含注释） | ✅ |
| 循环依赖 | 0 | ✅ |

---

## 5. 工具发现机制（Tool Discovery）

```python
# tools/ 目录自动扫描（universal_runner.py 启动时执行）
for _mod_info in pkgutil.iter_modules([str(_tools_dir)]):
    importlib.import_module(f"app.agentcore.tools.{_mod_info.name}")

# 意图域 → 工具组映射（唯一扩展点）
# 新增工具域：只需在 tools/ 加 @tool(group="new_group") + 在此注册
_DOMAIN_TOOL_GROUPS 已迁移到各 SubAgent 内部管理
```

### 当前工具组

| 工具组 | 工具文件 | 用途 |
|--------|----------|------|
| `abc_edit` | `abc_tools.py` | analyze_abc, validate_abc |
| `output` | `export_tools.py` | abc_to_sky_json, abc_to_midi_b64 |
| `audio` | `audio_tools.py`, `audio_evolve_tools.py`, `voice_clone_tools.py` | 音频生成/迭代/克隆 |
| `sovits` | `sovits_tools.py` | GPT-SoVITS TTS/克隆（需配置 SOVITS_BASE_URL） |

---

## 6. 扩展新意图域（只需两步）

```python
# Step 1: 创建 agents/new_agent.py
class NewAgent:
    async def run(self, session_id, message, publish, todo_mgr, ...) -> dict:
        # 实现 SubAgent 逻辑
        ...

# Step 2: 在 universal_runner.py _dispatch() 中注册
from app.agentcore.agents.new_agent import NewAgent
if domain == "new_domain":
    await todos_task
    return await NewAgent().run(...)
```

**无需修改：** `TodoManager`、`ReactExecutor`、`intent_router`、`abc_utils`

---

## 7. GPT-SoVITS 接入指南（后续）

当前状态：`sovits_tools.py` 已预置工具组，`AudioAgent._run_sovits()` 已实现调用逻辑。

接入步骤：
1. 部署 GPT-SoVITS 服务
2. 设置环境变量：`SOVITS_BASE_URL=http://your-sovits-host:9880`
3. 无需修改任何 Agent 代码，`AudioAgent._should_use_sovits()` 自动检测并路由

---

## 8. 待优化项（低优先级）

| 优先级 | 问题 | 方案 |
|--------|------|------|
| 低 | `session_getter/session_saver` 仍透传给部分 SubAgent | 封装为 Repository 模式或 Context 变量 |
| 低 | `create_agent.py` 内嵌 LLM prompt（65行） | 热加载 `abc-create.agent` 文件 |
| 低 | `audio_runner.py` 仍有独立 ReAct Loop | 音频逻辑复杂，由 AudioAgent 外层管控，暂不拆分 |
| 低 | TodoCritic 发现问题时只记录日志 | 可扩展为触发 replan（需权衡延迟） |
