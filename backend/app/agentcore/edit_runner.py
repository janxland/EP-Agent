"""
ABC Edit Runner — LLM 直接输出新 ABC

核心设计理念：
  ABC 编辑是音乐创作行为，不是程序操作。
  转调、变速、风格转换、加花、重写旋律——这些都是 LLM 的强项，
  不应该用工具去"计算"，工具计算出来的音乐没有艺术性。

流程：
  用户意图 + 当前 ABC + 谱子元数据
    → LLM（音乐专家 System Prompt）
    → 直接输出修改后的完整 ABC + 摘要
    → 可选：abc_to_sky_json / abc_to_midi_b64（纯格式转换）

工具使用原则：
  ✅ analyze_abc   — 帮 LLM 理解谱子结构（可选，意图复杂时使用）
  ✅ abc_to_sky_json — 输出格式转换（OutputAdapter）
  ✅ abc_to_midi_b64 — 输出格式转换（OutputAdapter）
  ❌ transpose_abc、change_tempo、change_style — 废弃，LLM 直接做
"""
from __future__ import annotations
import importlib
import json
import pkgutil
import re
import sys
from pathlib import Path
from typing import Callable, Awaitable, Literal

from app.agentcore.llm import complete, complete_with_tools
from app.pipeline.domain import ScoreMeta
from app.agentcore.tools import get_tool_schemas, call_tool

Publisher = Callable[[str, dict], Awaitable[None]]
Scene = Literal["editor", "player", "daw", "raw"]

# ─── 自动扫描注册 tools/ 目录 ─────────────────────────────────────────────────
_tools_pkg = "app.agentcore.tools"
_tools_dir = Path(__file__).parent / "tools"
for _mod_info in pkgutil.iter_modules([str(_tools_dir)]):
    _full_name = f"{_tools_pkg}.{_mod_info.name}"
    if _full_name not in sys.modules:
        importlib.import_module(_full_name)

# ─── System Prompt ────────────────────────────────────────────────────────────

_SYSTEM = """你是一位精通 ABC Notation 的专业音乐编辑助手，服务于 Sky: Children of the Light 游戏玩家。

## 你的核心能力
你直接用音乐家的耳朵和大脑来修改谱子——不依赖外部工具计算，而是凭借对音乐理论的深刻理解直接输出正确的 ABC。

## ABC Notation 关键规则

### Header 字段
- `X:` 序号（保持原值）
- `T:` 标题
- `C:` 作曲者
- `M:` 拍号（如 4/4、3/4、6/8）
- `L:` 默认音符时值（如 1/8）
- `Q:` 速度（如 Q:1/4=120，只改数字）
- `K:` 调号（如 K:C、K:G、K:Am、K:Bb）

### 音符语法
- 大写 C D E F G A B = 第4八度
- 小写 c d e f g a b = 第5八度
- `'` 后缀升八度（c' = 第6八度），`,` 后缀降八度
- `^` 前缀升半音，`_` 前缀降半音，`=` 还原
- 时值：1=默认，2=两倍，/2=一半，3/2=附点，4=四倍
- 休止符：`z`（有时值），`x`（不发声）
- 小节线：`|`，双小节线：`||`，重复：`|:`...`:|`
- 连线：`(3` 三连音，`-` 延音线

### 转调规则（你直接计算，不调工具）
- 每升高半音：C→^C/Db→D→^D/Eb→E→F→^F/Gb→G→^G/Ab→A→^A/Bb→B→c
- 升号调（G/D/A/E/B/F#/C#）用 `^`，降号调（F/Bb/Eb/Ab/Db/Gb/Cb）用 `_`
- 八度关系：大写第4八度，小写第5八度，`'`继续升

### Sky 游戏限制（重要！）
- Sky 乐器只有 15 个键：C D E F G A B c d e f g a b c'（C4 到 C6）
- 转调时需确保音符落在这个范围内，超出则移八度
- 不使用复杂和弦，保持单声部旋律

## 修改原则
1. **精确执行意图**：用户说转调就转调，说加快就改 Q: 字段
2. **保持音乐性**：修改后旋律仍然流畅自然，符合音乐逻辑
3. **风格修改靠创造力**：加花、装饰音、节奏变化——用你的音乐审美来决定
4. **不丢音符**：除非用户明确要求删减，否则保持音符完整性
5. **保持格式**：Header 格式不变，小节线位置尽量不变

## 输出格式（严格遵守）

修改后直接输出完整 ABC，然后另起一行输出摘要，格式如下：

```
X:1
T:标题
...（完整 ABC 内容）...

SUMMARY: 一句话中文摘要，说明做了什么修改
```

**绝对不要**输出 JSON、代码块标记（```）、解释性文字，只输出 ABC + SUMMARY 行。
"""

# ─── ABC 提取 ─────────────────────────────────────────────────────────────────

def _extract_abc_and_summary(text: str, fallback_abc: str) -> tuple[str, str]:
    """
    从 LLM 输出中提取 ABC 正文和 SUMMARY。
    策略：
      1. 找到 X: 开头的行，到 SUMMARY: 行之前为 ABC
      2. SUMMARY: 行之后为摘要
      3. 如果找不到，返回原 ABC + 原文作摘要
    """
    # 清理可能的 markdown 代码块
    text = re.sub(r'```[a-z]*\n?', '', text).strip()

    # 提取 SUMMARY
    summary = ""
    summary_match = re.search(r'SUMMARY:\s*(.+?)$', text, re.MULTILINE | re.IGNORECASE)
    if summary_match:
        summary = summary_match.group(1).strip()
        # 移除 SUMMARY 行
        text = text[:summary_match.start()].strip()

    # 找 ABC 正文（X: 开头）
    abc_match = re.search(r'^X:\s*\d', text, re.MULTILINE)
    if abc_match:
        abc = text[abc_match.start():].strip()
        # 确保 ABC 有效（至少有 K: 字段）
        if 'K:' in abc:
            return abc, summary or "修改完成"

    # 兜底：返回原 ABC
    return fallback_abc, summary or text[:100] or "修改完成"


# ─── Runner ───────────────────────────────────────────────────────────────────

class DirectEditRunner:
    """
    LLM 直接输出 ABC 的编辑 Runner。
    不使用 abc_edit 分组工具，LLM 凭音乐理解直接生成。
    """

    async def run(
        self,
        current_abc: str,
        intent: str,
        meta: ScoreMeta,
        context_summary: str,
        publish: Publisher,
        scene: Scene = "editor",
    ) -> dict:
        """
        执行 LLM 直接编辑，返回：
          {
            "abc": str,           # 修改后的 ABC
            "summary": str,       # 中文摘要
            "tool_calls": [...],  # 仅包含 OutputAdapter 工具调用记录
            "sky_json": str|None,
            "midi_b64": str|None,
          }
        """
        tool_call_records: list[dict] = []

        await publish("pipeline.step", {
            "step": "edit_start",
            "status": "running",
            "text": f"正在理解意图：{intent}",
        })

        # ── 构造 User Prompt ──────────────────────────────────────────────────
        user_prompt = (
            f"用户意图：{intent}\n\n"
            f"谱子信息：标题={meta.title}，调号={meta.key}，"
            f"BPM={meta.bpm:.0f}，拍号={meta.time_sig_num}/{meta.time_sig_den}，"
            f"音符数={meta.note_count}\n"
        )
        if context_summary:
            user_prompt += f"历史上下文：{context_summary}\n"
        user_prompt += f"\n当前 ABC 谱：\n{current_abc}"

        messages = [
            {"role": "system", "content": _SYSTEM},
            {"role": "user",   "content": user_prompt},
        ]

        await publish("pipeline.step", {
            "step": "llm_edit",
            "status": "running",
            "text": "LLM 正在修改谱子...",
        })

        # ── LLM 直接生成新 ABC ────────────────────────────────────────────────
        try:
            response = await complete(messages)
            raw_output = response if isinstance(response, str) else response.get("content", "")
        except Exception as e:
            await publish("pipeline.step", {
                "step": "llm_edit",
                "status": "failed",
                "text": f"LLM 调用失败: {e}",
            })
            raise

        # ── 提取 ABC + 摘要 ───────────────────────────────────────────────────
        new_abc, summary = _extract_abc_and_summary(raw_output, current_abc)

        await publish("pipeline.step", {
            "step": "llm_edit",
            "status": "succeeded",
            "text": summary,
        })

        # ── OutputAdapter：按场景追加格式转换 ────────────────────────────────
        sky_json: str | None = None
        midi_b64: str | None = None

        if scene in ("player", "raw"):
            await publish("pipeline.step", {
                "step": "output_adapt",
                "status": "running",
                "text": "正在生成 Sky JSON...",
            })
            try:
                sky_json = await call_tool("abc_to_sky_json", {"abc": new_abc})
                await publish("pipeline.step", {
                    "step": "output_adapt",
                    "status": "succeeded",
                    "text": "Sky JSON 生成完成",
                })
            except Exception as e:
                await publish("pipeline.step", {
                    "step": "output_adapt",
                    "status": "failed",
                    "text": f"Sky JSON 生成失败: {e}",
                })

        if scene in ("daw", "raw"):
            await publish("pipeline.step", {
                "step": "output_adapt",
                "status": "running",
                "text": "正在生成 MIDI...",
            })
            try:
                midi_b64 = await call_tool("abc_to_midi_b64", {"abc": new_abc})
                await publish("pipeline.step", {
                    "step": "output_adapt",
                    "status": "succeeded",
                    "text": "MIDI 生成完成",
                })
            except Exception as e:
                await publish("pipeline.step", {
                    "step": "output_adapt",
                    "status": "failed",
                    "text": f"MIDI 生成失败: {e}",
                })

        return {
            "abc":        new_abc,
            "summary":    summary,
            "tool_calls": tool_call_records,
            "sky_json":   sky_json,
            "midi_b64":   midi_b64,
        }


edit_agent_runner = DirectEditRunner()
