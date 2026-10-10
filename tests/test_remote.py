import asyncio
import socket
import struct
import threading
import unittest
from pathlib import Path
from unittest.mock import patch

from uihound import remote


class RemoteProtocolTests(unittest.TestCase):
    def test_control_messages_are_native_scrcpy_messages(self):
        left, right = socket.socketpair()
        try:
            controls = remote.Controls(left)
            controls.send({"type": "touch", "action": "down", "x": 12, "y": 20, "width": 100, "height": 200})
            self.assertEqual(struct.unpack(">BBQIIHHHII", remote.recv_exact(right, 32))[:7],
                             (2, 0, 1, 12, 20, 100, 200))
            controls.send({"type": "touch", "action": "move", "x": 13, "y": 21, "width": 100, "height": 200})
            self.assertEqual(remote.recv_exact(right, 32)[1], 2)
            controls.send({"type": "touch", "action": "up", "x": 13, "y": 21, "width": 100, "height": 200})
            self.assertEqual(remote.recv_exact(right, 32)[1], 1)
            controls.send({"type": "scroll", "x": 25, "y": 40, "width": 100, "height": 200, "deltaX": -1, "deltaY": 2})
            self.assertEqual(struct.unpack(">BIIHHhhI", remote.recv_exact(right, 21)),
                             (3, 25, 40, 100, 200, -2048, 4096, 0))
            controls.send({"type": "key", "key": "home"})
            self.assertEqual(struct.unpack(">BBIII", remote.recv_exact(right, 14)), (0, 0, 3, 0, 0))
            self.assertEqual(struct.unpack(">BBIII", remote.recv_exact(right, 14)), (0, 1, 3, 0, 0))
        finally:
            left.close()
            right.close()

    def test_invalid_control_never_reaches_device(self):
        sock = unittest.mock.Mock()
        controls = remote.Controls(sock)
        valid = {"type": "touch", "action": "up", "x": 1, "y": 2, "width": 100, "height": 100}
        with self.assertRaises(ValueError):
            controls.send(valid)
        with self.assertRaises(ValueError):
            controls.send({**valid, "action": "down", "x": float("nan")})
        with self.assertRaises(ValueError):
            controls.send({**valid, "action": "down", "x": 200})
        with self.assertRaises(ValueError):
            controls.send({"type": "key", "key": "shell"})
        sock.sendall.assert_not_called()

    def test_cancel_uses_last_real_video_dimensions(self):
        sock = unittest.mock.Mock()
        controls = remote.Controls(sock)
        controls.send({"type": "touch", "action": "down", "x": 10, "y": 20, "width": 720, "height": 1280})
        controls.release()
        packet = struct.unpack(">BBQIIHHHII", sock.sendall.call_args.args[0])
        self.assertEqual(packet[:7], (2, 3, 1, 10, 20, 720, 1280))
        self.assertFalse(controls.active)

    def test_failed_forward_does_not_remove_existing_forward(self):
        session = remote.ScrcpySession("serial")
        session.device = unittest.mock.Mock()
        session.device.forward.side_effect = RuntimeError("occupied")
        with patch.object(remote, "server_path", return_value=Path(__file__)):
            with self.assertRaisesRegex(RuntimeError, "occupied"):
                session.start()
        session.device.forward_remove.assert_not_called()

    def test_codec_and_frame_framing(self):
        async def run():
            left, right = socket.socketpair()
            left.setblocking(False)
            session = unittest.mock.Mock(video=left, width=720, height=1280, serial="device")
            messages = []

            class WebSocket:
                async def send_json(self, message):
                    messages.append(message)

                async def send_bytes(self, payload):
                    messages.append(payload)

            device = object()
            sps = b"\x00\x00\x00\x01\x67\x42\xc0\x1e"
            pps = b"\x00\x00\x00\x01\x68\xce\x06\xe2"
            frame = b"\x00\x00\x00\x01\x65\x88\x84"
            def packet(pts, payload):
                return struct.pack(">QI", pts, len(payload)) + payload
            try:
                with patch.dict(remote.inspector.__dict__, {"device": device, "serial": "device"}):
                    task = asyncio.create_task(remote.stream(session, WebSocket(), device))
                    right.sendall(packet(1 << 63, sps + pps) + packet((1 << 62) | 42, frame))
                    while len(messages) < 2:
                        await asyncio.sleep(.01)
                    task.cancel()
                    await asyncio.gather(task, return_exceptions=True)
                self.assertEqual(messages[0], {"type": "config", "codec": "avc1.42c01e", "width": 720, "height": 1280})
                self.assertEqual(messages[1], b"\x01" + struct.pack(">Q", 42) + sps + pps + frame)
            finally:
                left.close()
                right.close()
        asyncio.run(run())

    def test_binary_verification_rejects_wrong_content(self):
        with patch.object(remote, "SERVER_PATH", Path(__file__)):
            self.assertFalse(remote.server_ready())
            with self.assertRaisesRegex(RuntimeError, "checksum mismatch"):
                remote.server_path()

    def test_status_never_downloads_or_starts_scrcpy(self):
        download = unittest.mock.Mock()
        with patch("urllib.request.urlopen", download), patch.object(remote, "server_ready", return_value=False):
            self.assertFalse(remote.remote_status()["server_ready"])
            download.assert_not_called()

    def test_bundled_server_is_verified_without_network(self):
        with patch("urllib.request.urlopen", side_effect=AssertionError("Network must not be used")):
            self.assertTrue(remote.server_ready())
            self.assertEqual(remote.server_path(), remote.SERVER_PATH.expanduser())

    def test_missing_server_reports_error_without_network(self):
        with patch.object(remote, "SERVER_PATH", Path(__file__).with_name("missing-scrcpy-server")), \
                patch("urllib.request.urlopen", side_effect=AssertionError("Network must not be used")):
            self.assertFalse(remote.server_ready())
            with self.assertRaisesRegex(RuntimeError, "not found"):
                remote.server_path()

    def test_session_cleanup_removes_only_its_forward_and_server(self):
        session = remote.ScrcpySession("serial")
        session.device = unittest.mock.Mock()
        session.local = "tcp:49152"
        session.target = "/data/local/tmp/uihound-scrcpy-12345678.jar"
        session.shell = unittest.mock.Mock()
        session.control = unittest.mock.Mock()
        session.video = unittest.mock.Mock()
        shell = session.shell
        control = session.control
        video = session.video
        session.close()
        session.device.forward_remove.assert_called_once_with("tcp:49152")
        session.device.shell.assert_called_once_with("rm /data/local/tmp/uihound-scrcpy-12345678.jar", timeout=3)
        shell.close.assert_called_once()
        control.close.assert_called_once()
        video.close.assert_called_once()
        session.close()
        session.device.forward_remove.assert_called_once()

    def test_forward_handshake_retries_until_device_socket_is_ready(self):
        session = remote.ScrcpySession("serial")
        session.device = unittest.mock.Mock()
        stale_video, stale_control = unittest.mock.Mock(), unittest.mock.Mock()
        video, control = unittest.mock.Mock(), unittest.mock.Mock()
        with patch.object(remote, "server_path", return_value=Path(__file__)), \
             patch.object(remote.socket, "create_connection",
                          side_effect=[stale_video, stale_control, video, control]) as connect, \
             patch.object(remote, "recv_exact",
                          side_effect=[ConnectionError("ADB remote socket unavailable"), b"\x00",
                                       bytes(64), b"h264", struct.pack(">II", 720, 1280)]), \
             patch.object(remote.time, "sleep"):
            session.start()
        self.assertEqual(connect.call_count, 4)
        stale_video.close.assert_called_once()
        stale_control.close.assert_called_once()
        self.assertEqual((session.width, session.height), (720, 1280))
        session.close()

    def test_cancelled_websocket_waits_for_start_before_cleanup(self):
        async def run():
            entered = threading.Event()
            release = threading.Event()
            closed = threading.Event()
            device = object()

            class Session:
                def __init__(self, serial):
                    self.serial = serial

                def start(self):
                    entered.set()
                    release.wait(2)

                def close(self):
                    closed.set()

            websocket = unittest.mock.Mock()
            websocket.headers = {"host": "127.0.0.1:8000", "origin": "http://127.0.0.1:8000"}
            websocket.accept = unittest.mock.AsyncMock()
            websocket.send_json = unittest.mock.AsyncMock()
            websocket.close = unittest.mock.AsyncMock()
            try:
                with patch.dict(remote.inspector.__dict__, {"device": device, "serial": "device"}), \
                     patch.dict(remote.__dict__, {"ScrcpySession": Session}):
                    task = asyncio.create_task(remote.remote_websocket(websocket))
                    await asyncio.wait_for(asyncio.to_thread(entered.wait, 1), 2)
                    task.cancel()
                    await asyncio.sleep(.01)
                    self.assertFalse(closed.is_set())
                    release.set()
                    with self.assertRaises(asyncio.CancelledError):
                        await asyncio.wait_for(task, 2)
                    self.assertTrue(closed.is_set())
            finally:
                release.set()
        asyncio.run(run())

    def test_client_close_during_video_cleans_up_without_error(self):
        async def run():
            session = unittest.mock.Mock(serial="device")
            websocket = unittest.mock.Mock()
            websocket.headers = {"host": "localhost"}
            websocket.accept = unittest.mock.AsyncMock()
            websocket.send_json = unittest.mock.AsyncMock()
            websocket.close = unittest.mock.AsyncMock(side_effect=remote.WebSocketDisconnect())

            async def wait_for_disconnect(*_):
                await asyncio.sleep(60)

            with patch.dict(remote.inspector.__dict__, {"device": object(), "serial": "device"}), \
                 patch.object(remote, "ScrcpySession", return_value=session), \
                 patch.object(remote, "stream", new=unittest.mock.AsyncMock(side_effect=remote.WebSocketDisconnect())), \
                 patch.object(remote, "receive_controls", new=wait_for_disconnect):
                await asyncio.wait_for(remote.remote_websocket(websocket), 2)
            session.close.assert_called_once()
        asyncio.run(run())


if __name__ == "__main__":
    unittest.main()
