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

稳定性设计（v3）：
  - ensure_all_agents_loaded() 每次都对失败模块重试（非一次性）
  - 失败模块记录在 _FAILED_MODULES，下次请求时自动重试
  - get_agent() 返回 None 时自动触发重试加载（懒加载兜底）
  - 任何 agent 文件语法错误不会导致其他 agent 失效
"""
from __future__ import annotations
import logging
import sys
import importlib
import traceback as _tb
import pkgutil
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from app.agentcore.agents.base_agent import BaseAgent
    from app.agentcore.run_context import RunContext

_logger = logging.getLogger("ep_agent.registry")

# domain → AgentClass 映射表
_REGISTRY: dict[str, type] = {}

# domain 别名映射（多个 domain 名指向同一 Agent）
_ALIASES: dict[str, str] = {}

# 上次加载失败的模块集合（下次调用时重试）
_FAILED_MODULES: set[str] = set()

# 关键 domain 列表（缺失时触发重试）
_REQUIRED_DOMAINS = frozenset({"create", "edit", "convert", "query", "audio", "sovits", "h5_create"})


def register(*domains: str):
    """装饰器：将 Agent 类注册到一个或多个 domain。"""
    def decorator(cls):
        for d in domains:
            _REGISTRY[d] = cls
            _logger.debug("注册 Agent: domain=%s → %s", d, cls.__name__)
        return cls
    return decorator


def get_agent(domain: str) -> type | None:
    """
    按 domain 获取 AgentClass。
    返回 None 时自动触发懒加载重试（稳定性兜底）。
    """
    # 直接匹配
    if domain in _REGISTRY:
        return _REGISTRY[domain]
    # 别名匹配
    canonical = _ALIASES.get(domain)
    if canonical and canonical in _REGISTRY:
        return _REGISTRY[canonical]

    # 未找到：如果该 domain 是必需的且有失败模块，自动重试加载
    if domain in _REQUIRED_DOMAINS and _FAILED_MODULES:
        _logger.warning(
            "[registry] domain='%s' 未注册，有 %d 个失败模块，触发重试加载: %s",
            domain, len(_FAILED_MODULES), sorted(_FAILED_MODULES),
        )
        _retry_failed_modules()
        # 重试后再查一次
        if domain in _REGISTRY:
            return _REGISTRY[domain]

    return None


def list_domains() -> list[str]:
    """返回所有已注册的 domain 列表（含别名展开）。"""
    return sorted(set(list(_REGISTRY.keys()) + list(_ALIASES.keys())))


def _load_one_module(full: str) -> bool:
    """
    加载单个模块。成功返回 True，失败返回 False 并记录到 _FAILED_MODULES。
    核心策略：先清除 sys.modules 中的失败残留，再重新 import。
    """
    # 清除旧的失败残留（不管是否完整，强制重新加载）
    if full in sys.modules:
        cached = sys.modules[full]
        # 判断是否完整加载：有 __spec__ 且 __spec__.loader 不为 None
        is_complete = (
            getattr(cached, "__file__", None) is not None
            and getattr(cached, "__spec__", None) is not None
        )
        if is_complete and full not in _FAILED_MODULES:
            # 已正常加载且不在失败列表，跳过
            return True
        # 否则清除缓存，准备重新加载
        del sys.modules[full]

    try:
        importlib.import_module(full)
        _FAILED_MODULES.discard(full)  # 成功则从失败列表移除
        return True
    except SyntaxError as e:
        _FAILED_MODULES.add(full)
        _logger.error(
            "Agent 模块语法错误: %s — %s (line %d)\n"
            "  请修复文件后重新请求，系统将自动重试加载。",
            full, e.msg, e.lineno or 0,
        )
        return False
    except Exception as e:
        _FAILED_MODULES.add(full)
        _logger.error(
            "Agent 模块加载失败: %s — %s\n%s",
            full, e, _tb.format_exc(),
        )
        return False


def _retry_failed_modules():
    """重试所有失败模块的加载（在 get_agent 找不到时调用）。"""
    if not _FAILED_MODULES:
        return
    to_retry = set(_FAILED_MODULES)  # 快照，避免迭代时修改
    _logger.info("[registry] 重试加载失败模块: %s", sorted(to_retry))
    for full in to_retry:
        _load_one_module(full)


def ensure_all_agents_loaded():
    """
    扫描并加载 agents/ 目录下所有模块，触发 @register 装饰器。

    v3 稳定性设计：
    - 每次调用都会重试上次失败的模块（文件修复后无需重启）
    - 失败模块记录在 _FAILED_MODULES，get_agent() 找不到时自动重试
    - 语法错误只影响该文件，不影响其他 agent
    - 加载完成后打印注册状态，缺失 domain 打 ERROR（便于排查）
    """
    agents_dir = Path(__file__).parent / "agents"
    pkg = "app.agentcore.agents"

    for mod_info in pkgutil.iter_modules([str(agents_dir)]):
        full = f"{pkg}.{mod_info.name}"
        _load_one_module(full)

    # 加载完成后验证关键 domain
    missing = _REQUIRED_DOMAINS - set(_REGISTRY.keys())
    if missing:
        _logger.error(
            "Agent 注册不完整！缺失 domain: %s — 已注册: %s — 失败模块: %s",
            sorted(missing), sorted(_REGISTRY.keys()), sorted(_FAILED_MODULES),
        )
    else:
        _logger.info(
            "Agent 注册完成，已注册 domains: %s",
            sorted(_REGISTRY.keys()),
        )
