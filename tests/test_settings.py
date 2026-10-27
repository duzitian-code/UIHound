import base64
import json
import os
import subprocess
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

import test_backend
from test_backend import call_api
from uihound import config, file_dialog


ORIGIN = [(b"origin", b"http://127.0.0.1:8000"), (b"sec-fetch-site", b"same-origin")]


class DirectorySettingsTests(unittest.TestCase):
    setUp = test_backend.BackendTests.setUp

    def isolate_settings(self):
        names = ["PROJECT_DIR", "SNAPSHOT_DIR", "RECORDING_DIR", "INSPECTION_DIR", "IMPORT_DIR", "_path_overrides"]
        for name in names:
            current = getattr(config, name)
            self.addCleanup(setattr, config, name, current)
        preference_patch = patch.object(config, "PREFERENCES_FILE", self.root / "preferences.json")
        preference_patch.start()
        self.addCleanup(preference_patch.stop)

    def test_project_paths_overrides_and_persistent_preferences(self):
        self.isolate_settings()
        project = (self.root / "project").resolve()
        custom = (self.root / "separate-captures").resolve()
        payload = {"project_directory": str(project), "inspection_directory": str(custom)}
        self.assertEqual(call_api("/internal/settings/directories", "POST", payload)[0], 403)
        status, result = call_api("/internal/settings/directories", "POST", payload, headers=ORIGIN)
        self.assertEqual(status, 200, result)
        self.assertEqual(result["snapshot_directory"], str(project / "screenshots"))
        self.assertEqual(result["recording_directory"], str(project / "recordings"))
        self.assertEqual(result["inspection_directory"], str(custom))
        self.assertEqual(result["import_directory"], str(custom))
        saved = json.loads(config.PREFERENCES_FILE.read_text(encoding="utf-8"))
        self.assertEqual(saved["inspection_directory"], str(custom))
        self.assertEqual(config.resolve_directories(saved)["import_directory"], str(custom))
        self.assertTrue((project / "screenshots").is_dir())
        self.assertTrue(custom.is_dir())
        marker = custom / "existing.png"
        marker.write_bytes(b"keep")
        payload = {"project_directory": str(self.root / "next"), "snapshot_directory": "images", "import_directory": str(custom)}
        status, result = call_api("/internal/settings/directories", "POST", payload, headers=ORIGIN)
        self.assertEqual(status, 200, result)
        self.assertEqual(result["snapshot_directory"], str((self.root / "next" / "images").resolve()))
        self.assertEqual(result["inspection_directory"], str((self.root / "next" / "snapshots").resolve()))
        self.assertEqual(result["import_directory"], str(custom))
        self.assertEqual(marker.read_bytes(), b"keep")
        self.assertEqual(call_api("/internal/settings")[1], result)

    def test_restart_reloads_paths_and_new_outputs_use_them(self):
        self.isolate_settings()
        home = self.root / "home"
        config.PREFERENCES_FILE = home / "UiHound" / "settings.json"
        payload = {
            "project_directory": str((self.root / "project").resolve()),
            "snapshot_directory": str((self.root / "custom-images").resolve()),
        }
        status, settings = call_api("/internal/settings/directories", "POST", payload, headers=ORIGIN)
        self.assertEqual(status, 200, settings)
        script = """
import json, sys
from pathlib import Path
from unittest.mock import patch
with patch.object(Path, 'home', return_value=Path(sys.argv[1])):
    from uihound import config
    print(json.dumps(config.directory_settings()))
"""
        output = subprocess.run([sys.executable, "-c", script, str(home)],
                                capture_output=True, text=True, check=True, timeout=30)
        restored = json.loads(output.stdout)
        self.assertEqual(restored, settings)
        status, image = call_api("/snapshot/full")
        self.assertEqual(status, 200, image)
        self.assertEqual(Path(image["path"]).parent, Path(settings["snapshot_directory"]))
        _, capture = call_api("/internal/capture", "POST", {"hierarchy": True})
        status, archive = call_api("/internal/capture/archive/save", "POST", {
            "snapshot_id": capture["snapshot_id"],
        }, headers=ORIGIN)
        self.assertEqual(status, 200, archive)
        self.assertEqual(Path(archive["path"]).parent, Path(settings["inspection_directory"]))
        self.assertNotEqual(Path(archive["path"]).parent, Path(image["path"]).parent)

    def test_bad_settings_do_not_replace_working_configuration(self):
        self.isolate_settings()
        before = config.directory_settings()
        self.assertEqual(call_api("/internal/settings/directories", "POST", {
            "project_directory": "relative",
        }, headers=ORIGIN)[0], 400)
        blocked = self.root / "file"
        blocked.write_text("not a folder")
        self.assertEqual(call_api("/internal/settings/directories", "POST", {
            "project_directory": str(blocked),
        }, headers=ORIGIN)[0], 502)
        self.assertEqual(config.directory_settings(), before)
        self.assertFalse(config.PREFERENCES_FILE.exists())

    def test_screenshot_and_analysis_directories_cannot_overlap(self):
        self.isolate_settings()
        project = (self.root / "project").resolve()
        for folder in [project / "screenshots", project / "screenshots" / "snapshots", project]:
            with self.subTest(folder=folder):
                status, result = call_api("/internal/settings/directories", "POST", {
                    "project_directory": str(project), "inspection_directory": str(folder),
                }, headers=ORIGIN)
                self.assertEqual(status, 400, result)
                self.assertIn("必须独立", result["detail"])
        self.assertFalse(config.PREFERENCES_FILE.exists())

    def assert_picker_directory(self, run, kind):
        if os.name == "nt":
            self.assertEqual(run.call_args.kwargs["env"]["UIHOUND_PICKER_DIRECTORY"], str(config.IMPORT_DIR.resolve()))
            self.assertEqual(run.call_args.kwargs["env"]["UIHOUND_PICKER_KIND"], kind)
            script = run.call_args.args[0][-1]
            self.assertIn("$dialog.AutoUpgradeEnabled = $false", script)
            self.assertIn("$dialog.InitialDirectory = $env:UIHOUND_PICKER_DIRECTORY", script)
            self.assertIn("$dialog.RestoreDirectory = $true", script)
            self.assertIn("-STA", run.call_args.args[0])
        else:
            self.assertEqual(run.call_args.args[0][-2:], [str(config.IMPORT_DIR.resolve()), kind])

    def test_native_picker_defaults_and_independent_image_xml(self):
        config.IMPORT_DIR.mkdir()
        image = self.root / "picked.png"
        image.write_bytes(b"selected image bytes")
        xml = self.root / "picked.xml"
        xml.write_text("<hierarchy/>", encoding="utf-8")
        with patch.object(file_dialog.subprocess, "run") as run:
            run.return_value.stdout = json.dumps(str(image))
            self.assertEqual(call_api("/internal/capture/select-file", "POST", {"kind": "image"})[0], 403)
            run.assert_not_called()
            status, result = call_api("/internal/capture/select-file", "POST", {"kind": "image"}, headers=ORIGIN)
            self.assertEqual(status, 200, result)
            self.assertEqual(base64.b64decode(result["content"]), image.read_bytes())
            self.assert_picker_directory(run, "image")
            run.return_value.stdout = json.dumps(str(xml))
            status, result = call_api("/internal/capture/select-file", "POST", {"kind": "xml"}, headers=ORIGIN)
            self.assertEqual((status, result["content"]), (200, "<hierarchy/>"))
            self.assert_picker_directory(run, "xml")
            run.return_value.stdout = '""'
            self.assertEqual(call_api("/internal/capture/select-file", "POST", {"kind": "xml"}, headers=ORIGIN)[1], {"cancelled": True})

            other = self.root / "different-import"
            with patch.object(config, "IMPORT_DIR", other):
                run.return_value.stdout = json.dumps(str(xml))
                call_api("/internal/capture/select-file", "POST", {"kind": "xml"}, headers=ORIGIN)
                self.assert_picker_directory(run, "xml")
                self.assertTrue(other.is_dir())

    def test_native_picker_errors_are_reported(self):
        with patch.object(file_dialog.subprocess, "run", side_effect=subprocess.TimeoutExpired("picker", 300)):
            self.assertEqual(call_api("/internal/capture/select-file", "POST", {"kind": "xml"}, headers=ORIGIN)[0], 409)
        with patch.object(file_dialog.subprocess, "run", side_effect=subprocess.CalledProcessError(1, "picker", stderr="tkinter unavailable")):
            status, result = call_api("/internal/capture/select-file", "POST", {"kind": "image"}, headers=ORIGIN)
            self.assertEqual(status, 409)
            self.assertIn("tkinter unavailable", result["detail"])
        self.assertEqual(call_api("/internal/capture/select-file", "POST", {"kind": "exe"}, headers=ORIGIN)[0], 422)


if __name__ == "__main__":
    unittest.main()
