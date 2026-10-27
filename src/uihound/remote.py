"""Native scrcpy video and control transport for the browser remote view.

The WebSocket sends a JSON config (codec, width, height), then binary frames:
one byte keyframe flag, eight bytes unsigned big-endian microsecond PTS, and
one Annex-B H.264 access unit. A new config may precede a codec change.
"""

import asyncio
import hashlib
import json
import logging
import math
import os
import secrets
import socket
import struct
import time
from pathlib import Path

from adbutils import adb
from fastapi import APIRouter, WebSocket, WebSocketDisconnect
from PIL import Image
from starlette.websockets import WebSocketState

from .inspector import inspector
from .config import settings


logger = logging.getLogger(__name__)
router = APIRouter()
VERSION = "3.3.4"
SERVER_SHA256 = "8588238c9a5a00aa542906b6ec7e6d5541d9ffb9b5d0f6e1bc0e365e2303079e"
SERVER_PATH = Path(os.environ.get(
    "UIHOUND_SCRCPY_SERVER",
    Path(__file__).resolve().parent / "binaries" / f"scrcpy-server-v{VERSION}",
))
MAX_PACKET = 8 * 1024 * 1024
KEY_CODES = {"home": 3, "back": 4, "recent": 187, "volume_up": 24, "volume_down": 25}


def server_path() -> Path:
    """Return the verified bundled server or an explicitly configured copy."""
    path = SERVER_PATH.expanduser()
    if path.is_file():
        if hashlib.sha256(path.read_bytes()).hexdigest() == SERVER_SHA256:
            return path
        raise RuntimeError(f"scrcpy-server checksum mismatch at {path}; reinstall uihound or provide a verified copy")
    raise RuntimeError(
        f"scrcpy-server v{VERSION} not found at {path}; reinstall uihound "
        "or set UIHOUND_SCRCPY_SERVER to a verified copy"
    )


def server_ready() -> bool:
    path = SERVER_PATH.expanduser()
    return path.is_file() and hashlib.sha256(path.read_bytes()).hexdigest() == SERVER_SHA256


def recv_exact(sock: socket.socket, length: int) -> bytes:
    data = bytearray()
    while len(data) < length:
        chunk = sock.recv(length - len(data))
        if not chunk:
            raise ConnectionError("scrcpy video stream ended")
        data.extend(chunk)
    return bytes(data)


def sps_codec(config: bytes) -> str:
    """Extract AVC profile, compatibility and level from an Annex-B SPS."""
    for marker in (b"\x00\x00\x00\x01", b"\x00\x00\x01"):
        start = 0
        while (index := config.find(marker, start)) >= 0:
            nal = index + len(marker)
            if len(config) >= nal + 4 and config[nal] & 0x1f == 7:
                return "avc1." + config[nal + 1:nal + 4].hex()
            start = nal
    raise ValueError("scrcpy H.264 config has no SPS")


def position(message: dict) -> tuple[int, int, int, int]:
    values = []
    for name in ("x", "y", "width", "height"):
        value = message.get(name)
        if isinstance(value, bool) or not isinstance(value, (float, int)) or not math.isfinite(value):
            raise ValueError(f"{name} must be a finite number")
        values.append(value)
    x, y, width, height = values
    if not (1 <= width <= 65535 and 1 <= height <= 65535 and 0 <= x < width and 0 <= y < height):
        raise ValueError("Control position is outside the video bounds")
    w, h = round(width), round(height)
    return min(round(x), w - 1), min(round(y), h - 1), w, h


class Controls:
    def __init__(self, sock: socket.socket):
        self.sock = sock
        self.active: set[int] = set()
        self.positions: dict[int, tuple[int, int, int, int]] = {}

    def send(self, message: dict) -> None:
        if not isinstance(message, dict):
            raise ValueError("Control message must be an object")
        kind = message.get("type")
        if kind == "key":
            key = message.get("key")
            if key not in KEY_CODES:
                raise ValueError("Unsupported remote key")
            for action in (0, 1):
                self.sock.sendall(struct.pack(">BBIII", 0, action, KEY_CODES[key], 0, 0))
        elif kind == "touch":
            x, y, width, height = position(message)
            pointer = message.get("pointerId", 1)
            if isinstance(pointer, bool) or not isinstance(pointer, int) or not 0 <= pointer < (1 << 63):
                raise ValueError("Invalid touch pointerId")
            action_name = message.get("action")
            actions = {"down": 0, "up": 1, "move": 2, "cancel": 3}
            if action_name not in actions:
                raise ValueError("Unsupported touch action")
            if action_name == "down" and pointer in self.active:
                raise ValueError("Touch pointer is already down")
            if action_name != "down" and pointer not in self.active:
                raise ValueError("Touch pointer is not down")
            action = actions[action_name]
            pressure = 0 if action_name in {"up", "cancel"} else 0xffff
            self.sock.sendall(struct.pack(">BBQIIHHHII", 2, action, pointer, x, y, width, height, pressure, 0, 0))
            if action_name == "down":
                self.active.add(pointer)
                self.positions[pointer] = (x, y, width, height)
            elif action_name == "move":
                self.positions[pointer] = (x, y, width, height)
            elif action_name in {"up", "cancel"}:
                self.active.remove(pointer)
                self.positions.pop(pointer, None)
        elif kind == "scroll":
            x, y, width, height = position(message)
            deltas = (message.get("deltaX"), message.get("deltaY"))
            if any(isinstance(value, bool) or not isinstance(value, (float, int)) or not math.isfinite(value) for value in deltas):
                raise ValueError("Scroll deltas must be finite numbers")
            horizontal, vertical = (round(max(-16, min(16, value)) * 32767 / 16) for value in deltas)
            self.sock.sendall(struct.pack(">BIIHHhhI", 3, x, y, width, height, horizontal, vertical, 0))
        else:
            raise ValueError("Unsupported remote control type")

    def release(self) -> None:
        for pointer in tuple(self.active):
            x, y, width, height = self.positions[pointer]
            self.sock.sendall(struct.pack(">BBQIIHHHII", 2, 3, pointer, x, y, width, height, 0, 0, 0))
            self.active.remove(pointer)
            del self.positions[pointer]


class ScrcpySession:
    def __init__(self, serial: str, display_id: int = 0, *, full_resolution: bool = False):
        self.serial = serial
        self.display_id = display_id
        self.full_resolution = full_resolution
        self.device = adb.device(serial=serial)
        self.scid = secrets.randbelow(0x7fffffff) + 1
        self.remote = f"localabstract:scrcpy_{self.scid:08x}"
        self.local: str | None = None
        self.shell = None
        self.video: socket.socket | None = None
        self.control: socket.socket | None = None
        self.width = 0
        self.height = 0

    def start(self) -> None:
        try:
            path = server_path()
            target = f"/data/local/tmp/uihound-scrcpy-{self.scid:08x}.jar"
            self.target = target
            self.device.sync.push(str(path), target, check=True)
            with socket.socket() as probe:
                probe.bind(("127.0.0.1", 0))
                port = probe.getsockname()[1]
            local = f"tcp:{port}"
            self.device.forward(local, self.remote, norebind=True)
            self.local = local
            options = [
                f"CLASSPATH={target}", "app_process", "/", "com.genymobile.scrcpy.Server",
                VERSION, f"scid={self.scid:08x}", "log_level=warn", "audio=false",
                "video_codec=h264", f"max_size={0 if self.full_resolution else settings.scrcpy_max_size}",
                f"max_fps={settings.scrcpy_max_fps}", f"video_bit_rate={settings.scrcpy_bit_rate}",
                "tunnel_forward=true", "send_frame_meta=true", "control=true",
                "clipboard_autosync=false", "power_off_on_close=false",
                f"display_id={self.display_id}",
            ]
            if self.full_resolution:
                options.append("power_on=false")
            self.shell = self.device.shell(" ".join(options), stream=True)
            deadline = time.monotonic() + 10
            while True:
                try:
                    remaining = max(.1, deadline - time.monotonic())
                    self.video = socket.create_connection(("127.0.0.1", port), timeout=min(1, remaining))
                    self.control = socket.create_connection(("127.0.0.1", port), timeout=min(1, remaining))
                    self.video.settimeout(max(.1, deadline - time.monotonic()))
                    if recv_exact(self.video, 1) != b"\x00":
                        raise ConnectionError("scrcpy handshake failed")
                    break
                except (OSError, ConnectionError) as exc:
                    for sock in (self.control, self.video):
                        if sock is not None:
                            sock.close()
                    self.video = self.control = None
                    if time.monotonic() >= deadline:
                        raise RuntimeError(
                            f"scrcpy server did not start for {self.serial}: {exc}; check Android API level and device logs"
                        ) from exc
                    time.sleep(.1)
            recv_exact(self.video, 64)  # device name
            if recv_exact(self.video, 4) != b"h264":
                raise RuntimeError("Device does not support H.264 scrcpy video")
            self.width, self.height = struct.unpack(">II", recv_exact(self.video, 8))
            if not (0 < self.width <= 65535 and 0 < self.height <= 65535):
                raise RuntimeError("Invalid scrcpy video dimensions")
            self.video.setblocking(False)
            self.control.setblocking(True)
            self.control.settimeout(3)
        except Exception:
            self.close()
            raise

    def close(self) -> None:
        for sock in (self.control, self.video):
            if sock is not None:
                sock.close()
        self.control = self.video = None
        if self.shell is not None:
            self.shell.close()
            self.shell = None
        if self.local is not None:
            try:
                self.device.forward_remove(self.local)
            except Exception as exc:
                logger.warning("Could not remove scrcpy forward %s: %s", self.local, exc)
            self.local = None
        if hasattr(self, "target"):
            try:
                self.device.shell(f"rm {self.target}", timeout=3)
            except Exception as exc:
                logger.warning("Could not remove scrcpy server %s: %s", self.target, exc)
            del self.target


def capture_frame(serial: str, display_id: int) -> Image.Image:
    """Capture a native-size frame through Android's scrcpy display surface."""
    import av

    session = ScrcpySession(serial, display_id, full_resolution=True)
    try:
        session.start()
        if session.video is None:
            raise RuntimeError("scrcpy did not provide a screenshot video stream")
        decoder = av.CodecContext.create("h264", "r")
        deadline = time.monotonic() + 10
        config = b""
        total = 0
        while time.monotonic() < deadline:
            session.video.settimeout(max(.1, deadline - time.monotonic()))
            pts, size = struct.unpack(">QI", recv_exact(session.video, 12))
            if not 0 < size <= MAX_PACKET:
                raise RuntimeError("Invalid scrcpy screenshot packet length")
            total += size
            if total > 32 * 1024 * 1024:
                raise RuntimeError("scrcpy screenshot exceeded 32 MiB")
            packet = recv_exact(session.video, size)
            if pts & (1 << 63):
                config += packet
                if len(config) > 65536:
                    raise RuntimeError("scrcpy screenshot codec config too large")
                continue
            for frame in decoder.decode(av.Packet(config + packet)):
                return frame.to_image().convert("RGB")
            config = b""
        raise RuntimeError("Timed out waiting for a scrcpy screenshot")
    except av.error.FFmpegError as exc:
        raise RuntimeError(f"scrcpy screenshot decode failed: {exc}") from exc
    finally:
        session.close()


async def read_exact(sock: socket.socket, size: int) -> bytes:
    loop = asyncio.get_running_loop()
    data = bytearray()
    while len(data) < size:
        block = await loop.sock_recv(sock, size - len(data))
        if not block:
            raise ConnectionError("scrcpy video stream ended")
        data.extend(block)
    return bytes(data)


async def stream(session: ScrcpySession, websocket: WebSocket, connected_device: object) -> None:
    assert session.video is not None
    config = b""
    codec = ""
    while True:
        if inspector.device is not connected_device or inspector.serial != session.serial or inspector.display_id != session.display_id:
            raise ConnectionError("Connected device or display changed; remote session ended")
        header = await read_exact(session.video, 12)
        pts, size = struct.unpack(">QI", header)
        if not 0 < size <= MAX_PACKET:
            raise ConnectionError("Invalid scrcpy packet length")
        packet = await read_exact(session.video, size)
        if pts & (1 << 63):
            config += packet
            if len(config) > 65536:
                raise ConnectionError("scrcpy codec config too large")
            continue
        is_key = bool(pts & (1 << 62))
        if config:
            new_codec = sps_codec(config)
            if new_codec != codec:
                await websocket.send_json({"type": "config", "codec": new_codec, "width": session.width, "height": session.height})
                codec = new_codec
            packet = config + packet
            config = b""
        if not codec:
            raise ConnectionError("scrcpy did not supply H.264 codec config")
        await websocket.send_bytes(bytes((int(is_key),)) + struct.pack(">Q", pts & ((1 << 62) - 1)) + packet)


async def receive_controls(session: ScrcpySession, websocket: WebSocket, connected_device: object) -> None:
    assert session.control is not None
    controls = Controls(session.control)
    try:
        while True:
            raw = await websocket.receive_text()
            if inspector.device is not connected_device or inspector.serial != session.serial or inspector.display_id != session.display_id:
                raise ConnectionError("Connected device or display changed; remote session ended")
            if len(raw) > 2048:
                await websocket.send_json({"type": "error", "message": "Remote control message too large"})
                continue
            try:
                await run_control(controls.send, json.loads(raw))
            except (ValueError, TypeError, json.JSONDecodeError) as exc:
                await websocket.send_json({"type": "error", "message": str(exc)})
    finally:
        if session.control is not None and controls.active:
            try:
                await run_control(controls.release)
            except OSError:
                logger.warning("Could not release active scrcpy pointers on disconnect")


async def run_control(operation, *args) -> None:
    # Keep ordered writes off the event loop and finish in-flight input before cleanup.
    task = asyncio.create_task(asyncio.to_thread(operation, *args))
    try:
        await asyncio.shield(task)
    finally:
        if not task.done():
            await asyncio.gather(task, return_exceptions=True)


async def watch_device(session: ScrcpySession, connected_device: object) -> None:
    while inspector.device is connected_device and inspector.serial == session.serial and inspector.display_id == session.display_id:
        await asyncio.sleep(.5)
    raise ConnectionError("Connected device or display changed; remote session ended")


@router.get("/remote/status")
def remote_status() -> dict:
    return {
        "available": bool(inspector.device is not None and inspector.serial),
        "version": VERSION,
        "server_ready": server_ready(),
    }


async def send_error(websocket: WebSocket, error: Exception) -> None:
    if websocket.client_state == WebSocketState.CONNECTED and websocket.application_state == WebSocketState.CONNECTED:
        try:
            await websocket.send_json({"type": "error", "message": str(error)})
        except WebSocketDisconnect:
            logger.debug("Remote client disconnected before error delivery")


@router.websocket("/ws/remote")
async def remote_websocket(websocket: WebSocket) -> None:
    origin = websocket.headers.get("origin")
    host = websocket.headers.get("host", "")
    if origin and origin not in {f"http://{host}", f"https://{host}"}:
        await websocket.close(code=1008)
        return
    await websocket.accept()
    device = inspector.device
    serial = inspector.serial
    if device is None or not serial:
        await send_error(websocket, RuntimeError("Connect an Android device before starting remote view"))
        await websocket.close(code=1011)
        return
    session = ScrcpySession(serial)
    session.display_id = getattr(inspector, "display_id", 0)
    start_task: asyncio.Task | None = None
    tasks: list[asyncio.Task] = []
    try:
        await websocket.send_json({"type": "status", "message": f"正在准备 scrcpy {VERSION} · display {session.display_id}"})
        start_task = asyncio.create_task(asyncio.to_thread(session.start))
        await asyncio.shield(start_task)
        tasks = [
            asyncio.create_task(stream(session, websocket, device)),
            asyncio.create_task(receive_controls(session, websocket, device)),
            asyncio.create_task(watch_device(session, device)),
        ]
        done, pending = await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
        for task in pending:
            task.cancel()
        await asyncio.gather(*pending, return_exceptions=True)
        for task in done:
            try:
                task.result()
            except WebSocketDisconnect:
                pass
            except (ConnectionError, OSError, RuntimeError, ValueError) as exc:
                logger.warning("scrcpy remote ended: %s", exc)
                await send_error(websocket, exc)
    except WebSocketDisconnect:
        pass
    except (ConnectionError, OSError, RuntimeError, ValueError) as exc:
        logger.warning("scrcpy remote startup failed: %s", exc)
        await send_error(websocket, exc)
    finally:
        if start_task is not None and not start_task.done():
            await asyncio.gather(start_task, return_exceptions=True)
        for task in tasks:
            if not task.done():
                task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
        await asyncio.to_thread(session.close)
        try:
            await websocket.close()
        except (WebSocketDisconnect, RuntimeError, OSError):
            pass
