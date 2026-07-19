# MiniMax Python SDK

一个轻量、可复用、同步/异步并行设计的 MiniMax API Python SDK。它只提供客户端能力，不创建或启动任何 HTTP、FastAPI、Flask、Django 或 WebSocket 服务端。

> 状态：`0.1.0`，面向 MiniMax 当前公开 API。官方接口字段会随区域、模型和账号能力持续变化；未明确或新增字段请通过 `extra`、`extra_body`、`**kwargs` 或低层 `request()` 透传。

## 特性

- `httpx.Client` / `httpx.AsyncClient` 共享连接池
- 中国大陆与全球区域：
  - `mainland`: `https://api.minimaxi.com/v1`
  - `global`: `https://api.minimax.io/v1`
- Bearer 鉴权；API Key 只从显式 `Config`/客户端参数或 `MINIMAX_API_KEY` 读取
- 可配置超时、有限指数退避、`Retry-After`、瞬态 HTTP 状态重试
- 统一解析 MiniMax `base_resp` 和 HTTP 错误
- 每次请求自动附加 `X-Request-ID`，响应保留 `_request_id`
- 调试日志会脱敏 Authorization、token、音频/Base64 和文件内容
- OpenAI-compatible 与 Anthropic-compatible 文本普通/流式调用
- T2A JSON、HTTP 流、WebSocket 客户端、异步长文本任务
- voice、music、image、video、files 资源模块
- 轻量 `dataclass` / `TypedDict` 类型，不依赖 Pydantic
- `httpx.MockTransport` 单元测试，不需要真实 API Key

## 安装

Python 3.10+：

```bash
pip install -e .
```

开发测试依赖：

```bash
pip install -e '.[test]'
```

WebSocket TTS 可选依赖：

```bash
pip install -e '.[websocket]'
```

SDK **不会读取或写入 `.env` 文件**。`.env.example` 只展示建议的环境变量名，请用 shell、CI secret 或密钥管理系统设置：

```bash
export MINIMAX_API_KEY='your-key'
export MINIMAX_REGION='mainland'
```

也可以显式配置，密钥不会被持久化：

```python
from minimax_api import Config, MiniMax

config = Config(api_key="runtime-secret", region="global")
with MiniMax(config) as client:
    result = client.text.chat_completions(
        model="MiniMax-M3",
        messages=[{"role": "user", "content": "Hello"}],
    )
```

## 异步文本与流式返回

```python
import asyncio
from minimax_api import AsyncMiniMax

async def main() -> None:
    async with AsyncMiniMax() as client:
        async for chunk in client.text.chat_completions_stream(
            model="MiniMax-M3",
            messages=[{"role": "user", "content": "Explain async iterators."}],
            temperature=1.0,
            top_p=0.95,
            max_tokens=2048,
            tools=[{
                "type": "function",
                "function": {
                    "name": "lookup",
                    "description": "Look up a record",
                    "parameters": {"type": "object", "properties": {}},
                },
            }],
            tool_choice="auto",
            extra_body={"thinking": {"type": "adaptive"}},
        ):
            print(chunk)

asyncio.run(main())
```

普通 OpenAI-compatible 方法：`chat_completions()`。流式方法返回同步迭代器或异步迭代器，不会把整个响应预读到内存。

Anthropic-compatible：

```python
response = client.text.anthropic_messages(
    model="MiniMax-M3",
    max_tokens=2048,
    system="You are helpful.",
    messages=[{"role": "user", "content": "Hello"}],
    extra_body={"thinking": {"type": "adaptive"}},
)
```

对应流式方法为 `anthropic_messages_stream()`，使用官方区域原点下的 `/anthropic/v1/messages`。

## Speech

同步 T2A JSON：

```python
result = client.speech.t2a(
    model="speech-2.8-hd",
    text="你好，MiniMax。",
    voice_setting={"voice_id": "Chinese (Mandarin)_News_Anchor", "speed": 1},
    audio_setting={"sample_rate": 32000, "bitrate": 128000, "format": "mp3"},
    timestamp={"subtitle_enable": True, "subtitle_type": "sentence"},
    output_format="hex",
)
```

HTTP 流式 TTS：

```python
with open("speech.mp3", "wb") as output:
    for chunk in client.speech.t2a_stream(
        model="speech-2.8-turbo",
        text="Streaming speech",
        voice_setting={"voice_id": "English_expressive_narrator"},
        audio_setting={"format": "mp3"},
    ):
        output.write(chunk.audio)
```

长文本异步 T2A：

```python
created = client.speech.create_long_text(
    model="speech-2.8-hd",
    text="very long text...",
    voice_setting={"voice_id": "Chinese (Mandarin)_News_Anchor"},
    timestamp={"subtitle_enable": True},
)
status = client.speech.poll_long_text(created.task_id, interval=2, timeout=900)
audio = client.speech.download_long_text(status["file_id"])
```

WebSocket 客户端不启动服务端，按官方事件协议发送原始字典：

```python
async with client.speech.websocket() as ws:
    connected = await ws.receive_event()
    await ws.send_event({"event": "task_start", "model": "speech-2.8-turbo"})
    async for event in ws.events():
        print(event)
```

WebSocket 文档公开的事件序列比字段定义更稳定，因此接口刻意保留原始事件透传。

## Voice

```python
clone_file = client.voice.upload_voice_clone("speaker.wav")
prompt_file = client.voice.upload_prompt_audio("prompt.wav")

cloned = client.voice.voice_clone(
    file_id=clone_file.file_id,
    voice_id="MyVoice_2026",
    clone_prompt={
        "prompt_audio": prompt_file.file_id,
        "prompt_text": "Example prompt text",
    },
)

voices = client.voice.list_voices("all")
designed = client.voice.voice_design(
    prompt="温暖、可靠的成年女声",
    preview_text="欢迎使用 MiniMax。",
    voice_id="WarmVoice_2026",
)
```

本地校验：

- `voice_id` 长度 8–256，ASCII 字母开头，只允许字母、数字、`_`、`-`，结尾必须为字母或数字
- 克隆/提示音频必须为 mp3/m4a/wav、非空、≤20 MB
- WAV 可直接校验时长：克隆 10 秒–5 分钟，prompt audio 小于 8 秒
- MP3/M4A 不引入重型解码依赖，时长最终由官方服务校验

## Music

```python
lyrics = client.music.lyrics_generation(
    mode="write_full_song",
    prompt="一首关于夏日海边的轻快情歌",
)

song = client.music.music_generation(
    model="music-3.0",
    prompt=lyrics.raw.get("style_tags"),
    lyrics=lyrics.raw.get("lyrics"),
    output_format="url",
)

preprocessed = client.music.music_cover_preprocess(
    audio_url="https://example.com/reference.mp3"
)
cover = client.music.music_cover(
    prompt="warm acoustic pop",
    cover_feature_id=preprocessed.raw["cover_feature_id"],
    lyrics=preprocessed.raw["formatted_lyrics"],
)
```

`music_cover()` 也支持 `audio_url` 或 `audio_base64` 直接翻唱；三种来源必须且只能传一个。

## Image

```python
image = client.image.text_to_image(
    model="image-01",
    prompt="A minimal product photograph",
    aspect_ratio="1:1",
    response_format="url",
)

reference = client.image.image_to_image(
    model="image-01",
    prompt="Keep the person, change the background to a studio",
    subject_reference=[{"type": "character", "image_file": "https://example.com/a.png"}],
)
```

图生图的 `subject_reference` 子字段可能随模型变化，SDK 不做超出官方公共约束的硬编码。

## Video

```python
created = client.video.text_to_video(
    model="MiniMax-Hailuo-2.3",
    prompt="A cinematic sunrise over a lake",
    duration=6,
    resolution="1080P",
)
final = client.video.poll(created.task_id, interval=5, timeout=1800)

created_i2v = client.video.image_to_video(
    model="MiniMax-Hailuo-2.3",
    first_frame_image="https://example.com/frame.png",
    prompt="The camera slowly pushes in",
)
```

### 不稳定/需验证能力

- **Video Agent**：官方 `/video_template_generation` 与查询接口已标记 deprecated。SDK 保留 `video_agent()` / `query_agent()` 原始封装，调用前应确认账号仍可用。
- **取消视频任务**：截至本文档核验时，官方公开文档未定义稳定 cancel 路径。`video.cancel(task_id, path=...)` 要求调用方显式提供已由账号或企业网关确认的路径，SDK 不伪造默认端点。
- 对未充分公开能力，SDK 返回 `GenerationResult.raw` 或原始字典，不承诺未公开字段的稳定性。

## Files

```python
uploaded = client.files.upload("book.txt", purpose="t2a_async_input")
items = client.files.list(purpose="t2a_async_input")
metadata = client.files.retrieve(uploaded.file_id)
content = client.files.retrieve_content(uploaded.file_id)
download = client.files.download(uploaded.file_id, destination="downloads/")
client.files.delete(uploaded.file_id, purpose="t2a_async_input")
```

下载默认调用官方 `/files/retrieve_content`，不会把 Bearer Token 转发到响应中的第三方 CDN URL。

## 原始请求与字段透传

资源方法支持 `extra` 或 `**kwargs`。文本使用 `extra_body`：

```python
result = client.image.text_to_image(
    model="image-01",
    prompt="...",
    extra={"account_preview_field": True},
)

raw = client.request("POST", "/account_specific_endpoint", json={"field": "value"})
```

低层路径以配置的 `/v1` base URL 为根；因此 `"/foo"` 会请求 `.../v1/foo`。绝对 URL 也可用于企业网关，但调用方必须自行确认目标可信；SDK 的默认 Bearer Header 会随共享客户端发送。

## 错误、重试与日志

```python
from minimax_api import MiniMaxAPIError

try:
    client.video.query("task-id")
except MiniMaxAPIError as exc:
    print(exc.status_code)      # HTTP status
    print(exc.api_status_code)  # base_resp.status_code
    print(exc.request_id)
```

默认仅重试 408/409/429/500/502/503/504 和网络传输异常，重试次数有限。非幂等生成请求可能在连接中断时产生服务端已接受但客户端未收到响应的情况；生产环境应记录 `request_id`、任务 ID，并按业务风险调整 `RetryConfig`。

SDK 不主动配置全局 logging。只有应用显式开启 `minimax_api` logger 的 DEBUG 级别时才记录请求摘要，敏感字段会被替换为 `<redacted>`。

## 测试

```bash
python -m pytest
python -m compileall -q minimax_api tests
```

测试使用 `httpx.MockTransport`，不访问真实 API，也不需要真实 Key。

## 官方来源

- [API Overview](https://platform.minimaxi.com/docs/api-reference/api-overview)
- [Model release notes](https://platform.minimaxi.com/docs/release-notes/models)
- [Voice cloning](https://platform.minimaxi.com/docs/api-reference/voice-cloning-clone)
- [OpenAI-compatible Chat](https://platform.minimaxi.com/docs/api-reference/text-chat-openai)
- [Anthropic-compatible API](https://platform.minimaxi.com/docs/api-reference/text-anthropic-api)
- [Music generation](https://platform.minimaxi.com/docs/api-reference/music-generation)
- [Lyrics generation](https://platform.minimaxi.com/docs/api-reference/lyrics-generation)

## 稳定性声明

**官方接口字段随区域/账号持续变化，未明确字段通过 extra/kwargs 透传。**

SDK 不会把文档不足的能力包装成虚假的稳定接口。公开文档未明确的路径、枚举和响应结构应通过账号控制台、官方文档或企业网关先行确认。
