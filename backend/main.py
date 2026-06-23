"""
ABC-Agent 后端入口
FastAPI + uvicorn
"""
import os
import sys
import uvicorn
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.config import config
from app.pipeline.router import router
from app.pipeline.audio_router import router as audio_router
from app.pipeline.audio_chat_router import router as audio_chat_router

app = FastAPI(title="EP-Agent API", version="1.0.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(router)
app.include_router(audio_router)
app.include_router(audio_chat_router)

@app.get("/healthz")
async def health():
    return {"status": "ok"}

if __name__ == "__main__":
    addr = config.ADDR  # e.g. "0.0.0.0:8080"
    parts = addr.rsplit(":", 1)
    host = parts[0] if len(parts) == 2 else "0.0.0.0"
    port = int(parts[1]) if len(parts) == 2 else 8080
    uvicorn.run("main:app", host=host, port=port, reload=False)
