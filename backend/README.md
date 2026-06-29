# EP-Agent

> Sky: Children of the Light 谱子智能编辑平台 · AI 音频生成 · 音色克隆

---

## 技术栈

| 层 | 技术 |
|----|------|
| 前端 | Next.js 15 + TypeScript + Tailwind CSS + abcjs + Zustand |
| 后端 | Python + FastAPI + uvicorn |
| Agent | OpenAI SDK，Universal Runner + SubAgent 架构 |
| 通信 | SSE（Server-Sent Events）实时推送 Pipeline 进度 |
| 数据库 | SQLite（会话/消息/TODO 持久化） |

---

## 项目结构

```
EP-Agent/
├── docs/                          # 设计文档
│   ├── expert-system-design.md    # 专家系统设计（权威版）
│   ├── agent-architecture-v3.1.md # Agent 架构 v3.1（当前版本）
│   └── architecture.md            # 整体架构概览
│
├── backend/
│   ├── main.py                    # FastAPI 入口（lifespan 管理）
│   ├── requirements.txt           # Python 依赖
│   ├── agent/agents/              # .agent 配置文件（SubAgent 声明）
│   │   ├── abc-router.agent       # 意图路由 Agent
│   │   ├── abc-edit.agent         # 编辑 Agent
│   │   ├── abc-create.agent       # 创作 Agent
│   │   ├── abc-convert.agent      # 转换 Agent
│   │   ├── abc-audio.agent        # 音频/音色 Agent
│   │   ├── abc-query.agent        # 查询 Agent
│   │   └── h5-designer.agent      # H5 海报设计 Agent
│   ├── sky-music-tools/           # Sky 谱子工具库（内嵌副本）
│   └── app/
│       ├── config.py              # 环境变量配置
│       ├── pipeline/
│       │   ├── domain.py          # 领域模型（Score/Session）
│       │   ├── db.py              # SQLite 持久化层
│       │   ├── service.py         # 核心用例层
│       │   ├── router.py          # FastAPI 路由 + SSE Hub（主路由）
│       │   ├── audio_router.py    # 音频生成直接调用端点
│       │   └── audio_chat_router.py # 对话式音频生成端点
│       └── agentcore/
│           ├── domain_config.py   # 意图域配置中心（单一来源）
│           ├── role_config.py     # 角色配置中心（单一来源）
│           ├── intent_router.py   # 意图路由（LLM + 角色域过滤）
│           ├── todo_manager.py    # TODO 生命周期管理
│           ├── universal_runner.py # 纯编排层（路由→规划→调度→门控）
│           ├── react_executor.py  # 通用 ReAct Loop
│           ├── audio_runner.py    # 对话式音频生成 Runner
│           ├── edit_runner.py     # ABC 编辑 ReAct Runner
│           ├── abc_utils.py       # ABC 工具函数（验证/提取/时长）
│           ├── llm.py             # OpenAI SDK 客户端
│           ├── agent_loader.py    # .agent 文件热加载
│           ├── session_context.py # Session 上下文辅助
│           ├── agents/            # SubAgent 实现
│           │   ├── convert_agent.py
│           │   ├── edit_agent.py
│           │   ├── create_agent.py
│           │   ├── audio_agent.py
│           │   ├── query_agent.py
│           │   └── h5_agent.py    # H5 海报生成 SubAgent
│           └── tools/             # 工具注册表（@tool 自动扫描）
│               ├── __init__.py    # @tool 装饰器 + 注册表
│               ├── abc_tools.py   # ABC 编辑工具（group=abc_edit）
│               ├── audio_tools.py # 音频生成工具（group=audio）
│               ├── audio_evolve_tools.py # Prompt 进化工具
│               ├── voice_clone_tools.py  # 音色克隆工具（MiniMax）
│               ├── sovits_tools.py       # GPT-SoVITS 工具（可选）
│               ├── export_tools.py       # 导出工具
│               └── h5_tools.py           # H5 海报工具（group=h5）
│
└── frontend/
    └── src/
        ├── app/
        │   ├── layout.tsx         # 根布局（Noto Sans SC + Inter 双字体）
        │   └── (workspace)/
        │       ├── page.tsx       # 模式选择入口（小白/专业）
        │       ├── simple/        # 小白模式（三栏布局）
        │       └── pro/
        │           ├── page.tsx           # 专业模式守卫页（session 恢复/创建）
        │           └── [sessionId]/       # 专业模式主页面
        ├── entities/
        │   └── session/store.ts   # Score/Session 状态（SSE 驱动）
        ├── features/
        │   ├── chat/
        │   │   ├── store/chat.store.ts    # 对话状态（角色/消息/TODO/工具调用）
        │   │   └── types/chat.types.ts    # 消息类型定义
        │   └── workspace/
        │       └── store/workspace.store.ts # 工作区/Session 管理
        ├── shared/
        │   ├── constants/index.ts # 全局常量（布局/API/音频）
        │   ├── types/index.ts     # 共享类型（SSE/Score/Audio/Role）
        │   ├── lib/
        │   │   ├── api.ts         # REST API 服务层
        │   │   ├── sse-alignment.ts # SSE 对齐检查 + 序号守卫
        │   │   ├── tool-registry.ts # 工具注册表客户端
        │   │   └── utils.ts       # 通用工具函数
        │   └── hooks/
        │       └── useBackendHealth.ts # 后端健康检查 Hook
        └── widgets/
            ├── abc-editor/        # ABC 乐谱渲染组件
            ├── audio-panel/       # 音频生成/音色克隆面板
            ├── chat-panel/        # 对话面板（含工具调用卡片）
            ├── export-panel/      # 导出面板
            ├── pipeline-status/   # Pipeline 执行日志
            ├── role-switcher/     # 角色切换组件
            ├── upload-panel/      # 文件上传面板
            └── workspace-sidebar/ # 工作区侧边栏
```

---

## 快速开始

### 1. 后端

```bash
cd EP-Agent/backend

# 安装依赖
pip install -r requirements.txt

# 配置环境变量（复制模板后编辑）
cp .env.example .env

# 必填
export LLM_API_KEY="your-openai-or-compatible-api-key"

# 可选（音频生成）
export MINIMAX_API_KEY="your-minimax-key"
export SUNO_API_KEY="your-suno-key-via-ttapi"

# 可选（GPT-SoVITS 自部署）
export SOVITS_BASE_URL="http://your-sovits-server:9880"

# 启动
python -m uvicorn main:app --host 0.0.0.0 --port 8080 --reload
```

### 2. 前端

```bash
cd EP-Agent/frontend
npm install
npm run dev
# 访问 http://localhost:3000
```

---

## API 接口

### 核心会话

| 方法 | 路径 | 说明 |
|------|------|------|
| GET | `/healthz` | 健康检查 |
| GET | `/api/health` | 详细健康检查（含工具数/域数） |
| GET | `/api/health/tools` | 工具注册表（前端动态发现） |
| GET | `/api/health/domains` | 意图域配置（前端动态发现） |
| GET | `/api/workspaces` | 工作区列表 |
| POST | `/api/workspaces` | 创建工作区 |
| POST | `/api/sessions` | 创建 Session |
| GET | `/api/sessions/:id/stream` | SSE 事件流（含 replay） |
| POST | `/api/sessions/:id/chat` | 对话（Universal Runner） |
| GET | `/api/sessions/:id/messages` | 历史消息 |
| GET | `/api/sessions/:id/todos` | 历史 TODO |
| GET | `/api/roles` | 角色列表 |
| POST | `/api/sessions/:id/role` | 切换角色 |

### 音频生成

| 方法 | 路径 | 说明 |
|------|------|------|
| POST | `/api/sessions/:id/audio/chat` | 对话式音频生成/迭代 |
| GET | `/api/sessions/:id/audio/history` | 音频对话历史 |
| POST | `/api/audio/minimax` | MiniMax 直接生成 |
| POST | `/api/audio/suno` | Suno AI 直接生成 |
| POST | `/api/audio/voice/upload-sample` | 上传音色样本 |
| POST | `/api/audio/voice/clone` | 克隆音色 |
| POST | `/api/audio/voice/list` | 查询音色列表 |
| POST | `/api/audio/voice/synthesize` | TTS 合成 |

---

## 角色系统

| 角色 | ID | 意图域 | 状态 |
|------|----|--------|------|
| 🎵 Sky 乐谱专家 | `abc_expert` | convert/edit/create/query | ✅ |
| 🎧 音乐生成专家 | `music_producer` | audio/voice/create | ✅ |
| 🎤 音色克隆专家 | `voice_cloner` | voice/sovits | ✅ |
| 🎨 H5 设计专家 | `h5_designer` | h5_create/h5_edit/create | ✅ |
| 📊 PPT 专家 | `ppt_expert` | ppt_create/ppt_edit | 🔒 预留 |

---

## SSE 事件契约

前端 `chat.store.ts` 处理以下事件类型：

| 事件类型 | 说明 |
|----------|------|
| `connected` | SSE 连接建立 |
| `pipeline.step` | Pipeline 步骤状态更新 |
| `abc.updated` | ABC 谱子更新（含 replay） |
| `message.delta` | 流式文本增量 |
| `message.completed` | AI 回复完成 |
| `message.history` | 历史消息 replay |
| `tool.call` | 工具调用状态 |
| `todo.list` | TODO 列表（含 replay） |
| `todo.update` | TODO 单项状态更新 |
| `todo.append` | 追加新 TODO |
| `role.active` | 角色激活（切换/刷新恢复/降级补推） |
| `h5.ready` | H5 海报生成完毕（含 url_path/file_path/size_kb） |
| `error` | 错误事件 |

---

## 环境变量

| 变量 | 必填 | 说明 |
|------|------|------|
| `LLM_API_KEY` | ✅ | LLM API Key（OpenAI 兼容） |
| `LLM_BASE_URL` | — | LLM Base URL，默认 OpenAI |
| `LLM_MODEL` | — | 模型名，默认 gpt-4o-mini |
| `MINIMAX_API_KEY` | — | MiniMax 音频/音色 API Key |
| `SUNO_API_KEY` | — | Suno AI Key（通过 TTAPI） |
| `SOVITS_BASE_URL` | — | GPT-SoVITS 服务地址（自部署） |
| `APP_ADDR` | — | 监听地址，默认 0.0.0.0:8080 |
| `SESSION_TTL_SECONDS` | — | Session 内存 TTL，默认 7200s |
| `H5_OUTPUT_DIR` | — | H5 文件输出目录，默认 `/tmp/ep_agent_h5` |

---

## H5 编辑专家

### 支持的输入格式

| 格式 | 扩展名 | 解析工具 |
|------|--------|----------|
| MIDI | `.mid` `.midi` | `parse_midi_to_json` |
| ABC Notation | `.abc` `.txt` | `generate_h5_from_abc`（一步直达） |
| Sky JSON | `.json` `.txt` | `parse_sky_json_to_json` |
| 纯描述 | 无附件 | `generate_h5_poster`（仅元数据） |

### 可用模板

| 模板 ID | 名称 | 风格 |
|---------|------|------|
| `apple_dark` | 苹果暗色 | 深色毛玻璃 + 红色强调（默认） |
| `apple_light` | 苹果亮色 | 白色磨砂 + 蓝色强调 |
| `neon` | 霓虹电子 | 深黑背景 + 青色/粉色霓虹 |
| `minimal` | 极简白 | 纯白背景 + 深色文字 |

### H5 功能特性

- **封面层**：全屏沉浸式展示，旋转唱片动画，波形可视化
- **下拉交互**：手势/滚轮触发，苹果 Music 同款下拉切换体验
- **Web Audio 播放**：浏览器原生合成，无需服务器，点击即播
- **abcjs 渲染**：ABC 格式自动渲染乐谱图像
- **音符瀑布**：Canvas 绘制音符时间轴可视化
- **原生分享**：调用系统分享面板（iOS/Android），降级复制链接
- **单文件输出**：完整 HTML，无外部依赖，直接分享

### 工具链（h5 工具组）

```
parse_midi_to_json      MIDI base64 → JSON 音符数据
parse_abc_to_json       ABC Notation → JSON 音符数据
parse_sky_json_to_json  Sky JSON → JSON 音符数据
generate_h5_poster      乐谱数据 + 样式 → 完整 H5 HTML
generate_h5_from_abc    ABC 字符串直接生成 H5（快捷入口）
save_h5_file            保存 HTML 文件，返回访问路径
list_h5_templates       列出所有可用模板
```

### 扩展模板

未来新增模板只需：
1. 在 `h5_tools.py` 的 `_TEMPLATES` 字典中添加新模板配置
2. 更新 `list_h5_templates` 的返回列表
3. 无需修改任何其他文件
