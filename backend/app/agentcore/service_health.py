"""
服务可用性检测系统 v1.0
Service Health Check — 全局单例，动态维护所有外部服务的配置状态。

设计原则：
- 只检查「配置是否存在且格式合理」，不做真实 HTTP 探活（避免启动时额外延迟）
- 支持运行时热刷新（config 变更后调用 refresh() 即可）
- 结果注入 Agent 提示词，让 LLM 在推理阶段直接跳过不可用工具，
  消除「调用→失败→重新推理降级」的无效轮次

覆盖的服务（可按需扩展）：
  音频生成：suno / minimax_music / minimax_lyrics / minimax_cover
  语音合成：sovits
  核心 LLM：llm

使用方式：
  from app.agentcore.service_health import service_health

  # 获取可用服务列表（字符串）
  available = service_health.available_str()   # "minimax_music, minimax_lyrics"

  # 获取注入提示词的文本块
  block = service_health.prompt_block()

  # 强制刷新（config 热更新后调用）
  service_health.refresh()
"""
from __future__ import annotations

import logging
import threading
import time
from dataclasses import dataclass, field
from typing import Callable

logger = logging.getLogger("ep_agent.service_health")


# ─── 服务描述 ─────────────────────────────────────────────────────────────────

@dataclass
class ServiceDescriptor:
    """描述一个外部服务的检测规则"""
    name: str                          # 服务标识，如 "suno"
    display_name: str                  # 人类可读名，如 "Suno AI 音乐生成"
    tools: list[str]                   # 该服务对应的工具函数名列表
    check_fn: Callable[[], bool]       # 检测函数，返回 True=可用
    description: str = ""              # 服务功能说明（注入提示词时使用）


@dataclass
class ServiceStatus:
    name: str
    display_name: str
    tools: list[str]
    available: bool
    reason: str                        # 不可用时的简短原因
    checked_at: float = field(default_factory=time.time)


# ─── 检测函数工厂 ─────────────────────────────────────────────────────────────

def _key_check(get_key_fn: Callable[[], str], min_len: int = 8) -> Callable[[], bool]:
    """通用：检查 API Key 是否存在且长度合理"""
    def _check() -> bool:
        try:
            key = get_key_fn()
            return bool(key and len(key.strip()) >= min_len)
        except Exception:
            return False
    return _check


def _url_and_key_check(
    get_key_fn: Callable[[], str],
    get_url_fn: Callable[[], str],
    min_len: int = 8,
) -> Callable[[], bool]:
    """通用：检查 API Key + Base URL 均有效"""
    def _check() -> bool:
        try:
            key = get_key_fn()
            url = get_url_fn()
            return (
                bool(key and len(key.strip()) >= min_len)
                and bool(url and url.startswith("http"))
            )
        except Exception:
            return False
    return _check


# ─── 服务注册表 ───────────────────────────────────────────────────────────────

def _build_registry() -> list[ServiceDescriptor]:
    """构建服务描述注册表（延迟 import config，避免循环依赖）"""
    from app.config import config

    return [
        # ── 音频生成 ──────────────────────────────────────────────────────────
        ServiceDescriptor(
            name="suno",
            display_name="Suno AI 歌曲生成",
            tools=["generate_audio_suno"],
            check_fn=_url_and_key_check(
                lambda: config.SUNO_API_KEY,
                lambda: config.SUNO_BASE_URL,
            ),
            description="支持自定义歌词/风格生成完整歌曲，异步轮询，耗时约 60-120s",
        ),
        ServiceDescriptor(
            name="minimax_music",
            display_name="MiniMax 音乐生成",
            tools=["generate_audio_minimax"],
            check_fn=_url_and_key_check(
                lambda: config.MINIMAX_API_KEY,
                lambda: config.MINIMAX_BASE_URL,
            ),
            description="同步生成，支持歌词/纯音乐，自动落盘，耗时约 30-90s",
        ),
        ServiceDescriptor(
            name="minimax_lyrics",
            display_name="MiniMax 歌词生成",
            tools=["generate_lyrics_minimax"],
            check_fn=_url_and_key_check(
                lambda: config.MINIMAX_API_KEY,
                lambda: config.MINIMAX_BASE_URL,
            ),
            description="根据主题自动生成带 Verse/Chorus 结构的歌词，耗时约 10-30s",
        ),
        ServiceDescriptor(
            name="minimax_cover",
            display_name="MiniMax AI 翻唱",
            tools=["generate_cover_minimax"],
            check_fn=_url_and_key_check(
                lambda: config.MINIMAX_API_KEY,
                lambda: config.MINIMAX_BASE_URL,
            ),
            description="对已有音频进行 AI 风格翻唱/转换",
        ),
        # ── 语音合成 ──────────────────────────────────────────────────────────
        ServiceDescriptor(
            name="sovits",
            display_name="GPT-SoVITS 语音合成",
            tools=["synthesize_speech_sovits", "clone_voice_sovits"],
            check_fn=_url_and_key_check(
                lambda: config.SOVITS_BASE_URL or "http://placeholder",  # URL 即凭证
                lambda: config.SOVITS_BASE_URL,
                min_len=4,
            ),
            description="本地部署语音克隆/合成，需自行部署 GPT-SoVITS 服务",
        ),
        # ── 核心 LLM ──────────────────────────────────────────────────────────
        ServiceDescriptor(
            name="llm",
            display_name="核心 LLM",
            tools=["__llm__"],
            check_fn=_key_check(lambda: config.LLM_API_KEY, min_len=8),
            description="主推理模型，所有 Agent 依赖",
        ),
    ]


# ─── 全局健康管理器 ───────────────────────────────────────────────────────────

class ServiceHealthManager:
    """
    全局服务可用性管理器（线程安全单例）。

    生命周期：
      1. 模块导入时自动创建实例并执行首次检测
      2. 可随时调用 refresh() 触发重新检测
      3. 提供 prompt_block() 供 Agent 注入提示词
    """

    def __init__(self):
        self._lock = threading.RLock()
        self._statuses: dict[str, ServiceStatus] = {}
        self._registry: list[ServiceDescriptor] = []
        self._initialized = False

    def _ensure_init(self):
        if not self._initialized:
            with self._lock:
                if not self._initialized:
                    self._registry = _build_registry()
                    self._do_check()
                    self._initialized = True

    def refresh(self) -> None:
        """强制重新检测所有服务（config 热更新后调用）"""
        with self._lock:
            self._registry = _build_registry()
            self._do_check()
        logger.info("[ServiceHealth] 已刷新服务状态: %s", self.summary_str())

    def _do_check(self) -> None:
        """执行所有服务检测（在锁内调用）"""
        new_statuses: dict[str, ServiceStatus] = {}
        for svc in self._registry:
            try:
                ok = svc.check_fn()
                reason = "" if ok else "API Key 未配置或格式无效"
            except Exception as e:
                ok = False
                reason = f"检测异常: {e}"

            new_statuses[svc.name] = ServiceStatus(
                name=svc.name,
                display_name=svc.display_name,
                tools=svc.tools,
                available=ok,
                reason=reason,
            )

        self._statuses = new_statuses

    # ── 查询接口 ──────────────────────────────────────────────────────────────

    def is_available(self, service_name: str) -> bool:
        """查询指定服务是否可用"""
        self._ensure_init()
        with self._lock:
            s = self._statuses.get(service_name)
            return s.available if s else False

    def available_services(self) -> list[str]:
        """返回所有可用服务名列表"""
        self._ensure_init()
        with self._lock:
            return [name for name, s in self._statuses.items() if s.available]

    def unavailable_services(self) -> list[str]:
        """返回所有不可用服务名列表"""
        self._ensure_init()
        with self._lock:
            return [name for name, s in self._statuses.items() if not s.available]

    def available_tools(self) -> set[str]:
        """返回所有可用工具函数名集合"""
        self._ensure_init()
        with self._lock:
            tools: set[str] = set()
            for name, s in self._statuses.items():
                if s.available:
                    svc = next((x for x in self._registry if x.name == name), None)
                    if svc:
                        tools.update(svc.tools)
            return tools

    def unavailable_tools(self) -> set[str]:
        """返回所有不可用工具函数名集合"""
        self._ensure_init()
        with self._lock:
            tools: set[str] = set()
            for name, s in self._statuses.items():
                if not s.available:
                    svc = next((x for x in self._registry if x.name == name), None)
                    if svc:
                        tools.update(svc.tools)
            return tools

    def summary_str(self) -> str:
        """单行摘要，用于日志"""
        self._ensure_init()
        with self._lock:
            parts = []
            for name, s in self._statuses.items():
                icon = "✅" if s.available else "❌"
                parts.append(f"{icon}{name}")
            return " | ".join(parts)

    def available_str(self) -> str:
        """可用服务列表字符串，用于提示词注入"""
        self._ensure_init()
        with self._lock:
            avail = [s.display_name for s in self._statuses.values() if s.available]
            return "、".join(avail) if avail else "（无可用服务）"

    def prompt_block(self) -> str:
        """
        生成注入 Agent System Prompt 的服务状态文本块。

        格式示例：
        【当前可用服务】
        ✅ MiniMax 音乐生成 → 工具: generate_audio_minimax（同步生成，自动落盘）
        ✅ MiniMax 歌词生成 → 工具: generate_lyrics_minimax
        ❌ Suno AI 歌曲生成 → 【禁止调用】generate_audio_suno（API Key 未配置）
        """
        self._ensure_init()
        lines = ["【当前可用服务状态 — 系统自动检测，每次请求动态注入】"]
        with self._lock:
            for name, s in self._statuses.items():
                # 跳过 LLM 本身（不需要在提示词里展示）
                if name == "llm":
                    continue
                svc = next((x for x in self._registry if x.name == name), None)
                tool_names = ", ".join(s.tools) if svc else ""
                if s.available:
                    desc = svc.description if svc else ""
                    lines.append(f"  ✅ {s.display_name} → 可调用: {tool_names}（{desc}）")
                else:
                    lines.append(
                        f"  ❌ {s.display_name} → 【严禁调用】{tool_names}"
                        f"（原因: {s.reason}，调用必然失败，直接跳过）"
                    )

        lines.append("规则：❌ 标记的工具本轮严禁调用；如所有生成工具均不可用，告知用户配置对应 API Key。")
        return "\n".join(lines)

    def get_all_statuses(self) -> list[dict]:
        """返回所有服务状态的字典列表（供 API 端点暴露）"""
        self._ensure_init()
        with self._lock:
            result = []
            for name, s in self._statuses.items():
                svc = next((x for x in self._registry if x.name == name), None)
                result.append({
                    "name": s.name,
                    "display_name": s.display_name,
                    "available": s.available,
                    "reason": s.reason,
                    "tools": s.tools,
                    "description": svc.description if svc else "",
                    "checked_at": s.checked_at,
                })
            return result


# ─── 全局单例 ─────────────────────────────────────────────────────────────────

service_health = ServiceHealthManager()
