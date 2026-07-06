"""
TraceCollector — 工具调用审计链路收集器 (v1.2)

设计原则：
  - 零侵入：挂载到 publish 函数，不修改任何 SubAgent / 工具函数
  - 自动收集：拦截 tool.call / message.delta / message.completed SSE 事件，自动记录 Span
  - 持久化：end_trace() 一次性写入 SQLite traces/spans/fixtures 三张表
  - 轻量：每次对话只增加 <1ms 开销（纯内存操作，最后统一写库）

挂载方式（service.py 中仅需 4 行）：
    tracer = TraceCollector(session_id, message, role_id, attachment_name)
    traced_publish = tracer.wrap_publish(publish)
    result = await universal_runner.run(..., publish=traced_publish, ...)
    await tracer.end_trace(status="succeeded")

v1.1 修复：
  - BUG-1: tool_result 始终为 "{}" — 新增 tool_result 字段存储 result_preview 原文
  - BUG-2: call_id 重复 running 事件会创建重复 span — 加入重复检测
  - ISSUE-3: domain 提取正则太脆弱 — 改用多策略提取 + 正则兜底
  - ISSUE-4: end_trace 直接调用同步 DB 阻塞事件循环 — 改用 run_in_executor

v1.2 新增：
  - LLM model span 收集：拦截 pipeline.step(round_idx) 开始、message.completed 结束
    记录每一轮 ReAct 的模型调用（model/input_tokens/output_tokens/finish_reason）
  - token 汇总：end_trace 时自动从 model span 累加 input_tokens/output_tokens
    写入 traces 表，前端可直接展示总 token 消耗
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import re
from datetime import datetime, timezone
from typing import Callable, Awaitable

_logger = logging.getLogger("ep_agent.trace")

Publisher = Callable[[str, dict], Awaitable[None]]


# ── 工具函数 ──────────────────────────────────────────────────────────────────

def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _calc_duration_ms(started_iso: str, ended_iso: str) -> int:
    """计算两个 ISO 时间字符串之间的毫秒数，解析失败返回 0。
    T1 修复：改用 datetime.fromisoformat() 支持任意时区格式（Python 3.7+）。
    """
    try:
        from datetime import timezone as _tz
        def _parse(s: str):
            # Python 3.11+ fromisoformat 支持所有 ISO 8601 格式
            # Python 3.7-3.10 不支持 'Z' 后缀，手动替换
            return datetime.fromisoformat(s.replace('Z', '+00:00'))
        s = _parse(started_iso)
        e = _parse(ended_iso)
        return max(0, int((e - s).total_seconds() * 1000))
    except Exception:
        return 0


def _hash_args(args: dict | str) -> str:
    """对工具参数做 SHA256 哈希（用于 fixture 匹配，防 PII 直接存储）"""
    try:
        if isinstance(args, str):
            # 已经是 JSON 字符串，先解析再规范化
            try:
                args = json.loads(args)
            except Exception:
                pass
        canonical = json.dumps(args, sort_keys=True, ensure_ascii=False)
        return hashlib.sha256(canonical.encode()).hexdigest()[:16]
    except Exception:
        return "unknown"


def _safe_json(obj) -> str:
    """安全序列化，截断超长字段"""
    try:
        s = json.dumps(obj, ensure_ascii=False)
        return s[:4096] if len(s) > 4096 else s
    except Exception:
        return "{}"


# domain 提取正则（多策略，优先级从高到低）
_DOMAIN_PATTERNS = [
    re.compile(r"意图[：:]\s*([a-z_]+)"),          # "意图：sovits"
    re.compile(r"路由到\s*([a-z_]+)\s*域"),         # "路由到 sovits 域"
    re.compile(r"domain[=:]\s*([a-z_]+)"),          # "domain=sovits"
    re.compile(r"\b(sovits|voice|audio|edit|convert|create|query|h5)\b"),  # 关键词兜底
]

def _extract_domain(text: str) -> str:
    """从 pipeline.step routing 文本中提取 domain，多策略兜底"""
    for pat in _DOMAIN_PATTERNS:
        m = pat.search(text)
        if m:
            return m.group(1).strip()
    return ""


# ── TraceCollector ─────────────────────────────────────────────────────────────

class TraceCollector:
    """
    一次 universal_chat 调用对应一个 TraceCollector 实例。
    通过 wrap_publish 拦截 SSE 事件，自动记录所有工具调用 Span。
    """

    def __init__(
        self,
        session_id: str,
        message: str,
        role_id: str = "",
        attachment_name: str = "",
        workspace_id: str = "",
        project_id: str = "",
    ):
        from app.pipeline.domain import new_id
        self.trace_id        = new_id("trace")
        self.session_id      = session_id
        self.message         = message[:500]
        self.role_id         = role_id
        self.attachment_name = attachment_name
        self.workspace_id    = workspace_id
        self.project_id      = project_id
        self.domain          = ""          # 由 routing 事件延迟填写
        self.started_at      = _now_iso()
        self.spans: list[dict] = []
        self.step_idx        = 0
        # BUG-2 修复：用 set 记录已见过的 running call_id，防重复 span
        self._seen_running: set[str] = set()
        self._call_id_map: dict[str, int] = {}  # call_id → spans 列表索引
        # v1.2：LLM model span 追踪
        # round_idx → model span 在 spans 列表中的索引（用于 message.completed 时回填）
        self._model_span_map: dict[int, int] = {}
        # 当前轮次的 token 累积（来自 message.delta 携带的 usage 字段）
        self._round_input_tokens: dict[int, int] = {}
        self._round_output_tokens: dict[int, int] = {}
        self._current_round_idx: int = 0
        self._current_model: str = ""

    # ── 包装 publish（核心挂载点）──────────────────────────────────────────────

    def wrap_publish(self, publish: Publisher) -> Publisher:
        """
        返回包装后的 publish 函数。
        执行顺序：先原始推送（确保 SSE 不受影响），再审计记录（失败不影响主流程）。
        """
        async def wrapped(evt_type: str, payload: dict, **kwargs):
            # ① 先执行原始推送，确保 SSE 不受影响
            await publish(evt_type, payload, **kwargs)
            # ② 后台审计（异常完全隔离，不影响主流程）
            try:
                self._handle_event(evt_type, payload)
            except Exception as e:
                _logger.debug("[TraceCollector] 事件处理异常（已隔离）: %s", e)
        return wrapped

    def _handle_event(self, evt_type: str, payload: dict):
        """处理各类 SSE 事件，更新 spans 列表（同步，纯内存操作）"""

        # ── 工具调用 ──────────────────────────────────────────────────────────
        if evt_type == "tool.call":
            status    = payload.get("status", "")
            call_id   = payload.get("call_id", "")
            tool_name = payload.get("tool", "")

            if status == "running":
                # BUG-2 修复：同一 call_id 只创建一次 span
                # 边界条件：call_id 为空时不做去重（空 call_id 可能是多个不同工具）
                if call_id and call_id in self._seen_running:
                    return
                if call_id:
                    self._seen_running.add(call_id)

                span = {
                    "span_id":            _gen_span_id(),
                    "trace_id":           self.trace_id,
                    "parent_span_id":     "",
                    "agent_name":         "",
                    "span_kind":          "tool",
                    "round_idx":          payload.get("round_idx", 0),
                    "step_idx":           self.step_idx,
                    "tool_name":          tool_name,
                    "tool_args":          _safe_json(payload.get("arguments", {})),
                    "tool_args_hash":     _hash_args(payload.get("arguments", {})),
                    # BUG-1 说明：SSE 不传完整 tool_result JSON，
                    # tool_result 在 succeeded 时用 result_preview 字符串填充（非 JSON 对象）
                    # Phase 2 重播时 fixture 匹配基于 tool_args_hash，result 用于展示
                    "tool_result":        "{}",
                    "tool_result_preview": "",
                    "attempt":            1,
                    "model":              "",
                    "temperature":        0.0,
                    "input_tokens":       0,
                    "output_tokens":      0,
                    "finish_reason":      "",
                    "started_at":         _now_iso(),
                    "ended_at":           "",
                    "duration_ms":        0,
                    "status":             "running",
                    "error_msg":          "",
                    "call_id":            call_id,
                }
                self._call_id_map[call_id] = len(self.spans)
                self.spans.append(span)
                self.step_idx += 1

            elif status in ("succeeded", "failed"):
                idx = self._call_id_map.get(call_id)
                if idx is not None and idx < len(self.spans):
                    span  = self.spans[idx]
                    ended = _now_iso()
                    result_preview = payload.get("result_preview", "")[:200]
                    # 优先使用 full_result（完整返回值），如没有则回退到 result_preview
                    # full_result 由 ReactExecutor 在 tool.call succeeded 事件中附加（需对应修改）
                    # 当前安全层：取 result_preview 存入 tool_result，同时保留 4096 截断防止 DB 膨胀
                    full_result = payload.get("full_result", "") or result_preview
                    tool_result_str = full_result[:4096] if len(full_result) > 4096 else full_result

                    span["status"]               = "ok" if status == "succeeded" else "error"
                    span["tool_result_preview"]  = result_preview
                    span["tool_result"]          = tool_result_str
                    span["ended_at"]             = ended
                    span["duration_ms"]          = _calc_duration_ms(span["started_at"], ended)
                    if status == "failed":
                        span["error_msg"] = payload.get("error", "")[:500]

        # ── 路由步骤（提取 domain + 开启 LLM model span）─────────────────────
        elif evt_type == "pipeline.step":
            step      = payload.get("step", "")
            status    = payload.get("status", "")
            round_idx = payload.get("round_idx", 0)

            # 优先从 payload.domain 字段读取（最可靠）
            if payload.get("domain"):
                self.domain = payload["domain"]
            # 其次从 routing 步骤的 text 字段提取
            elif step == "routing" and status == "succeeded":
                text = payload.get("text", "")
                extracted = _extract_domain(text)
                if extracted:
                    self.domain = extracted

            # v1.2：新 ReAct 轮次开始 → 创建 model span（等 message.completed 回填结束时间）
            # pipeline.step 携带 round_idx + stream_turn_id 时视为新轮次开始
            if status == "running" and payload.get("stream_turn_id") and \
               round_idx not in self._model_span_map:
                model_name = payload.get("model", self._current_model) or ""
                span = {
                    "span_id":            _gen_span_id(),
                    "trace_id":           self.trace_id,
                    "parent_span_id":     "",
                    "agent_name":         payload.get("agent_name", ""),
                    "span_kind":          "model",
                    "round_idx":          round_idx,
                    "step_idx":           self.step_idx,
                    "tool_name":          "",
                    "tool_args":          "{}",
                    "tool_args_hash":     "",
                    "tool_result":        "{}",
                    "tool_result_preview": "",
                    "attempt":            1,
                    "model":              model_name,
                    "temperature":        payload.get("temperature", 0.0),
                    "input_tokens":       0,
                    "output_tokens":      0,
                    "finish_reason":      "",
                    "started_at":         _now_iso(),
                    "ended_at":           "",
                    "duration_ms":        0,
                    "status":             "running",
                    "error_msg":          "",
                    "call_id":            payload.get("stream_turn_id", ""),
                }
                self._model_span_map[round_idx] = len(self.spans)
                self.spans.append(span)
                self.step_idx += 1
                self._current_round_idx = round_idx

        # ── message.delta：累积当次轮次 token（后端可能在 delta 里携带 usage）──
        elif evt_type == "message.delta":
            usage = payload.get("usage") or {}
            if usage:
                ri = self._current_round_idx
                self._round_input_tokens[ri]  = usage.get("input_tokens",  self._round_input_tokens.get(ri, 0))
                self._round_output_tokens[ri] = usage.get("output_tokens", self._round_output_tokens.get(ri, 0))
            # 同步更新当前模型名（message.delta 可能携带 model 字段）
            if payload.get("model"):
                self._current_model = payload["model"]

        # ── message.completed：回填当前轮次 model span 的结束时间 + token ────
        elif evt_type == "message.completed":
            ri      = self._current_round_idx
            ended   = _now_iso()
            # 从 completed payload 或累积 delta 中取 token
            usage   = payload.get("usage") or {}
            in_tok  = usage.get("input_tokens",  self._round_input_tokens.get(ri, 0))
            out_tok = usage.get("output_tokens", self._round_output_tokens.get(ri, 0))
            finish  = payload.get("finish_reason", "stop")
            model   = payload.get("model", self._current_model) or ""

            idx = self._model_span_map.get(ri)
            if idx is not None and idx < len(self.spans):
                span = self.spans[idx]
                span["ended_at"]      = ended
                span["duration_ms"]   = _calc_duration_ms(span["started_at"], ended)
                span["status"]        = "ok"
                span["input_tokens"]  = in_tok
                span["output_tokens"] = out_tok
                span["finish_reason"] = finish
                if model:
                    span["model"] = model

    # ── 结束 Trace，写入 SQLite ───────────────────────────────────────────────

    async def end_trace(
        self,
        status: str = "succeeded",
        input_tokens: int = 0,
        output_tokens: int = 0,
    ):
        """
        写入 traces / spans / replay_fixtures 三张表。
        ISSUE-4 修复：使用 run_in_executor 避免同步 DB 操作阻塞 asyncio 事件循环。
        应在 universal_runner.run() 完成后调用。

        v1.2：自动从 model span 汇总 token（外部传入值作为兜底）。
        """
        ended_at = _now_iso()

        # v1.2：从所有 model span 累加 token（比外部传入更精确）
        total_in  = sum(s.get("input_tokens",  0) for s in self.spans if s.get("span_kind") == "model")
        total_out = sum(s.get("output_tokens", 0) for s in self.spans if s.get("span_kind") == "model")
        # 外部传入值作为兜底（model span 没有 token 数据时使用）
        final_in  = total_in  if total_in  > 0 else input_tokens
        final_out = total_out if total_out > 0 else output_tokens

        trace_row = {
            "trace_id":        self.trace_id,
            "session_id":      self.session_id,
            "workspace_id":    self.workspace_id,
            "project_id":      self.project_id,
            "domain":          self.domain,
            "role_id":         self.role_id,
            "user_message":    self.message,
            "attachment_name": self.attachment_name,
            "started_at":      self.started_at,
            "ended_at":        ended_at,
            "duration_ms":     _calc_duration_ms(self.started_at, ended_at),
            "status":          status,
            "total_steps":     self.step_idx,
            "input_tokens":    final_in,
            "output_tokens":   final_out,
        }

        # 补全未完成的 spans（异常退出时可能有 running 状态）
        for span in self.spans:
            if span["status"] == "running":
                span["status"]   = "skipped"
                span["ended_at"] = ended_at

        # 竞态防护：在提交线程池前做快照，防止 run_in_executor 执行期间
        # asyncio 事件循环运行其他协程修改 spans 列表
        spans_snapshot = [dict(s) for s in self.spans]
        trace_row_snapshot = dict(trace_row)

        # ISSUE-4 修复：在线程池中执行同步 DB 写入，不阻塞事件循环
        def _write_to_db():
            from app.pipeline import db as _db
            from app.pipeline.domain import new_id as _new_id

            _db.insert_trace(trace_row_snapshot)
            for span in spans_snapshot:
                _db.insert_span(span)
            # 生成 fixtures（仅 ok 状态的 tool span）
            for span in spans_snapshot:
                if span["span_kind"] == "tool" and span["status"] == "ok":
                    _db.insert_fixture({
                        "fixture_id":     _new_id("fix"),
                        "trace_id":       self.trace_id,
                        "span_id":        span["span_id"],
                        "tool_name":      span["tool_name"],
                        "tool_args_hash": span["tool_args_hash"],
                        "tool_result":    span["tool_result"],
                    })
            _logger.info(
                "[TraceCollector] trace 落库完成 trace_id=%s domain=%s steps=%d status=%s",
                self.trace_id, self.domain, self.step_idx, status,
            )

        try:
            loop = asyncio.get_running_loop()
            await loop.run_in_executor(None, _write_to_db)
        except RuntimeError:
            # 没有运行中的事件循环（测试场景），直接同步执行
            try:
                _write_to_db()
            except Exception as e:
                _logger.warning("[TraceCollector] trace 落库失败: %s", e)
        except Exception as e:
            _logger.warning("[TraceCollector] trace 落库失败（不影响主流程）: %s", e)

    def get_trace_id(self) -> str:
        return self.trace_id

    def get_spans(self) -> list[dict]:
        return list(self.spans)


# ── 辅助函数 ──────────────────────────────────────────────────────────────────

def _gen_span_id() -> str:
    from app.pipeline.domain import new_id
    return new_id("span")
