"""Bounded, device-scoped screen snapshots used by hierarchy and color modes."""

import base64
import io
import re
import threading
import time
import uuid
from collections import OrderedDict
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

from lxml import etree
from PIL import Image
from uiautomator2.xpath import PageSource

from .inspector import Inspector


@dataclass
class Inspection:
    device: Any
    image: Image.Image
    xml: str | None
    hierarchy: dict[str, Any]
    created: float


class InspectionStore:
    def __init__(self):
        self._items: OrderedDict[str, Inspection] = OrderedDict()
        self._lock = threading.Lock()

    def capture(self, inspector: Inspector, with_hierarchy: bool) -> dict[str, Any]:
        device = inspector.require_device()
        image = inspector.screenshot().convert("RGB")
        xml = device.dump_hierarchy() if with_hierarchy else None
        hierarchy = inspector.hierarchy(xml) if xml is not None else {
            "elements": [], "tree": None, "empty": True, "node_count": 0,
        }
        if inspector.device is not device:
            raise RuntimeError("设备已切换，请重新获取画面")
        snapshot_id = uuid.uuid4().hex
        with self._lock:
            self._items[snapshot_id] = Inspection(device, image, xml, hierarchy, time.monotonic())
            while len(self._items) > 8:
                self._items.popitem(last=False)
        buffer = io.BytesIO()
        image.save(buffer, format="PNG")
        return {
            **hierarchy, "snapshot_id": snapshot_id,
            "image": base64.b64encode(buffer.getvalue()).decode("ascii"),
            "width": image.width, "height": image.height,
            "captured_at": datetime.now(timezone.utc).isoformat(),
        }

    def get(self, snapshot_id: str, inspector: Inspector) -> Inspection:
        with self._lock:
            item = self._items.get(snapshot_id)
        if item is None or time.monotonic() - item.created > 1800:
            raise ValueError("画面快照已过期，请点击刷新")
        if item.device is not inspector.require_device():
            raise ValueError("设备已切换，请点击刷新")
        return item

    def clear(self) -> None:
        with self._lock:
            self._items.clear()

    def search(self, snapshot_id: str, query: str, inspector: Inspector) -> list[dict[str, Any]]:
        item = self.get(snapshot_id, inspector)
        if item.xml is None:
            raise ValueError("当前为颜色快照，请点击 hierarchy 获取层级")
        elements = item.hierarchy["elements"]
        query = query.strip()
        if query.startswith(("/", "(")):
            source = PageSource.parse(item.xml)
            try:
                matches = source.root.xpath(query, namespaces={"re": "http://exslt.org/regular-expressions"})
                if not isinstance(matches, list) or any(not isinstance(node, etree._Element) for node in matches):
                    raise ValueError("XPath 必须返回元素节点")
                matched = set(matches)
                return [
                    element for element in elements
                    if any(node in matched for node in source.root.xpath(element["xpath"]))
                ]
            except etree.XPathError as exc:
                raise ValueError(f"XPath 无效：{exc}") from exc
        selector = Inspector.parse_selector(query)
        instance = selector.pop("instance", None)
        if instance is not None and (type(instance) is not int or instance < 0):
            raise ValueError("instance 必须是非负整数")
        aliases = {"description": "contentDescription"}
        def matches(element: dict[str, Any]) -> bool:
            for key, expected in selector.items():
                operation = "exact"
                for suffix in ("Contains", "Matches"):
                    if key.endswith(suffix):
                        key, operation = key[:-len(suffix)], suffix
                        break
                actual = element.get(aliases.get(key, key), "")
                if operation == "Contains":
                    if not isinstance(expected, str):
                        raise ValueError("Contains 查询值必须是字符串")
                    if expected not in str(actual):
                        return False
                elif operation == "Matches":
                    if not isinstance(expected, str):
                        raise ValueError("Matches 查询值必须是字符串")
                    try:
                        if not re.fullmatch(expected, str(actual)):
                            return False
                    except re.error as exc:
                        raise ValueError(f"正则表达式无效：{exc}") from exc
                elif actual != expected:
                    return False
            return True
        found = [element for element in elements if matches(element)]
        return found if instance is None else found[instance:instance + 1]


inspections = InspectionStore()
