# MiniMax 全模型控制台（Frontend）

独立 Next.js App Router + TypeScript 前端，用于通过**你自己的安全 API 网关**访问 MiniMax 文本、语音、音色克隆、音乐、图像、视频、文件与异步任务能力。

> 本项目没有后端服务，不会在浏览器中接收或保存 MiniMax API Key，也不会直连 MiniMax 官方 API。

## 启动

```bash
cd /app/.workspace/minimax/frontend
npm install
npm run dev
```

打开 `http://localhost:3000`。

类型检查：

```bash
npm run type-check
```

## 环境变量

复制 `.env.example` 为 `.env.local`，只配置网关的公开基础地址：

```bash
NEXT_PUBLIC_MINIMAX_GATEWAY_URL=https://your-gateway.example.com
```

也可在界面的“网关设置”中保存地址。该值属于非敏感偏好，会写入浏览器 `localStorage`。**禁止**把 MiniMax API Key 放入任何 `NEXT_PUBLIC_*` 变量，因为这些变量会进入浏览器 bundle。

## API Key 安全边界

- MiniMax API Key 只应保存在你控制的服务端网关中。
- 网关负责认证、鉴权、限流、审计、参数校验、文件安全检查与上游错误脱敏。
- 浏览器只向网关发送业务参数；本项目没有 API Key 输入框。
- 网关应限制 CORS 来源，并为用户会话提供独立认证，不应成为公开匿名代理。
- 网关日志不得记录完整 API Key、敏感音频原文或不必要的个人数据。

## 网关契约

完整契约见：

- `../docs/gateway-contract.md`
- `../docs/frontend.md`

前端期望以下路由：

| 能力 | 方法与路径 |
|---|---|
| Text 流式聊天 | `POST /v1/text/chat`（SSE） |
| Speech TTS | `POST /v1/speech/synthesize` |
| Voice Clone | `POST /v1/voice-clones` |
| Image | `POST /v1/images/generations` |
| Video | `POST /v1/videos/generations` |
| Music | `POST /v1/music/generations` |
| Files | `GET/POST /v1/files`，`DELETE /v1/files/:id` |
| Jobs | `GET /v1/jobs`，`GET /v1/jobs/:id`，`POST /v1/jobs/:id/cancel` |

响应可以是同步资源，也可以是 Job；前端不会将缺少的结果伪造成成功。

## SSE 约定

`POST /v1/text/chat` 应返回 `Content-Type: text/event-stream`。事件示例：

```text
data: {"delta":"你好"}

data: {"delta":"，世界"}

data: [DONE]

```

解析器支持多行 `data:`、注释心跳、`[DONE]`、CRLF 与末尾残留 buffer。

## 官方文档

- MiniMax 开放平台（中国大陆）：https://platform.minimaxi.com/
- MiniMax API 接口概览：https://platform.minimaxi.com/docs/api-reference/api-overview
- MiniMax API Docs（International）：https://platform.minimax.io/docs/api-reference/api-overview
- MiniMax 官方 GitHub：https://github.com/MiniMax-AI

请以官方文档的当前模型名、参数限制、计费和内容安全要求为准；网关负责将本控制台的稳定契约映射到官方接口。

## 项目结构

```text
src/app/                       页面与全局样式
src/components/console/        四区 IDE 工作台外壳
src/components/ui/             表单、状态与结果组件
src/features/                  Text/Speech/Voice/Media/Files/Jobs
src/shared/api/client.ts       JSON/FormData/SSE 统一客户端
src/shared/api/domains/        领域 API 适配器
src/shared/sse/parser.ts       SSE 流解析器
src/state/console-store.ts     Zustand UI 与运行态
```
