"""
BaseAgent — 所有 SubAgent 的抽象基类（v4.0 解耦架构）

设计原则：
  - 统一接口：所有 Agent 只接收 RunContext，不再接收 13 个散参数
  - 向后兼容：旧 Agent 可保留原有 run(**kwargs) 签名，
    通过 _run_legacy() 适配层调用，逐步迁移
  - 生命周期钩子：before_run / after_run 供子类扩展（日志/监控）

迁移路径：
  Phase 1（当前）：BaseAgent 提供适配层，旧 Agent 无需改动
  Phase 2：各 Agent 逐步实现 run(ctx) 并删除 _run_legacy
"""
from __future__ import annotations

import logging
from abc import ABC, abstractmethod
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from app.agentcore.run_context import RunContext

_logger = logging.getLogger("ep_agent.base_agent")


class BaseAgent(ABC):
    """
    所有 SubAgent 的基类。

    子类实现 run(ctx) 即可，框架负责：
      - 调用前后的日志记录
      - 异常统一包装（不吞异常，确保上层能感知）
    """

    async def execute(self, ctx: "RunContext") -> dict:
        """
        框架入口（由 _dispatch 调用）。
        包含生命周期钩子，子类实现 run() 即可。
        """
        agent_name = self.__class__.__name__
        _logger.info(
            "[trace=%s] %s.execute 开始 domain=%s",
            ctx.trace_id[:8] if ctx.trace_id else "?",
            agent_name,
            ctx.domain,
        )
        try:
            result = await self.run(ctx)
            _logger.info(
                "[trace=%s] %s.execute 完成",
                ctx.trace_id[:8] if ctx.trace_id else "?",
                agent_name,
            )
            return result or {}
        except Exception as e:
            _logger.error(
                "[trace=%s] %s.execute 异常: %s",
                ctx.trace_id[:8] if ctx.trace_id else "?",
                agent_name,
                e,
                exc_info=True,
            )
            raise

    @abstractmethod
    async def run(self, ctx: "RunContext") -> dict:
        """
        子类实现：执行 Agent 核心逻辑。

        从 ctx 读取所有需要的字段：
          ctx.session_id / ctx.message / ctx.publish
          ctx.workspace_id / ctx.project_id
          ctx.attachment_* / ctx.domain / ctx.has_score
          ctx.extra（链式意图传递的中间结果）

        返回结果 dict（格式由各 Agent 自定义）。
        """
        ...
