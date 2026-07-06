"""
FastAPI 路由层 — 转发入口（thin shim）

原 1886 行巨型文件已拆分到 app/pipeline/routers/ 各子模块：
  hub.py       — SSE 共享状态 (_queues / _sequences / _publish / _make_publisher)
  sse.py       — GET  /sessions/{id}/stream
  session.py   — Session CRUD / chat / abort / history / export / context / role
  workspace.py — Workspace & Project CRUD
  files.py     — 工作区文件系统 API
  health.py    — /health / /health/tools / /health/domains / /roles / /models
  audit.py     — 审计 & 重播 API
  workflow.py  — 工作流模板 API

此文件仅做两件事：
  1. re-export routers 包的聚合 router（main.py 的 import 路径不变）
  2. 暴露 _WS_FILE_ROOT（main.py 用于挂载静态文件服务）
"""
from app.pipeline.routers import router                          # noqa: F401  re-export
from app.pipeline.routers.files import _WS_FILE_ROOT            # noqa: F401  re-export

__all__ = ["router", "_WS_FILE_ROOT"]
