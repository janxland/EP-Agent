"""
Agent Loader — 运行时加载 .agent 文件作为 system prompt

设计目标：
  - 消除 Runner 层的硬编码 system prompt 字符串
  - 支持热更新：修改 .agent 文件后无需重启服务
  - 支持模板变量渲染：{{ @variable("key") }} → 实际值
  - 支持分区提取：role / static_context / workflow / output_contract

.agent 文件格式：
  ---
  name: score-editor
  description: ...
  tools: [...]
  max_steps: 1
  ---

  <role>
  角色定义...
  </role>

  <static_context>
  静态知识...
  </static_context>

  <dynamic_context>
  {{ @variable("current_abc") }}
  </dynamic_context>

  <workflow>
  工作流步骤...
  </workflow>

  <output_contract>
  输出约定...
  </output_contract>

使用示例：
    from app.agentcore.agent_loader import load_agent_prompt

    # 加载完整 system prompt（role + static_context）
    system = load_agent_prompt("score-editor")

    # 加载并渲染动态变量
    system = load_agent_prompt("score-editor", sections=["role", "static_context"])

    # 渲染 dynamic_context 作为 user prompt 补充
    dynamic = load_agent_prompt(
        "score-editor",
        sections=["dynamic_context"],
        variables={"intent": "转调到G大调", "current_abc": "X:1\\nT:..."},
    )
"""
from __future__ import annotations

import re
from pathlib import Path
from typing import Sequence

# ─── 路径配置 ─────────────────────────────────────────────────────────────────

# 默认 agents 目录：backend/agent/agents/
_DEFAULT_AGENTS_DIR = Path(__file__).resolve().parent.parent.parent / "agent" / "agents"


# ─── 内部解析器 ───────────────────────────────────────────────────────────────

def _strip_frontmatter(text: str) -> tuple[dict, str]:
    """
    剥离 YAML frontmatter（--- ... ---），返回 (meta_dict, body)。
    meta_dict 仅做简单 key:value 解析，不依赖 PyYAML。
    """
    meta: dict = {}
    body = text

    if text.startswith("---"):
        end = text.find("\n---", 3)
        if end != -1:
            fm_block = text[3:end].strip()
            body = text[end + 4:].lstrip("\n")
            for line in fm_block.splitlines():
                if ":" in line:
                    k, _, v = line.partition(":")
                    meta[k.strip()] = v.strip()

    return meta, body


def _extract_section(body: str, section: str) -> str:
    """
    从 body 中提取 <section>...</section> 标签内容。
    返回空字符串表示该 section 不存在。
    """
    pattern = rf"<{section}>(.*?)</{section}>"
    m = re.search(pattern, body, re.DOTALL)
    if m:
        return m.group(1).strip()
    return ""


def _render_variables(text: str, variables: dict[str, str]) -> str:
    """
    渲染模板变量：{{ @variable("key") }} → variables[key]
    未找到的变量保留原样（不报错）。
    """
    def replacer(m: re.Match) -> str:
        key = m.group(1)
        return str(variables.get(key, m.group(0)))

    return re.sub(r'\{\{\s*@variable\("([^"]+)"\)\s*\}\}', replacer, text)


# ─── 公开 API ─────────────────────────────────────────────────────────────────

def load_agent_prompt(
    agent_name: str,
    *,
    sections: Sequence[str] = ("role", "static_context"),
    variables: dict[str, str] | None = None,
    agents_dir: Path | None = None,
) -> str:
    """
    加载并返回 .agent 文件的 system prompt 字符串。

    参数：
        agent_name:  agent 文件名（不含 .agent 后缀），如 "score-editor"
        sections:    要提取并拼接的标签列表，默认 ["role", "static_context"]
        variables:   模板变量字典，用于渲染 {{ @variable("key") }}
        agents_dir:  自定义 agents 目录路径（默认 backend/agent/agents/）

    返回：
        拼接后的 prompt 字符串（各 section 之间用空行分隔）

    异常：
        FileNotFoundError：找不到 .agent 文件时抛出
    """
    dir_path = agents_dir or _DEFAULT_AGENTS_DIR
    agent_path = dir_path / f"{agent_name}.agent"

    if not agent_path.exists():
        raise FileNotFoundError(
            f"Agent file not found: {agent_path}\n"
            f"Available: {[p.stem for p in dir_path.glob('*.agent')]}"
        )

    # 热更新：每次调用都重新读文件
    raw = agent_path.read_text(encoding="utf-8")
    _meta, body = _strip_frontmatter(raw)

    parts: list[str] = []
    for section in sections:
        content = _extract_section(body, section)
        if content:
            parts.append(content)

    prompt = "\n\n".join(parts)

    # 渲染模板变量
    if variables:
        prompt = _render_variables(prompt, variables)

    return prompt


def load_agent_meta(
    agent_name: str,
    *,
    agents_dir: Path | None = None,
) -> dict:
    """
    加载 .agent 文件的 frontmatter 元数据（name/description/tools/max_steps）。

    返回示例：
        {"name": "score-editor", "description": "...", "tools": "...", "max_steps": "1"}
    """
    dir_path = agents_dir or _DEFAULT_AGENTS_DIR
    agent_path = dir_path / f"{agent_name}.agent"

    if not agent_path.exists():
        raise FileNotFoundError(f"Agent file not found: {agent_path}")

    raw = agent_path.read_text(encoding="utf-8")
    meta, _ = _strip_frontmatter(raw)
    return meta


def list_agents(agents_dir: Path | None = None) -> list[str]:
    """列出所有可用的 agent 名称（不含 .agent 后缀）"""
    dir_path = agents_dir or _DEFAULT_AGENTS_DIR
    if not dir_path.exists():
        return []
    return [p.stem for p in sorted(dir_path.glob("*.agent"))]
