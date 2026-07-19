"""MiniMax file management and safe content download."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, Optional

from ..models import FileDownload, GenerationResult
from ..transport import AsyncTransport, Transport
from ..validation import validate_general_file
from ._base import normalized


class FilesResource:
    def __init__(self, transport: Transport) -> None:
        self._transport = transport

    def upload(self, file_path: str | Path, *, purpose: str) -> GenerationResult:
        path = validate_general_file(file_path)
        with path.open("rb") as stream:
            payload = self._transport.request(
                "POST",
                "/files/upload",
                data={"purpose": purpose},
                files={"file": (path.name, stream, "application/octet-stream")},
            )
        return normalized(payload)

    def list(self, *, purpose: str) -> Dict[str, Any]:
        return self._transport.request("GET", "/files/list", params={"purpose": purpose})

    def retrieve(self, file_id: str | int) -> Dict[str, Any]:
        return self._transport.request(
            "GET", "/files/retrieve", params={"file_id": file_id}
        )

    def retrieve_content(self, file_id: str | int) -> bytes:
        response = self._transport.request_raw(
            "GET", "/files/retrieve_content", params={"file_id": file_id}
        )
        return response.content

    def delete(self, file_id: str | int, *, purpose: str) -> Dict[str, Any]:
        fields = {
            "file_id": (None, str(file_id)),
            "purpose": (None, purpose),
        }
        return self._transport.request("POST", "/files/delete", files=fields)

    def download(
        self, file_id: str | int, *, destination: Optional[str | Path] = None
    ) -> FileDownload:
        metadata = self.retrieve(file_id)
        content = self.retrieve_content(file_id)
        file_info = metadata.get("file") if isinstance(metadata.get("file"), dict) else {}
        filename = file_info.get("filename")
        saved_to = None
        if destination is not None:
            target = Path(destination).expanduser()
            if target.exists() and target.is_dir():
                target = target / (filename or str(file_id))
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(content)
            saved_to = target
        return FileDownload(
            content=content,
            filename=filename,
            content_type=None,
            source_url=file_info.get("download_url"),
            saved_to=saved_to,
        )


class AsyncFilesResource:
    def __init__(self, transport: AsyncTransport) -> None:
        self._transport = transport

    async def upload(self, file_path: str | Path, *, purpose: str) -> GenerationResult:
        path = validate_general_file(file_path)
        with path.open("rb") as stream:
            payload = await self._transport.request(
                "POST",
                "/files/upload",
                data={"purpose": purpose},
                files={"file": (path.name, stream, "application/octet-stream")},
            )
        return normalized(payload)

    async def list(self, *, purpose: str) -> Dict[str, Any]:
        return await self._transport.request("GET", "/files/list", params={"purpose": purpose})

    async def retrieve(self, file_id: str | int) -> Dict[str, Any]:
        return await self._transport.request(
            "GET", "/files/retrieve", params={"file_id": file_id}
        )

    async def retrieve_content(self, file_id: str | int) -> bytes:
        response = await self._transport.request_raw(
            "GET", "/files/retrieve_content", params={"file_id": file_id}
        )
        return response.content

    async def delete(self, file_id: str | int, *, purpose: str) -> Dict[str, Any]:
        fields = {"file_id": (None, str(file_id)), "purpose": (None, purpose)}
        return await self._transport.request("POST", "/files/delete", files=fields)

    async def download(
        self, file_id: str | int, *, destination: Optional[str | Path] = None
    ) -> FileDownload:
        metadata = await self.retrieve(file_id)
        content = await self.retrieve_content(file_id)
        file_info = metadata.get("file") if isinstance(metadata.get("file"), dict) else {}
        filename = file_info.get("filename")
        saved_to = None
        if destination is not None:
            target = Path(destination).expanduser()
            if target.exists() and target.is_dir():
                target = target / (filename or str(file_id))
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(content)
            saved_to = target
        return FileDownload(
            content=content,
            filename=filename,
            source_url=file_info.get("download_url"),
            saved_to=saved_to,
        )
