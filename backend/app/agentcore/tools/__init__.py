"""
工具注册表 - Tool Registry（支持分组隔离）

@tool 装饰器注册工具到指定分组，不同 Agent 只获取自己需要的工具子集。

用法：
    # 注册到默认分组（abc_edit）
    @tool
    def transpose_abc(abc: str, semitones: int) -> str:
        '''将 ABC 谱转调指定半音数'''
        ...

    # 注册到指定分组
    @tool(group="audio")
    async def generate_audio_suno(prompt: str) -> dict:
        '''使用 Suno AI 生成音频'''
        ...

    # 获取指定分组的工具 schema
    schemas = get_tool_schemas("abc_edit")
    schemas = get_tool_schemas("audio")
    schemas = get_tool_schemas()          # 获取所有工具

    # 执行工具调用（跨分组均可）
    result = await call_tool("transpose_abc", {"abc": "...", "semitones": 7})
"""
from __future__ import annotations
import inspect
import asyncio
import typing as _typing
from typing import Any, Callable

# ─── 注册表 ───────────────────────────────────────────────────────────────────

# 结构：{group_name: {tool_name: fn}}
_registry: dict[str, dict[str, Callable]] = {}

DEFAULT_GROUP = "abc_edit"


def tool(fn: Callable | None = None, *, group: str = DEFAULT_GROUP) -> Callable:
    """
    装饰器：注册函数为 Agent 可调用工具。

    支持两种用法：
        @tool                        # 注册到默认分组 abc_edit
        @tool(group="audio")         # 注册到 audio 分组
    """
    def _register(f: Callable) -> Callable:
        _registry.setdefault(group, {})[f.__name__] = f
        return f

    if fn is not None:
        # 直接 @tool 无括号调用
        return _register(fn)
    # @tool(group=...) 带参数调用，返回装饰器
    return _register


def get_tool_schemas(group: str | None = None) -> list[dict]:
    """
    生成工具的 OpenAI function calling schema。
    group=None  → 返回所有分组的工具
    group="abc_edit" → 只返回 abc_edit 分组
    """
    schemas = []
    if group is None:
        all_fns = {name: fn for g in _registry.values() for name, fn in g.items()}
    else:
        all_fns = _registry.get(group, {})

    for name, fn in all_fns.items():
        schemas.append({
            "type": "function",
            "function": {
                "name": name,
                "description": (inspect.getdoc(fn) or "").strip(),
                "parameters": _build_parameters(fn),
            }
        })
    return schemas


def get_tool_names(group: str | None = None) -> list[str]:
    """获取工具名列表"""
    if group is None:
        return [name for g in _registry.values() for name in g]
    return list(_registry.get(group, {}).keys())


def get_registered_groups() -> list[str]:
    """获取所有已注册的分组名"""
    return list(_registry.keys())


async def call_tool(name: str, arguments: dict) -> Any:
    """执行工具调用，跨分组均可，支持同步和异步函数"""
    import contextvars

    # 在所有分组中查找
    fn = None
    for group_fns in _registry.values():
        if name in group_fns:
            fn = group_fns[name]
            break

    if fn is None:
        all_names = get_tool_names()
        raise ValueError(f"Tool not found: {name!r}. Available: {all_names}")

    if asyncio.iscoroutinefunction(fn):
        return await fn(**arguments)
    else:
        # 同步工具在线程池执行时，必须用 copy_context().run() 把当前协程的
        # ContextVar 快照（含 session_id）传入线程，否则线程内 ContextVar 全为默认值。
        # 这是 Python 官方推荐的跨线程传递 ContextVar 的唯一正确方式。
        ctx = contextvars.copy_context()
        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(None, ctx.run, lambda: fn(**arguments))


# ─── Schema 生成（从类型注解推导）────────────────────────────────────────────

_PY_TO_JSON = {
    "str": "string",
    "int": "integer",
    "float": "number",
    "bool": "boolean",
    "dict": "object",
    "list": "array",
}


def _resolve_type_name(py_type) -> str:
    """
    将 Python 类型注解解析为 JSON Schema 类型字符串。
    支持：
      - 普通类型：str, int, float, bool, dict, list
      - Optional[X]  → 取 X 的类型（忽略 None）
      - Union[X, None] / X | None → 同上
      - Literal["a","b"] → 保留 enum，类型取第一个值的类型
    """
    if py_type is None:
        return "string"

    # 处理 Optional[X] / Union[X, None]
    origin = getattr(py_type, "__origin__", None)
    if origin is not None:
        # Union (包含 Optional)
        if origin is getattr(_typing, "Union", None):
            args = [a for a in py_type.__args__ if a is not type(None)]
            if args:
                return _resolve_type_name(args[0])
            return "string"
        # Literal
        if origin is getattr(_typing, "Literal", None):
            first = py_type.__args__[0] if py_type.__args__ else ""
            return _PY_TO_JSON.get(type(first).__name__, "string")
        # list / dict generics
        raw_origin = getattr(origin, "__name__", str(origin))
        return _PY_TO_JSON.get(raw_origin, "string")

    # Python 3.10+ X | None 语法 → types.UnionType
    try:
        import types as _types
        if isinstance(py_type, _types.UnionType):
            args = [a for a in py_type.__args__ if a is not type(None)]
            if args:
                return _resolve_type_name(args[0])
            return "string"
    except AttributeError:
        pass

    raw = getattr(py_type, "__name__", str(py_type))
    return _PY_TO_JSON.get(raw, "string")


def _build_parameters(fn: Callable) -> dict:
    sig = inspect.signature(fn)
    try:
        hints = fn.__annotations__
    except Exception:
        hints = {}

    properties: dict[str, dict] = {}
    required: list[str] = []

    # 从 docstring 提取参数说明（格式：param_name: 说明文字）
    doc = inspect.getdoc(fn) or ""
    param_docs: dict[str, str] = {}
    for line in doc.splitlines():
        line = line.strip()
        for pname in sig.parameters:
            if line.startswith(f"{pname}:") or line.startswith(f"{pname} :"):
                desc = line.split(":", 1)[1].strip()
                param_docs[pname] = desc

    for pname, param in sig.parameters.items():
        if pname in ("self", "cls"):
            continue

        py_type = hints.get(pname, None)
        type_name = _resolve_type_name(py_type)

        prop: dict = {"type": type_name}
        if pname in param_docs:
            prop["description"] = param_docs[pname]

        # Literal 枚举值
        origin = getattr(py_type, "__origin__", None)
        if origin is getattr(_typing, "Literal", None):
            prop["enum"] = list(py_type.__args__)

        properties[pname] = prop

        # 没有默认值 = required（Optional 有默认值 None 时不算 required）
        if param.default is inspect.Parameter.empty:
            required.append(pname)

    return {
        "type": "object",
        "properties": properties,
        "required": required,
    }
