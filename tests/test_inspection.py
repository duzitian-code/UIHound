import base64
import io
import unittest
from unittest.mock import patch

from PIL import Image

import test_backend
from test_backend import call_api
from uihound.inspection import InspectionStore


class InspectionTests(unittest.TestCase):
    setUp = test_backend.BackendTests.setUp

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
        with self.assertRaises(ValueError):
            store.get(capture["snapshot_id"], self.inspector)
        self.inspector.device = self.device
        for _ in range(9):
            store.capture(self.inspector, False)
        with self.assertRaises(ValueError):
            store.get(capture["snapshot_id"], self.inspector)

    def test_snapshot_missing_region_rejected(self):
        _, capture = call_api("/internal/capture", "POST", {"hierarchy": False})
        for region in [None, {"x1": 0}, {"x1": -1, "y1": 0, "x2": 10, "y2": 20}]:
            status, _ = call_api("/internal/capture/save", "POST", {"snapshot_id": capture["snapshot_id"], "kind": "region", "region": region})
            self.assertEqual(status, 400)


if __name__ == "__main__":
    unittest.main()
