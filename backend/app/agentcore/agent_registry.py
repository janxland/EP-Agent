"""
Agent 注册表 — 消灭 _dispatch() 中的 if-elif domain 链（v4.0 解耦架构）

使用方式：
    # 注册（在各 agent.py 文件顶部）
    @register("edit")
    class EditAgent(BaseAgent):
        async def run(self, ctx: RunContext) -> dict: ...

    # 分发（在 universal_runner._dispatch 中）
    AgentClass = get_agent(domain)
    result = await AgentClass().run(ctx)

扩展新 domain：只需新建 agent 文件 + @register，无需修改 _dispatch。
"""
from __future__ import annotations
import logging
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from app.agentcore.agents.base_agent import BaseAgent
    from app.agentcore.run_context import RunContext

_logger = logging.getLogger("ep_agent.registry")

# domain → AgentClass 映射表
_REGISTRY: dict[str, type] = {}

# domain 别名映射（多个 domain 名指向同一 Agent）
_ALIASES: dict[str, str] = {
    "voice": "audio",       # voice 域 → AudioAgent
}


def register(*domains: str):
    """
    装饰器：将 Agent 类注册到一个或多个 domain。

    示例：
        @register("edit")
        class EditAgent(BaseAgent): ...

        @register("audio", "voice")
        class AudioAgent(BaseAgent): ...
    """
    def decorator(cls):
        for d in domains:
            if d in _REGISTRY:
                _logger.warning(
                    "domain '%s' 已注册为 %s，现被 %s 覆盖",
                    d, _REGISTRY[d].__name__, cls.__name__
                )
            _REGISTRY[d] = cls
            _logger.debug("注册 Agent: domain=%s → %s", d, cls.__name__)
        return cls
    return decorator


def get_agent(domain: str) -> type | None:
    """
    按 domain 获取 AgentClass。
    先查注册表，再查别名，找不到返回 None（调用方负责兜底到 QueryAgent）。
    """
    # 直接匹配
    if domain in _REGISTRY:
        return _REGISTRY[domain]
    # 别名匹配
    canonical = _ALIASES.get(domain)
    if canonical and canonical in _REGISTRY:
        return _REGISTRY[canonical]
    return None


def list_domains() -> list[str]:
    """返回所有已注册的 domain 列表（含别名展开）。"""
    return sorted(set(list(_REGISTRY.keys()) + list(_ALIASES.keys())))


def ensure_all_agents_loaded():
    """
    强制 import 所有 agents/ 下的模块，触发 @register 装饰器执行。
    在 universal_runner 模块加载时调用一次即可。
    """
    import importlib
    import pkgutil
    from pathlib import Path

    agents_dir = Path(__file__).parent / "agents"
    pkg = "app.agentcore.agents"
    for mod_info in pkgutil.iter_modules([str(agents_dir)]):
        full = f"{pkg}.{mod_info.name}"
        try:
            importlib.import_module(full)
        except Exception as e:
            _logger.warning("Agent 模块加载失败: %s — %s", full, e)
