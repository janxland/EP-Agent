"""
导出工具集 - Agent 可调用的格式转换工具

工具清单：
  abc_to_sky_json  — ABC Notation → Sky/CUBY JSON（供小程序键盘使用）
  finish_task      — 任务完成信号（所有 Agent 必须以此结束 ReAct Loop）

注意：ABC → MIDI 转换请使用 abc_tools.abc_to_midi，
该工具自动写入当前项目 .sky/ 目录，无需传 workspace_id / project_id。
"""
from __future__ import annotations
import sys
import json
import asyncio
from pathlib import Path
from app.agentcore.tools import tool

# sky-music-tools 已内置在 backend/sky-music-tools/，直接注入路径
_SKY_TOOLS_DIR = str(Path(__file__).resolve().parent.parent.parent.parent / "sky-music-tools")
if _SKY_TOOLS_DIR not in sys.path:
    sys.path.insert(0, _SKY_TOOLS_DIR)


@tool
def finish_task(summary: str = "") -> dict:
    """
    【任务完成信号】所有 Agent 在完成全部工作后必须调用此工具，表示 ReAct Loop 正常结束。

    ⚠️ 铁律：
    - 必须是最后一个调用的工具
    - 调用前确认所有文件已保存、所有操作已完成
    - summary 中包含本次任务的关键结果（文件路径、操作摘要等）

    summary: 任务完成摘要（建议包含生成文件路径、操作结果等关键信息）
    返回: {"status": "finished", "summary": str}
    """
    return {"status": "finished", "summary": summary or "任务已完成"}


@tool
async def abc_to_sky_json(abc: str) -> str:
    """将 ABC 谱转换为 Sky/CUBY JSON 格式，供小程序键盘使用。
    abc: 完整的 ABC Notation 字符串
    返回 JSON 字符串（数组格式，包含 songNotes）
    ⚠️ 如需生成 MIDI 文件，请使用 abc_to_midi 工具（自动保存到项目 .sky/ 目录）。
    """
    from tools.abc_to_json import abc_to_cuby_json
    loop = asyncio.get_running_loop()
    cuby = await loop.run_in_executor(None, abc_to_cuby_json, abc)
    return json.dumps([cuby], ensure_ascii=False, indent=2)
