"""
音频 Prompt 进化工具 - 支持对话式迭代改进

核心工具：
  evolve_audio_prompt  - 根据用户反馈进化 prompt（大厂对话式迭代的核心）
  diff_audio_params    - 对比两次生成参数，生成可读 diff

设计参考：
  Google MusicLM / Udio / Suno 的"继续创作"和"风格调整"交互模式
  - 保留用户满意的部分（主题、情绪基调）
  - 精准修改用户不满意的部分（风格词、节奏、乐器）
  - 输出 diff 让用户知道改了什么
"""
from __future__ import annotations
from app.agentcore.tools import tool
from app.agentcore.llm import complete


# ─── Prompt 进化关键词映射（确定性规则，不消耗 LLM）────────────────────────

_EVOLUTION_RULES: list[tuple[list[str], dict]] = [
    # (触发词列表, {add: [...], remove: [...], set: {key: val}})
    (["欢快", "活泼", "开心", "轻松", "upbeat", "lively"],
     {"add": ["upbeat", "lively", "energetic", "cheerful"],
      "remove": ["melancholic", "sad", "slow", "dark"]}),

    (["悲伤", "忧郁", "感伤", "melancholic", "sad"],
     {"add": ["melancholic", "introspective", "emotional"],
      "remove": ["upbeat", "lively", "cheerful", "energetic"]}),

    (["慢", "抒情", "舒缓", "slow", "lyrical"],
     {"add": ["slow", "lyrical", "gentle", "flowing"],
      "remove": ["upbeat", "fast", "energetic", "driving"]}),

    (["快", "激烈", "有力", "fast", "energetic", "powerful"],
     {"add": ["fast", "energetic", "powerful", "driving"],
      "remove": ["slow", "gentle", "lyrical"]}),

    (["中国风", "国风", "古风", "chinese"],
     {"add": ["chinese traditional", "erhu", "guzheng", "pipa"],
      "remove": ["western", "electronic", "jazz"]}),

    (["爵士", "jazz"],
     {"add": ["jazz", "smooth", "swing", "late night lounge"],
      "remove": ["classical", "electronic", "chinese traditional"]}),

    (["古典", "交响", "classical", "orchestral"],
     {"add": ["classical", "orchestral", "elegant", "strings"],
      "remove": ["electronic", "jazz", "pop"]}),

    (["电子", "电音", "合成", "electronic", "synthwave"],
     {"add": ["electronic", "synthesizer", "modern", "digital"],
      "remove": ["acoustic", "classical", "traditional"]}),

    (["钢琴", "piano"],
     {"add": ["piano", "acoustic piano"],
      "remove": []}),

    (["去掉人声", "纯音乐", "instrumental", "无人声"],
     {"add": [], "remove": [], "set": {"instrumental": True}}),

    (["加人声", "有歌词", "vocal"],
     {"add": [], "remove": [], "set": {"instrumental": False}}),

    (["民谣", "folk", "acoustic"],
     {"add": ["folk", "acoustic", "indie", "guitar"],
      "remove": ["electronic", "orchestral"]}),

    (["流行", "pop", "现代"],
     {"add": ["pop", "modern", "catchy"],
      "remove": ["classical", "traditional"]}),
]


def _apply_rules(prompt: str, feedback: str, params: dict) -> tuple[str, list[str]]:
    """
    基于规则快速进化 prompt（确定性，不调用 LLM）。
    返回 (new_prompt, changes_list)
    """
    prompt_words = [w.strip() for w in prompt.split(",")]
    changes: list[str] = []
    new_params = dict(params)

    for triggers, ops in _EVOLUTION_RULES:
        if any(t in feedback for t in triggers):
            # 添加新词
            for word in ops.get("add", []):
                if word not in prompt:
                    prompt_words.append(word)
                    changes.append(f"+ {word}")
            # 移除旧词
            for word in ops.get("remove", []):
                before = len(prompt_words)
                prompt_words = [w for w in prompt_words if word.lower() not in w.lower()]
                if len(prompt_words) < before:
                    changes.append(f"- {word}")
            # 设置参数
            for key, val in ops.get("set", {}).items():
                if new_params.get(key) != val:
                    new_params[key] = val
                    changes.append(f"set {key}={val}")

    new_prompt = ", ".join(w for w in prompt_words if w.strip())
    return new_prompt, changes, new_params


@tool(group="audio")
async def evolve_audio_prompt(
    last_prompt: str,
    last_style: str,
    last_lyrics: str,
    last_instrumental: bool,
    user_feedback: str,
    score_context: str = "",
) -> dict:
    """根据用户对上次音频的反馈，进化生成参数（对话式迭代核心工具）。
    last_prompt: 上次使用的 prompt
    last_style: 上次使用的风格标签（Suno 用）
    last_lyrics: 上次使用的歌词
    last_instrumental: 上次是否纯音乐
    user_feedback: 用户的改进要求，如"再欢快一点"、"换成爵士风"、"去掉人声"
    score_context: 当前谱子的上下文（可选，用于辅助理解）
    返回: {"new_prompt": str, "new_style": str, "new_lyrics": str, "new_instrumental": bool,
           "changes": [...], "diff_summary": str, "needs_llm": bool}
    """
    # 先尝试规则匹配（快速、无 token 消耗）
    base_params = {
        "prompt": last_prompt,
        "style": last_style,
        "lyrics": last_lyrics,
        "instrumental": last_instrumental,
    }
    new_prompt, changes, new_params = _apply_rules(last_prompt, user_feedback, base_params)

    # 规则匹配命中了有效变化
    if changes:
        diff_summary = f"根据「{user_feedback}」调整：" + "；".join(changes)
        return {
            "new_prompt":       new_prompt,
            "new_style":        new_params.get("style", last_style),
            "new_lyrics":       last_lyrics,  # 歌词默认保留
            "new_instrumental": new_params.get("instrumental", last_instrumental),
            "changes":          changes,
            "diff_summary":     diff_summary,
            "needs_llm":        False,
            "method":           "rule_based",
        }

    # 规则没有命中 → 调用 LLM 处理复杂意图
    llm_prompt = f"""你是音乐 prompt 工程师。根据用户反馈改进音乐生成参数。

上次 prompt: {last_prompt}
上次风格标签: {last_style}
上次歌词: {last_lyrics[:200] if last_lyrics else '无'}
上次是否纯音乐: {last_instrumental}
{f'谱子背景: {score_context}' if score_context else ''}

用户反馈: {user_feedback}

请输出改进后的参数（JSON 格式）：
{{
  "new_prompt": "改进后的 prompt（英文，逗号分隔的描述词）",
  "new_style": "改进后的风格标签",
  "new_lyrics": "改进后的歌词（如需修改；否则返回空字符串）",
  "new_instrumental": true/false,
  "changes": ["改变1", "改变2"],
  "diff_summary": "一句话说明改了什么"
}}

规则：
- prompt 用英文描述词，逗号分隔
- 保留用户满意的部分，只改用户不满意的部分
- changes 用中文说明每个具体改动
"""
    import json
    raw = await complete([{"role": "user", "content": llm_prompt}], temperature=0.3)

    # 提取 JSON
    try:
        start = raw.find("{")
        end = raw.rfind("}") + 1
        result = json.loads(raw[start:end])
        result["needs_llm"] = True
        result["method"] = "llm"
        # 确保 new_lyrics 为空时保留原歌词
        if not result.get("new_lyrics"):
            result["new_lyrics"] = last_lyrics
        return result
    except Exception:
        # LLM 输出解析失败，返回原参数 + 错误说明
        return {
            "new_prompt":       last_prompt,
            "new_style":        last_style,
            "new_lyrics":       last_lyrics,
            "new_instrumental": last_instrumental,
            "changes":          [],
            "diff_summary":     f"无法解析「{user_feedback}」，请更具体描述",
            "needs_llm":        True,
            "method":           "fallback",
        }


@tool(group="audio")
def diff_audio_params(params_before: dict, params_after: dict) -> dict:
    """对比两次音频生成参数，生成可读的 diff 报告。
    params_before: 上一次生成参数 dict
    params_after: 本次生成参数 dict
    返回: {"diffs": [...], "summary": str}
    """
    diffs: list[str] = []
    fields = ["prompt", "style", "lyrics", "instrumental", "model", "provider"]

    for field in fields:
        before = params_before.get(field, "")
        after = params_after.get(field, "")
        if before != after:
            if field == "instrumental":
                diffs.append(
                    f"人声：{'纯音乐' if after else '有人声'}"
                    f"（原：{'纯音乐' if before else '有人声'}）"
                )
            elif field == "prompt":
                # 找出新增和删除的词
                words_before = set(w.strip().lower() for w in str(before).split(","))
                words_after  = set(w.strip().lower() for w in str(after).split(","))
                added   = words_after - words_before
                removed = words_before - words_after
                if added:
                    diffs.append(f"Prompt 新增：{', '.join(added)}")
                if removed:
                    diffs.append(f"Prompt 移除：{', '.join(removed)}")
            elif field == "style":
                diffs.append(f"风格：{after}（原：{before}）")
            elif field == "model":
                diffs.append(f"模型：{after}（原：{before}）")
            elif field == "provider":
                diffs.append(f"服务商：{after}（原：{before}）")

    summary = "；".join(diffs) if diffs else "参数未发生变化"
    return {"diffs": diffs, "summary": summary}
