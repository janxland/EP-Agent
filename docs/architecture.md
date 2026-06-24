# ABC-Agent 架构文档

> 最后更新：2026-06-23 | 当前版本：Python + FastAPI + Tool-Calling Agent

---

## 一、项目定位

ABC-Agent 是面向 **Sky: Children of the Light** 玩家的音乐谱子智能编辑平台。

```
Sky JSON 谱  →  ABC Notation  →  AI Agent 编辑  →  ABC / MIDI / Sky JSON
```

核心价值：
- 用自然语言描述修改意图（"转 G 大调"、"加快 20%"、"改成爵士风格"）
- Agent 自主决定调用哪些工具、按什么顺序执行
- 支持多种场景出口：编辑器实时预览、C端小程序直取 JSON、DAW 导入 MIDI

---

## 二、整体架构

```
┌──────────────────────────────────────────────────────────────┐
│                    调用方（Caller）                            │
│                                                              │
│   编辑器前端（Next.js）      C端小程序       未来 DAW 插件     │
│   scene=editor              scene=player    scene=daw        │
└────────────┬─────────────────────┬──────────────┬───────────┘
             │                     │              │
             └─────────────────────▼──────────────┘
                         HTTP + SSE
┌──────────────────────────────────────────────────────────────┐
│                  后端（Python + FastAPI）                      │
│                                                              │
│  ┌─────────────────────────────────────────────────────┐    │
│  │              pipeline 层（业务用例）                  │    │
│  │   convert()   edit(scene)   export_score()          │    │
│  └──────────────────────┬──────────────────────────────┘    │
│                         │                                    │
│  ┌──────────────────────▼──────────────────────────────┐    │
│  │           agentcore（Tool-Calling Agent）             │    │
│  │                                                      │    │
│  │   LLM ←→ Tool Registry                              │    │
│  │   ┌─────────────────────────────────────────────┐   │    │
│  │   │  工具注册表（@tool 装饰器自动注册）            │   │    │
│  │   │  transpose_abc  change_tempo  change_style   │   │    │
│  │   │  analyze_abc    get_abc_header add_ornament  │   │    │
│  │   │  abc_to_sky_json  abc_to_midi_b64            │   │    │
│  │   └─────────────────────────────────────────────┘   │    │
│  │                         │                            │    │
│  │              OutputAdapter（场景路由）                │    │
│  └──────────────────────┬──────────────────────────────┘    │
│                         │                                    │
│  ┌──────────────────────▼──────────────────────────────┐    │
│  │              sky-music-tools（直接 import）           │    │
│  │   parser  abc_writer  abc_to_json  midi_writer       │    │
│  └─────────────────────────────────────────────────────┘    │
│                                                              │
│  SSE Hub（asyncio.Queue，每个 session 一个队列）              │
└──────────────────────────────────────────────────────────────┘
```

---

## 三、技术栈

| 层 | 技术 | 说明 |
|----|------|------|
| 前端 | Next.js 15 + TypeScript | App Router，分层架构 |
| UI | Tailwind CSS + abcjs | abcjs 浏览器端实时渲染乐谱 |
| 状态 | Zustand | handleSSEEvent 统一处理所有事件 |
| 后端 | Python 3.11 + FastAPI | uvicorn 启动，CORS 中间件 |
| Agent | OpenAI SDK（Tool Calling） | complete_with_tools() 驱动 loop |
| LLM | deepseek-ai/DeepSeek-V3 | 硅基流动，OpenAI 兼容接口 |
| 工具层 | 纯 Python 函数 + @tool 装饰器 | 算法工具 + LLM 工具分离 |
| 通信 | SSE（Server-Sent Events） | asyncio.Queue 实现 Hub |
| 谱子工具 | sky-music-tools（直接 import） | 无 subprocess，零开销 |

---

## 四、目录结构

```
ABC-Agent/
├── docs/
│   └── architecture.md          # 本文档
│
├── frontend/                    # Next.js 前端
│   └── src/
│       ├── app/
│       │   ├── (workspace)/page.tsx   # 主工作台，三栏布局
│       │   ├── layout.tsx
│       │   └── providers.tsx
│       ├── entities/
│       │   └── session/store.ts       # Zustand，handleSSEEvent()
│       ├── widgets/
│       │   ├── upload-panel/          # 拖拽上传 + Session 创建
│       │   ├── abc-editor/            # abcjs 渲染 + 意图输入
│       │   ├── pipeline-status/       # 工具调用卡片 + 日志
│       │   └── export-panel/          # 三格式导出
│       └── shared/
│           ├── lib/api.ts             # API 层，subscribeToSession SSE
│           └── types/index.ts         # Score/Session/SSEEvent/ToolCallRecord
│
└── backend/                     # Python 后端
    ├── main.py                  # FastAPI 装配 + uvicorn 入口
    ├── requirements.txt
    └── app/
        ├── config.py            # 环境变量（LLM_API_KEY/BASE_URL/MODEL）
        ├── pipeline/
        │   ├── domain.py        # Score / Session / ScoreMeta / ABCVersion
        │   ├── service.py       # 用例：convert / edit(scene) / export_score
        │   └── router.py        # FastAPI 路由 + SSE Hub（asyncio.Queue）
        └── agentcore/
            ├── llm.py           # complete / complete_stream / complete_with_tools
            ├── edit_runner.py   # Tool-Calling Agent Loop + OutputAdapter
            └── tools/
                ├── __init__.py  # @tool 装饰器 + registry + call_tool()
                ├── abc_tools.py # 8 个工具（见下方工具清单）
                └── export_tools.py  # abc_to_sky_json / abc_to_midi_b64
```

---

## 五、核心流程

### 5.1 JSON → ABC 转换

```
用户上传 Sky JSON 文件
    ↓
POST /api/sessions/:id/convert
    ↓
service.convert()
  → sys.path.insert(sky-music-tools)
  → parse_game_score(tmp_file)    # 直接 Python import
  → to_abc_notation(score_obj)
    ↓
SSE 推送 pipeline.step(convert, succeeded)
SSE 推送 abc.updated { abc, version:1 }
    ↓
前端 store.handleSSEEvent → updateABC → abcjs 渲染
```

### 5.2 Tool-Calling Agent 编辑流程

```
用户输入意图 + scene 参数
    ↓
POST /api/sessions/:id/edit  { intent, scene }
    ↓
service.edit()
    ↓
ToolCallAgentRunner.run()
    │
    ├─ messages = [system_prompt, user(intent + abc + meta)]
    │
    ├─ Loop（最多 6 轮）:
    │    ↓
    │    complete_with_tools(messages, tool_schemas)
    │    ↓
    │    finish_reason == "stop" → 输出摘要，退出
    │    finish_reason == "tool_calls" →
    │        for each tool_call:
    │            SSE 推送 tool.call(running)
    │            call_tool(name, args)       ← 实际执行工具
    │            SSE 推送 tool.call(succeeded/failed)
    │            messages.append(tool_result)
    │
    └─ OutputAdapter（按 scene 追加）:
         scene=player → call_tool("abc_to_sky_json", abc)
         scene=daw    → call_tool("abc_to_midi_b64", abc)
         scene=raw    → 两个都追加
    ↓
SSE 推送 abc.updated { abc, version, summary }
    ↓
返回 { abc_notation, tool_calls[], summary, sky_json?, midi_b64? }
```

### 5.3 导出

```
POST /api/sessions/:id/export  { format, instrument }
    ↓
service.export_score()
  format=abc  → 直接返回 UTF-8 文本
  format=midi → abc_to_cuby_json → parse_game_score → to_midi → bytes
  format=json → abc_to_cuby_json → JSON bytes
    ↓
Response(content, Content-Disposition: attachment)
```

---

## 六、工具注册表

工具通过 `@tool` 装饰器自动注册，LLM 调用时自动生成 schema。

### 确定性工具（纯算法，不消耗 LLM token）

| 工具名 | 功能 | 实现方式 |
|--------|------|----------|
| `transpose_abc` | 精确转调（半音级别） | 音符映射表算法 |
| `change_tempo` | 修改 BPM（Q: 字段） | 正则替换 |
| `analyze_abc` | 分析调号/速度/音符数 | 行扫描解析 |
| `get_abc_header` | 提取所有 Header 字段 | 正则匹配 |

### LLM 驱动工具（今天通用模型，未来可换垂直模型）

| 工具名 | 功能 | 扩展点 |
|--------|------|--------|
| `change_style` | 风格转换（爵士/中国风/古典） | `# TODO: VERTICAL_MODEL` |
| `add_ornament` | 添加装饰音（颤音/波音/倚音） | `# TODO: VERTICAL_MODEL` |

### 导出工具（OutputAdapter 调用）

| 工具名 | 功能 | 输出 |
|--------|------|------|
| `abc_to_sky_json` | ABC → Sky/CUBY JSON | JSON 字符串 |
| `abc_to_midi_b64` | ABC → MIDI base64 | base64 字符串 |

### 添加新工具（只需两步）

```python
# abc_tools.py 中添加：
@tool
def my_new_tool(abc: str, param: str) -> str:
    """工具描述（LLM 会读这段话决定何时调用）。
    abc: 完整的 ABC Notation 字符串
    param: 参数说明
    """
    # 实现逻辑
    return new_abc
# 完成！重启后端，LLM 自动感知新工具
```

---

## 七、场景路由（OutputAdapter）

同一个 Agent 编辑流程，通过 `scene` 参数控制输出格式，**Agent 本身不感知场景**。

| scene | 调用方 | 额外输出 | 典型用途 |
|-------|--------|----------|----------|
| `editor` | 编辑器前端 | 无 | 实时预览 ABC 乐谱 |
| `player` | C端小程序 | `sky_json` | 直接导入键盘演奏 |
| `daw` | DAW 插件 | `midi_b64` | 导入 DAW 编辑 |
| `raw` | 批处理/API | `sky_json` + `midi_b64` | 一次获取所有格式 |

请求示例：
```json
POST /api/sessions/:id/edit
{ "intent": "转成 G 大调", "scene": "player" }

// 响应额外包含：
{ "sky_json": "[{\"songNotes\": [...]}]" }
```

---

## 八、SSE 消息协议

所有实时事件通过 `GET /api/sessions/:id/stream` 推送。

```json
{
  "id": "evt_a1b2c3d4",
  "type": "tool.call",
  "session_id": "sess_xxxxxxxx",
  "display": true,
  "sequence": 0,
  "timestamp": "2026-06-23T06:00:00Z",
  "payload": {
    "tool": "transpose_abc",
    "arguments": { "semitones": 7 },
    "status": "succeeded",
    "result_preview": "X:1\nT:斗地主..."
  }
}
```

| 事件类型 | 触发时机 | 前端处理 |
|----------|----------|----------|
| `connected` | SSE 连接建立 | 忽略 |
| `pipeline.step` | 每个 Pipeline 阶段变化 | appendLog(step) |
| `tool.call` | 每次工具调用开始/结束 | 更新 ToolCallCard |
| `abc.updated` | ABC 谱更新 | updateABC → abcjs 重渲染 |
| `message.delta` | LLM 流式输出 | appendStreamDelta |
| `message.completed` | 流式输出结束 | commitStreamMessage |
| `error` | 任意异常 | appendLog(error) |

---

## 九、换垂直模型的路径

当专业音乐模型上线后，只需替换工具内部实现，**Agent 编排层零改动**：

```python
# 当前（通用 LLM）：
@tool
async def change_style(abc: str, style: str) -> str:
    """将 ABC 谱转换为指定音乐风格"""
    # TODO: VERTICAL_MODEL
    from app.agentcore.llm import complete
    result = await complete([...prompt...])
    return result

# 未来（垂直模型）：
@tool
async def change_style(abc: str, style: str) -> str:
    """将 ABC 谱转换为指定音乐风格"""
    response = await vertical_music_model.transform(
        abc=abc, target_style=style
    )
    return response.abc
```

替换范围：`abc_tools.py` 中对应函数的函数体，其他文件不动。

---

## 十、API 接口清单

| 方法 | 路径 | 参数 | 说明 |
|------|------|------|------|
| GET | `/healthz` | — | 健康检查 |
| POST | `/api/sessions` | — | 创建 Session，返回 session_id |
| GET | `/api/sessions/:id` | — | 查询 Session 状态 |
| GET | `/api/sessions/:id/stream` | — | SSE 事件流（长连接） |
| POST | `/api/sessions/:id/convert` | `json_content, file_name` | Sky JSON → ABC |
| POST | `/api/sessions/:id/edit` | `intent, scene` | Tool-Calling Agent 编辑 |
| POST | `/api/sessions/:id/export` | `format, instrument` | 导出文件 |
| POST | `/api/audio/suno` | `prompt, style, lyrics...` | Suno AI 直接生成 |
| POST | `/api/audio/minimax` | `prompt, lyrics...` | MiniMax 直接生成 |
| POST | `/api/audio/minimax/cover` | `audio_url, prompt` | MiniMax 翻唱 |
| POST | `/api/audio/minimax/lyrics` | `prompt` | MiniMax 生成歌词 |
| POST | `/api/sessions/:id/audio/chat` | `message, provider` | **对话式音频生成（核心）** |
| GET | `/api/sessions/:id/audio/history` | — | 获取音频对话历史 |
| DELETE | `/api/sessions/:id/audio/history` | — | 清空音频对话历史 |

---

## 十一、启动方式

```bash
# 后端
cd ABC-Agent/backend
pip install -r requirements.txt
export LLM_API_KEY="sk-..."
export LLM_BASE_URL="https://api.siliconflow.cn/v1"
export LLM_MODEL="deepseek-ai/DeepSeek-V3"
python3 -m uvicorn main:app --host 0.0.0.0 --port 8082 --reload

# 前端
cd ABC-Agent/frontend
npm install && npm run dev
# → http://localhost:3001
```

---

## 十二、对话式音频生成架构

> 参考 Google MusicLM / Udio / Suno 的对话迭代模式，通过自然语言逐步改进生成的音乐。

```
用户消息
  ↓
AudioChatRunner._route()     # Router LLM：识别意图域
  ├─ audio_generate           # 首次生成（无历史 or 明确重新生成）
  ├─ audio_iterate            # 迭代改进（有历史 + "再X一点"等）
  └─ audio_cover              # 翻唱（提供音频 URL）
  ↓
工具发现（按意图加载 audio 分组工具）
  ├─ abc_to_audio_prompt      # 提取谱子特征 → prompt
  ├─ evolve_audio_prompt      # 规则匹配(13类) + LLM兜底 → 进化参数
  ├─ diff_audio_params        # 对比两次参数 → diff 说明
  ├─ generate_audio_minimax   # MiniMax 生成
  ├─ generate_audio_suno      # Suno 生成
  └─ generate_cover_minimax   # MiniMax 翻唱
  ↓
AudioSession 追加到 Session.audio_history
  → 返回 audio_url + diff_summary + suggestions
```

**与大厂的对比：**

| 平台 | 迭代方式 | 本项目 |
|------|----------|--------|
| Suno Web | 每次全新生成，无历史关联 | AudioSession 保存历史 |
| Udio | Remix 功能（手动调参） | 自然语言 → 自动调参 |
| Google MusicLM | 向量空间插值 | 规则映射（13类）+ LLM 兜底 |

详细说明见 [docs/audio-chat-guide.md](./audio-chat-guide.md)

---

## 十三、扩展方向

| 方向 | 优先级 | 说明 |
|------|--------|------|
| 垂直音乐模型接入 | 高 | 替换 `change_style`/`add_ornament` 工具实现 |
| 转调半音精确度优化 | 高 | 在工具 docstring 中补充调号对照表，引导 LLM 选参 |
| Redis Pub/Sub | 中 | 替换内存 SSE Hub，支持多 worker 部署 |
| PostgreSQL 持久化 | 中 | 替换内存 `_sessions` dict，支持历史版本管理 |
| 多步回滚 | 中 | 利用 `Score.history[]` 已有版本链，加 undo API |
| 向量长期记忆 | 中 | 跨 Session 记住用户风格偏好（pgvector） |
| 哼唱转谱入口 | 低 | 新增 `hum_to_abc` 工具，接入 AMT 模型 |
| 批量转换 | 低 | 并发 Session，支持多首曲子批处理 |
