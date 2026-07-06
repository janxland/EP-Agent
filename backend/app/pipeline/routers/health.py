"""
健康检查 / 工具注册表 / 意图域 / 角色 / 模型 路由
"""
from __future__ import annotations

from fastapi import APIRouter, HTTPException

from app.config import config
import app.agentcore.llm as _llm

router = APIRouter()

# ── 预设模型列表 ──────────────────────────────────────────────────────────────
_MODELS = [
    {"id": "deepseek-ai/DeepSeek-V4-Pro",    "name": "DeepSeek V4 Pro",    "group": "旗舰", "desc": "DeepSeek 最强旗舰，1049K 超长上下文"},
    {"id": "deepseek-ai/DeepSeek-V4-Flash",  "name": "DeepSeek V4 Flash",  "group": "快速", "desc": "高速低成本，1049K 上下文，推荐日常"},
    {"id": "deepseek-ai/DeepSeek-V3.2",      "name": "DeepSeek V3.2",      "group": "旗舰", "desc": "中文优化旗舰，164K 上下文"},
    {"id": "deepseek-ai/DeepSeek-R1-0528",   "name": "DeepSeek R1",        "group": "推理", "desc": "深度思考推理，复杂逻辑/数学"},
    {"id": "Qwen/Qwen3.6-27B",               "name": "Qwen3.6-27B",        "group": "旗舰", "desc": "通义千问旗舰，262K 上下文"},
    {"id": "Qwen/Qwen3.6-35B-A3B",           "name": "Qwen3.6-35B-A3B",   "group": "推理", "desc": "MoE 推理增强，262K 上下文"},
    {"id": "Qwen/Qwen3.5-397B-A17B",         "name": "Qwen3.5-397B",       "group": "旗舰", "desc": "超大 MoE，262K 上下文"},
    {"id": "MiniMax/MiniMax-M2.5",           "name": "MiniMax M2.5",       "group": "旗舰", "desc": "MiniMax 旗舰，197K 上下文"},
    {"id": "THUDM/GLM-5.2",                  "name": "GLM-5.2",            "group": "旗舰", "desc": "智谱最新旗舰，1049K 超长上下文"},
    {"id": "THUDM/GLM-5.1",                  "name": "GLM-5.1",            "group": "快速", "desc": "智谱快速版，205K 上下文"},
    {"id": "moonshotai/Kimi-K2.7",           "name": "Kimi K2.7",          "group": "旗舰", "desc": "月之暗面最新，262K 上下文"},
    {"id": "stepfun-ai/Step-3.5-Flash",      "name": "Step-3.5-Flash",     "group": "快速", "desc": "阶跃星辰快速版，262K 上下文"},
]


# ── Health ────────────────────────────────────────────────────────────────────

@router.get("/health")
async def health_check():
    from app.agentcore.tools import get_tool_names
    from app.agentcore.domain_config import list_domains
    try:
        tool_count   = len(get_tool_names())
        domain_count = len(list_domains(enabled_only=True))
        tools_ok     = tool_count > 0
    except Exception:
        tool_count = domain_count = 0
        tools_ok   = False
    return {"status": "ok" if tools_ok else "degraded",
            "tools_ok": tools_ok, "tool_count": tool_count, "domain_count": domain_count}


@router.get("/health/tools")
async def health_tools():
    from app.agentcore.tools import get_tool_names, get_tool_schemas, get_registered_groups

    def _icon(name: str) -> str:
        if "transpose" in name or "key" in name:  return "🎵"
        if "tempo" in name or "bpm" in name:      return "⏱️"
        if "midi" in name:                        return "🎹"
        if "sky" in name or "convert" in name:    return "🎮"
        if "audio" in name or "suno" in name:     return "🎧"
        if "voice" in name or "sovits" in name:   return "🎤"
        if "router" in name or "intent" in name:  return "🧭"
        if "edit" in name or "abc" in name:       return "✏️"
        return "🔧"

    try:
        groups   = get_registered_groups()
        schemas  = get_tool_schemas()
        names    = get_tool_names()
        critical = ["abc_transpose", "abc_to_sky_json", "convert_sky_json"]
        checks   = {n: ("ok" if n in names else "not_registered") for n in critical}
        tools_list = [
            {"name": s["function"]["name"],
             "label": s["function"]["name"].replace("_", " "),
             "icon": _icon(s["function"]["name"]),
             "description": s["function"].get("description", ""),
             "group": next((g for g in groups if s["function"]["name"] in get_tool_names(g)), "default")}
            for s in schemas
        ]
    except Exception as e:
        return {"tools_ok": False, "tool_count": 0, "tools": [], "error": str(e)}
    return {"tools_ok": all(v == "ok" for v in checks.values()),
            "tool_count": len(names), "groups": groups,
            "tools": tools_list, "critical_checks": checks}


@router.get("/health/domains")
async def health_domains():
    from app.agentcore.domain_config import to_frontend_map, build_router_prompt
    try:
        domains = to_frontend_map()
        return {"domains": domains,
                "enabled_count": sum(1 for d in domains if d["enabled"]),
                "total_count": len(domains),
                "router_prompt_preview": build_router_prompt()[:500]}
    except Exception as e:
        raise HTTPException(500, f"域配置加载失败: {e}")


# ── Roles ─────────────────────────────────────────────────────────────────────

@router.get("/roles")
async def list_roles():
    from app.agentcore.role_config import to_frontend_list
    try:
        return {"roles": to_frontend_list()}
    except Exception as e:
        raise HTTPException(500, f"角色配置加载失败: {e}")


# ── Models ────────────────────────────────────────────────────────────────────

@router.get("/models")
async def list_models():
    active = config.LLM_MODEL
    models = []
    found  = False
    for m in _MODELS:
        item = dict(m)
        if item["id"] == active:
            item["current"] = True
            found = True
        models.append(item)
    if not found:
        models.append({"id": active, "name": active.split("/")[-1],
                       "group": "自定义", "desc": f"当前配置：{active}", "current": True})
    return {"models": models, "active": active}


@router.patch("/models/active")
async def set_active_model(body: dict):
    model_id = body.get("model_id", "").strip()
    if not model_id:
        raise HTTPException(400, "model_id is required")
    config.LLM_MODEL = model_id
    _llm.reset_client()
    return {"ok": True, "active": model_id}
