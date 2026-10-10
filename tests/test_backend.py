import asyncio
import base64
import importlib
import io
import json
import socket
import tempfile
import unittest
import xml.etree.ElementTree as ET
from pathlib import Path
from unittest.mock import AsyncMock, Mock, patch
from urllib.parse import urlencode

import uvicorn
from PIL import Image
from lxml import etree
from uiautomator2.xpath import PageSource

from uihound import config, media
from uihound.inspector import Inspector

web = importlib.import_module("uihound.main")

XML = """<hierarchy rotation="0"><node class="android.widget.FrameLayout" bounds="[0,0][100,200]">
<node class="android.widget.TextView" resource-id="app:id/title" text="Hello" bounds="[10,20][51,61]" clickable="true"/>
<node class="android.widget.TextView" text="Duplicate" bounds="[0,70][100,90]"/>
<node class="android.widget.TextView" text="Duplicate" bounds="[0,100][100,120]"/>
</node></hierarchy>"""


def call_api(path, method="GET", data=None, headers=None):
    async def call():
        messages = []
        target, _, query = path.partition("?")
        scope = {
            "type": "http", "http_version": "1.1", "asgi": {"version": "3.0"},
            "method": method, "scheme": "http", "path": target, "raw_path": target.encode(),
            "query_string": query.encode(), "root_path": "",
            "headers": [(b"host", b"127.0.0.1:8000"), (b"content-type", b"application/json"), *(headers or [])],
            "client": ("127.0.0.1", 1234), "server": ("127.0.0.1", 8000),
        }

        async def receive():
            return {"type": "http.request", "body": json.dumps(data).encode() if data is not None else b"", "more_body": False}

        async def send(message):
            messages.append(message)

        await web.app(scope, receive, send)
        status = next(message["status"] for message in messages if message["type"] == "http.response.start")
        body = b"".join(message.get("body", b"") for message in messages)
        return status, body
    status, body = asyncio.run(call())
    try:
        return status, json.loads(body)
    except json.JSONDecodeError:
        return status, body


class BackendTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory(prefix="uihound-test-")
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name)
        self.settings_patch = patch.object(config, "SNAPSHOT_DIR", self.root)
        self.settings_patch.start()
        self.addCleanup(self.settings_patch.stop)
        self.inspector = Inspector()
        self.device = Mock()
        self.device.info = {"productName": "test"}
        self.device.dump_hierarchy.return_value = XML
        self.device.screenshot.return_value = Image.new("RGB", (100, 200), "blue")
        self.inspector.device = self.device
        self.inspector.serial = "test-device"
        self.api_patch = patch.object(web, "inspector", self.inspector)
        self.api_patch.start()
        self.addCleanup(self.api_patch.stop)

    def test_hierarchy_selectors_native_xpath_and_geometry(self):
        result = self.inspector.hierarchy()
        self.assertEqual(result["node_count"], 5)
        target = result["elements"][1]
        self.assertEqual(target["u2Selector"], 'resourceId="app:id/title"')
        self.assertEqual(target["xpath"], '//*[@resource-id="app:id/title"]')
        self.assertEqual((target["centerX"], target["centerY"]), (30.5, 40.5))
        native_tree = PageSource.parse(XML)
        for element in result["elements"]:
            self.assertEqual(len(native_tree.find_elements(element["xpath"])), 1)
        self.assertIsNone(result["elements"][-1]["u2Selector"])
        self.assertEqual(result["elements"][-1]["xpath"], "/hierarchy[1]/*[1]/*[3]")

    def test_xpath_unique_attributes_quotes_and_structural_fallback(self):
        root = ET.Element("hierarchy")
        rows = [
            {"resource-id": "app:id/unique", "content-desc": "unique desc", "text": "unique text"},
            {"resource-id": "duplicate", "content-desc": '描述 \'单引号\' 和 "双引号" \\ []', "text": "text too"},
            {"resource-id": "duplicate", "content-desc": "same", "text": '中文 "text"'},
            {"content-desc": "same", "text": "repeated"},
            {"text": "repeated"},
            {"resource-id": "${placeholder}", "content-desc": "unknown", "text": "锟斤拷"},
            {"resource-id": "hidden-duplicate"},
            {"resource-id": "hidden-duplicate", "bounds": ""},
        ]
        for attributes in rows:
            ET.SubElement(root, "node", {"class": "android.widget.TextView", "bounds": "[0,0][10,20]", **attributes})
        xml = ET.tostring(root, encoding="unicode")
        result = self.inspector.hierarchy(xml)
        children = result["tree"]["children"]
        self.assertEqual(children[0]["xpath"], '//*[@resource-id="app:id/unique"]')
        self.assertTrue(children[1]["xpath"].startswith('//*[@content-desc=concat('))
        self.assertTrue(children[2]["xpath"].startswith('//*[@text='))
        for index in range(3, len(rows)):
            self.assertEqual(children[index]["xpath"], f"/hierarchy[1]/*[{index + 1}]")
        raw = etree.fromstring(xml.encode())
        native = PageSource.parse(xml)
        for index, item in enumerate(children):
            self.assertEqual(raw.xpath(item["xpath"]), [raw[index]])
            self.assertEqual(native.root.xpath(item["xpath"]), [native.root[index]])
        # Attribute locators survive sibling insertions without changing the selected node.
        raw.insert(0, etree.Element("node"))
        self.assertEqual(raw.xpath(children[0]["xpath"])[0].get("resource-id"), "app:id/unique")

    def test_literal_selectors_escape_and_reject_code(self):
        value = 'quote" slash\\ 中文'
        query = self.inspector._selector_expression("text", value)
        self.inspector.native_selector(query)
        self.device.assert_called_with(text=value)
        for invalid in ["**{}", 'text=__import__("os").system("x")', 'text="a", text="b"', 'text=[]']:
            with self.subTest(query=invalid), self.assertRaises(ValueError):
                self.inspector.native_selector(invalid)

    def test_regular_selector_enumeration(self):
        selector = Mock()
        selector.count = 2
        first, second = Mock(), Mock()
        first.info = {"bounds": {"left": 0, "top": 0, "right": 4, "bottom": 6}}
        second.info = {"bounds": {"left": 5, "top": 5, "right": 10, "bottom": 15}}
        selector.__getitem__ = Mock(side_effect=[first, second])
        self.device.return_value = selector
        self.assertEqual(len(self.inspector._resolve('text="test"')), 2)
        selector.all.assert_not_called()

    def test_snapshot_contract_and_crops(self):
        status, full = call_api("/snapshot/full?filename=full.png")
        self.assertEqual(status, 200)
        self.assertTrue(full["success"])
        self.assertEqual(full["image"], full["base64"])
        self.assertEqual(full["path"], full["file_path"])
        self.assertIn("timestamp", full)
        self.assertTrue(Path(full["file_path"]).is_file())
        status, crop = call_api("/snapshot/region?x1=30&y1=50&x2=10&y2=20&save_dir=sub&filename=crop.png")
        self.assertEqual(status, 200)
        self.assertEqual(crop["region"], {"x1": 10, "y1": 20, "x2": 30, "y2": 50})
        with Image.open(io.BytesIO(base64.b64decode(crop["base64"]))) as image:
            self.assertEqual(image.size, (20, 30))
        with patch.object(self.inspector, "_resolve", return_value=[{"bounds": [1, 2, 11, 22]}]):
            status, element = call_api("/snapshot/element?" + urlencode({"selector": 'text="Hello"'}))
        self.assertEqual(status, 200)
        self.assertEqual(element["bounds"]["right"], 11)

    def test_snapshot_errors_do_not_overwrite(self):
        self.inspector.save_snapshot("full", filename="saved.png")
        for filename in ["saved.png", "../outside.png", "..\\outside.png", "CON.png", "a.png:evil", "file.jpg"]:
            with self.subTest(filename=filename), self.assertRaises(ValueError):
                self.inspector.save_snapshot("full", filename=filename)
        for path in ["..", str(self.root.parent)]:
            with self.assertRaises(ValueError):
                self.inspector.save_snapshot("full", custom_path=path)
        self.assertEqual(call_api("/snapshot/full?save_dir=one&custom_path=two")[0], 400)
        self.assertEqual(call_api("/snapshot/region?x1=0&y1=0&x2=0&y2=10")[0], 400)
        self.assertEqual(call_api("/snapshot/region?x1=0&y1=0&x2=101&y2=10")[0], 400)

    def test_internal_routes_and_legacy_aliases(self):
        for path, method in [("/internal/tree", "POST"), ("/api/hierarchy", "GET")]:
            self.assertEqual(call_api(path, method)[1]["node_count"], 5)
        for path in ["/internal/settings", "/api/settings"]:
            self.assertEqual(call_api(path)[1]["snapshot_directory"], str(self.root.resolve()))
        status, result = call_api("/device/status")
        self.assertEqual(status, 200)
        self.assertEqual(result["device_serial"], result["serial"])
        self.assertEqual(result["u2_ready"], result["uiautomator2"])
        self.assertEqual(call_api("/internal/getScreenImg", "POST")[1]["width"], 100)
        self.assertEqual(call_api("/internal/disconnect", "POST")[0], 200)
        self.assertFalse(call_api("/device/status")[1]["connected"])
        self.assertEqual(call_api("/internal/tree", "POST")[0], 409)

    def test_native_element_actions_and_gestures(self):
        target = Mock()
        self.device.return_value = target
        for path, data in [
            ("clickElem", {"selector": 'text="Hello"'}),
            ("inputText", {"selector": 'text="Hello"', "text": "new"}),
            ("clearText", {"selector": 'text="Hello"'}),
        ]:
            self.assertEqual(call_api("/internal/" + path, "POST", data)[0], 200)
        target.click.assert_called_once()
        target.set_text.assert_called_once_with("new")
        target.clear_text.assert_called_once()
        call_api("/internal/inputText", "POST", {"text": "focus"})
        self.device.send_keys.assert_called_once_with("focus", clear=False)
        call_api("/internal/clearText", "POST", {"selector": "/hierarchy/*[1]"})
        self.device.xpath.return_value.set_text.assert_called_once_with("")
        for action in ["back", "home", "recent"]:
            self.assertEqual(call_api("/internal/action", "POST", {"action": action})[0], 200)
        self.assertEqual(call_api("/internal/action", "POST", {"action": "swipe", "start_x": 1, "start_y": 2, "end_x": 3, "end_y": 4})[0], 200)
        self.device.swipe.assert_called_once_with(1, 2, 3, 4, duration=0.35)

    def test_clipboard_restrictions_without_mutating_clipboard(self):
        screenshot = self.inspector.save_snapshot("full")
        path = "/internal/snapshots/copy-file"
        with patch.object(media.sys, "platform", "win32"), patch.object(media.subprocess, "run") as run:
            run.return_value.returncode = 0
            self.assertEqual(call_api(path, "POST", {"path": screenshot["path"]})[0], 403)
            headers = [(b"origin", b"http://127.0.0.1:8000"), (b"sec-fetch-site", b"same-origin")]
            self.assertEqual(call_api(path, "POST", {"path": screenshot["path"]}, headers)[0], 200)
            self.assertIn("SetFileDropList", run.call_args.args[0][-1])
            self.assertEqual(run.call_args.kwargs["env"]["UIHOUND_CLIPBOARD_FILE"], screenshot["path"])
            self.assertEqual(call_api(path, "POST", {"path": str(self.root.parent / "outside.png")}, headers)[0], 400)

    def test_static_assets_and_schema(self):
        status, html = call_api("/")
        self.assertEqual(status, 200)
        self.assertIn(b"/static/app.js?v=", html)
        for name in ["app.js", "style.css", "logo.svg", "theme.js", "recording.js", "media.js"]:
            self.assertIn(f"/static/{name}?v=".encode(), html)
            self.assertEqual(call_api("/static/" + name)[0], 200)
        paths = web.app.openapi()["paths"]
        self.assertEqual(set(paths) - {"/"}, {"/device/status", "/snapshot/full", "/snapshot/element", "/snapshot/region"})

    def test_settings_environment(self):
        with patch.dict("os.environ", {
            "UIHOUND_PORT": "8765", "UIHOUND_OPEN_BROWSER": "false",
            "UIHOUND_SNAPSHOT_DIR": str(self.root / "custom-images"),
            "UIHOUND_RECORDING_DIR": str(self.root / "custom-videos"),
        }):
            settings = config.Settings()
        self.assertEqual(settings.port, 8765)
        self.assertFalse(settings.open_browser)
        self.assertEqual(settings.snapshot_dir, self.root / "custom-images")
        self.assertEqual(settings.recording_dir, self.root / "custom-videos")

    def test_display_and_command_share_package_version(self):
        status, page = call_api("/")
        self.assertEqual(status, 200)
        self.assertIn(f'v{web.__version__}'.encode(), page)
        self.assertNotIn(b"<!--UIHOUND_VERSION-->", page)
        for flag in ("--version", "-V"):
            with patch("sys.argv", ["uihound", flag]), patch.object(web, "BrowserServer") as server, \
                    patch("sys.stdout", new_callable=io.StringIO) as output:
                with self.assertRaises(SystemExit) as exit_result:
                    web.main()
                self.assertEqual(exit_result.exception.code, 0)
                self.assertEqual(output.getvalue().strip(), f"UiHound {web.__version__}")
                server.assert_not_called()

    def test_media_defaults_use_user_home(self):
        with patch.dict("os.environ", {}, clear=True), patch.object(Path, "home", return_value=self.root):
            with patch.object(Path, "cwd", return_value=self.root / "first"):
                first = config.Settings()
            with patch.object(Path, "cwd", return_value=self.root / "second"):
                second = config.Settings()
        self.assertEqual(first.snapshot_dir, self.root / "UiHound" / "screenshots")
        self.assertEqual(first.recording_dir, self.root / "UiHound" / "recordings")
        self.assertEqual(first.snapshot_dir, second.snapshot_dir)
        self.assertEqual(first.recording_dir, second.recording_dir)

    def test_stream_freeze_resume_keeps_device_available(self):
        async def run():
            incoming = asyncio.Queue()
            incoming.put_nowait({"type": "websocket.connect"})
            frames = []
            frame_ready = asyncio.Event()
            scope = {
                "type": "websocket", "asgi": {"version": "3.0"}, "scheme": "ws",
                "path": "/internal/ws/screen", "raw_path": b"/internal/ws/screen",
                "query_string": b"", "root_path": "", "headers": [],
                "client": ("127.0.0.1", 1234), "server": ("127.0.0.1", 8000),
                "subprotocols": [],
            }
            async def send(message):
                if message["type"] == "websocket.send" and message.get("bytes"):
                    frames.append(message["bytes"])
                    frame_ready.set()
            task = asyncio.create_task(web.app(scope, incoming.get, send))
            try:
                await asyncio.wait_for(frame_ready.wait(), 2)
                incoming.put_nowait({"type": "websocket.receive", "text": '{"type":"pause"}'})
                await asyncio.sleep(.2)
                count = len(frames)
                await asyncio.sleep(.2)
                self.assertEqual(len(frames), count)
                self.assertEqual(self.inspector.screenshot().size, (100, 200))
                frame_ready.clear()
                incoming.put_nowait({"type": "websocket.receive", "text": '{"type":"resume"}'})
                await asyncio.wait_for(frame_ready.wait(), 2)
                self.assertGreater(len(frames), count)
            finally:
                incoming.put_nowait({"type": "websocket.disconnect", "code": 1000})
                await asyncio.wait_for(task, 2)
        asyncio.run(run())

    def test_browser_opens_only_after_ready_once(self):
        async def run():
            server = web.BrowserServer(uvicorn.Config(web.app, host="127.0.0.1", port=8765))
            async def ready(*args, **kwargs):
                server.started = True
            with patch.object(uvicorn.Server, "startup", side_effect=ready), patch.object(web.webbrowser, "open", return_value=True) as browser:
                await server.startup()
                await server.startup()
                browser.assert_called_once_with("http://127.0.0.1:8765")
            server = web.BrowserServer(uvicorn.Config(web.app), open_browser=True)
            with patch.object(uvicorn.Server, "startup", new_callable=AsyncMock), patch.object(web.webbrowser, "open") as browser:
                await server.startup()
                browser.assert_not_called()
        asyncio.run(run())

    def test_actual_server_ready_before_browser_callback(self):
        async def run():
            sock = socket.socket()
            sock.bind(("127.0.0.1", 0))
            port = sock.getsockname()[1]
            server = web.BrowserServer(uvicorn.Config(web.app, host="127.0.0.1", port=port, log_level="error"))
            def browser_open(url):
                self.assertTrue(server.started)
                with socket.create_connection(("127.0.0.1", port), timeout=2):
                    pass
                return True
            server.config.load()
            server.lifespan = server.config.lifespan_class(server.config)
            with patch.object(web.webbrowser, "open", side_effect=browser_open) as browser:
                try:
                    await server.startup(sockets=[sock])
                    browser.assert_called_once()
                finally:
                    await server.shutdown(sockets=[sock])
                    sock.close()
        asyncio.run(run())


if __name__ == "__main__":
    unittest.main()
