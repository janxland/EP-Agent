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
async def abc_to_midi_file(abc: str, instrument: int = 0, filename: str = "") -> dict:
    """将 ABC 谱转换为 MIDI 文件，保存到工作区并返回可访问 URL（不返回 base64）。
    abc: 完整的 ABC Notation 字符串
    instrument: MIDI 乐器编号（0=钢琴, 40=小提琴, 46=竖琴 等 GM 标准）
    filename: 输出文件名（不含扩展名，留空则自动生成）
    返回: {"midi_url": str, "file_path": str, "size_bytes": int}
    """
    import uuid
    import re
    from tools.abc_to_json import abc_to_cuby_json
    from tools.parser import parse_game_score
    from tools.midi_writer import to_midi
    from app.config import config

    loop = asyncio.get_running_loop()

    # 生成安全文件名
    if not filename:
        # 尝试从 ABC T: 字段提取标题
        for line in abc.splitlines():
            if line.startswith("T:"):
                filename = line[2:].strip()
                break
        if not filename:
            filename = f"midi_{uuid.uuid4().hex[:8]}"
    safe_name = re.sub(r"[^\w\-]", "_", filename)

    # ABC → CUBY JSON → Score → MIDI
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
        # 复制到 H5 midi 静态目录，通过 URL 访问（与 generate_h5_from_midi 保持一致）
        from pathlib import Path as _Path
        import shutil as _shutil
        h5_midi_dir = _Path(config.H5_OUTPUT_DIR) / "midi"
        h5_midi_dir.mkdir(parents=True, exist_ok=True)
        dest = h5_midi_dir / f"{safe_name}.mid"
        _shutil.copy2(tmp_mid, dest)
        # 相对路径：H5 页面与 midi/ 目录同在 H5_OUTPUT_DIR 下
        # 模板里直接用 midi/xxx.mid 即可，无需绝对 URL
        midi_url = f"midi/{safe_name}.mid"
        return {
            "midi_url":   midi_url,
            "file_path":  str(dest),
            "size_bytes": dest.stat().st_size,
            "_note": "相对路径，H5 页面与 midi/ 同目录，直接加载",
        }
    finally:
        for p in [tmp_json, tmp_mid]:
            try:
                os.unlink(p)
            except Exception:
                pass
