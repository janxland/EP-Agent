"""
routers 包 — 统一注册所有子路由到 FastAPI APIRouter
用法（main.py 或 app 入口）：
    from app.pipeline.routers import router
    app.include_router(router)
"""
from fastapi import APIRouter

from app.pipeline.routers import sse, workspace, session, files, health, audit, workflow
from app.pipeline import audio_chat_router, audio_router

router = APIRouter(prefix="/api")

router.include_router(sse.router)
router.include_router(workspace.router)
router.include_router(session.router)
router.include_router(files.router)
router.include_router(health.router)
router.include_router(audit.router)
router.include_router(workflow.router)
router.include_router(audio_chat_router.router)
router.include_router(audio_router.router)
