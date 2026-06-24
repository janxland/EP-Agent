"""
导出工具集 - Agent 可调用的格式转换工具
这些工具既可以被 Agent 主动调用，也可以被 OutputAdapter 按场景自动追加
"""
from __future__ import annotations
import sys
import json
import tempfile
import os
import asyncio
from pathlib import Path
from app.agentcore.tools import tool

# sky-music-tools 已内置在 backend/sky-music-tools/，直接注入路径
_SKY_TOOLS_DIR = str(Path(__file__).resolve().parent.parent.parent.parent / "sky-music-tools")
if _SKY_TOOLS_DIR not in sys.path:
    sys.path.insert(0, _SKY_TOOLS_DIR)


@tool
async def abc_to_sky_json(abc: str) -> str:
    """将 ABC 谱转换为 Sky/CUBY JSON 格式，供小程序键盘使用。
    abc: 完整的 ABC Notation 字符串
    返回 JSON 字符串（数组格式，包含 songNotes）
    """
    from tools.abc_to_json import abc_to_cuby_json
    loop = asyncio.get_running_loop()
    cuby = await loop.run_in_executor(None, abc_to_cuby_json, abc)
    return json.dumps([cuby], ensure_ascii=False, indent=2)


@tool
async def abc_to_midi_b64(abc: str, instrument: int = 0) -> str:
    """将 ABC 谱转换为 MIDI 文件并返回 base64 编码字符串，供 DAW 或播放器使用。
    abc: 完整的 ABC Notation 字符串
    instrument: MIDI 乐器编号（0=钢琴, 40=小提琴, 46=竖琴 等 GM 标准）
    """
    import base64
    from tools.abc_to_json import abc_to_cuby_json
    from tools.parser import parse_game_score
    from tools.midi_writer import to_midi

    loop = asyncio.get_running_loop()

    # ABC → CUBY JSON → Score 对象 → MIDI
    with tempfile.NamedTemporaryFile(mode='w', suffix='.json',
                                     delete=False, encoding='utf-8') as f:
        cuby = abc_to_cuby_json(abc)
        json.dump([cuby], f, ensure_ascii=False)
        tmp_json = f.name
    tmp_mid = tmp_json + '.mid'

    try:
        score_obj = await loop.run_in_executor(None, parse_game_score, tmp_json)
        await loop.run_in_executor(
            None,
            lambda: to_midi(score_obj, tmp_mid,
                            instrument=instrument,
                            add_expression=True,
                            humanize_ticks=6)
        )
        with open(tmp_mid, 'rb') as f:
            data = f.read()
        return base64.b64encode(data).decode('ascii')
    finally:
        for p in [tmp_json, tmp_mid]:
            try:
                os.unlink(p)
            except Exception:
                pass
