# MiniMax 控制台网关契约

本文定义 `minimax/frontend` 与外部安全网关之间的建议契约。网关不在本仓库前端范围内，需由部署方独立实现。

## 1. 通用规则

- 基础地址由 `NEXT_PUBLIC_MINIMAX_GATEWAY_URL` 或 UI 设置提供。
- 浏览器不得携带 MiniMax API Key；网关在服务端注入上游凭据。
- 推荐使用网关自己的 HttpOnly Session Cookie 或短期用户令牌认证。
- JSON 请求：`Content-Type: application/json`。
- 文件上传：`multipart/form-data`，字段为 `file` 与 `purpose`。
- JSON 错误建议：

```json
{
  "code": "INVALID_ARGUMENT",
  "message": "适合展示给用户的错误说明",
  "requestId": "req_123",
  "details": {}
}
```

- 推荐在响应头返回 `x-request-id`。
- 网关应执行超时、重试边界、速率限制、配额、审计、CORS 白名单和内容安全检查。

## 2. Text 流式聊天

### `POST /v1/text/chat`

请求：

```json
{
  "model": "MiniMax-M2.5",
  "messages": [{ "role": "user", "content": "你好" }],
  "temperature": 0.7,
  "topP": 0.95
}
```

响应：`text/event-stream`。前端识别 `delta`、`content` 或 `text` 字段：

```text
: heartbeat

data: {"delta":"你好"}

data: {"delta":"，很高兴见到你"}

data: [DONE]

```

## 3. Speech

### `POST /v1/speech/synthesize`

请求可包含 `model`、`text`、`voiceId`、`speed`、`volume`、`pitch`、`format`、`languageBoost`。

同步响应示例：

```json
{ "audioUrl": "https://signed.example/audio.mp3", "durationMs": 3200 }
```

异步响应也可返回标准 Job。

## 4. Voice Clone

### `POST /v1/voice-clones`

```json
{
  "fileId": "file_123",
  "voiceId": "brand_voice_01",
  "name": "品牌音色",
  "language": "zh-CN",
  "promptAudioFileId": "file_optional",
  "promptText": "可选校准文本"
}
```

返回 `{ "voiceId": "brand_voice_01", "status": "ready" }` 或标准 Job。

### `DELETE /v1/voice-clones/:voiceId`

删除网关或上游音色资源。前端领域接口已预留，当前 UI 未暴露删除按钮。

## 5. Image / Video / Music

- `POST /v1/images/generations`
- `POST /v1/videos/generations`
- `POST /v1/music/generations`

推荐统一返回：

```json
{
  "id": "job_123",
  "capability": "video",
  "status": "queued",
  "createdAt": "2026-07-17T10:00:00Z"
}
```

或者最小 `{ "jobId": "job_123" }`。完成结果通过 Jobs 获取，输出结构由网关定义并原样显示。

## 6. Files

### `POST /v1/files`

Multipart 字段：

- `file`: 二进制文件
- `purpose`: `general`、`voice_clone`、`video_reference` 等

返回：

```json
{
  "id": "file_123",
  "filename": "sample.wav",
  "purpose": "voice_clone",
  "bytes": 102400,
  "createdAt": "2026-07-17T10:00:00Z"
}
```

### `GET /v1/files?cursor=...`

```json
{ "items": [], "nextCursor": null }
```

### `DELETE /v1/files/:id`

成功可返回 `204 No Content`。

## 7. Jobs

### `GET /v1/jobs?status=running&cursor=...`

```json
{ "items": [], "nextCursor": null }
```

### `GET /v1/jobs/:id`

标准 Job：

```json
{
  "id": "job_123",
  "capability": "image",
  "status": "succeeded",
  "createdAt": "2026-07-17T10:00:00Z",
  "updatedAt": "2026-07-17T10:00:10Z",
  "progress": 100,
  "output": { "url": "https://signed.example/result.png" }
}
```

状态枚举：`queued | running | succeeded | failed | cancelled`。

### `POST /v1/jobs/:id/cancel`

返回取消后的标准 Job。网关应保证幂等。

## 8. 安全与生产建议

1. 对文件类型、MIME、魔数、大小与时长做服务端校验，并运行恶意文件扫描。
2. 使用短时签名 URL 返回媒体，不暴露永久对象存储地址。
3. 将上游 MiniMax 错误映射为稳定错误码，避免泄露认证头或内部堆栈。
4. 为 Voice Clone 增加声音授权确认、风控和可追溯审计。
5. 对生成请求增加租户、用户、预算和并发限制。
6. SSE 中断时关闭上游连接，避免继续计费。
