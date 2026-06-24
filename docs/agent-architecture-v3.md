# EP-Agent 架构深度优化方案 v3
> 基于 magic-coding-service + ClaudeCode 泄露机制深度学习

---

## 1. 核心差距分析（对标 magic-coding-service）

### 1.1 架构层面差距

| 维度 | magic-coding-service | EP-Agent 现状 | 差距 |
|------|---------------------|---------------|------|
| **Router/SubAgent 分层** | Router 独立编排，SubAgent 专注实现，职责严格隔离 | universal_runner 单体承担路由+规划+执行全部职责 | 🔴 严重 |
| **上下文隔离** | topic_context 独立存储，Router→SubAgent 结构化传递，SubAgent 间不共享 | session.intent_history 全量注入，无隔离边界 | 🟡 中等 |
| **finish_task 门控** | 唯一完成信号，finish=true 前不得有 pending todo，Router 强制校验 | finish_all() 随时可调，无强制门控 | 🔴 严重 |
| **output_contract** | 每个 Agent 有严格 JSON 输出契约，Router 解析校验 | LLM 自由文本输出，无结构化契约 | 🟡 中等 |
| **黑名单/边界机制** | Router 维护禁改文件列表，SubAgent 强制遵守 | 无边界控制，各 runner 可随意调用任何函数 | 🟡 中等 |
| **TodoCritic** | 独立 LLM 评审 TODO 结构，内置于 replace_todos 工具 | critic_check 已实现但未接入执行流程 | 🟡 中等 |
| **SubAgent 并发** | run_agents_in_parallel 真正并发多 SubAgent | 无 SubAgent 机制，全串行 | 🔵 低优先级 |
| **complete_todo 纪律** | 真实落地后强制调用，finish_task 前门控 | v3 已修复 complete_one | ✅ 已修复 |

### 1.2 EP-Agent 特有问题（v3 已修复）

```
Bug1: _do_convert 降级调用时 session_getter/session_saver 未传入 → NameError 被 except 吞
Bug2: TODO 在 LLM 调用前就被标记为 done → 虚假完成
Bug3: ReAct Loop stop 时不检查 running TODO → 提前退出
Bug4: query 域 finish_all 在 LLM 调用前执行 → 3/3 全绿但实际没执行
Bug5: _do_edit_react 签名参数不一致
```

---

## 2. magic-coding-service 核心机制学习

### 2.1 Router/SubAgent 分层架构

```
用户请求
    ↓
Router Agent（编排层）
    ├── generate_current_plan()    → 规划 Current.md（唯一事实源）
    ├── sync_prisma_schema()       → 数据层收口（Router 独占）
    ├── write_file(layout.tsx...)  → 共享基础设施收口
    ├── 维护 blacklist             → 禁改文件列表
    └── run_agents_in_parallel()   → 并发调度多个 SubAgent
            ↓
    Fullstack SubAgent（实现层）
        ├── list_todos()           → 查看/创建 TODO
        ├── replace_todos()        → 重建 TODO（触发 TodoCritic）
        ├── complete_todo()        → 真实落地后强制调用
        ├── run_write_tasks_in_parallel() → 并发写文件
        └── finish_task()          → 唯一完成信号（门控：no pending todos）
```

### 2.2 TodoCritic 机制（todo_critic.go）

**触发时机**：`replace_todos` 工具内部自动调用，SubAgent 无感知

**检查范围（只检查结构错误）**：
- ✅ TODO 缺少明显必需的步骤
- ✅ 同一条 TODO 描述与 files 明显矛盾
- ✅ 把单一交付结果拆成无法独立落地的碎片

**不检查（避免过度干预）**：
- ❌ 业务完整性、功能是否丰富
- ❌ 页面矩阵是否完整、交互是否摆设
- ❌ 实现顺序、component_type

**输出格式**：
```json
{"pass": true|false, "issues": [...], "required_fixes": [...]}
```

### 2.3 finish_task 门控机制

```
SubAgent 执行流程：
  replace_todos() → [TodoCritic 自动评审] → 执行工具
  → complete_todo(id)  ← 真实落地后立刻调用
  → complete_todo(id)  ← 每个 TODO 都必须调用
  → [检查：no pending todos]
  → finish_task()      ← 唯一完成信号

Router 执行流程：
  generate_current_plan() → sync_prisma() → 共享基础设施收口
  → run_agents_in_parallel() → [等待所有 SubAgent finish_task]
  → 后置补齐共享组件 → npm run typecheck
  → finish_task()  ← Router 的唯一完成信号
```

### 2.4 上下文隔离原则

```
topic_context（话题级共享）
    ↓ 注入
Router Agent 执行上下文（路由判断、阶段结论、调度决策）
    ↓ 结构化传递（只传必要信息，不传完整内部上下文）
SubAgent 执行上下文（当前分派的工作，私有 TODO 清单）
    ↓ 结果回传（只传结论和变更摘要）
Router Agent → 统一决定是否写回 topic_context
```

**关键原则**：
- SubAgent 间不直接共享各自的内部上下文
- SubAgent 不继承 Router 的完整内部上下文
- Router 通过结构化 message 传递必要信息（2-3 句话，不是完整 prompt）

### 2.5 output_contract 机制

每个 Agent 有严格的 JSON 输出契约：

```json
{
  "finish": true,
  "finish_reason": "为什么现在可以结束（1句话）",
  "summary": "本轮工作摘要（短摘要，不是长篇报告）",
  "user_visible_message": "给用户看的简短结果（1-2句）",
  "context_notes": ["要沉淀到上下文的关键结论（每条1句）"],
  "handoff_note": "给 Router 的下一轮交接说明",
  "expected_files": ["本轮确认创建或修改过的关键文件路径"]
}
```

**门控规则**：
- `finish=true` 前不得有 pending todo
- 不允许把骨架/占位/stub 伪装成已完成
- `finish=false` 时禁止输出 summary

---

## 3. EP-Agent 对标改进方案

### 3.1 立即可做（不破坏现有架构）

#### A. 在 universal_runner 中接入 TodoCritic

```python
# 在 TodoManager.plan() 之后，执行之前调用
critic_result = await todo_mgr.critic_check(message, domain)
if not critic_result.get("pass", True):
    # 重新规划 TODO（修正结构问题）
    await todo_mgr.replan_with_fixes(critic_result["required_fixes"], publish)
```

#### B. 为每个域建立 output_contract 校验

每个 `_do_xxx` 方法返回前，验证：
- 所有 TODO 已 complete_one（无 pending/running）
- 返回 dict 包含必要字段（domain, message, abc_updated）

#### C. 强化 finish_task 门控

```python
async def _assert_all_done(todo_mgr: TodoManager, domain: str):
    """finish_task 前的门控检查，确保无 pending/running TODO"""
    pending = todo_mgr.get_pending_ids()
    running = todo_mgr.get_running_ids()
    if pending or running:
        raise RuntimeError(
            f"[{domain}] finish_task 门控失败："
            f"pending={pending}, running={running}"
        )
```

### 3.2 中期改进（Router/SubAgent 分层）

#### EP-Agent 的 Router/SubAgent 映射

```
UniversalChatRunner（Router 角色）
    ├── _route_intent()          → 意图路由（对标 generate_current_plan）
    ├── TodoManager.plan()       → TODO 规划（对标 replace_todos）
    ├── TodoManager.critic_check() → TodoCritic（对标 todo_critic.go）
    └── 按 domain 调度 SubAgent
            ↓
    ConvertAgent（convert 域）
        → 解析 Sky JSON → 转换 ABC → 落库
    EditAgent（edit 域）
        → ReAct Loop → 工具调用 → 验证 → 落库
    CreateAgent（create 域）
        → LLM 创作 → 验证修正 → 落库
    AudioAgent（audio/voice 域）
        → 音频生成/迭代 → 返回 URL
    QueryAgent（query 域）
        → 注入上下文 → LLM 回答
```

每个 SubAgent：
- 有自己的 TODO 清单（私有，不共享）
- 执行完后通过 finish_task 信号回报 Router
- Router 决定是否继续调度下一个 SubAgent

#### 新增 agent prompt 文件

```
EP-Agent/backend/agent/agents/
    abc-router.agent     ✅ 已有（但需升级为 Router 角色）
    abc-edit.agent       ✅ 已有
    abc-audio.agent      ✅ 已有
    abc-convert.agent    ❌ 缺失（需新增）
    abc-create.agent     ❌ 缺失（需新增）
    abc-query.agent      ❌ 缺失（需新增）
```

### 3.3 长期改进（并发 SubAgent）

目前 EP-Agent 是音乐创作场景，各域之间有依赖关系（convert → edit），
暂时不需要真正的并发 SubAgent。

但可以在以下场景引入并发：
- `convert` + `query`：同时转换谱子并分析信息
- `edit` × N：同时对多个谱子片段进行编辑

---

## 4. 立即实施：接入 TodoCritic + finish_task 门控

见 `universal_runner.py` v3.1 版本。

---

## 5. 新增 agent prompt 文件

### abc-convert.agent

```yaml
name: abc-convert
description: Sky JSON → ABC 转换专用 Agent
tools:
  - convert_sky_json
  - validate_abc
max_steps: 3
```

### abc-create.agent

```yaml
name: abc-create
description: ABC 谱创作专用 Agent（世界顶级音乐创作大师）
tools:
  - validate_abc
max_steps: 5
```

### abc-query.agent

```yaml
name: abc-query
description: 谱子信息查询/分析专用 Agent
tools: []
max_steps: 2
```

---

## 6. 关键设计原则总结

### 6.1 TODO 执行纪律（必须遵守）

```
1. pending → running：开始执行时立刻 tick
2. running → done：真实落地后立刻 complete_one（禁止在执行前调用）
3. finish_task：所有 TODO complete_one 后才允许调用
4. finish_all：只用于异常兜底（status="failed"），禁止批量标绿
```

### 6.2 上下文传递原则

```
Router → SubAgent：只传 2-3 句话的结构化 message
SubAgent → Router：只传结论和变更摘要（不传完整内部上下文）
SubAgent 间：不直接共享上下文
```

### 6.3 output_contract 原则

```
每个 Agent 必须有明确的输出契约：
- finish=true 前：no pending todos
- summary：短摘要，不是长篇报告
- 不允许把骨架/占位伪装成已完成
```

### 6.4 工具发现原则

```
新增工具：只需在 tools/ 加 @tool(group="xxx")
新增意图域：只需在 _DOMAIN_TOOL_GROUPS 加一行
新增 SubAgent：只需新建 xxx.agent 文件 + 对应 _do_xxx 方法
无需修改 Router 执行逻辑
```

---

## 7. GPT-SoVITS 接入路径

```python
# 1. 部署 GPT-SoVITS 服务
# 2. 设置环境变量
SOVITS_BASE_URL=http://your-sovits-server:9880
SOVITS_API_KEY=your-key  # 可选

# 3. 无需修改任何 Runner 代码
# sovits_tools.py 已注册 @tool(group="sovits")
# _DOMAIN_TOOL_GROUPS["sovits"] = ["sovits"] 已配置
# _do_audio() 已自动检测 SOVITS_BASE_URL 并路由
```

工具清单（已实现占位）：
- `sovits_tts`：文本转语音
- `sovits_clone_voice`：zero-shot 音色克隆
- `sovits_list_models`：列出已部署模型
