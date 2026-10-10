import asyncio
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from urllib.parse import urlencode

from fastapi import FastAPI

from uihound import config, media


ORIGIN = [(b"origin", b"http://127.0.0.1:8000"), (b"sec-fetch-site", b"same-origin")]
WEBM = b"\x1a\x45\xdf\xa3" + b"\x00" * 12


def call_api(path, method="GET", data=None, *, raw=None, headers=(), client="127.0.0.1", receive_messages=None, application=None):
    app = application or FastAPI()
    if application is None:
        app.include_router(media.router, prefix="/internal", include_in_schema=False)

    async def call():
        target, _, query = path.partition("?")
        scope = {
            "type": "http", "http_version": "1.1", "asgi": {"version": "3.0"},
            "method": method, "scheme": "http", "path": target, "raw_path": target.encode(),
            "query_string": query.encode(), "root_path": "",
            "headers": [(b"host", b"127.0.0.1:8000"),
                        (b"content-type", b"video/webm" if raw is not None else b"application/json"), *headers],
            "client": (client, 1234), "server": ("127.0.0.1", 8000),
        }
        incoming = iter(receive_messages or [
            {"type": "http.request", "body": raw if raw is not None else json.dumps(data).encode() if data is not None else b"",
             "more_body": False},
        ])
        messages = []

        async def receive():
            return next(incoming)

        async def send(message):
            messages.append(message)

        await app(scope, receive, send)
        status = next(item["status"] for item in messages if item["type"] == "http.response.start")
        body = b"".join(item.get("body", b"") for item in messages)
        try:
            body = json.loads(body)
        except (UnicodeDecodeError, json.JSONDecodeError):
            pass
        return status, body

    return asyncio.run(call())


def url(endpoint, **params):
    return endpoint + "?" + urlencode(params)


class MediaTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory(prefix="uihound-media-test-")
        self.addCleanup(temp.cleanup)
        base = Path(temp.name).resolve()
        self.snapshots = base / "snapshots"
        self.recordings = base / "recordings"
        self.snapshots.mkdir()
        self.recordings.mkdir()
        for name, value in (("SNAPSHOT_DIR", self.snapshots), ("RECORDING_DIR", self.recordings)):
            setting = patch.object(config, name, value)
            setting.start()
            self.addCleanup(setting.stop)

    def test_list_sorted_filtered_and_escaped_urls(self):
        folder = self.snapshots / "space & text"
        folder.mkdir()
        older = folder / "old.png"
        newer = folder / "new #1.PNG"
        older.write_bytes(b"old")
        newer.write_bytes(b"newer")
        (folder / "ignore.webm").write_bytes(b"x")
        os.utime(older, (1, 1))
        status, result = call_api(url("/internal/media/list", kind="snapshot", directory=str(folder)))
        self.assertEqual(status, 200)
        self.assertEqual(result["directory"], str(folder))
        self.assertEqual([entry["name"] for entry in result["files"]], [newer.name, older.name])
        self.assertEqual(result["files"][0]["size"], 5)
        self.assertIn("new+%231.PNG", result["files"][0]["url"])
        self.assertEqual(call_api(result["files"][0]["url"]), (200, b"newer"))
        self.assertEqual(call_api(url("/internal/media/list", kind="recording"))[1]["directory"], str(self.recordings))

    @unittest.skipUnless(os.name == "nt", "Windows short paths")
    def test_windows_short_path_alias(self):
        import ctypes

        buffer = ctypes.create_unicode_buffer(32768)
        length = ctypes.windll.kernel32.GetShortPathNameW(str(self.snapshots), buffer, len(buffer))
        self.assertGreater(length, 0)
        self.assertLess(length, len(buffer))
        alias = Path(buffer.value)
        file = self.snapshots / "image.png"
        file.write_bytes(b"png")
        self.assertEqual(call_api(url("/internal/media/file", kind="snapshot", path=str(alias / file.name))),
                         (200, b"png"))
        self.assertEqual(call_api(url("/internal/media/file", kind="snapshot", path=str(alias / "missing.png")))[0],
                         404)
        status, result = call_api("/internal/media/directory", "POST",
                                  {"kind": "snapshot", "path": str(alias / "new-folder")}, headers=ORIGIN)
        self.assertEqual((status, result), (200, {"directory": str(self.snapshots / "new-folder")}))

    def test_rejects_invalid_kind_traversal_suffix_and_links(self):
        valid = self.snapshots / "valid.png"
        valid.write_bytes(b"png")
        for path in (self.snapshots.parent / "outside.png", self.snapshots / ".." / "outside.png"):
            self.assertEqual(call_api(url("/internal/media/file", kind="snapshot", path=str(path)))[0], 400)
        self.assertEqual(call_api(url("/internal/media/list", kind="invalid"))[0], 400)
        self.assertEqual(call_api(url("/internal/media/file", kind="snapshot", path=str(valid.with_suffix(".webm"))))[0], 400)
        self.assertEqual(call_api(url("/internal/media/file", kind="snapshot", path=str(self.snapshots / "missing.png")))[0], 404)
        link = self.snapshots / "link.png"
        try:
            link.symlink_to(valid)
        except (OSError, NotImplementedError):
            pass
        else:
            self.assertEqual(call_api(url("/internal/media/file", kind="snapshot", path=str(link)))[0], 400)
            self.assertEqual(call_api(url("/internal/media/list", kind="snapshot"))[1]["files"][0]["name"], valid.name)
        subdir = self.snapshots / "linked"
        try:
            subdir.symlink_to(self.snapshots.parent, target_is_directory=True)
        except (OSError, NotImplementedError):
            pass
        else:
            self.assertEqual(call_api(url("/internal/media/list", kind="snapshot", directory=str(subdir)))[0], 400)
            self.assertEqual(call_api("/internal/media/delete", "POST",
                                      {"kind": "snapshot", "paths": [str(subdir / "outside.png")]},
                                      headers=ORIGIN)[0], 400)

    def test_delete_prevalidates_all_then_reports_partial_errors(self):
        first = self.snapshots / "first.png"
        second = self.snapshots / "second.png"
        first.write_bytes(b"first")
        second.write_bytes(b"second")
        endpoint = "/internal/media/delete"
        self.assertEqual(call_api(endpoint, "POST", {"kind": "snapshot", "paths": [str(first)]})[0], 403)
        self.assertEqual(call_api(endpoint, "POST", {"kind": "snapshot", "paths": [str(first)]},
                                  headers=[(b"origin", b"http://[invalid")])[0], 403)
        self.assertEqual(call_api(endpoint, "POST", {"kind": "snapshot", "paths": [str(first)]},
                                  headers=[(b"origin", b"http://127.0.0.1:8000"),
                                           (b"sec-fetch-site", b"cross-site")])[0], 403)
        self.assertEqual(call_api(endpoint, "POST", {"kind": "snapshot", "paths": [str(first)]},
                                  headers=ORIGIN, client="192.0.2.10")[0], 403)
        invalid = {"kind": "snapshot", "paths": [str(first), str(second.with_suffix(".webm"))]}
        self.assertEqual(call_api(endpoint, "POST", invalid, headers=ORIGIN)[0], 400)
        self.assertTrue(first.exists())
        missing = self.snapshots / "missing.png"
        status, result = call_api(endpoint, "POST", {"kind": "snapshot", "paths": [str(first), str(missing)]},
                                  headers=ORIGIN)
        self.assertEqual(status, 200)
        self.assertEqual(result["deleted"], [str(first)])
        self.assertEqual(result["errors"][0]["path"], str(missing))
        self.assertTrue(second.exists())

    def test_create_open_clipboard_and_read_safety(self):
        folder = self.recordings / "clips"
        create = "/internal/media/directory"
        status, result = call_api(create, "POST", {"kind": "recording", "path": str(folder)}, headers=ORIGIN)
        self.assertEqual((status, result), (200, {"directory": str(folder)}))
        self.assertTrue(folder.is_dir())
        self.assertEqual(call_api(create, "POST", {"kind": "recording", "path": str(folder / ".." / "bad")},
                                  headers=ORIGIN)[0], 400)
        clip = folder / "clip.webm"
        clip.write_bytes(WEBM)
        with patch.object(media.sys, "platform", "win32"), patch.object(media.os, "startfile", create=True) as launch, \
                patch.object(media.subprocess, "run") as run:
            self.assertEqual(call_api("/internal/media/open-directory", "POST",
                                      {"kind": "recording", "path": str(folder)}, headers=ORIGIN)[0], 200)
            launch.assert_called_once_with(folder)
            self.assertEqual(call_api("/internal/media/copy-file", "POST",
                                      {"kind": "recording", "path": str(clip)}, headers=ORIGIN)[0], 200)
            self.assertEqual(run.call_args.kwargs["env"]["UIHOUND_CLIPBOARD_FILE"], str(clip))
            self.assertEqual(call_api("/internal/media/copy-file", "POST",
                                      {"kind": "recording", "path": str(folder / "missing.webm")},
                                      headers=ORIGIN)[0], 404)
        self.assertEqual(call_api(url("/internal/media/file", kind="recording", path=str(clip))), (200, WEBM))

    def test_macos_open_directory_and_copy_file(self):
        folder = self.recordings / "clips"
        folder.mkdir()
        clip = folder / "clip.webm"
        clip.write_bytes(WEBM)
        with patch.object(media.sys, "platform", "darwin"), patch.object(media.subprocess, "run") as run:
            self.assertEqual(call_api("/internal/media/open-directory", "POST",
                                      {"kind": "recording", "path": str(folder)}, headers=ORIGIN)[0], 200)
            self.assertEqual(run.call_args.args[0], ["open", str(folder)])
            self.assertTrue(run.call_args.kwargs["check"])
            self.assertEqual(call_api("/internal/media/copy-file", "POST",
                                      {"kind": "recording", "path": str(clip)}, headers=ORIGIN)[0], 200)
            command = run.call_args.args[0]
            self.assertEqual(command[0:2], ["osascript", "-e"])
            self.assertEqual(command[-1], str(clip))
            self.assertIn("POSIX file", command[2])

    def test_upload_streams_limits_and_cleans_failures(self):
        endpoint = url("/internal/recordings/save", directory="session")
        self.assertEqual(call_api(endpoint, "POST", raw=WEBM)[0], 403)
        status, saved = call_api(endpoint, "POST", raw=WEBM, headers=ORIGIN)
        self.assertEqual(status, 200)
        self.assertEqual(saved["size"], len(WEBM))
        self.assertEqual(Path(saved["path"]).read_bytes(), WEBM)
        self.assertEqual(call_api(saved["url"]), (200, WEBM))
        with patch.object(media, "MAX_RECORDING_BYTES", 8):
            self.assertEqual(call_api(endpoint, "POST", raw=WEBM, headers=ORIGIN)[0], 413)
        self.assertEqual(call_api(endpoint, "POST", raw=b"BAD!", headers=ORIGIN)[0], 400)
        self.assertEqual(call_api(endpoint, "POST", raw=b"", headers=ORIGIN)[0], 400)
        self.assertEqual(call_api(endpoint, "POST", raw=media.WEBM_HEADER, headers=ORIGIN)[0], 400)
        incoming = [
            {"type": "http.request", "body": WEBM[:8], "more_body": True},
            {"type": "http.disconnect"},
        ]
        self.assertEqual(call_api(endpoint, "POST", raw=WEBM, headers=ORIGIN, receive_messages=incoming)[0], 400)
        self.assertEqual(list((self.recordings / "session").glob("*.webm")), [Path(saved["path"])])
        self.assertEqual(list((self.recordings / "session").glob("*.part")), [])

    def test_uncreated_recordings_directory_lists_empty(self):
        missing = self.recordings / "not-created"
        with patch.object(config, "RECORDING_DIR", missing):
            self.assertEqual(call_api(url("/internal/media/list", kind="recording")),
                             (200, {"files": [], "directory": str(missing)}))
            self.assertFalse(missing.exists())
            self.assertEqual(call_api(url("/internal/media/list", kind="recording", directory=str(missing))),
                             (200, {"files": [], "directory": str(missing)}))


if __name__ == "__main__":
    unittest.main()
