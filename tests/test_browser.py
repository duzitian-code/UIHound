import functools
import base64
import json
import os
import re
import shutil
import subprocess
import tempfile
import threading
import time
import unittest
import importlib
from unittest.mock import patch
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.request import urlopen
from websockets.sync.client import connect

from uihound.config import STATIC_DIR
from uihound import config
from test_media import call_api, ORIGIN

TEST_DIR = Path(__file__).parent
EDGE = shutil.which("msedge") or r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe"


class Handler(SimpleHTTPRequestHandler):
    def log_message(self, *_):
        pass

    def do_GET(self):
        if self.path == "/":
            html = (STATIC_DIR / "index.html").read_text(encoding="utf-8")
            html = html.replace(
                '<script src="/static/app.js"></script>',
                '<script>' + (TEST_DIR / "browser_fixture.js").read_text(encoding="utf-8") +
                '</script><script src="/static/app.js"></script><script>' +
                (TEST_DIR / "remote_assertions.js").read_text(encoding="utf-8") +
                '\n' + (TEST_DIR / "browser_assertions.js").read_text(encoding="utf-8") + '</script>',
            )
            content = html.encode()
            content_type = "text/html; charset=utf-8"
        elif self.path in {"/fixture.js", "/assertions.js", "/remote-assertions.js"}:
            name = {"/fixture.js": "browser_fixture.js", "/assertions.js": "browser_assertions.js", "/remote-assertions.js": "remote_assertions.js"}[self.path]
            content = (TEST_DIR / name).read_bytes()
            content_type = "text/javascript; charset=utf-8"
        else:
            return super().do_GET()
        self.send_response(200)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(content)))
        self.end_headers()
        self.wfile.write(content)


@unittest.skipUnless(Path(EDGE).is_file(), "Microsoft Edge not available")
class BrowserTests(unittest.TestCase):
    def test_desktop_layout_and_existing_features(self):
        server = ThreadingHTTPServer(("127.0.0.1", 0), functools.partial(Handler, directory=str(STATIC_DIR.parent)))
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            for width, height, theme in [(1920, 1080, "dark"), (1280, 720, "dark"), (1920, 1080, "light"), (1280, 720, "light")]:
                case = os.environ.get("UIHOUND_BROWSER_CASE")
                if case and case != f"{width}x{height}-{theme}":
                    continue
                with self.subTest(size=(width, height), theme=theme), tempfile.TemporaryDirectory(prefix="uihound-browser-") as profile:
                    args = [
                        EDGE, "--headless", "--disable-gpu", "--no-first-run", "--no-default-browser-check",
                        "--disable-background-networking", "--hide-scrollbars", "--force-device-scale-factor=1",
                        f"--user-data-dir={profile}", f"--window-size={width},{height}",
                        "--remote-debugging-port=0",
                        "--disable-background-timer-throttling", "--disable-renderer-backgrounding",
                        "--disable-backgrounding-occluded-windows", "--disable-features=CalculateNativeWinOcclusion",
                    ]
                    screenshot = os.environ.get("UIHOUND_BROWSER_SCREENSHOT")
                    process = subprocess.Popen([*args, "about:blank"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                    try:
                        port_file = Path(profile) / "DevToolsActivePort"
                        deadline = time.monotonic() + 20
                        port = None
                        while port is None and time.monotonic() < deadline:
                            try:
                                lines = port_file.read_text().splitlines()
                                if lines:
                                    port = lines[0]
                            except (FileNotFoundError, PermissionError):
                                pass  # Edge may still hold the port file while publishing it.
                            if port is None:
                                time.sleep(.1)
                        self.assertIsNotNone(port, "Edge debugging endpoint did not start")
                        with urlopen(f"http://127.0.0.1:{port}/json/list") as response:
                            tabs = json.load(response)
                        page = next(tab for tab in tabs if tab["type"] == "page")
                        with connect(page["webSocketDebuggerUrl"], max_size=20_000_000) as ws:
                            sequence = 0
                            def command(method, params):
                                nonlocal sequence
                                sequence += 1
                                ws.send(json.dumps({"id": sequence, "method": method, "params": params}))
                                while True:
                                    result = json.loads(ws.recv(timeout=45))
                                    if result.get("id") == sequence:
                                        self.assertNotIn("error", result)
                                        return result["result"]
                            command("Page.enable", {})
                            command("Emulation.setFocusEmulationEnabled", {"enabled": True})
                            command("Page.addScriptToEvaluateOnNewDocument", {"source": f"localStorage.setItem('uihound-theme', '{theme}');"})
                            command("Page.navigate", {"url": f"http://127.0.0.1:{server.server_port}"})
                            command("Page.bringToFront", {})
                            time.sleep(.5)
                            result = command("Runtime.evaluate", {
                                "expression": "new Promise((resolve) => { const start = Date.now(); const timer = setInterval(() => { if (document.body?.dataset.result || Date.now() - start > 30000) { clearInterval(timer); resolve(document.body?.dataset.result || 'TIMEOUT: ' + (window.fixtureErrors || []).join(';')); } }, 100); })",
                                "awaitPromise": True, "returnByValue": True,
                            })
                            diagnostic = command("Runtime.evaluate", {"expression": "JSON.stringify({href:location.href,phase:document.body.dataset.phase,result:document.body.dataset.result,errors:window.fixtureErrors,requests:window.fixtureRequests?.slice(-4)})", "returnByValue": True})
                            command("Runtime.evaluate", {
                                "expression": "(async () => { if (state.recorder) { await state.recorder.stop(); state.recorder.unsaved = null; } })()",
                                "awaitPromise": True,
                            })
                            self.assertEqual(result["result"].get("value"), "PASS", diagnostic)
                            recording = command("Runtime.evaluate", {
                                "expression": "new Promise(resolve => { const reader = new FileReader(); reader.onload = () => resolve(reader.result.split(',')[1]); reader.readAsDataURL(fixtureVideo); })",
                                "awaitPromise": True, "returnByValue": True,
                            })
                            binary = base64.b64decode(recording["result"]["value"])
                            application = importlib.import_module("uihound.main").app
                            with patch.object(config, "RECORDING_DIR", Path(profile) / "recordings"):
                                status, saved = call_api("/internal/recordings/save", "POST", raw=binary, headers=ORIGIN, application=application)
                                self.assertEqual(status, 200, saved)
                                self.assertEqual(Path(saved["path"]).read_bytes(), binary)
                                status, listing = call_api("/internal/media/list?kind=recording", application=application)
                                self.assertEqual(status, 200)
                                self.assertEqual(listing["files"][0]["path"], saved["path"])
                                self.assertEqual(call_api(saved["url"], application=application), (200, binary))
                                status, removed = call_api("/internal/media/delete", "POST", {"kind": "recording", "paths": [saved["path"]]}, headers=ORIGIN, application=application)
                                self.assertEqual((status, removed["deleted"]), (200, [saved["path"]]))
                                self.assertFalse(Path(saved["path"]).exists())
                            if screenshot and width == 1920:
                                png = command("Page.captureScreenshot", {"format": "png"})
                                target = Path(screenshot)
                                if theme == "light":
                                    target = target.with_stem(target.stem + "-light")
                                target.write_bytes(base64.b64decode(png["data"]))
                    finally:
                        process.terminate()
                        process.wait(timeout=15)
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=5)


if __name__ == "__main__":
    unittest.main()
