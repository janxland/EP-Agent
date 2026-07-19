"""TypedDict request/response schemas without a runtime validation dependency."""

from __future__ import annotations

from typing import Any, Dict, List, Literal, TypedDict, Union


class BaseResp(TypedDict, total=False):
    status_code: int
    status_msg: str


class ChatMessage(TypedDict, total=False):
    role: str
    content: Any
    name: str
    tool_call_id: str
    tool_calls: List[Dict[str, Any]]


class ToolFunction(TypedDict, total=False):
    name: str
    description: str
    parameters: Dict[str, Any]


class Tool(TypedDict):
    type: Literal["function"]
    function: ToolFunction


class VoiceSetting(TypedDict, total=False):
    voice_id: str
    speed: float
    vol: float
    pitch: int


class AudioSetting(TypedDict, total=False):
    sample_rate: int
    audio_sample_rate: int
    bitrate: int
    format: Literal["mp3", "wav", "flac", "pcm"]
    channel: int


class FileObject(TypedDict, total=False):
    file_id: Union[str, int]
    bytes: int
    created_at: int
    filename: str
    purpose: str
    download_url: str


class TaskResponse(TypedDict, total=False):
    task_id: Union[str, int]
    status: str
    file_id: Union[str, int]
    base_resp: BaseResp
