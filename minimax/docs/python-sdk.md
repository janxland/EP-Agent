# MiniMax Python SDK 实现说明

代码位置：`minimax/python-sdk/`

本 SDK 是纯客户端库，不包含也不会启动 HTTP/FastAPI/Flask/Django 服务端。同步调用使用共享 `httpx.Client`，异步调用使用共享 `httpx.AsyncClient`；WebSocket 仅实现客户端连接接口。

## 1. 安全边界

1. API Key 只接受：
   - `Config(api_key="...")`
   - `MiniMax(api_key="...")` / `AsyncMiniMax(api_key="...")`
   - 环境变量 `MINIMAX_API_KEY`
2. SDK 不加载 `.env`，不把 Key 写入任何文件。
3. 默认请求头使用 `Authorization: Bearer <key>`。
4. 每次请求生成 `X-Request-ID`；网关返回 `x-request-id`、`request-id` 或 `trace-id` 时优先采用，JSON 响应通过 `_request_id` 暴露。
5. DEBUG 日志对 Authorization、token、API key、音频、Base64 和文件内容脱敏。
6. 文件内容下载走 MiniMax 官方 `retrieve_content`，默认不把 Bearer Token 发给响应中的第三方下载 URL。

## 2. 配置与区域

```python
from minimax_api import Config, RetryConfig

config = Config(
    api_key="runtime-only-secret",
    region="mainland",
    timeout=60,
    connect_timeout=10,
    retry=RetryConfig(
        max_retries=3,
        initial_delay=0.5,
        max_delay=8,
        backoff_factor=2,
    ),
)
```

| 区域 | Base URL |
|---|---|
| mainland | `https://api.minimaxi.com/v1` |
| global | `https://api.minimax.io/v1` |

可通过 `MINIMAX_BASE_URL` 或 `Config(base_url=...)` 指向已获授权的企业网关。

## 3. 传输层

`minimax_api/transport.py` 负责：

- 同步/异步共享客户端
- Bearer 鉴权与默认 User-Agent
- connect/read/write/pool 超时（由 `httpx.Timeout` 统一表达）
- 网络错误与瞬态 HTTP 状态有限重试
- 指数退避和小幅 jitter
- 数字秒或 HTTP-date 格式 `Retry-After`
- HTTP 错误与 `base_resp.status_code != 0` 统一转换为 `MiniMaxAPIError`
- 请求追踪 ID
- 脱敏诊断日志

默认重试状态：408、409、429、500、502、503、504。生成类 POST 可能存在“服务端已接受、客户端未收到响应”的不确定性，生产环境需要结合 request ID、task ID 和业务幂等策略调整重试。

## 4. 公共类型

- `Config`, `Region`, `RetryConfig`
- `GenerationResult`: 标准化提取 `id`、`task_id`、`file_id`、`trace_id`，并保留 `raw`
- `TTSChunk`: 解码后的音频 bytes、原始帧、状态、trace ID、结束标志
- `FileDownload`: 内容、文件名、来源 URL、保存位置
- `schemas.py`: `TypedDict` 请求/响应辅助类型
- `MiniMaxAPIError`, `MiniMaxTransportError`, `MiniMaxValidationError`

媒体接口的未公开或持续变化字段始终可从 `GenerationResult.raw` 获取。

## 5. Text

### OpenAI-compatible

稳定路径：`POST /v1/chat/completions`。

方法：

- `text.chat_completions(...)`
- `text.chat_completions_stream(...)`

支持 `model`、`messages`、`temperature`、`top_p`、`max_tokens`、`tools`、`tool_choice`、`extra_body`。流式按 SSE 解析，同步返回迭代器，异步返回异步迭代器。

官方较新模型可能推荐 `max_completion_tokens`；可通过 `extra_body={"max_completion_tokens": ...}` 传递。用户要求的 `max_tokens` 仍保留以兼容 OpenAI 风格调用。

### Anthropic-compatible

稳定路径：区域 API 原点 + `/anthropic/v1/messages`，即不是 `/v1/anthropic/...`。

方法：

- `text.anthropic_messages(...)`
- `text.anthropic_messages_stream(...)`

默认附加 `anthropic-version: 2023-06-01`。多轮工具调用和 thinking block 应由调用方原样回传完整 content。

## 6. Speech

| 能力 | 方法 | 官方路径 |
|---|---|---|
| 同步 T2A | `speech.t2a` | `POST /v1/t2a_v2` |
| HTTP 流 TTS | `speech.t2a_stream` | `POST /v1/t2a_v2`, `stream=true` |
| WebSocket TTS | `speech.websocket` | `/ws/v1/t2a_v2` |
| 长文本创建 | `speech.create_long_text` | `POST /v1/t2a_async_v2` |
| 长文本查询 | `speech.query_long_text` | `GET /v1/query/t2a_async_query_v2` |
| 长文本轮询 | `speech.poll_long_text` | 上述查询接口 |
| 长文本下载 | `speech.download_long_text` | `GET /v1/files/retrieve_content` |

`voice_setting`、`audio_setting`、`timestamp` 均以可扩展 dict 传入。`timestamp` 会展开进顶层，适配 `subtitle_enable`、`subtitle_type` 等当前或后续字段。

HTTP 流式解析兼容逐行 JSON 与 `data:` 前缀，音频十六进制字段会转为 bytes。WebSocket 公开文档目前主要保证事件顺序，具体字段持续变化，因此客户端提供 `send_event(dict)`、`receive_event()`、`events()` 原始事件接口。

## 7. Voice

| 能力 | 方法 | 路径/用途 |
|---|---|---|
| 上传克隆样本 | `voice.upload_voice_clone` | `/files/upload`, purpose=`voice_clone` |
| 上传提示音频 | `voice.upload_prompt_audio` | `/files/upload`, purpose=`prompt_audio` |
| 快速复刻 | `voice.voice_clone` | `POST /voice_clone` |
| 音色设计 | `voice.voice_design` | `POST /voice_design` |
| 查询音色 | `voice.list_voices` | `POST /get_voice` |

本地严格校验 voice ID、文件存在性、扩展名、非空和大小。WAV 使用标准库读取时长；MP3/M4A 为保持轻量不做本地解码，时长由官方 API 最终校验。

## 8. Music

| 能力 | 方法 | 官方路径 |
|---|---|---|
| 音乐生成 | `music.music_generation` | `POST /music_generation` |
| 歌词生成/编辑 | `music.lyrics_generation` | `POST /lyrics_generation` |
| 翻唱前处理 | `music.music_cover_preprocess` | `POST /music_cover_preprocess` |
| 音乐翻唱 | `music.music_cover` | `POST /music_generation`, model=`music-cover` |

`lyrics_generation` 要求 `mode` 为 `write_full_song` 或 `edit`。翻唱可直接传 `audio_url`/`audio_base64`，或先预处理得到 `cover_feature_id`；来源字段做互斥校验。

## 9. Image

文生图和图生图都使用官方 `POST /image_generation`：

- `image.text_to_image(model, prompt, ...)`
- `image.image_to_image(model, prompt, subject_reference, ...)`

图生图的主体参考子字段依模型变化，不额外伪造固定 schema，原样透传调用方 dict。

## 10. Video

| 能力 | 方法 | 路径 |
|---|---|---|
| 文生视频 | `video.text_to_video` | `POST /video_generation` |
| 图生视频 | `video.image_to_video` | `POST /video_generation` |
| 查询 | `video.query` | `GET /query/video_generation` |
| 轮询 | `video.poll` | 查询接口循环 |
| Video Agent | `video.video_agent` | `POST /video_template_generation`（官方 deprecated） |
| Agent 查询 | `video.query_agent` | `GET /query/video_template_generation`（official deprecated） |
| 取消 | `video.cancel(path=...)` | 无官方稳定默认路径 |

轮询将 success 视为成功，将 fail/failed/expired/cancelled/canceled 视为失败，状态比较不区分大小写。

### 必须由网关/账号验证

- Video Agent 的模板、路径可用性和配额，因为官方已标记 deprecated。
- 视频任务取消：官方公开文档未给出稳定 cancel 端点。SDK 只提供要求显式 `path` 的通用方法，不声明任何默认路径存在。
- 新模型、回调字段、首尾帧/主体参考等账号级预览字段，应使用 `extra`/kwargs 原样透传。

## 11. Files

| 能力 | 方法 | 官方路径 |
|---|---|---|
| 上传 | `files.upload` | `POST /files/upload` |
| 列表 | `files.list` | `GET /files/list` |
| 元数据 | `files.retrieve` | `GET /files/retrieve` |
| 内容 | `files.retrieve_content` | `GET /files/retrieve_content` |
| 删除 | `files.delete` | `POST /files/delete`，multipart |
| 下载 | `files.download` | retrieve + retrieve_content |

公开用途包括 `voice_clone`、`prompt_audio`、`t2a_async_input`、`video_understanding`；生成文件还可能返回 `t2a_async`、`video_generation` 等 purpose。SDK 不将用途硬编码为封闭枚举，以适配账号变化。

## 12. 扩展策略

**官方接口字段随区域/账号持续变化，未明确字段通过 extra/kwargs 透传。**

示例：

```python
client.video.text_to_video(
    model="MiniMax-Hailuo-2.3",
    prompt="...",
    extra={"preview_account_field": "value"},
)

client.request(
    "POST",
    "/confirmed_account_endpoint",
    json={"raw_field": "value"},
)
```

低层方法仍使用统一鉴权、重试、错误和 request ID。传绝对 URL 时必须确认目标可信，避免把 Bearer 凭据发送到非预期主机。

## 13. 测试

测试目录：`minimax/python-sdk/tests/`。

覆盖：

- 非零 `base_resp` 错误
- HTTP 429 重试和 `Retry-After`
- Bearer 与 request ID
- voice ID、WAV 时长、扩展名与文件存在性
- SSE `[DONE]`、注释与多事件
- TTS hex 音频解码和流式错误
- 异步流式迭代器
- OpenAI/Anthropic 路径和字段透传
- 视频取消显式路径

执行：

```bash
cd minimax/python-sdk
python -m pytest
python -m compileall -q minimax_api tests
```

测试不需要真实 API Key，不访问公网。

## 14. 官方来源

核心核验来源：

1. [API Overview](https://platform.minimaxi.com/docs/api-reference/api-overview)
2. [Models release notes](https://platform.minimaxi.com/docs/release-notes/models)
3. [Voice Cloning](https://platform.minimaxi.com/docs/api-reference/voice-cloning-clone)
4. [OpenAI-compatible Chat](https://platform.minimaxi.com/docs/api-reference/text-chat-openai)
5. [Anthropic-compatible API](https://platform.minimaxi.com/docs/api-reference/text-anthropic-api)
6. [T2A HTTP](https://platform.minimaxi.com/docs/api-reference/speech-t2a-http)
7. [Video Generation](https://platform.minimaxi.com/docs/api-reference/video-generation-t2v)
8. [File Upload](https://platform.minimaxi.com/docs/api-reference/file-management-upload)
9. [Music Generation](https://platform.minimaxi.com/docs/api-reference/music-generation)
10. [Lyrics Generation](https://platform.minimaxi.com/docs/api-reference/lyrics-generation)
11. [Music Cover Preprocess](https://platform.minimaxi.com/docs/api-reference/music-cover-preprocess)

文档核验时间：2026-07-17。生产使用前应再次检查对应区域文档和账号控制台。
