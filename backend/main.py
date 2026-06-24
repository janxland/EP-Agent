"""
EP-Agent 后端入口
FastAPI + uvicorn
"""
import os
import sys
from pathlib import Path
import uvicorn
from contextlib import asynccontextmanager
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

from app.config import config
from app.pipeline.router import router
from app.pipeline.audio_router import router as audio_router
from app.pipeline.audio_chat_router import router as audio_chat_router
from app.pipeline.db import init_db

# H5 海报输出目录（统一从 config 读取，与 h5_tools.py 共享同一配置源）
_H5_OUTPUT_DIR = Path(config.H5_OUTPUT_DIR)
_H5_OUTPUT_DIR.mkdir(parents=True, exist_ok=True)


@asynccontextmanager
async def lifespan(app: FastAPI):
    """应用生命周期管理（替代已废弃的 @app.on_event）"""
    # 启动阶段：初始化 SQLite 数据库
    init_db()
    yield
    # 关闭阶段：可在此添加资源清理逻辑


app = FastAPI(
    title="EP-Agent API",
    version="1.0.0",
    description="Sky 谱子智能编辑 · AI 音频生成 · 音色克隆",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(router)
app.include_router(audio_router)
app.include_router(audio_chat_router)

# ── H5 海报静态文件服务 ──────────────────────────────────────────
# save_h5_file 工具将 HTML 保存到 _H5_OUTPUT_DIR，
# 前端通过 /h5/{filename}.html 直接访问生成的海报页面。
app.mount("/h5", StaticFiles(directory=str(_H5_OUTPUT_DIR), html=True), name="h5")


@app.get("/healthz")
async def health():
    return {"status": "ok", "version": "1.0.0"}


if __name__ == "__main__":
    addr = config.ADDR  # e.g. "0.0.0.0:8080"
    parts = addr.rsplit(":", 1)
    host = parts[0] if len(parts) == 2 else "0.0.0.0"
    port = int(parts[1]) if len(parts) == 2 else 8080
    uvicorn.run("main:app", host=host, port=port, reload=False)
