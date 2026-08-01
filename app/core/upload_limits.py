"""Shared upload and archive validation for untrusted browser files."""

from __future__ import annotations

import asyncio
import os
import stat
import uuid
from pathlib import Path
from pathlib import PurePosixPath
from typing import BinaryIO, Iterable
from zipfile import ZipFile, ZipInfo

from fastapi import HTTPException, UploadFile


MIB = 1024 * 1024
WORKFLOW_JSON_MAX_BYTES = 32 * MIB
ARCHIVE_MAX_BYTES = 512 * MIB
UPLOAD_FILE_MAX_BYTES = 500 * MIB
UPLOAD_REQUEST_MAX_BYTES = 1024 * MIB
ARCHIVE_MAX_ENTRIES = 2000
ARCHIVE_RESOURCE_MAX_BYTES = 500 * MIB
ARCHIVE_EXPANDED_MAX_BYTES = 2 * 1024 * MIB
ARCHIVE_RATIO_MAX = 200
GRAPH_MAX_NODES = 5000
GRAPH_MAX_CONNECTIONS = 20000
COPY_CHUNK_BYTES = MIB


async def upload_size(upload: UploadFile) -> int:
    if not hasattr(upload, "file"):
        buffered = getattr(upload, "_data", None)
        return len(buffered) if isinstance(buffered, (bytes, bytearray, memoryview)) else 0

    def measure() -> int:
        stream = upload.file
        current = stream.tell()
        stream.seek(0, os.SEEK_END)
        size = stream.tell()
        stream.seek(current, os.SEEK_SET)
        return int(size)

    return await asyncio.to_thread(measure)


def upload_size_sync(upload: UploadFile) -> int:
    """Measure a spooled upload while already running in a worker thread."""

    if not hasattr(upload, "file"):
        buffered = getattr(upload, "_data", None)
        return len(buffered) if isinstance(buffered, (bytes, bytearray, memoryview)) else 0
    stream = upload.file
    current = stream.tell()
    stream.seek(0, os.SEEK_END)
    size = int(stream.tell())
    stream.seek(current, os.SEEK_SET)
    return size


async def ensure_upload_size(upload: UploadFile, maximum: int, label: str = "文件") -> int:
    size = await upload_size(upload)
    if size > maximum:
        raise HTTPException(status_code=413, detail=f"{label}超过大小上限")
    return size


def ensure_upload_size_sync(upload: UploadFile, maximum: int, label: str = "文件") -> int:
    size = upload_size_sync(upload)
    if size > maximum:
        raise HTTPException(status_code=413, detail=f"{label}超过大小上限")
    return size


async def ensure_request_upload_size(
    uploads: Iterable[UploadFile],
    *,
    per_file: int = UPLOAD_FILE_MAX_BYTES,
    total: int = UPLOAD_REQUEST_MAX_BYTES,
) -> int:
    aggregate = 0
    for upload in uploads:
        size = await ensure_upload_size(upload, per_file, upload.filename or "文件")
        aggregate += size
        if aggregate > total:
            raise HTTPException(status_code=413, detail="本次上传文件总量超过 1 GiB")
    return aggregate


async def read_upload_limited(upload: UploadFile, maximum: int, label: str = "文件") -> bytes:
    await ensure_upload_size(upload, maximum, label)
    await upload.seek(0)
    data = await upload.read(maximum + 1)
    if len(data) > maximum:
        raise HTTPException(status_code=413, detail=f"{label}超过大小上限")
    return data


async def save_upload_to_path_limited(
    upload: UploadFile,
    destination: str | os.PathLike[str],
    maximum: int,
    label: str = "file",
) -> int:
    """Copy a spooled upload to disk without buffering its contents in memory."""

    await ensure_upload_size(upload, maximum, label)
    target = Path(destination)
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary = target.with_name(f".{target.name}.{uuid.uuid4().hex}.upload")

    def copy() -> int:
        total = 0
        upload.file.seek(0)
        try:
            with temporary.open("wb") as handle:
                while True:
                    chunk = upload.file.read(COPY_CHUNK_BYTES)
                    if not chunk:
                        break
                    total += len(chunk)
                    if total > maximum:
                        raise HTTPException(status_code=413, detail=f"{label} exceeds the size limit")
                    handle.write(chunk)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary, target)
            return total
        finally:
            try:
                temporary.unlink(missing_ok=True)
            except OSError:
                pass

    return await asyncio.to_thread(copy)


def read_upload_stream_limited(upload: UploadFile, maximum: int, label: str = "文件") -> bytes:
    ensure_upload_size_sync(upload, maximum, label)
    upload.file.seek(0)
    data = upload.file.read(maximum + 1)
    if len(data) > maximum:
        raise HTTPException(status_code=413, detail=f"{label}超过大小上限")
    return data


def normalize_archive_name(name: str) -> str:
    normalized = str(name or "").replace("\\", "/")
    path = PurePosixPath(normalized)
    if (
        not normalized
        or normalized.startswith("/")
        or path.is_absolute()
        or any(part in {"", ".", ".."} for part in path.parts)
        or (path.parts and ":" in path.parts[0])
    ):
        raise HTTPException(status_code=400, detail=f"压缩包包含越界路径：{name}")
    return path.as_posix()


def _is_symlink(info: ZipInfo) -> bool:
    mode = (info.external_attr >> 16) & 0xFFFF
    return stat.S_IFMT(mode) == stat.S_IFLNK


def validate_zip_archive(zf: ZipFile) -> dict[str, ZipInfo]:
    entries = zf.infolist()
    if len(entries) > ARCHIVE_MAX_ENTRIES:
        raise HTTPException(status_code=413, detail="压缩包条目超过 2000 个")
    normalized: dict[str, ZipInfo] = {}
    expanded = 0
    for info in entries:
        name = normalize_archive_name(info.filename)
        if name in normalized:
            raise HTTPException(status_code=400, detail=f"压缩包包含重复路径：{name}")
        if _is_symlink(info):
            raise HTTPException(status_code=400, detail=f"压缩包不允许符号链接：{name}")
        if info.file_size > ARCHIVE_RESOURCE_MAX_BYTES:
            raise HTTPException(status_code=413, detail=f"压缩包单个条目超过 500 MiB：{name}")
        expanded += int(info.file_size)
        if expanded > ARCHIVE_EXPANDED_MAX_BYTES:
            raise HTTPException(status_code=413, detail="压缩包解压总量超过 2 GiB")
        if info.file_size > 10 * MIB:
            ratio = info.file_size / max(1, info.compress_size)
            if ratio > ARCHIVE_RATIO_MAX:
                raise HTTPException(status_code=400, detail=f"压缩比异常，疑似 ZIP 炸弹：{name}")
        normalized[name] = info
    return normalized


def copy_zip_entry_limited(zf: ZipFile, info: ZipInfo, destination: BinaryIO, maximum: int) -> int:
    total = 0
    with zf.open(info, "r") as source:
        while True:
            chunk = source.read(COPY_CHUNK_BYTES)
            if not chunk:
                break
            total += len(chunk)
            if total > maximum:
                raise HTTPException(status_code=413, detail=f"解压条目超过大小上限：{info.filename}")
            destination.write(chunk)
    return total


def validate_graph_size(nodes: object, connections: object) -> None:
    if isinstance(nodes, list) and len(nodes) > GRAPH_MAX_NODES:
        raise HTTPException(status_code=413, detail="工作流节点超过 5000 个")
    if isinstance(connections, list) and len(connections) > GRAPH_MAX_CONNECTIONS:
        raise HTTPException(status_code=413, detail="工作流连线超过 20000 条")
