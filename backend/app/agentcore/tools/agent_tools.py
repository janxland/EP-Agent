"""
Agent-as-Tool — 将 SubAgent 封装为工具，供其他 Agent 调用 (v5)

这是 Multi-Agent 协作的核心机制：
  - 任何 Agent 都可以通过工具调用的方式调用其他 Agent
  - 调用方不需要知道被调用 Agent 的内部实现
  - 结果通过返回值传递，保持状态一致性

使用场景：
  H5Agent 执行中发现需要先转换文件：
    → call_convert_agent(content=sky_json, file_name="score.txt")
    → 拿到 abc_notation 后继续生成 H5

  EditAgent 执行后用户想要 H5：
    → call_h5_agent(abc_content=abc, template="luoxiaohei")
"""
from __future__ import annotations

import logging

from app.agentcore.tools import tool

_logger = logging.getLogger("ep_agent.agent_tools")


@tool(group="agent_call")
async def call_convert_agent(
    content: str,
    file_name: str = "score.txt",
) -> dict:
    """
    调用 ConvertAgent 将 Sky JSON 或 ABC 内容转换为标准 ABC 谱。
    当当前 Agent 需要先转换文件再继续工作时调用此工具。
    content: Sky JSON 字符串或 ABC 文本内容
    file_name: 原始文件名（用于格式推断，如 score.txt / score.abc）
    返回: {"abc_notation": str, "meta": dict, "success": bool}
    """
    from app.agentcore.session_context import get_current_session_id
    from app.pipeline import service as _svc

    session_id = get_current_session_id()

    async def _silent_publish(evt_type: str, payload: dict, **kwargs):
        pass

    try:
        result = await _svc.convert(
            session_id=session_id,
            json_content=content,
            file_name=file_name,
            publish=_silent_publish,
        )
        abc = result.get("abc_notation", "")
        _logger.info("[agent_tools] call_convert_agent 成功，abc长度=%d", len(abc))
        return {
            "success": True,
            "abc_notation": abc,
            "meta": result.get("meta", {}),
        }
    except Exception as exc:
        _logger.warning("[agent_tools] call_convert_agent 失败: %s", exc)
        return {"success": False, "error": str(exc), "abc_notation": ""}


@tool(group="agent_call")
async def call_h5_agent(
    abc_content: str = "",
    midi_workspace_path: str = "",
    title: str = "",
    template: str = "luoxiaohei",
    video_url: str = "",
) -> dict:
    """
    调用 H5Agent 生成乐谱海报页面。
    当需要将当前谱子或 MIDI 转为 H5 页面时调用。
    abc_content: ABC 谱内容（与 midi_workspace_path 二选一）
    midi_workspace_path: MIDI 文件工作区路径（与 abc_content 二选一）
    title: 曲名
    template: 模板名（luoxiaohei/apple/miku/neon/ins）
    video_url: 可选视频链接
    返回: {"url_path": str, "workspace_path": str, "success": bool}
    """
    from app.agentcore.tools.h5_tools import (
        get_h5_template, save_h5_output, generate_h5_from_midi,
    )
    import re
    import json as _json

    if midi_workspace_path:
        try:
            result = generate_h5_from_midi(
                midi_workspace_path=midi_workspace_path,
                title=title,
                template=template,
                video_url=video_url,
            )
            success = "error" not in result
            _logger.info("[agent_tools] call_h5_agent(midi) success=%s", success)
            return {
                "success": success,
                "url_path": result.get("url_path", ""),
                "workspace_path": result.get("workspace_path", ""),
                "error": result.get("error", ""),
            }
        except Exception as exc:
            return {"success": False, "error": str(exc)}

    if abc_content:
        try:
            tpl = get_h5_template(template)
            if "error" in tpl:
                return {"success": False, "error": tpl["error"]}

            html = tpl["html"]
            config = {
                "TITLE": title or "乐谱",
                "ABC_CONTENT": abc_content.replace("\n", "\\n"),
                "FORMAT_LABEL": "ABC Notation",
                "MIDI_URL": "",
                "VIDEO_URL": video_url or "",
                "NOTES_JSON": [],
            }
            pattern = r'(<script id="ep-config"[^>]*>)([\s\S]*?)(</script>)'
            replacement = (
                r'\g<1>\n'
                + _json.dumps(config, ensure_ascii=False, indent=2)
                + r'\n\g<3>'
            )
            html = re.sub(pattern, replacement, html)

            result = save_h5_output(html=html, filename=title or "score", template=template)
            success = "error" not in result
            _logger.info("[agent_tools] call_h5_agent(abc) success=%s", success)
            return {
                "success": success,
                "url_path": result.get("url_path", ""),
                "workspace_path": result.get("workspace_path", ""),
                "error": result.get("error", ""),
            }
        except Exception as exc:
            return {"success": False, "error": str(exc)}

    return {"success": False, "error": "需要提供 abc_content 或 midi_workspace_path"}


@tool(group="agent_call")
async def call_audio_agent(
    prompt: str,
    style: str = "",
    bpm: int = 0,
    key: str = "",
    provider: str = "auto",
) -> dict:
    """
    调用 AudioAgent 生成配乐音频。
    当需要为谱子生成背景音乐时调用。
    prompt: 音频描述（风格/情感/场景）
    style: 音乐风格（如 "轻柔钢琴"）
    bpm: 节拍（0=自动）
    key: 调号（如 "C大调"，可选）
    provider: 音频提供商（auto/suno/minimax）
    返回: {"audio_url": str, "success": bool}
    """
    try:
        from app.agentcore.tools.audio_tools import generate_audio_auto
        full_prompt = prompt
        if style:
            full_prompt = f"[{style}] {prompt}"
        if bpm:
            full_prompt += f"，BPM {bpm}"
        if key:
            full_prompt += f"，{key}"

        result = await generate_audio_auto(prompt=full_prompt, provider=provider)
        _logger.info("[agent_tools] call_audio_agent success")
        return {
            "success": True,
            "audio_url": result.get("audio_url", ""),
            "provider":  result.get("provider", ""),
        }
    except Exception as exc:
        _logger.warning("[agent_tools] call_audio_agent 失败: %s", exc)
        return {"success": False, "error": str(exc)}
