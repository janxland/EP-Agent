"""
H5 解析器模块 — MIDI / ABC / Sky JSON 解析内部实现

所有函数均为私有辅助函数，不直接注册为工具。
由 h5_tools.py 中的 @tool 入口函数调用。
"""
from __future__ import annotations

import json
import re
import struct


def parse_midi_bytes(raw: bytes, title: str = "") -> dict:
    """
    轻量级 MIDI 解析器（不依赖第三方库）。
    设计原则：宽容解析，有多少音符取多少，解析异常时返回已收集的部分结果而非报错。
    """
    result = {
        "title": title or "未命名乐曲",
        "bpm": 120,
        "notes": [],
        "duration_ms": 0,
        "track_count": 0,
    }

    if len(raw) < 14:
        # 文件太短，但不报错，返回空音符（后续生成空海报）
        result["_warn"] = "MIDI 文件过短，使用空音符生成海报"
        return result
    if raw[:4] != b"MThd":
        result["_warn"] = "非标准 MIDI 文件头，尝试宽容解析"
        # 不直接返回，继续尝试解析

    try:
        n_trks = struct.unpack(">H", raw[10:12])[0]
        ticks  = struct.unpack(">H", raw[12:14])[0]
        result["track_count"] = n_trks

        pos    = 14
        tempo  = 500000  # 默认 120 BPM
        notes  = []

        for trk_idx in range(n_trks):
            if pos + 8 > len(raw):
                break
            # 宽容处理：跳过非 MTrk chunk（如 MIDI Type 2 的其他 chunk 类型）
            if raw[pos:pos+4] != b"MTrk":
                # 尝试向前扫描找下一个 MTrk
                found = False
                for scan in range(pos, min(pos + 1024, len(raw) - 4)):
                    if raw[scan:scan+4] == b"MTrk":
                        pos = scan
                        found = True
                        break
                if not found:
                    break
            if pos + 8 > len(raw):
                break
            trk_len = struct.unpack(">I", raw[pos+4:pos+8])[0]
            trk_end = min(pos + 8 + trk_len, len(raw))  # 防止 trk_len 超出文件边界
            pos += 8

            abs_tick   = 0
            active: dict[int, int] = {}  # pitch → start_tick
            last_status = 0

            while pos < trk_end:
                try:
                    # 读取 delta time（可变长编码）
                    delta = 0
                    for _ in range(4):  # VLQ 最多 4 字节
                        if pos >= trk_end:
                            break
                        b = raw[pos]; pos += 1
                        delta = (delta << 7) | (b & 0x7F)
                        if not (b & 0x80):
                            break
                    abs_tick += delta

                    if pos >= trk_end:
                        break

                    status = raw[pos]
                    if status & 0x80:
                        last_status = status; pos += 1
                    else:
                        status = last_status  # running status

                    if pos > trk_end:
                        break

                    msg_type = status & 0xF0

                    if msg_type == 0x90:  # Note On
                        if pos + 1 >= trk_end:
                            break
                        pitch = raw[pos]; vel = raw[pos+1]; pos += 2
                        if vel > 0:
                            active[pitch] = abs_tick
                        else:
                            if pitch in active:
                                dur = abs_tick - active.pop(pitch)
                                notes.append({
                                    "pitch": pitch,
                                    "tick": abs_tick - dur,
                                    "dur_tick": max(1, dur),
                                })

                    elif msg_type == 0x80:  # Note Off
                        if pos + 1 >= trk_end:
                            break
                        pitch = raw[pos]; pos += 2
                        if pitch in active:
                            dur = abs_tick - active.pop(pitch)
                            notes.append({
                                "pitch": pitch,
                                "tick": abs_tick - dur,
                                "dur_tick": max(1, dur),
                            })

                    elif msg_type == 0xFF:  # Meta
                        if pos >= trk_end:
                            break
                        meta_type = raw[pos]; pos += 1
                        meta_len  = 0
                        for _ in range(4):  # VLQ 最多 4 字节
                            if pos >= trk_end:
                                break
                            b = raw[pos]; pos += 1
                            meta_len = (meta_len << 7) | (b & 0x7F)
                            if not (b & 0x80):
                                break
                        if pos + meta_len > len(raw):
                            meta_len = len(raw) - pos  # 截断保护
                        meta_data = raw[pos:pos+meta_len]; pos += meta_len

                        if meta_type == 0x51 and len(meta_data) >= 3:
                            t = struct.unpack(">I", b"\x00" + meta_data[:3])[0]
                            if t > 0:
                                tempo = t
                                result["bpm"] = round(60_000_000 / tempo)
                        elif meta_type == 0x03 and not title:
                            try:
                                result["title"] = meta_data.decode("utf-8", errors="replace").strip()
                            except Exception:
                                pass
                        elif meta_type == 0x2F:  # End of Track
                            break

                    elif msg_type in (0xA0, 0xB0, 0xE0):
                        if pos + 1 < len(raw):
                            pos += 2
                        else:
                            break
                    elif msg_type in (0xC0, 0xD0):
                        if pos < len(raw):
                            pos += 1
                        else:
                            break
                    elif status in (0xF0, 0xF7):  # SysEx
                        sysex_len = 0
                        for _ in range(4):
                            if pos >= trk_end:
                                break
                            b = raw[pos]; pos += 1
                            sysex_len = (sysex_len << 7) | (b & 0x7F)
                            if not (b & 0x80):
                                break
                        pos = min(pos + sysex_len, trk_end)
                    else:
                        pos += 1  # 未知字节，跳过

                except (IndexError, struct.error):
                    # 单个事件解析失败，跳过继续（不中断整个 track）
                    pos += 1
                    continue

            # 将 track 中未关闭的 note 按 track 结束时间关闭
            for pitch, start_tick in active.items():
                notes.append({
                    "pitch": pitch,
                    "tick": start_tick,
                    "dur_tick": max(1, abs_tick - start_tick),
                })

            pos = trk_end

        # 将 tick 转换为毫秒
        tick_ms = (tempo / 1000) / ticks  # ms per tick
        note_names = ["C", "C#", "D", "D#", "E", "F", "F#", "G", "G#", "A", "A#", "B"]
        out_notes = []
        for n in notes[:256]:  # 最多 256 个音符
            t_ms  = int(n["tick"] * tick_ms)
            d_ms  = max(50, int(n["dur_tick"] * tick_ms))
            pitch = n["pitch"]
            note_name = f"{note_names[pitch % 12]}{pitch // 12 - 1}"
            out_notes.append({
                "pitch": note_name,
                "midi": pitch,
                "time_ms": t_ms,
                "duration_ms": d_ms,
            })

        result["notes"] = sorted(out_notes, key=lambda x: x["time_ms"])
        if result["notes"]:
            last = result["notes"][-1]
            result["duration_ms"] = last["time_ms"] + last["duration_ms"]

    except Exception as e:
        # 顶层异常：记录警告但不报 error，返回已收集的部分结果
        result["_warn"] = f"MIDI 解析遇到异常（{e}），已提取部分音符数据"

    return result


def parse_abc_notes(abc: str) -> dict:
    """
    从 ABC Notation 字符串提取元数据和音符列表。
    返回: {"title", "bpm", "key", "notes", "abc_clean"}
    """
    result: dict = {"notes": [], "abc_clean": abc.strip()}
    title = ""

    for line in abc.splitlines():
        line = line.strip()
        if line.startswith("T:") and not title:
            title = line[2:].strip()
        elif line.startswith("Q:"):
            try:
                bpm_str = re.search(r"(\d+)", line[2:])
                if bpm_str:
                    result["bpm"] = int(bpm_str.group(1))
            except Exception:
                pass
        elif line.startswith("K:"):
            result["key"] = line[2:].strip()

    result["title"] = title or "未命名乐曲"
    result.setdefault("bpm", 120)
    result.setdefault("key", "C")

    # 尝试调用 sky-music-tools 进行深度解析
    try:
        import sys
        from pathlib import Path
        sky_tools_path = str(
            Path(__file__).parent.parent.parent.parent / "sky-music-tools"
        )
        if sky_tools_path not in sys.path:
            sys.path.insert(0, sky_tools_path)
        from tools.abc_to_json import abc_to_note_events
        events = abc_to_note_events(abc)
        result["notes"] = [
            {
                "pitch": e.pitch,
                "time_ms": int(e.time_ms),
                "duration_ms": int(e.duration_ms),
            }
            for e in events
        ]
    except Exception:
        # 降级：简单提取音符字母（不含时值）
        note_pattern = re.compile(r"[A-Ga-g][',]?")
        raw_notes = note_pattern.findall(abc)
        result["notes"] = [
            {"pitch": n, "time_ms": i * 300, "duration_ms": 250}
            for i, n in enumerate(raw_notes[:128])
        ]

    return result


def parse_sky_json(sky_json_str: str, title: str = "") -> dict:
    """
    将 Sky: Children of the Light 游戏导出的 JSON 谱子解析为通用音符数据。
    返回: {"title", "bpm", "key_count", "notes"}
    """
    try:
        data = json.loads(sky_json_str)
    except Exception as e:
        return {"error": f"JSON 解析失败: {e}", "notes": []}

    if isinstance(data, list):
        song_data = data[0] if data else {}
    else:
        song_data = data

    result: dict = {
        "title": title or song_data.get("name", "未命名乐曲"),
        "bpm": song_data.get("bpm", 120),
        "key_count": song_data.get("pitchLevel", 15),
        "notes": [],
    }

    for note in song_data.get("songNotes", []):
        result["notes"].append({
            "pitch": note.get("key", ""),
            "time_ms": int(float(note.get("time", 0)) * 1000),
            "duration_ms": 200,
        })

    return result
