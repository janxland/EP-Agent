"""
RoleConfig — 角色配置中心（单一来源）

角色（Role）= 意图域子集 + 专属系统 Prompt 风格 + 工具权限 + 前端 UI 主题

设计原则：
  - 每个角色声明自己「擅长的意图域」，路由器只在这些域中判断意图
  - 角色存在 session 级别（切换角色不丢失对话，只改变后续行为）
  - 未来新增角色（PPT专家/音色克隆专家）只需在 ROLE_CONFIG 中添加一条记录
  - 角色不感知具体执行逻辑，只影响「路由范围」和「Prompt 风格」

扩展新角色（只需两步）：
  Step 1：在 ROLE_CONFIG 中添加 RoleMeta 记录
  Step 2：（可选）新建对应的 .agent 文件，提供专属 system prompt
  无需修改任何执行逻辑。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal


@dataclass
class RoleMeta:
    id: str                      # 角色标识（英文，URL-safe）
    name: str                    # 角色名称（前端展示）
    tagline: str                 # 一句话简介（前端卡片副标题）
    icon: str                    # emoji 图标
    color: str                   # 主题色（Tailwind 颜色名，如 orange/blue/purple）
    domains: list[str]           # 该角色擅长的意图域（domain_config.py 中的 name）
    system_prompt_extra: str     # 注入到每次对话的角色专属 prompt 补充
    greeting: str                # 首次对话时的欢迎语（前端展示）
    capabilities: list[str]      # 能力标签列表（前端展示）
    agent_file: str = ""         # 对应的 .agent 文件名（可选，热加载专属 prompt）
    enabled: bool = True         # 是否启用


# ── 角色注册表 ──────────────────────────────────────────────────────────────────

ROLE_CONFIG: dict[str, RoleMeta] = {

    # ── 🎵 Sky 乐谱专家（当前主角色，默认）────────────────────────────────────
    "abc_expert": RoleMeta(
        id="abc_expert",
        name="Sky 乐谱专家",
        tagline="Sky 游戏乐谱转换、编辑与创作",
        icon="🎵",
        color="orange",
        domains=["convert", "edit", "create", "query"],
        system_prompt_extra=(
            "你是 Sky: Children of the Light 游戏的专业乐谱助手。\n"
            "专长：Sky 谱子解析、ABC Notation 编辑、乐谱创作、音乐理论。\n"
            "始终保持专业音乐家的视角，用简洁中文回答，避免技术术语堆砌。"
        ),
        greeting=(
            "你好！我是 Sky 乐谱专家 🎵\n\n"
            "我可以帮你：\n"
            "- 📂 **解析谱子**：上传 Sky 游戏导出的 .txt 谱子文件\n"
            "- ✏️ **编辑谱子**：转调、变速、改风格、加花…\n"
            "- 🎼 **创作谱子**：描述你想要的风格，我来创作\n"
            "- 🔍 **分析谱子**：查询调号、音符数、结构分析\n\n"
            "上传谱子文件或直接告诉我你想做什么吧！"
        ),
        capabilities=["Sky 谱解析", "ABC 编辑", "乐谱创作", "音乐分析"],
        agent_file="music-router",
    ),

    # ── 🎧 音乐生成专家 ────────────────────────────────────────────────────────
    "music_producer": RoleMeta(
        id="music_producer",
        name="音乐生成专家",
        tagline="AI 配乐生成、音频迭代与风格转换",
        icon="🎧",
        color="blue",
        domains=["audio", "voice", "create"],
        system_prompt_extra=(
            "你是专业的 AI 音乐制作人，擅长用 AI 工具生成高质量配乐。\n"
            "专长：MiniMax/Suno 音频生成、风格迭代、配乐建议、音频质量评估。\n"
            "用制作人的视角给出专业建议，注重情感表达和风格一致性。"
        ),
        greeting=(
            "你好！我是音乐生成专家 🎧\n\n"
            "我可以帮你：\n"
            "- 🎼 **生成配乐**：描述风格/情感，AI 自动生成音乐\n"
            "- 🔄 **迭代改进**：对已生成的音频进行风格调整\n"
            "- 🎤 **翻唱 Cover**：将现有音频转换为不同风格\n"
            "- 🎵 **创作旋律**：先创作 ABC 谱再生成音频\n\n"
            "告诉我你想要什么风格的音乐吧！"
        ),
        capabilities=["AI 配乐生成", "风格迭代", "翻唱 Cover", "旋律创作"],
        agent_file="",
    ),

    # ── 🎤 音色克隆专家 ────────────────────────────────────────────────────────
    "voice_cloner": RoleMeta(
        id="voice_cloner",
        name="音色克隆专家",
        tagline="声音克隆、TTS 合成与音色管理",
        icon="🎤",
        color="purple",
        domains=["voice", "sovits"],
        system_prompt_extra=(
            "你是专业的音色克隆和 TTS 合成专家。\n"
            "专长：零样本音色克隆、高质量 TTS 合成、音色库管理、GPT-SoVITS 接入。\n"
            "注重音色的自然度和情感表达，给出专业的录音和合成建议。"
        ),
        greeting=(
            "你好！我是音色克隆专家 🎤\n\n"
            "我可以帮你：\n"
            "- 🔊 **克隆音色**：上传参考音频，提取音色特征\n"
            "- 🗣️ **TTS 合成**：用克隆的音色朗读任意文本\n"
            "- 📚 **管理音色库**：查看、命名、删除已保存的音色\n"
            "- 🎙️ **SoVITS 高质量克隆**：零样本高保真音色克隆\n\n"
            "上传一段 5-30 秒的参考音频开始克隆吧！"
        ),
        capabilities=["音色克隆", "TTS 合成", "音色库管理", "SoVITS"],
        agent_file="",
        enabled=True,  # 即使 sovits 未配置，基础 voice 功能仍可用
    ),

    # ── 🎨 H5 设计专家 ────────────────────────────────────────────────────────
    "h5_designer": RoleMeta(
        id="h5_designer",
        name="H5 设计专家",
        tagline="乐谱 H5 海报设计与生成",
        icon="🎨",
        color="pink",
        domains=["h5_create", "h5_edit", "create"],
        system_prompt_extra=(
            "你是专业的 H5 乐谱海报设计师，擅长将乐谱转化为精美的移动端分享页面。\n"
            "专长：MIDI/ABC/Sky JSON 解析、苹果风格 H5 设计、音乐可视化、移动端分享优化。\n"
            "注重视觉美感和交互体验，生成的 H5 页面应该让人眼前一亮。"
        ),
        greeting=(
            "你好！我是 H5 设计专家 🎨\n\n"
            "我可以帮你：\n"
            "- 🎵 **MIDI 转海报**：上传 .mid 文件，生成带播放器的 H5 海报\n"
            "- 📝 **ABC 转海报**：将 ABC 乐谱渲染为精美 H5 页面\n"
            "- 🎮 **Sky JSON 转海报**：Sky 游戏谱子一键生成分享海报\n"
            "- 🎨 **多种模板**：苹果暗色/亮色/霓虹/极简，随心切换\n\n"
            "上传乐谱文件或告诉我你想要什么风格的海报吧！"
        ),
        capabilities=["MIDI 解析", "ABC 渲染", "H5 生成", "苹果风格设计", "移动端分享"],
        agent_file="h5-designer",
        enabled=True,
    ),

    # ── 📊 PPT 专家（预留）────────────────────────────────────────────────────
    "ppt_expert": RoleMeta(
        id="ppt_expert",
        name="PPT 专家",
        tagline="演示文稿设计、内容规划与自动生成",
        icon="📊",
        color="green",
        domains=["ppt_create", "ppt_edit", "ppt_design"],  # 待实现的意图域
        system_prompt_extra=(
            "你是专业的演示文稿设计师，擅长结构化内容和视觉设计。\n"
            "专长：PPT 结构规划、内容提炼、视觉设计建议、自动生成幻灯片。\n"
            "注重逻辑清晰、视觉美观、信息密度适中。"
        ),
        greeting=(
            "你好！我是 PPT 专家 📊\n\n"
            "我可以帮你：\n"
            "- 📝 **规划结构**：根据主题自动生成大纲\n"
            "- 🎨 **设计建议**：配色方案、排版风格推荐\n"
            "- ✨ **自动生成**：一键生成完整 PPT\n"
            "- ✏️ **编辑优化**：修改内容、调整风格\n\n"
            "告诉我你要做什么主题的 PPT 吧！"
        ),
        capabilities=["结构规划", "内容提炼", "设计建议", "自动生成"],
        agent_file="",
        enabled=False,  # 待实现
    ),
}

# ── 默认角色 ──────────────────────────────────────────────────────────────────
DEFAULT_ROLE_ID = "abc_expert"


# ── 查询接口 ──────────────────────────────────────────────────────────────────

def get_role(role_id: str) -> RoleMeta | None:
    """按 ID 获取角色配置（不存在返回 None）。"""
    return ROLE_CONFIG.get(role_id)


def get_role_or_default(role_id: str | None) -> RoleMeta:
    """获取角色，不存在时返回默认角色。"""
    if role_id:
        role = ROLE_CONFIG.get(role_id)
        if role and role.enabled:
            return role
    return ROLE_CONFIG[DEFAULT_ROLE_ID]


def list_roles(enabled_only: bool = True) -> list[RoleMeta]:
    """列出所有（或仅启用的）角色。"""
    roles = list(ROLE_CONFIG.values())
    if enabled_only:
        roles = [r for r in roles if r.enabled]
    return roles


def get_role_domains(role_id: str | None) -> list[str]:
    """
    获取角色的意图域列表（路由器用此过滤可路由的域）。
    未指定角色时返回所有启用域。
    """
    role = get_role_or_default(role_id)
    return role.domains


def build_role_system_prompt(role_id: str | None) -> str:
    """
    生成角色专属的 system prompt 补充段落。
    注入到每次对话的 system message 开头，赋予 LLM 角色人格。
    """
    role = get_role_or_default(role_id)
    return role.system_prompt_extra


def to_frontend_list() -> list[dict]:
    """
    生成前端角色列表 JSON，供 /api/roles 端点返回。
    """
    return [
        {
            "id":           r.id,
            "name":         r.name,
            "tagline":      r.tagline,
            "icon":         r.icon,
            "color":        r.color,
            "capabilities": r.capabilities,
            "greeting":     r.greeting,
            "enabled":      r.enabled,
            "domains":      r.domains,
        }
        for r in list(ROLE_CONFIG.values())
    ]
