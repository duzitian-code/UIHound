import base64
import io
import unittest
import json
from pathlib import Path
from unittest.mock import patch

from PIL import Image

import test_backend
from test_backend import call_api
from uihound.inspection import InspectionStore

ORIGIN = [(b"origin", b"http://127.0.0.1:8000"), (b"sec-fetch-site", b"same-origin")]


class InspectionTests(unittest.TestCase):
    setUp = test_backend.BackendTests.setUp

    def test_analysis_archive_roundtrip_and_default_directory(self):
        status, listing = call_api("/internal/capture/archives")
        self.assertEqual(status, 200, listing)
        default = (self.root / "analysis").resolve()
        self.assertEqual(listing, {"directory": str(default), "files": []})
        _, capture = call_api("/internal/capture", "POST", {"hierarchy": True})
        self.assertEqual(call_api("/internal/capture/archive/save", "POST", {
            "snapshot_id": capture["snapshot_id"],
        })[0], 403)
        status, saved = call_api("/internal/capture/archive/save", "POST", {
            "snapshot_id": capture["snapshot_id"],
        }, headers=ORIGIN)
        self.assertEqual(status, 200, saved)
        folder = Path(saved["path"])
        self.assertEqual(folder.parent, default)
        self.assertIn("_display_0_", folder.name)
        self.assertEqual((folder / "hierarchy.xml").read_text(encoding="utf-8"), test_backend.XML)
        self.assertEqual(json.loads((folder / "metadata.json").read_text()), {"display_id": 0})
        with Image.open(folder / "screen.png") as image:
            self.assertEqual(image.getpixel((1, 1)), (0, 0, 255))
        self.assertEqual(call_api("/internal/capture/archives")[1]["files"][0]["path"], str(folder))
        call_api("/internal/disconnect", "POST")
        test_backend.web.inspections.clear()
        status, loaded = call_api("/internal/capture/archive/load", "POST", {"path": str(folder)}, headers=ORIGIN)
        self.assertEqual(status, 200, loaded)
        self.assertEqual(loaded["xml"], test_backend.XML)
        self.assertEqual(loaded["node_count"], capture["node_count"])
        self.assertEqual(loaded["image"], capture["image"])
        self.assertEqual(call_api("/internal/capture/find", "POST", {
            "snapshot_id": loaded["snapshot_id"], "query": 'text="Hello"',
        })[1]["count"], 1)

    def test_analysis_archive_rejects_bad_paths_and_cleans_failed_save(self):
        _, capture = call_api("/internal/capture", "POST", {"hierarchy": True})
        for directory in ["..", str(self.root.parent)]:
            self.assertEqual(call_api("/internal/capture/archive/save", "POST", {
                "snapshot_id": capture["snapshot_id"], "directory": directory,
            }, headers=ORIGIN)[0], 400)
        for path in [str(self.root.parent), str(self.root / "analysis" / "missing")]:
            self.assertEqual(call_api("/internal/capture/archive/load", "POST", {"path": path}, headers=ORIGIN)[0], 400)
        with patch.object(Image.Image, "save", side_effect=OSError("disk full")):
            self.assertEqual(call_api("/internal/capture/archive/save", "POST", {
                "snapshot_id": capture["snapshot_id"],
            }, headers=ORIGIN)[0], 502)
        self.assertEqual(list((self.root / "analysis").iterdir()), [])
        _, color = call_api("/internal/capture", "POST", {"hierarchy": False})
        self.assertEqual(call_api("/internal/capture/archive/save", "POST", {
            "snapshot_id": color["snapshot_id"],
        }, headers=ORIGIN)[0], 400)

    def test_other_displays_are_excluded_without_changing_native_paths(self):
        xml = """<hierarchy>
        <node class="android.view.View" display-id="3" bounds="[0,0][10,20]" text="Other"/>
        <node class="android.view.View" display-id="0" bounds="[0,0][10,20]" text="Shared"/>
        <node class="android.view.View" display-id="1" bounds="[0,0][10,20]" text="Shared">
          <node class="android.widget.TextView" bounds="[0,0][5,10]" text="Inherited"/>
        </node>
        <node class="android.widget.TextView" bounds="[10,20][50,60]" text="Default"/>
        </hierarchy>"""
        self.device.dump_hierarchy.return_value = xml
        store = InspectionStore()
        capture = store.capture(self.inspector, True)
        self.assertEqual(capture["node_count"], 3)
        self.assertEqual([item["text"] for item in capture["elements"]], ["Shared", "Default"])
        self.assertEqual(capture["elements"][0]["xpath"], "/hierarchy[1]/*[2]")
        self.assertEqual(len(capture["tree"]["children"]), 2)
        for query in ['text="Other"', 'text="Inherited"', '/hierarchy[1]/*[1]', '/hierarchy[1]/*[3]']:
            self.assertEqual(store.search(capture["snapshot_id"], query, self.inspector), [])
        self.assertEqual(len(store.search(capture["snapshot_id"], '/hierarchy[1]/*[2]', self.inspector)), 1)
        source = test_backend.PageSource.parse(xml)
        for element in capture["elements"]:
            self.assertEqual(len(source.find_elements(element["xpath"])), 1)

    def test_static_capture_is_lossless_and_search_does_not_read_device(self):
        status, capture = call_api("/internal/capture", "POST", {"hierarchy": True})
        self.assertEqual(status, 200)
        self.assertEqual(capture["node_count"], 5)
        with Image.open(io.BytesIO(base64.b64decode(capture["image"]))) as image:
            self.assertEqual(image.format, "PNG")
            self.assertEqual(image.getpixel((1, 1)), (0, 0, 255))
        self.device.dump_hierarchy.reset_mock()
        self.device.screenshot.reset_mock()
        for query, count in [('text="Duplicate"', 2), ('textContains="ello"', 1), ('textMatches="H.*"', 1), ('clickable=True', 1), ('//android.widget.TextView', 3), ('/hierarchy[1]/*[1]/*[1]', 1), ('//*[@resource-id="app:id/title"]', 1)]:
            status, result = call_api("/internal/capture/find", "POST", {"snapshot_id": capture["snapshot_id"], "query": query})
            self.assertEqual(status, 200, result)
            self.assertEqual(result["count"], count)
            if count == 1:
                self.assertEqual(result["elements"][0]["xpath"], '//*[@resource-id="app:id/title"]')
        self.device.dump_hierarchy.assert_not_called()
        self.device.screenshot.assert_not_called()

    def test_captured_crops_use_old_image(self):
        _, capture = call_api("/internal/capture", "POST", {"hierarchy": True})
        self.device.screenshot.return_value = Image.new("RGB", (100, 200), "red")
        self.device.screenshot.reset_mock()
        for kind, extra in [
            ("full", {}),
            ("region", {"region": {"x1": 10, "y1": 20, "x2": 30, "y2": 40}}),
            ("element", {"xpath": capture["elements"][1]["xpath"]}),
        ]:
            status, result = call_api("/internal/capture/save", "POST", {
                "snapshot_id": capture["snapshot_id"], "kind": kind, **extra,
            })
            self.assertEqual(status, 200, result)
            with Image.open(io.BytesIO(base64.b64decode(result["image"]))) as image:
                self.assertEqual(image.getpixel((0, 0)), (0, 0, 255))
        self.device.screenshot.assert_not_called()

    def test_color_capture_skips_hierarchy_and_rejects_lookup(self):
        status, capture = call_api("/internal/capture", "POST", {"hierarchy": False})
        self.assertEqual(status, 200)
        self.device.dump_hierarchy.assert_not_called()
        self.assertEqual(call_api("/internal/capture/find", "POST", {"snapshot_id": capture["snapshot_id"], "query": 'text="Hello"'})[0], 400)

    def test_capture_validation_expiry_device_scope(self):
        store = InspectionStore()
        capture = store.capture(self.inspector, True)
        with self.assertRaises(ValueError):
            store.search(capture["snapshot_id"], "count(//*)", self.inspector)
        with patch("uihound.inspection.time.monotonic", return_value=10**12):
            with self.assertRaises(ValueError):
                store.get(capture["snapshot_id"], self.inspector)
        self.inspector.device = object()
        self.assertIsNotNone(store.get(capture["snapshot_id"], self.inspector))
        self.inspector.device = self.device
        for _ in range(9):
            store.capture(self.inspector, False)
        with self.assertRaises(ValueError):
            store.get(capture["snapshot_id"], self.inspector)

    def test_import_offline_search_crop_and_display(self):
        image = io.BytesIO()
        Image.new("RGB", (100, 200), "green").save(image, "PNG")
        xml = '<hierarchy><node display-id="3" text="Report" bounds="[1,2][20,30]"/><node display-id="0" text="Main"/></hierarchy>'
        status, capture = call_api("/internal/capture/import", "POST", {
            "image": base64.b64encode(image.getvalue()).decode(), "xml": xml, "display_id": 3,
        })
        self.assertEqual(status, 200, capture)
        self.assertEqual(capture["display_id"], 3)
        self.assertEqual([item["text"] for item in capture["elements"]], ["Report"])
        self.assertEqual(capture["elements"][0]["xpath"], '//*[@text="Report"]')
        call_api("/internal/disconnect", "POST")
        status, found = call_api("/internal/capture/find", "POST", {
            "snapshot_id": capture["snapshot_id"], "query": 'text="Report"',
        })
        self.assertEqual((status, found["count"]), (200, 1))
        status, saved = call_api("/internal/capture/save", "POST", {
            "snapshot_id": capture["snapshot_id"], "kind": "element", "xpath": found["elements"][0]["xpath"],
        })
        self.assertEqual(status, 200, saved)
        with Image.open(io.BytesIO(base64.b64decode(saved["image"]))) as crop:
            self.assertEqual(crop.size, (19, 28))
            self.assertEqual(crop.getpixel((0, 0)), (0, 128, 0))
        self.device.screenshot.assert_not_called()
        self.device.dump_hierarchy.assert_not_called()
        for bad_xml in ["<hierarchy>", '<!DOCTYPE hierarchy><hierarchy/>']:
            self.assertEqual(call_api("/internal/capture/import", "POST", {
                "image": base64.b64encode(image.getvalue()).decode(), "xml": bad_xml,
            })[0], 400)
        self.assertEqual(call_api("/internal/capture/import", "POST", {"image": "bad", "xml": xml})[0], 400)

    def test_disconnect_preserves_capture(self):
        _, capture = call_api("/internal/capture", "POST", {"hierarchy": True})
        call_api("/internal/disconnect", "POST")
        status, found = call_api("/internal/capture/find", "POST", {
            "snapshot_id": capture["snapshot_id"], "query": 'text="Hello"',
        })
        self.assertEqual((status, found["count"]), (200, 1))

    def test_selected_display_screenshot_tree_and_inputs(self):
        dumpsys = '''Logical Displays:
          Display 0:
            mDisplayId=0
            mBaseDisplayInfo=DisplayInfo{"Main", displayId 0, uniqueId "local:111"}
          Display 3:
            mDisplayId=3
            mBaseDisplayInfo=DisplayInfo{"Secondary", displayId 3, uniqueId "local:999"}
        '''
        self.device.dump_hierarchy.return_value = '''<hierarchy>
          <node display-id="0" text="Main" bounds="[0,0][100,200]"/>
          <node display-id="3" text="Secondary" bounds="[0,0][60,40]">
            <node text="Child" bounds="[1,2][20,30]"/>
          </node>
          <node text="Default" bounds="[0,0][100,200]"/>
        </hierarchy>'''
        image = io.BytesIO()
        Image.new("RGB", (60, 40), "yellow").save(image, "PNG")
        with patch("adbutils.adb.device") as adb_device:
            adb_device.return_value.shell.side_effect = lambda args, **kw: (
                dumpsys if args == ["dumpsys", "display"] else
                image.getvalue() if args == ["screencap", "-p", "-d", "999"] else ""
            )
            status, displays = call_api("/internal/displays")
            self.assertEqual((status, displays["displays"]), (200, [0, 3]))
            self.assertEqual(call_api("/internal/display", "POST", {"display_id": 9})[0], 400)
            self.assertEqual(self.inspector.display_id, 0)
            self.assertEqual(call_api("/internal/display", "POST", {"display_id": 3})[0], 200)
            status, capture = call_api("/internal/capture", "POST", {"hierarchy": True})
            self.assertEqual(status, 200, capture)
            self.assertEqual((capture["width"], capture["height"], capture["display_id"]), (60, 40, 3))
            self.assertEqual([item["text"] for item in capture["elements"]], ["Secondary", "Child"])
            self.assertEqual(capture["node_count"], 3)
            self.assertEqual(call_api("/internal/action", "POST", {"action": "home"})[0], 200)
            adb_device.return_value.shell.assert_called_with(["input", "-d", "3", "keyevent", "3"])
            self.assertEqual(call_api("/internal/clickElem", "POST", {"selector": 'text="Child"'})[0], 200)
            adb_device.return_value.shell.assert_called_with(["input", "-d", "3", "tap", "10.5", "16.0"])
            self.assertEqual(call_api("/internal/clickElem", "POST", {"selector": 'text="Main"'})[0], 400)
        self.device.screenshot.assert_not_called()
        self.device.press.assert_not_called()

    def test_capture_rejects_display_switch_during_read(self):
        store = InspectionStore()
        def switch_display():
            self.inspector.display_id = 3
            return self.device.dump_hierarchy.return_value
        self.device.dump_hierarchy.side_effect = switch_display
        with self.assertRaisesRegex(RuntimeError, "屏幕已切换"):
            store.capture(self.inspector, True)

    def test_viewport_maps_redacted_ivi_display_info(self):
        output = """mViewports=[DisplayViewport{displayId=1, uniqueId='local:999', physicalPort=6},
          DisplayViewport{displayId=3, uniqueId='local:777', physicalPort=10}]
          Display 1:
            mDisplayId=1
            mBaseDisplayInfo=DisplayInfo{uniqueId "****"}
        """
        with patch("adbutils.adb.device") as device:
            device.return_value.shell.return_value = output
            self.assertEqual(self.inspector._physical_display_id(1), 999)
            self.assertEqual(self.inspector._physical_display_id(3), 777)
            self.assertEqual(self.inspector._physical_display_id(0), 0)
            with self.assertRaises(RuntimeError):
                self.inspector._physical_display_id(7)

    def test_screenshot_fallback_order_and_no_wrong_display(self):
        self.inspector.display_id = 1
        image = Image.new("RGB", (192, 48), "green")
        with patch.object(self.inspector, "_adb_screenshot", side_effect=RuntimeError("not supported")) as adb_shot, \
             patch("uihound.remote.capture_frame", return_value=image) as scrcpy:
            self.assertIs(self.inspector.screenshot(), image)
            adb_shot.assert_called_once_with(1)
            scrcpy.assert_called_once_with("test-device", 1)
        self.device.screenshot.assert_not_called()
        self.inspector.display_id = 0
        self.device.screenshot.side_effect = OSError("u2 unavailable")
        with patch.object(self.inspector, "_adb_screenshot", return_value=image) as adb_shot, \
             patch("uihound.remote.capture_frame") as scrcpy:
            self.assertIs(self.inspector.screenshot(), image)
            adb_shot.assert_called_once_with(0)
            scrcpy.assert_not_called()
        self.inspector.display_id = 3
        with patch.object(self.inspector, "_adb_screenshot", side_effect=RuntimeError("ADB denied")), \
             patch("uihound.remote.capture_frame", side_effect=RuntimeError("scrcpy denied")):
            with self.assertRaisesRegex(RuntimeError, "ADB denied.*scrcpy denied"):
                self.inspector.screenshot()

    def test_snapshot_missing_region_rejected(self):
        _, capture = call_api("/internal/capture", "POST", {"hierarchy": False})
        for region in [None, {"x1": 0}, {"x1": -1, "y1": 0, "x2": 10, "y2": 20}]:
            status, _ = call_api("/internal/capture/save", "POST", {"snapshot_id": capture["snapshot_id"], "kind": "region", "region": region})
            self.assertEqual(status, 400)


if __name__ == "__main__":
    unittest.main()
