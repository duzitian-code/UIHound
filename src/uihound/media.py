"""Local screenshot and browser recording file APIs."""

import os
import subprocess
import sys
from datetime import datetime, timezone
from ipaddress import ip_address
from pathlib import Path
from urllib.parse import urlencode, urlsplit
from uuid import uuid4

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field
from starlette.requests import ClientDisconnect

from . import config

router = APIRouter()
MAX_RECORDING_BYTES = 256 * 1024 * 1024
WEBM_HEADER = b"\x1a\x45\xdf\xa3"


class PathRequest(BaseModel):
    kind: str
    path: str = Field(min_length=1, max_length=2048)


class DeleteRequest(BaseModel):
    kind: str
    paths: list[str] = Field(min_length=1)


def _root(kind: str) -> tuple[Path, str]:
    if kind == "snapshot":
        return config.SNAPSHOT_DIR.resolve(), ".png"
    if kind == "recording":
        return config.RECORDING_DIR.resolve(), ".webm"
    raise HTTPException(status_code=400, detail="不支持的媒体类型")


def _safe_path(root: Path, value: str | None, *, directory: bool) -> Path:
    if value is None or value == "":
        if directory:
            return root
        raise HTTPException(status_code=400, detail="请提供文件路径")
    requested = Path(value).expanduser()
    if ".." in requested.parts:
        raise HTTPException(status_code=400, detail="不允许使用上级目录路径")
    if not directory and not requested.is_absolute():
        raise HTTPException(status_code=400, detail="文件路径必须为绝对路径")
    candidate = requested if requested.is_absolute() else root / requested
    candidate = Path(os.path.abspath(candidate))
    if candidate != root and root not in candidate.parents:
        raise HTTPException(status_code=400, detail="路径超出允许的媒体根目录")
    current = root
    for part in candidate.relative_to(root).parts:
        if any(char in part for char in '<>:"|?*') or part.endswith((" ", ".")):
            raise HTTPException(status_code=400, detail="路径包含非法文件名字符")
        current = current / part
        try:
            stat = current.lstat()
        except FileNotFoundError:
            continue
        if current.is_symlink() or (getattr(stat, "st_file_attributes", 0) & 0x400):
            raise HTTPException(status_code=400, detail="不允许操作链接或重解析点")
    if candidate.resolve() != candidate:
        raise HTTPException(status_code=400, detail="路径解析到不允许的位置")
    return candidate


def _directory(kind: str, value: str | None, *, create: bool = False) -> Path:
    root, _ = _root(kind)
    path = _safe_path(root, value, directory=True)
    if create:
        try:
            path.mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            raise HTTPException(status_code=400, detail=f"无法创建目录：{exc}") from exc
        _safe_path(root, str(path), directory=True)
    if not path.is_dir():
        raise HTTPException(status_code=404, detail="媒体目录不存在")
    return path


def _file(kind: str, value: str, *, require_exists: bool = True) -> Path:
    root, suffix = _root(kind)
    path = _safe_path(root, value, directory=False)
    if path == root or path.suffix.lower() != suffix:
        raise HTTPException(status_code=400, detail=f"仅允许操作 {suffix} 文件")
    if require_exists and not path.is_file():
        raise HTTPException(status_code=404, detail="媒体文件已删除或移动")
    return path


def _metadata(kind: str, path: Path) -> dict:
    stat = path.stat()
    return {
        "path": str(path), "name": path.name,
        "url": "/internal/media/file?" + urlencode({"kind": kind, "path": str(path)}),
        "size": stat.st_size,
        "modified": datetime.fromtimestamp(stat.st_mtime, timezone.utc).isoformat(),
    }


def _local_mutation(request: Request) -> None:
    try:
        local_client = request.client is not None and ip_address(request.client.host).is_loopback
    except ValueError:
        local_client = False
    origin = request.headers.get("origin")
    try:
        parsed = urlsplit(origin) if origin else None
    except ValueError:
        parsed = None
    if (
        not local_client
        or request.url.hostname not in {"localhost", "127.0.0.1", "::1"}
        or parsed is None
        or parsed.netloc != request.url.netloc
        or parsed.scheme != request.url.scheme
        or request.headers.get("sec-fetch-site") not in {None, "same-origin"}
    ):
        raise HTTPException(status_code=403, detail="文件操作仅支持在服务所在电脑通过本机同源网页使用")


@router.get("/media/list")
def list_media(kind: str, directory: str | None = Query(default=None, max_length=2048)) -> dict:
    root, _ = _root(kind)
    requested = _safe_path(root, directory, directory=True)
    if not requested.exists():
        return {"files": [], "directory": str(requested)}
    folder = _directory(kind, directory)
    files = []
    for entry in folder.iterdir():
        try:
            path = _file(kind, str(entry))
        except HTTPException:
            continue
        files.append(_metadata(kind, path))
    files.sort(key=lambda item: (item["modified"], item["name"]), reverse=True)
    return {"files": files, "directory": str(folder)}


@router.post("/media/delete", dependencies=[Depends(_local_mutation)])
def delete_media(payload: DeleteRequest) -> dict:
    paths = [_file(payload.kind, value, require_exists=False) for value in payload.paths]
    deleted, errors = [], []
    for path in paths:
        try:
            _file(payload.kind, str(path))
            path.unlink()
            deleted.append(str(path))
        except HTTPException as exc:
            errors.append({"path": str(path), "message": str(exc.detail)})
        except OSError as exc:
            errors.append({"path": str(path), "message": str(exc)})
    return {"deleted": deleted, "errors": errors}


@router.post("/media/open-directory", dependencies=[Depends(_local_mutation)])
def open_directory(payload: PathRequest) -> dict:
    folder = _directory(payload.kind, payload.path, create=True)
    try:
        if sys.platform == "win32":
            os.startfile(folder)
        elif sys.platform == "darwin":
            subprocess.run(["open", str(folder)], check=True, capture_output=True, timeout=15)
        elif sys.platform.startswith("linux"):
            subprocess.run(["xdg-open", str(folder)], check=True, capture_output=True, timeout=15)
        else:
            raise HTTPException(status_code=501, detail="当前系统暂不支持打开目录")
    except subprocess.TimeoutExpired as exc:
        raise HTTPException(status_code=500, detail="打开目录超时，请重试") from exc
    except subprocess.CalledProcessError as exc:
        detail = exc.stderr.decode(errors="replace").strip() if exc.stderr else str(exc)
        raise HTTPException(status_code=500, detail=f"无法打开目录：{detail}") from exc
    except OSError as exc:
        raise HTTPException(status_code=500, detail=f"无法打开目录：{exc}") from exc
    return {"directory": str(folder)}


@router.post("/media/directory", dependencies=[Depends(_local_mutation)])
def create_directory(payload: PathRequest) -> dict:
    return {"directory": str(_directory(payload.kind, payload.path, create=True))}


@router.post("/media/copy-file", dependencies=[Depends(_local_mutation)])
def copy_file(payload: PathRequest) -> dict:
    path = _file(payload.kind, payload.path)
    try:
        if sys.platform == "win32":
            script = (
                "$ErrorActionPreference = 'Stop'; "
                "Add-Type -AssemblyName System.Windows.Forms; "
                "$files = New-Object System.Collections.Specialized.StringCollection; "
                "[void]$files.Add($env:UIHOUND_CLIPBOARD_FILE); "
                "[System.Windows.Forms.Clipboard]::SetFileDropList($files)"
            )
            subprocess.run(
                ["powershell.exe", "-NoProfile", "-NonInteractive", "-STA", "-Command", script],
                env={**os.environ, "UIHOUND_CLIPBOARD_FILE": str(path)},
                capture_output=True, check=True, timeout=15,
                creationflags=subprocess.CREATE_NO_WINDOW,
            )
            message = "文件已复制，可在资源管理器中按 Ctrl+V 粘贴"
        elif sys.platform == "darwin":
            script = "on run argv\nset the clipboard to (POSIX file (item 1 of argv))\nend run"
            subprocess.run(
                ["osascript", "-e", script, str(path)],
                capture_output=True, check=True, timeout=15,
            )
            message = "文件已复制，可在 Finder 中按 Command+V 粘贴"
        else:
            raise HTTPException(status_code=501, detail="复制文件目前仅支持 Windows 和 macOS")
    except subprocess.TimeoutExpired as exc:
        raise HTTPException(status_code=500, detail="复制文件超时，请重试") from exc
    except subprocess.CalledProcessError as exc:
        detail = exc.stderr.decode(errors="replace").strip() if exc.stderr else str(exc)
        raise HTTPException(status_code=500, detail=f"复制文件失败：{detail}") from exc
    except OSError as exc:
        raise HTTPException(status_code=500, detail=f"无法访问文件剪贴板：{exc}") from exc
    return {"message": message}


@router.get("/media/file")
def media_file(kind: str, path: str = Query(min_length=1, max_length=2048)) -> FileResponse:
    file = _file(kind, path)
    return FileResponse(file, media_type="image/png" if kind == "snapshot" else "video/webm")


@router.post("/recordings/save", dependencies=[Depends(_local_mutation)])
async def save_recording(
    request: Request, directory: str | None = Query(default=None, max_length=2048),
) -> dict:
    if request.headers.get("content-type", "").split(";", 1)[0].strip().lower() != "video/webm":
        raise HTTPException(status_code=415, detail="录屏必须为 video/webm")
    folder = _directory("recording", directory, create=True)
    path = folder / f"record_{datetime.now(timezone.utc):%Y%m%d_%H%M%S_%f}_{uuid4().hex}.webm"
    temporary = path.with_suffix(".part")
    size = 0
    header = b""
    completed = False
    created = False
    try:
        with temporary.open("xb") as output:
            created = True
            async for chunk in request.stream():
                size += len(chunk)
                if size > MAX_RECORDING_BYTES:
                    raise HTTPException(status_code=413, detail="录屏超过 256 MiB 上限")
                if len(header) < 4:
                    header += chunk[:4 - len(header)]
                    if len(header) == 4 and header != WEBM_HEADER:
                        raise HTTPException(status_code=400, detail="无效的 WebM 文件头")
                output.write(chunk)
        if size <= 4:
            raise HTTPException(status_code=400, detail="录屏为空或缺少视频内容")
        # Windows rename refuses replacement; use an exclusive link on POSIX.
        if os.name == "nt":
            temporary.rename(path)
        else:
            os.link(temporary, path)
            temporary.unlink()
        completed = True
        return _metadata("recording", path)
    except ClientDisconnect as exc:
        raise HTTPException(status_code=400, detail="录屏上传连接已断开") from exc
    except OSError as exc:
        raise HTTPException(status_code=500, detail=f"保存录屏失败：{exc}") from exc
    finally:
        if created and not completed:
            temporary.unlink(missing_ok=True)
