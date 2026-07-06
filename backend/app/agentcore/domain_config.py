"""
DomainConfig — 意图域配置中心（单一来源）

所有意图域的元数据在此统一定义，消除以下漂移：
  - intent_router.py 的 _ROUTER_SYSTEM 意图域描述
  - todo_manager.py 的 _TODO_SYSTEM TODO 模板
  - universal_runner.py 的 _dispatch if/elif 分支
  - 前端 TodoListCard / ChatPanel 的 DOMAIN_LABEL 映射

扩展新意图域（如 sovits）只需在 DOMAIN_CONFIG 中添加一条记录，
其他文件通过 get_domain / list_domains / build_router_prompt / build_todo_prompt
动态读取，无需手动同步。
"""
from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class DomainMeta:
    name: str            # 意图域标识（英文，与 SSE domain 字段一致）
    label: str           # 中文名称（前端展示）
    icon: str            # emoji 图标
    description: str     # 路由器判断依据（注入 _ROUTER_SYSTEM）
    todo_template: str   # TODO 规划模板（注入 _TODO_SYSTEM）
    agent_class: str     # 对应 SubAgent 类名（universal_runner 动态加载）
    tool_groups: list[str] = field(default_factory=list)  # 关联工具分组
    requires_score: bool = False   # 是否需要已有谱子（edit 域）
    enabled: bool = True           # 是否启用（False = 未配置/未部署时跳过）


# ── 意图域注册表（按路由优先级排列）─────────────────────────────────────────────

DOMAIN_CONFIG: dict[str, DomainMeta] = {

    "convert": DomainMeta(
        name="convert",
        label="解析谱子",
        icon="🎮",
        description=(
            "用户提供了 Sky 游戏谱子文件。判断依据：\n"
            "  * 附件名含 .txt/.json 且内容含 songNotes/key/bpm/pitchLevel 等字段\n"
            "  * 消息含 songNotes 字段或 JSON 数组格式谱子\n"
            "  ⚠️ Sky 谱子常以 .txt 格式导出，不要因为扩展名是 .txt 就误判为 create！\n"
            "  ⚠️ 只要附件内容含有 songNotes 字段，无论扩展名，必须路由到 convert！"
        ),
        todo_template="[1]告知用户转换结果（1个TODO即可，转换/保存/验证均由Python代码层自动完成，LLM只需说明结果）",
        agent_class="ConvertAgent",
        tool_groups=["abc_edit"],
    ),

    "edit": DomainMeta(
        name="edit",
        label="编辑谱子",
        icon="✏️",
        description="修改已有谱子（转调/变速/风格/加花等），需已有谱子",
        todo_template="[1]获取ABC并执行修改 [2]按需导出（1-2个TODO，保存由Python层自动完成无需列为TODO，简单编辑无导出需求时只需1个TODO）",
        agent_class="EditAgent",
        tool_groups=["abc_edit"],
        requires_score=True,
    ),

    "create": DomainMeta(
        name="create",
        label="创作谱子",
        icon="🎵",
        description=(
            "从零创作 ABC 谱子（用户描述音乐风格/旋律/情感等，无附件谱子）。\n"
            "注意：若有附件且含 songNotes，应路由到 convert 而非 create"
        ),
        todo_template="[1]创作ABC谱子 [2]按需导出（1-2个TODO，保存由Python层自动完成无需列为TODO，无导出需求时只需1个TODO，禁止把「构思」「验证」「保存」单独列为TODO）",
        agent_class="CreateAgent",
        tool_groups=["abc_edit"],
    ),

    "audio": DomainMeta(
        name="audio",
        label="生成音频",
        icon="🎧",
        description="生成/迭代音频（「生成配乐」/「再欢快一点」/「翻唱」等）",
        todo_template="[1]生成音频（2个TODO即可，禁止拆分「分析」「调用」「等待」为多个TODO）",
        agent_class="AudioAgent",
        tool_groups=["audio"],
    ),

    "voice": DomainMeta(
        name="voice",
        label="MiniMax音色",
        icon="🎤",
        description=(
            "MiniMax 云端音色克隆（用户明确指定 MiniMax，或 GPT-SoVITS 不可用时的路径）。\n"
            "⚠️ 用户说「用 MiniMax」/「MiniMax 克隆」/「用云端克隆」→ 必须路由到 voice 域，不得路由到 sovits！\n"
            "⚠️ 若用户明确提到「GPT-SoVITS」/「SoVITS」/「本地克隆」→ 路由到 sovits 域。\n"
            "关键词：「MiniMax 克隆」/「用 MiniMax」/「云端音色」/「查看已克隆音色列表」"
        ),
        todo_template="[1]克隆/合成音频 [2]保存结果（2个TODO，禁止冗余步骤）",
        agent_class="AudioAgent",
        tool_groups=["audio"],
    ),

    # ── GPT-SoVITS 音色克隆（本地部署优先，MiniMax 降级）────────────────────────
    "sovits": DomainMeta(
        name="sovits",
        label="音色克隆",
        icon="🎙️",
        description=(
            "音色克隆 / 语音合成（GPT-SoVITS 本地优先，MiniMax 降级）。\n"
            "关键词：「克隆声音」/「用我的声音」/「音色克隆」/「语音合成」/\n"
            "        「TTS」/「文字转语音」/「合成语音」/「声音克隆」/\n"
            "        「GPT-SoVITS」/「SoVITS」/「查看音色模型」\n"
            "附件含 .wav/.mp3/.m4a 且用户提到「克隆」/「声音」时优先路由到此域\n"
            "⚠️ 用户明确说「用 MiniMax」时不路由到此域，应路由到 voice 域"
        ),
        todo_template="[1]克隆音色 [2]合成/保存（2个TODO，禁止把「查询」「等待」单独列出）",
        agent_class="VoiceCloneAgent",
        tool_groups=["sovits"],
    ),

    "query": DomainMeta(
        name="query",
        label="查询分析",
        icon="🔍",
        description="查询/分析谱子信息（「这首是什么调」/「有多少音符」等）",
        todo_template="[1]回答用户（1个TODO即可，QueryAgent直接LLM回答，谱子上下文已自动注入，无需工具调用）",
        agent_class="QueryAgent",
        tool_groups=[],
    ),

    # ── H5 乐谱页面生成（播放器 / 海报 / 可视化）─────────────────────────────
    "h5_create": DomainMeta(
        name="h5_create",
        label="H5 页面",
        icon="🎨",
        description=(
            "生成 HTML 页面（播放器/海报/可视化），支持 MIDI/ABC/Sky JSON 输入。\n"
            "关键词：「H5」/「HTML」/「网页」/「页面」/「播放器」/「播放MIDI」/\n"
            "        「MIDI播放」/「海报」/「分享页」/「乐谱页面」/「生成页面」\n"
            "附件含 .mid/.midi/.abc 扩展名时优先路由到此域\n"
            "⚠️ 用户说「生成一个HTML播放MIDI」「做一个播放页面」均路由到此域"
        ),
        todo_template="[1]解析乐谱 [2]生成HTML页面 [3]保存文件（3个TODO，禁止把「渲染」「样式」单独拆出）",
        agent_class="H5Agent",
        tool_groups=["h5"],
    ),

    # ── H5 页面编辑 ──────────────────────────────────────────────────────────
    "h5_edit": DomainMeta(
        name="h5_edit",
        label="H5 编辑",
        icon="🖌️",
        description=(
            "编辑已生成的 H5 页面（更换模板/修改标题/调整样式/切换播放器）。\n"
            "关键词：「换个模板」/「改成暗色」/「修改标题」/「重新生成」"
        ),
        todo_template="[1]读取H5并应用修改 [2]保存（2个TODO，读取和修改合并为一步）",
        agent_class="H5Agent",
        tool_groups=["h5"],
    ),

}


# ── 查询接口 ─────────────────────────────────────────────────────────────────

def get_domain(name: str) -> DomainMeta | None:
    """按名称获取意图域配置（不存在返回 None）。"""
    return DOMAIN_CONFIG.get(name)


def list_domains(enabled_only: bool = True) -> list[DomainMeta]:
    """列出所有（或仅启用的）意图域。"""
    domains = list(DOMAIN_CONFIG.values())
    if enabled_only:
        domains = [d for d in domains if d.enabled]
    return domains


def build_router_prompt() -> str:
    """
    动态生成 IntentRouter 的意图域描述段落。
    intent_router.py 调用此函数构建 _ROUTER_SYSTEM，消除手动维护漂移。
    """
    lines = []
    for d in list_domains(enabled_only=True):
        # 多行 description 缩进对齐
        desc_lines = d.description.strip().splitlines()
        first = desc_lines[0]
        rest  = "\n".join(f"    {l}" for l in desc_lines[1:])
        entry = f"- {d.name:<10}: {first}"
        if rest:
            entry += "\n" + rest
        lines.append(entry)
    return "\n".join(lines)


def build_todo_prompt() -> str:
    """
    动态生成 TodoManager 的意图域 TODO 模板段落。
    todo_manager.py 调用此函数构建 _TODO_SYSTEM，消除手动维护漂移。
    """
    lines = []
    for d in list_domains(enabled_only=True):
        lines.append(f"- {d.name} 域：{d.todo_template}")
    return "\n".join(lines)


def to_frontend_map() -> list[dict]:
    """
    生成前端 DOMAIN_LABEL 格式的 JSON，供 /health/tools 端点返回。
    前端从此接口动态获取意图域配置，消除前后端各自维护的漂移。
    """
    return [
        {
            "name":    d.name,
            "label":   d.label,
            "icon":    d.icon,
            "enabled": d.enabled,
        }
        for d in list(DOMAIN_CONFIG.values())
    ]
