# ABC-Agent

Sky: Children of the Light 谱子智能编辑平台。

上传游戏 JSON 谱 → AI Agent 转换为 ABC 乐谱 → 智能转调/转风格 → 导出 ABC / MIDI / JSON

---

## 技术栈

| 层 | 技术 |
|----|------|
| 前端 | Next.js 15 + TypeScript + Tailwind CSS + abcjs + Zustand |
| 后端 | **Python + FastAPI + uvicorn**（直接 import sky-music-tools） |
| Agent | OpenAI SDK，两阶段 Router + Edit |
| 通信 | SSE（Server-Sent Events）实时推送 Pipeline 进度 |

---

## 项目结构

```
ABC-Agent/
├── docs/
│   └── architecture.md
├── frontend/                 # Next.js 前端
│   └── src/
│       ├── app/              # 路由层（App Router）
│       ├── entities/         # 领域实体层（Session Store）
│       ├── widgets/          # 页面级组件
│       └── shared/           # API 层 / 类型定义
└── backend/                  # Python 后端
    ├── main.py               # FastAPI 入口
    ├── requirements.txt
    └── app/
        ├── config.py         # 环境变量配置
        ├── pipeline/
        │   ├── domain.py     # 领域模型（Score/Session）
        │   ├── service.py    # 核心用例（直接调用 sky-music-tools）
        │   └── router.py     # FastAPI 路由 + SSE Hub
        └── agentcore/
            ├── llm.py        # OpenAI SDK 客户端
            └── edit_runner.py # 两阶段 Agent（Router + Edit）
```

---

## 快速开始

### 1. 后端

```bash
cd ABC-Agent/backend

# 安装依赖（一次性）
pip install -r requirements.txt

# 配置环境变量
export LLM_API_KEY="your-api-key"
export LLM_BASE_URL="https://api.openai.com/v1"   # 可选，默认 OpenAI
export LLM_MODEL="gpt-4o-mini"                     # 可选
export SKILL_DIR="/app/.workspace/.magic/skills/sky-music-tools"  # 默认值

# 启动
python3 -m uvicorn main:app --host 0.0.0.0 --port 8080
# 或开发模式（热重载）
python3 -m uvicorn main:app --host 0.0.0.0 --port 8080 --reload
```

### 2. 前端

```bash
cd ABC-Agent/frontend
npm install
npm run dev
# 访问 http://localhost:3001
```

---

## API 接口

| 方法 | 路径 | 说明 |
|------|------|------|
| GET | `/healthz` | 健康检查 |
| POST | `/api/sessions` | 创建 Session |
| POST | `/api/sessions/:id/convert` | JSON → ABC 转换 |
| POST | `/api/sessions/:id/edit` | 意图驱动 ABC 修改 |
| POST | `/api/sessions/:id/export` | 导出 ABC / MIDI / JSON |
| GET | `/api/sessions/:id/stream` | SSE 事件流 |

---

## 核心流程

### Step 1: JSON → ABC
```
上传 Sky JSON → FastAPI → parse_game_score() + to_abc_notation() → ABC 字符串
（直接 Python import，无 subprocess）
```

### Step 2: 意图识别 + ABC 修改
```
用户输入意图 → RouterAgent（LLM 识别意图类型）→ EditAgent（流式修改 ABC）→ SSE 推送
```

支持意图类型：`transpose`（转调）/ `tempo`（速度）/ `style`（风格）/ `structure`（结构）/ `custom`

### Step 3: 导出
| 格式 | 用途 | 实现 |
|------|------|------|
| `.abc` | 标准乐谱文本 | 直接返回 |
| `.mid` | DAW 导入 | `to_midi()` |
| `.json` | Sky 小程序键盘 | `abc_to_cuby_json()` |

---

## 与 Go 版本对比

| 项目 | Go + Gin | Python + FastAPI |
|------|----------|-----------------|
| sky-music-tools 调用 | subprocess Bridge | 直接 import ✅ |
| SSE | 手写 Hub | asyncio.Queue ✅ |
| LLM 调用 | 手写 HTTP | openai SDK ✅ |
| 代码量 | ~800 行 | ~450 行 ✅ |
| 启动方式 | go build | pip install ✅ |
