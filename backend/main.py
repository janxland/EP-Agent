"""
EP-Agent 后端入口
FastAPI + uvicorn
"""
import os
import sys
import logging
from pathlib import Path
import uvicorn

# ── 日志配置（启动时立即生效）────────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    datefmt="%H:%M:%S",
    force=True,  # 覆盖 uvicorn 默认 handler
)
# ep_agent 命名空间全部 INFO 级别输出
logging.getLogger("ep_agent").setLevel(logging.DEBUG)
from contextlib import asynccontextmanager
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

from app.config import config
from app.pipeline.router import router, _WS_FILE_ROOT
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

# ── 工作区静态文件服务（图片/MIDI/音频 CDN 直出）────────────────
# 前端通过 /workspace/{workspace_id}/{path} 直接访问二进制文件，
# 绕开 encoding=raw API，避免 Next.js rewrites 缓冲导致图片加载失败。
app.mount("/workspace", StaticFiles(directory=str(_WS_FILE_ROOT)), name="workspace")


@app.get("/healthz")
async def health():
    return {"status": "ok", "version": "1.0.0"}


if __name__ == "__main__":
    addr = config.ADDR  # e.g. "0.0.0.0:8080"
    parts = addr.rsplit(":", 1)
    host = parts[0] if len(parts) == 2 else "0.0.0.0"
    port = int(parts[1]) if len(parts) == 2 else 8080
    uvicorn.run("main:app", host=host, port=port, reload=False)
