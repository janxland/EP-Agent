"""
工作区文件系统 API
路径规则：data/workspace/{ws_id}/projects/{proj_id}/
"""
from __future__ import annotations
import base64 as _base64
import logging
import mimetypes as _mimetypes
import os as _os
import shutil as _shutil
from pathlib import Path as _Path

from fastapi import APIRouter, File, Form, HTTPException, UploadFile
from fastapi.responses import Response as _Response
from pydantic import BaseModel

from app.pipeline import db as _db

router  = APIRouter()
_logger = logging.getLogger("ep_agent.ws_files")

_WS_FILE_ROOT     = _Path(__file__).resolve().parent.parent.parent.parent / "data" / "workspace"
_WS_FILE_ROOT.mkdir(parents=True, exist_ok=True)

_BLOCKED_EXTS     = {".py", ".sh", ".bash", ".exe", ".bat", ".cmd", ".ps1"}
_MAX_UPLOAD_BYTES = 200 * 1024 * 1024   # 200 MB
_TEXT_EXTS        = {".abc", ".txt", ".md", ".json", ".html", ".htm",
                     ".css", ".js", ".ts", ".xml", ".yaml", ".yml", ".csv", ".svg", ".log"}


def _ws_safe_path(workspace_id: str, rel: str, project_id: str = "") -> _Path:
    """安全路径解析，防止路径遍历。"""
    base = (
        (_WS_FILE_ROOT / workspace_id / "projects" / project_id).resolve()
        if project_id
        else (_WS_FILE_ROOT / workspace_id).resolve()
    )
    norm = _os.path.normpath(rel)
    if norm.startswith("..") or norm.startswith("/") or norm.startswith("\\"):
        raise HTTPException(400, "路径越界")
    target     = base / norm
    target_abs = _os.path.abspath(str(target))
    base_abs   = _os.path.abspath(str(base))
    if not target_abs.startswith(base_abs + _os.sep) and target_abs != base_abs:
        raise HTTPException(400, "路径越界")
    return _Path(target_abs)


def _file_entry(p: _Path, base: _Path) -> dict:
    rel  = str(p.relative_to(base))
    mime, _ = _mimetypes.guess_type(str(p))
    return {
        "path":    rel,
        "name":    p.name,
        "ext":     p.suffix.lower().lstrip("."),
        "size":    p.stat().st_size,
        "mime":    mime or "application/octet-stream",
        "is_text": p.suffix.lower() in _TEXT_EXTS,
    }


# ── List ──────────────────────────────────────────────────────────────────────

@router.get("/workspaces/{workspace_id}/files")
async def list_ws_files(workspace_id: str, project_id: str = "", subdir: str = ""):
    """列出项目文件树；有 project_id 时回退扫描 ws 根（兼容旧上传路径）"""
    if project_id:
        base = _WS_FILE_ROOT / workspace_id / "projects" / project_id
    else:
        base = _WS_FILE_ROOT / workspace_id
    scan = (base / subdir) if subdir else base

    files: list[dict] = []
    seen:  set[str]   = set()

    if scan.exists():
        for p in sorted(scan.rglob("*")):
            if p.is_file():
                files.append(_file_entry(p, base))
                seen.add(p.name)

    # 回退扫描 ws 根（Fix-B2：兼容旧版上传到 ws 根的文件）
    if project_id:
        ws_root = _WS_FILE_ROOT / workspace_id
        if ws_root.exists() and ws_root != base:
            ws_scan = (ws_root / subdir) if subdir else ws_root
            if ws_scan.exists():
                for p in sorted(ws_scan.rglob("*")):
                    try:
                        p.relative_to(ws_root / "projects")
                        continue
                    except ValueError:
                        pass
                    if p.is_file() and p.name not in seen:
                        files.append(_file_entry(p, ws_root))
                        seen.add(p.name)

    return {"workspace_id": workspace_id, "project_id": project_id, "files": files}


# ── Read ──────────────────────────────────────────────────────────────────────

@router.get("/workspaces/{workspace_id}/files/content")
async def get_ws_file(workspace_id: str, path: str, encoding: str = "text", project_id: str = ""):
    target = _ws_safe_path(workspace_id, path, project_id)
    if not target.exists():
        raise HTTPException(404, f"文件不存在: {path}")
    if encoding == "raw":
        data = target.read_bytes()
        mime, _ = _mimetypes.guess_type(str(target))
        return _Response(content=data, media_type=mime or "application/octet-stream")
    if encoding == "base64":
        data = target.read_bytes()
        return {"path": path, "content": _base64.b64encode(data).decode("ascii"), "encoding": "base64"}
    return {"path": path, "content": target.read_text(encoding="utf-8", errors="replace"), "encoding": "text"}


# ── Write ─────────────────────────────────────────────────────────────────────

class WsFileWriteRequest(BaseModel):
    path:     str
    content:  str
    encoding: str = "text"   # "text" | "base64"


@router.put("/workspaces/{workspace_id}/files")
async def put_ws_file(workspace_id: str, req: WsFileWriteRequest, project_id: str = ""):
    try:
        ext = _Path(req.path).suffix.lower()
        if ext in _BLOCKED_EXTS:
            raise HTTPException(400, f"禁止写入 {ext} 类型文件")
        target = _ws_safe_path(workspace_id, req.path, project_id)
        target.parent.mkdir(parents=True, exist_ok=True)
        if req.encoding == "base64":
            try:
                data = _base64.b64decode(req.content)
            except Exception as e:
                raise HTTPException(400, f"base64 解码失败：{e}")
            if len(data) > _MAX_UPLOAD_BYTES:
                raise HTTPException(400, "文件超过限制")
            target.write_bytes(data)
            return {"ok": True, "path": req.path, "size": len(data)}
        else:
            raw = req.content.encode("utf-8")
            if len(raw) > _MAX_UPLOAD_BYTES:
                raise HTTPException(400, "文件超过限制")
            target.write_text(req.content, encoding="utf-8")
            return {"ok": True, "path": req.path, "size": len(raw)}
    except HTTPException:
        raise
    except Exception as e:
        _logger.exception("[put_ws_file] 写入失败 workspace=%s path=%r", workspace_id, req.path)
        raise HTTPException(500, f"文件写入失败：{e}")


# ── Upload（multipart）────────────────────────────────────────────────────────

@router.post("/workspaces/{workspace_id}/files/upload")
async def upload_ws_file(
    workspace_id: str,
    file:         UploadFile = File(...),
    path:         str        = Form(...),
    project_id:   str        = Form(""),
    session_id:   str        = Form(""),
):
    """
    multipart/form-data 上传二进制文件（最大 200MB）。
    Fix-B1：未传 project_id 时自动从 session DB 补全，确保文件落到正确项目目录。
    """
    try:
        ext = _Path(path).suffix.lower()
        if ext in _BLOCKED_EXTS:
            raise HTTPException(400, f"禁止上传 {ext} 类型文件")

        # Fix-B1: 补全 project_id
        eff_proj = project_id.strip()
        if not eff_proj and session_id.strip():
            try:
                si = _db.get_session_info(session_id.strip())
                if si:
                    eff_proj = (si.get("project_id") or "").strip()
                    if eff_proj:
                        _logger.info("[upload] project_id 从 session 补全: sess=%s proj=%s", session_id, eff_proj)
            except Exception:
                pass

        target = _ws_safe_path(workspace_id, path, eff_proj)
        target.parent.mkdir(parents=True, exist_ok=True)

        written = 0
        with target.open("wb") as f:
            while chunk := await file.read(1024 * 1024):
                written += len(chunk)
                if written > _MAX_UPLOAD_BYTES:
                    target.unlink(missing_ok=True)
                    raise HTTPException(400, "文件超过 200MB 限制")
                f.write(chunk)

        _logger.info("[upload] 成功 workspace=%s path=%r size=%d", workspace_id, path, written)
        return {"ok": True, "path": path, "size": written}
    except HTTPException:
        raise
    except Exception as e:
        _logger.exception("[upload] 失败 workspace=%s path=%r", workspace_id, path)
        raise HTTPException(500, f"文件上传失败：{e}")


# ── Delete ────────────────────────────────────────────────────────────────────

@router.delete("/workspaces/{workspace_id}/files")
async def delete_ws_file(workspace_id: str, path: str, project_id: str = ""):
    target = _ws_safe_path(workspace_id, path, project_id)
    if not target.exists():
        return {"ok": True, "message": "文件不存在（已删除）"}
    if target.is_dir():
        _shutil.rmtree(target)
    else:
        target.unlink()
    return {"ok": True, "path": path}


# ── Copy / Rename / Move ──────────────────────────────────────────────────────

class WsFileCopyRequest(BaseModel):
    src_path: str
    dst_path: str


@router.post("/workspaces/{workspace_id}/files/copy")
async def copy_ws_file(workspace_id: str, req: WsFileCopyRequest, project_id: str = ""):
    src = _ws_safe_path(workspace_id, req.src_path, project_id)
    dst = _ws_safe_path(workspace_id, req.dst_path, project_id)
    if not src.exists():
        raise HTTPException(404, f"源文件不存在：{req.src_path}")
    if dst.suffix.lower() in _BLOCKED_EXTS:
        raise HTTPException(400, f"禁止复制为 {dst.suffix} 类型文件")
    dst.parent.mkdir(parents=True, exist_ok=True)
    _shutil.copy2(src, dst)
    return {"ok": True, "src": req.src_path, "dst": req.dst_path, "size": dst.stat().st_size}


class WsFileRenameRequest(BaseModel):
    src_path: str
    new_name: str


@router.post("/workspaces/{workspace_id}/files/rename")
async def rename_ws_file(workspace_id: str, req: WsFileRenameRequest, project_id: str = ""):
    if "/" in req.new_name or "\\" in req.new_name:
        raise HTTPException(400, "new_name 不能含路径分隔符")
    if _Path(req.new_name).suffix.lower() in _BLOCKED_EXTS:
        raise HTTPException(400, f"禁止重命名为该类型文件")
    base = (_WS_FILE_ROOT / workspace_id / "projects" / project_id) if project_id else (_WS_FILE_ROOT / workspace_id)
    src  = _ws_safe_path(workspace_id, req.src_path, project_id)
    if not src.exists():
        raise HTTPException(404, f"文件不存在：{req.src_path}")
    dst = src.parent / req.new_name
    _ws_safe_path(workspace_id, str(dst.relative_to(base)), project_id)
    src.rename(dst)
    return {"ok": True, "src": req.src_path, "dst": str(dst.relative_to(base))}


class WsFileMoveRequest(BaseModel):
    src_path: str
    dst_path: str


@router.post("/workspaces/{workspace_id}/files/move")
async def move_ws_file(workspace_id: str, req: WsFileMoveRequest, project_id: str = ""):
    if _Path(req.dst_path).suffix.lower() in _BLOCKED_EXTS:
        raise HTTPException(400, f"禁止移动为该类型文件")
    src = _ws_safe_path(workspace_id, req.src_path, project_id)
    dst = _ws_safe_path(workspace_id, req.dst_path, project_id)
    if not src.exists():
        raise HTTPException(404, f"源文件不存在：{req.src_path}")
    dst.parent.mkdir(parents=True, exist_ok=True)
    _shutil.move(str(src), str(dst))
    return {"ok": True, "src": req.src_path, "dst": req.dst_path, "size": dst.stat().st_size}
