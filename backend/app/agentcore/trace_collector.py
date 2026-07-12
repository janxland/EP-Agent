"""
TraceCollector — 工具调用审计链路收集器 (v1.4)

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

v1.3 新增：
  - SubAgent 内部 LLM 调用可见：识别 tool.call 中 tool 名以 "llm:" 开头的事件，
    自动标记为 span_kind="model"，从 arguments 中提取 model/temperature/agent_name
    create_agent._llm_with_span() 发布此类事件，让质量流水线每次 LLM 调用都有 span
  - agent.call 事件支持：新增 evt_type="agent.call" 分支，记录 span_kind="agent" span，
    支持 parent_span_id 树形调用层级（主 Agent → SubAgent → LLM 三层可见）
  - tool.call running 分支扩展：llm: span 从 arguments 提取 model/temperature/agent_name，
    普通 tool span 保持原有逻辑不变
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
        # v1.5：思考链累积缓冲（per round_idx → reasoning 文本片段列表）
        self._round_reasoning: dict[int, list[str]] = {}

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

            # v1.3：llm: 前缀的工具名视为 SubAgent 内部 LLM 调用，标记为 model span
            # create_agent/_llm_with_span 发布此类事件，让 LLM 推理过程在审计中可见
            is_llm_span = tool_name.startswith("llm:")

            if status == "running":
                # BUG-2 修复：同一 call_id 只创建一次 span
                # 边界条件：call_id 为空时不做去重（空 call_id 可能是多个不同工具）
                if call_id and call_id in self._seen_running:
                    return
                if call_id:
                    self._seen_running.add(call_id)

                # llm: span 从 arguments 中提取模型名和 temperature
                args = payload.get("arguments", {})
                if isinstance(args, str):
                    try:
                        args = __import__("json").loads(args)
                    except Exception:
                        args = {}
                _model    = args.get("model", "") if is_llm_span else ""
                _temp     = args.get("temperature", 0.0) if is_llm_span else 0.0
                _agent_nm = args.get("agent", "") if is_llm_span else ""

                span = {
                    "span_id":            _gen_span_id(),
                    "trace_id":           self.trace_id,
                    "parent_span_id":     "",
                    # llm: span 用 arguments.agent 字段填充 agent_name
                    "agent_name":         _agent_nm,
                    # llm: 前缀 → model span；普通工具 → tool span
                    "span_kind":          "model" if is_llm_span else "tool",
                    "round_idx":          payload.get("round_idx", 0),
                    "step_idx":           self.step_idx,
                    "tool_name":          tool_name,
                    "tool_args":          _safe_json(payload.get("arguments", {})),
                    "tool_args_hash":     _hash_args(payload.get("arguments", {})),
                    "tool_result":        "{}",
                    "tool_result_preview": "",
                    "attempt":            1,
                    "model":              _model,
                    "temperature":        _temp,
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
                    tool_result_str = full_result  # v1.5：不截断，完整保留工具返回内容

                    span["status"]               = "ok" if status == "succeeded" else "error"
                    span["tool_result_preview"]  = result_preview
                    span["tool_result"]          = tool_result_str
                    span["ended_at"]             = ended
                    span["duration_ms"]          = _calc_duration_ms(span["started_at"], ended)
                    if status == "failed":
                        span["error_msg"] = payload.get("error", "")[:500]

        # ── LangGraph 节点进入/退出（v1.4 新增：细粒度闭环可见性）──────────────
        # graph.node_enter → 创建 span_kind="node" span，记录节点开始时间
        # graph.node_exit  → 回填结束时间，让每个 LangGraph 节点的耗时都可见
        # 跳过 supervisor/reflect/LangGraph/__start__ 等调度节点（噪音多、价值低）
        elif evt_type == "graph.node_enter":
            node_name = payload.get("node", "")
            _SKIP_NODES = {"supervisor", "reflect_node", "__start__", "LangGraph", ""}
            if node_name not in _SKIP_NODES:
                node_call_id = f"node_{node_name}"
                if node_call_id not in self._seen_running:
                    self._seen_running.add(node_call_id)
                    span = {
                        "span_id":            _gen_span_id(),
                        "trace_id":           self.trace_id,
                        "parent_span_id":     "",
                        "agent_name":         node_name,
                        "span_kind":          "node",
                        "round_idx":          0,
                        "step_idx":           self.step_idx,
                        "tool_name":          node_name,
                        "tool_args":          "{}",
                        "tool_args_hash":     "",
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
                        "call_id":            node_call_id,
                    }
                    self._call_id_map[node_call_id] = len(self.spans)
                    self.spans.append(span)
                    self.step_idx += 1

        elif evt_type == "graph.node_exit":
            node_name = payload.get("node", "")
            node_call_id = f"node_{node_name}"
            idx = self._call_id_map.get(node_call_id)
            if idx is not None and idx < len(self.spans):
                s = self.spans[idx]
                ended = _now_iso()
                has_error = bool(payload.get("has_error"))
                err_msg   = payload.get("error_msg", "")[:500]
                s["status"]        = "error" if has_error else "ok"
                s["ended_at"]      = ended
                s["duration_ms"]   = _calc_duration_ms(s["started_at"], ended)
                s["tool_result_preview"] = (
                    f"→ {payload.get('next_node', 'END')}"
                    + (f" [ERROR] {err_msg[:80]}" if has_error else "")
                )
                if has_error and err_msg:
                    s["error_msg"] = err_msg

        # ── pipeline.step 细粒度打点（v1.4 新增）────────────────────────────────
        # 将 pipeline.step 事件记录为轻量 span，让每个处理阶段都有时间戳
        # running → 创建 span；succeeded/failed → 回填结束时间
        elif evt_type == "pipeline.step":
            step      = payload.get("step", "")
            status    = payload.get("status", "")
            round_idx = payload.get("round_idx", 0)

            # 提取 domain（原有逻辑保留）
            if payload.get("domain"):
                self.domain = payload["domain"]
            elif step == "routing" and status == "succeeded":
                text = payload.get("text", "")
                extracted = _extract_domain(text)
                if extracted:
                    self.domain = extracted

            # v1.4：pipeline.step running → 创建轻量 step span
            # BUG-TC1 修复：同名 step 可能多次出现（如 create_extend running→succeeded→running）
            # 用 step + round_idx 组合作为 call_id，确保每次 running 都创建独立 span
            _step_seq = sum(1 for k in self._call_id_map if k.startswith(f"step_{step}_"))
            step_call_id = f"step_{step}_{_step_seq}"
            if status == "running" and step and step_call_id not in self._seen_running:
                self._seen_running.add(step_call_id)
                span = {
                    "span_id":            _gen_span_id(),
                    "trace_id":           self.trace_id,
                    "parent_span_id":     "",
                    "agent_name":         payload.get("agent_name", ""),
                    "span_kind":          "step",
                    "round_idx":          round_idx,
                    "step_idx":           self.step_idx,
                    "tool_name":          step,
                    "tool_args":          "{}",
                    "tool_args_hash":     "",
                    "tool_result":        "{}",
                    "tool_result_preview": payload.get("text", "")[:200],
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
                    "call_id":            step_call_id,
                }
                self._call_id_map[step_call_id] = len(self.spans)
                self.spans.append(span)
                self.step_idx += 1
            elif status in ("succeeded", "failed") and step:
                # 回填：找最近一个同名 step 且状态为 running 的 span
                _latest_step_id = None
                for _k in reversed(list(self._call_id_map.keys())):
                    if _k.startswith(f"step_{step}_"):
                        _candidate_idx = self._call_id_map[_k]
                        if _candidate_idx < len(self.spans) and self.spans[_candidate_idx]["status"] == "running":
                            _latest_step_id = _k
                            break
                if _latest_step_id:
                    step_call_id = _latest_step_id
                idx = self._call_id_map.get(step_call_id)
                if idx is not None and idx < len(self.spans):
                    s = self.spans[idx]
                    ended = _now_iso()
                    s["status"]              = "ok" if status == "succeeded" else "error"
                    s["ended_at"]            = ended
                    s["duration_ms"]         = _calc_duration_ms(s["started_at"], ended)
                    s["tool_result_preview"] = payload.get("text", "")[:200]
                    if status == "failed":
                        s["error_msg"] = payload.get("text", "")[:500]

            # v1.2：新 ReAct 轮次开始 → 创建 model span（原有逻辑，现在移到 pipeline.step 分支内）
            if status == "running" and payload.get("stream_turn_id") and \
               round_idx not in self._model_span_map:
                model_name = payload.get("model", self._current_model) or ""
                model_span = {
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
                self.spans.append(model_span)
                self.step_idx += 1
                self._current_round_idx = round_idx
            return  # pipeline.step 分支处理完毕，直接返回

        # ── SubAgent 调用层级追踪（v1.3 新增）────────────────────────────────
        elif evt_type == "agent.call":
            status     = payload.get("status", "")
            span_id    = payload.get("span_id", _gen_span_id())
            agent_name = payload.get("agent_name", "")

            if status == "running":
                if span_id in self._seen_running:
                    return
                self._seen_running.add(span_id)
                span = {
                    "span_id":            span_id,
                    "trace_id":           self.trace_id,
                    "parent_span_id":     payload.get("parent_span_id", ""),
                    "agent_name":         agent_name,
                    "span_kind":          "agent",
                    "round_idx":          payload.get("round_idx", 0),
                    "step_idx":           self.step_idx,
                    "tool_name":          "",
                    "tool_args":          "{}",
                    "tool_args_hash":     "",
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
                    "call_id":            span_id,
                }
                self._call_id_map[span_id] = len(self.spans)
                self.spans.append(span)
                self.step_idx += 1
            elif status in ("succeeded", "failed"):
                idx = self._call_id_map.get(span_id)
                if idx is not None and idx < len(self.spans):
                    s = self.spans[idx]
                    ended = _now_iso()
                    s["status"]      = "ok" if status == "succeeded" else "error"
                    s["ended_at"]    = ended
                    s["duration_ms"] = _calc_duration_ms(s["started_at"], ended)
                    if status == "failed":
                        s["error_msg"] = payload.get("error", "")[:500]

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
            # v1.5：累积思考链 delta（DeepSeek-R1 / V4-Flash 等推理模型）
            reasoning_delta = payload.get("reasoning_delta", "")
            if reasoning_delta:
                ri = self._current_round_idx
                if ri not in self._round_reasoning:
                    self._round_reasoning[ri] = []
                self._round_reasoning[ri].append(reasoning_delta)

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
                # v1.5：把累积的思考链写入 model span
                reasoning_text = "".join(self._round_reasoning.get(ri, []))
                if reasoning_text:
                    span["tool_result"] = reasoning_text  # v1.5：完整保留思考链，不截断
                    preview = reasoning_text[:193]
                    span["tool_result_preview"] = "<think>" + preview + ("…" if len(reasoning_text) > 193 else "")

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
