import asyncio
import argparse
import base64
import hashlib
import io
import logging
import webbrowser
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException, Query, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field
import uvicorn

from . import __version__, config, media
from .inspector import inspector
from .inspection import inspections
from .remote import router as remote_router
from .media import router as media_router


config.SNAPSHOT_DIR.mkdir(parents=True, exist_ok=True)
config.LOG_DIR.mkdir(parents=True, exist_ok=True)
config.RECORDING_DIR.mkdir(parents=True, exist_ok=True)
logging.basicConfig(
    filename=config.LOG_DIR / "uihound.log",
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
)
logger = logging.getLogger("uihound")


def static_asset(path: str) -> Path:
    asset = config.STATIC_DIR / path
    if not asset.is_file() or config.STATIC_DIR.resolve() not in asset.resolve().parents:
        raise HTTPException(status_code=404, detail="静态资源不存在")
    return asset


def static_version() -> str:
    digest = hashlib.sha256()
    for name in ("index.html", "style.css", "app.js", "color.js", "remote.js", "theme.js", "recording.js", "media.js", "logo.svg"):
        asset = static_asset(name)
        digest.update(name.encode("utf-8"))
        digest.update(asset.read_bytes())
    return digest.hexdigest()[:16]


@asynccontextmanager
async def lifespan(_: FastAPI):
    config.SNAPSHOT_DIR.mkdir(parents=True, exist_ok=True)
    config.LOG_DIR.mkdir(parents=True, exist_ok=True)
    yield


app = FastAPI(
    title="UiHound",
    version=__version__,
    docs_url=None,
    redoc_url=None,
    openapi_url=None,
    lifespan=lifespan,
)
app.mount("/static", StaticFiles(directory=config.STATIC_DIR), name="static")
app.include_router(remote_router, prefix="/internal", include_in_schema=False)
app.include_router(media_router, prefix="/internal", include_in_schema=False)


class ConnectRequest(BaseModel):
    serial: str = Field(min_length=1, max_length=256)


class SearchRequest(BaseModel):
    query: str = Field(min_length=1, max_length=2048)


class PointRequest(BaseModel):
    x: int = Field(ge=0)
    y: int = Field(ge=0)


class ActionRequest(BaseModel):
    action: str
    x: int | None = Field(default=None, ge=0)
    y: int | None = Field(default=None, ge=0)
    text: str | None = Field(default=None, max_length=4096)
    start_x: int | None = Field(default=None, ge=0)
    start_y: int | None = Field(default=None, ge=0)
    end_x: int | None = Field(default=None, ge=0)
    end_y: int | None = Field(default=None, ge=0)
    duration: float | None = Field(default=None, ge=0.1, le=10)

class ElementActionRequest(BaseModel):
    selector: str | None = Field(default=None, max_length=2048)
    text: str | None = Field(default=None, max_length=4096)


class SnapshotPathRequest(BaseModel):
    path: str = Field(min_length=1, max_length=2048)

class CaptureRequest(BaseModel):
    hierarchy: bool = True


class FrozenSearchRequest(SearchRequest):
    snapshot_id: str = Field(min_length=1, max_length=64)


class FrozenSnapshotRequest(BaseModel):
    snapshot_id: str = Field(min_length=1, max_length=64)
    kind: str
    custom_path: str | None = Field(default=None, max_length=2048)
    region: dict[str, int] | None = None
    xpath: str | None = Field(default=None, max_length=4096)


def invoke(operation: Any) -> Any:
    try:
        return operation()
    except (ValueError, LookupError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except RuntimeError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except Exception as exc:
        logger.exception("Device operation failed")
        raise HTTPException(status_code=502, detail=str(exc)) from exc


@app.get("/")
def index() -> HTMLResponse:
    asset = static_asset("index.html")
    html = asset.read_text(encoding="utf-8")
    version = static_version()
    html = html.replace("/static/style.css", f"/static/style.css?v={version}")
    html = html.replace("/static/app.js", f"/static/app.js?v={version}")
    html = html.replace("/static/color.js", f"/static/color.js?v={version}")
    html = html.replace("/static/remote.js", f"/static/remote.js?v={version}")
    html = html.replace("/static/theme.js", f"/static/theme.js?v={version}")
    html = html.replace("/static/recording.js", f"/static/recording.js?v={version}")
    html = html.replace("/static/media.js", f"/static/media.js?v={version}")
    html = html.replace("/static/logo.svg", f"/static/logo.svg?v={version}")
    return HTMLResponse(
        content=html,
        headers={
            "Cache-Control": "no-store, no-cache, must-revalidate, max-age=0",
            "Pragma": "no-cache",
            "Expires": "0",
        },
    )


@app.get("/device/status")
def device_status() -> dict[str, Any]:
    return inspector.status()


@app.get("/internal/settings", include_in_schema=False)
@app.get("/api/settings", include_in_schema=False)
def app_settings() -> dict[str, str]:
    return {"snapshot_directory": str(config.SNAPSHOT_DIR.resolve()), "recording_directory": str(config.RECORDING_DIR.resolve())}


@app.post("/internal/settings/snapshot-directory", include_in_schema=False)
@app.post("/api/settings/snapshot-directory", include_in_schema=False)
def set_snapshot_directory(request: SnapshotPathRequest) -> dict[str, str]:
    def set_directory() -> dict[str, str]:
        directory = inspector._snapshot_directory(request.path)
        directory.mkdir(parents=True, exist_ok=True)
        return {"snapshot_directory": str(directory)}

    return invoke(set_directory)


@app.post("/internal/scanAdb", include_in_schema=False)
@app.get("/api/devices", include_in_schema=False)
def list_devices() -> dict[str, Any]:
    return invoke(lambda: {"devices": inspector.adb_devices()})


@app.post("/internal/connect", include_in_schema=False)
@app.post("/api/connect", include_in_schema=False)
def connect_device(request: ConnectRequest) -> dict[str, Any]:
    def connect() -> dict[str, Any]:
        result = inspector.connect(request.serial)
        inspections.clear()
        return result
    return invoke(connect)


@app.post("/internal/disconnect", include_in_schema=False)
def disconnect_device() -> dict[str, str]:
    inspections.clear()
    return inspector.disconnect()


@app.post("/internal/capture", include_in_schema=False)
def capture_inspection(request: CaptureRequest) -> dict[str, Any]:
    return invoke(lambda: inspections.capture(inspector, request.hierarchy))


@app.post("/internal/capture/find", include_in_schema=False)
def find_in_capture(request: FrozenSearchRequest) -> dict[str, Any]:
    def find() -> dict[str, Any]:
        matches = inspections.search(request.snapshot_id, request.query, inspector)
        return {"count": len(matches), "elements": matches}
    return invoke(find)


@app.post("/internal/capture/save", include_in_schema=False)
def save_capture(request: FrozenSnapshotRequest) -> dict[str, Any]:
    def save() -> dict[str, Any]:
        item = inspections.get(request.snapshot_id, inspector)
        region = request.region
        if request.kind == "element":
            element = next((node for node in item.hierarchy["elements"] if node["xpath"] == request.xpath), None)
            if element is None:
                raise ValueError("元素不在当前快照中，请重新选择")
            box = element["bounds"]
            region = {"x1": box["left"], "y1": box["top"], "x2": box["right"], "y2": box["bottom"]}
        if request.kind not in {"full", "element", "region"}:
            raise ValueError("不支持的截图类型")
        if region is not None and set(region) != {"x1", "y1", "x2", "y2"}:
            raise ValueError("选区需要 x1、y1、x2、y2")
        return inspector.save_snapshot(
            request.kind, request.custom_path,
            region=region, image=item.image,
        )
    return invoke(save)


@app.post("/internal/getScreenImg", include_in_schema=False)
@app.get("/api/screen", include_in_schema=False)
def screen_preview() -> dict[str, Any]:
    def capture() -> dict[str, Any]:
        image = inspector.screenshot()
        buffer = io.BytesIO()
        image.save(buffer, format="JPEG", quality=config.SCREENSHOT_QUALITY)
        return {
            "image": base64.b64encode(buffer.getvalue()).decode("ascii"),
            "width": image.width,
            "height": image.height,
        }

    return invoke(capture)


@app.websocket("/ws/screen")
@app.websocket("/internal/ws/screen")
async def screen_stream(websocket: WebSocket) -> None:
    await websocket.accept()
    command_task: asyncio.Task[Any] | None = None
    try:
        stream_device = inspector.require_device()
        image = await asyncio.to_thread(inspector.screenshot)
        await websocket.send_json({"type": "screen", "width": image.width, "height": image.height})
        command_task = asyncio.create_task(websocket.receive_json())
        paused = False
        frame_interval = 1 / config.SCREEN_STREAM_FPS
        while True:
            if inspector.device is not stream_device:
                await websocket.close(code=1000)
                return
            if command_task.done():
                command = command_task.result()
                command_task = asyncio.create_task(websocket.receive_json())
                if command.get("type") == "pause":
                    paused = True
                elif command.get("type") == "resume":
                    paused = False
            if paused:
                await asyncio.wait({command_task}, timeout=1)
                continue
            started = asyncio.get_running_loop().time()
            image = await asyncio.to_thread(inspector.screenshot)
            frame = io.BytesIO()
            image.save(frame, format="JPEG", quality=config.SCREEN_STREAM_QUALITY)
            await websocket.send_bytes(frame.getvalue())
            remaining = frame_interval - (asyncio.get_running_loop().time() - started)
            if remaining > 0:
                await asyncio.wait({command_task}, timeout=remaining)
    except WebSocketDisconnect:
        return
    except Exception as exc:
        logger.exception("Screen stream failed")
        try:
            await websocket.send_json({"type": "error", "message": str(exc)})
        except WebSocketDisconnect:
            pass
        await websocket.close(code=1011)
    finally:
        if command_task and not command_task.done():
            command_task.cancel()


@app.post("/internal/tree", include_in_schema=False)
@app.get("/api/hierarchy", include_in_schema=False)
def hierarchy() -> dict[str, Any]:
    return invoke(inspector.hierarchy)


@app.post("/internal/find", include_in_schema=False)
@app.post("/api/search", include_in_schema=False)
def search_elements(request: SearchRequest) -> dict[str, Any]:
    elements = invoke(lambda: inspector.search(request.query))
    return {"count": len(elements), "elements": elements}


@app.post("/internal/inspect", include_in_schema=False)
@app.post("/api/inspect", include_in_schema=False)
def inspect_point(request: PointRequest) -> dict[str, Any]:
    return invoke(lambda: inspector.element_for_point(request.x, request.y))


@app.post("/internal/clickElem", include_in_schema=False)
def click_element(request: ElementActionRequest) -> dict[str, str]:
    def click() -> dict[str, str]:
        if not request.selector:
            raise ValueError("请先选择元素")
        inspector.native_selector(request.selector).click()
        return {"message": "元素已点击"}
    return invoke(click)


@app.post("/internal/inputText", include_in_schema=False)
def input_text(request: ElementActionRequest) -> dict[str, str]:
    def send() -> dict[str, str]:
        if request.text is None:
            raise ValueError("请输入文本")
        if request.selector:
            inspector.native_selector(request.selector).set_text(request.text)
        else:
            inspector.require_device().send_keys(request.text, clear=False)
        return {"message": "文本已发送"}
    return invoke(send)


@app.post("/internal/clearText", include_in_schema=False)
def clear_text(request: ElementActionRequest) -> dict[str, str]:
    def clear() -> dict[str, str]:
        if request.selector:
            target = inspector.native_selector(request.selector)
            if request.selector.strip().startswith(("/", "(")):
                target.set_text("")
            else:
                target.clear_text()
        else:
            inspector.require_device().clear_text()
        return {"message": "输入框已清空"}
    return invoke(clear)


@app.post("/internal/action", include_in_schema=False)
@app.post("/api/action", include_in_schema=False)
def perform_action(request: ActionRequest) -> dict[str, str]:
    def perform() -> dict[str, str]:
        device = inspector.require_device()
        if request.action == "back":
            device.press("back")
        elif request.action == "home":
            device.press("home")
        elif request.action == "recent":
            device.press("recent")
        elif request.action == "swipe":
            coordinates = (request.start_x, request.start_y, request.end_x, request.end_y)
            if any(value is None for value in coordinates):
                raise ValueError("滑动操作需要起点和终点坐标")
            device.swipe(*coordinates, duration=request.duration or 0.35)
        elif request.action == "long_click":
            if request.x is None or request.y is None:
                raise ValueError("长按操作需要设备坐标")
            device.long_click(request.x, request.y, duration=request.duration or 0.8)
        elif request.action == "click":
            if request.x is None or request.y is None:
                raise ValueError("点击操作需要设备坐标")
            device.click(request.x, request.y)
        elif request.action == "input":
            if request.text is None:
                raise ValueError("请输入要发送的文本")
            device.send_keys(request.text, clear=False)
        elif request.action == "clear":
            device.clear_text()
        else:
            raise ValueError("不支持的设备操作")
        return {"message": "操作已完成"}

    return invoke(perform)


@app.get("/snapshot/full")
def snapshot_full(
    custom_path: str | None = Query(default=None, max_length=2048),
    save_dir: str | None = Query(default=None, max_length=2048),
    filename: str | None = Query(default=None, min_length=1, max_length=255),
) -> dict[str, Any]:
    return invoke(lambda: inspector.save_snapshot("full", snapshot_directory(save_dir, custom_path), filename=filename))


def snapshot_directory(save_dir: str | None, custom_path: str | None) -> str | None:
    if save_dir and custom_path and inspector._snapshot_directory(save_dir) != inspector._snapshot_directory(custom_path):
        raise ValueError("save_dir 与 custom_path 不能指定不同目录")
    return save_dir or custom_path


@app.post("/internal/snapshots/copy-file", include_in_schema=False)
@app.post("/api/snapshots/copy-file", include_in_schema=False)
def copy_snapshot_file(payload: SnapshotPathRequest, request: Request) -> dict[str, str]:
    media._local_mutation(request)
    return media.copy_file(media.PathRequest(kind="snapshot", path=payload.path))


@app.get("/snapshot/element")
def snapshot_element(
    selector: str = Query(min_length=1, max_length=2048),
    custom_path: str | None = Query(default=None, max_length=2048),
    save_dir: str | None = Query(default=None, max_length=2048),
    filename: str | None = Query(default=None, min_length=1, max_length=255),
) -> dict[str, Any]:
    return invoke(lambda: inspector.save_snapshot(
        "element", snapshot_directory(save_dir, custom_path), selector=selector, filename=filename,
    ))


@app.get("/snapshot/region")
def snapshot_region(
    x1: int = Query(ge=0),
    y1: int = Query(ge=0),
    x2: int = Query(ge=0),
    y2: int = Query(ge=0),
    custom_path: str | None = Query(default=None, max_length=2048),
    save_dir: str | None = Query(default=None, max_length=2048),
    filename: str | None = Query(default=None, min_length=1, max_length=255),
) -> dict[str, Any]:
    region = {"x1": x1, "y1": y1, "x2": x2, "y2": y2}
    return invoke(lambda: inspector.save_snapshot(
        "region", snapshot_directory(save_dir, custom_path), region=region, filename=filename,
    ))


class BrowserServer(uvicorn.Server):
    def __init__(self, server_config: uvicorn.Config, open_browser: bool = True):
        super().__init__(server_config)
        self.open_browser = open_browser

    async def startup(self, sockets=None) -> None:
        await super().startup(sockets)
        if self.started and not self.should_exit and self.open_browser:
            self.open_browser = False
            host = self.config.host
            if host in {"0.0.0.0", "::"}:
                host = "127.0.0.1"
            if ":" in host:
                host = f"[{host}]"
            url = f"http://{host}:{self.config.port}"
            try:
                opened = await asyncio.to_thread(webbrowser.open, url)
            except webbrowser.Error as exc:
                logger.warning("Browser launch failed: %s", exc)
                opened = False
            if not opened:
                print(f"Please open {url} in your browser.")


def main() -> None:
    parser = argparse.ArgumentParser(description="UiHound browser-based Android UI inspector")
    parser.add_argument("--host", default=config.HOST)
    parser.add_argument("--port", type=int, default=config.PORT)
    parser.add_argument("--no-browser", action="store_true", help="Do not open the default browser")
    args = parser.parse_args()
    if not 1 <= args.port <= 65535:
        parser.error("--port must be between 1 and 65535")
    server = BrowserServer(
        uvicorn.Config(app, host=args.host, port=args.port),
        open_browser=config.settings.open_browser and not args.no_browser,
    )
    server.run()


if __name__ == "__main__":
    main()
