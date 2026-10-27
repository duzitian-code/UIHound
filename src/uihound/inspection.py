"""Bounded screen snapshots that remain available for offline analysis."""

import base64
import io
import re
import threading
import time
import uuid
import json
from pathlib import Path
from xml.etree.ElementTree import ParseError
from collections import OrderedDict
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

from lxml import etree
from PIL import Image
from uiautomator2.xpath import PageSource

from .inspector import Inspector
from . import config, media


def archive_directory(value: str | None = None) -> Path:
    root = config.INSPECTION_DIR.resolve()
    return media._safe_path(root, value, directory=True)


def archive_files(value: str) -> tuple[Path, Path, Path]:
    root = config.INSPECTION_DIR.resolve()
    folder = media._safe_path(root, value, directory=True)
    if folder == root:
        raise ValueError("请选择已保存的分析快照文件夹")
    paths = tuple(media._safe_path(root, str(folder / name), directory=False)
                  for name in ("screen.png", "hierarchy.xml", "metadata.json"))
    if not all(path.is_file() for path in paths):
        raise ValueError("分析快照不完整，需要 screen.png、hierarchy.xml 和 metadata.json")
    return paths


def list_archives(directory: str | None) -> dict[str, Any]:
    folder = archive_directory(directory)
    files = []
    if folder.exists():
        if not folder.is_dir():
            raise ValueError("导入路径必须是目录")
        for entry in folder.iterdir():
            if not entry.name.startswith("capture_") or entry.is_symlink() or not entry.is_dir():
                continue
            image, xml, metadata = archive_files(str(entry))
            files.append({"path": str(entry), "name": entry.name, "modified": metadata.stat().st_mtime})
    files.sort(key=lambda item: (item["modified"], item["name"]), reverse=True)
    return {"directory": str(folder), "files": files}


@dataclass
class Inspection:
    device: Any
    image: Image.Image
    xml: str | None
    hierarchy: dict[str, Any]
    created: float
    display_id: int = 0


class InspectionStore:
    def __init__(self):
        self._items: OrderedDict[str, Inspection] = OrderedDict()
        self._lock = threading.Lock()

    def capture(self, inspector: Inspector, with_hierarchy: bool) -> dict[str, Any]:
        device = inspector.require_device()
        display_id = getattr(inspector, "display_id", 0)
        image = inspector.screenshot().convert("RGB")
        xml = device.dump_hierarchy() if with_hierarchy else None
        hierarchy = inspector.hierarchy(xml, display_id=display_id) if xml is not None else {
            "elements": [], "tree": None, "empty": True, "node_count": 0,
        }
        if inspector.device is not device or getattr(inspector, "display_id", 0) != display_id:
            raise RuntimeError("设备或屏幕已切换，请重新获取画面")
        return self.store(device, image, xml, hierarchy, display_id, inspector.screenshot_method)

    def import_files(self, inspector: Inspector, image_data: bytes, xml: str, display_id: int) -> dict[str, Any]:
        if "<!DOCTYPE" in xml.upper() or "<!ENTITY" in xml.upper():
            raise ValueError("导入 XML 不允许包含 DTD 或实体定义")
        if len(xml) > 8 * 1024 * 1024:
            raise ValueError("XML 超过 8 MiB 限制")
        try:
            root = etree.fromstring(xml.encode("utf-8"), etree.XMLParser(resolve_entities=False, no_network=True))
            if root.tag != "hierarchy":
                raise ValueError("XML 根节点必须为 Android hierarchy")
            if sum(1 for _ in root.iter()) > 100_000:
                raise ValueError("XML 节点不能超过 100000 个")
            with Image.open(io.BytesIO(image_data)) as source:
                if source.format not in {"PNG", "JPEG", "WEBP"}:
                    raise ValueError("图片仅支持 PNG、JPEG 或 WebP")
                if source.width * source.height > 32_000_000:
                    raise ValueError("图片不能超过 3200 万像素")
                image = source.convert("RGB")
            hierarchy = inspector.hierarchy(xml, display_id=display_id)
        except (OSError, Image.DecompressionBombError, etree.XMLSyntaxError, ParseError, RecursionError) as exc:
            raise ValueError(f"无法解析导入文件：{exc}") from exc
        return self.store(None, image, xml, hierarchy, display_id)

    def store(self, device: Any, image: Image.Image, xml: str | None,
              hierarchy: dict[str, Any], display_id: int, screenshot_method: str | None = None) -> dict[str, Any]:
        snapshot_id = uuid.uuid4().hex
        with self._lock:
            self._items[snapshot_id] = Inspection(device, image, xml, hierarchy, time.monotonic(), display_id)
            while len(self._items) > 8:
                self._items.popitem(last=False)
        buffer = io.BytesIO()
        image.save(buffer, format="PNG")
        return {
            **hierarchy, "snapshot_id": snapshot_id,
            "image": base64.b64encode(buffer.getvalue()).decode("ascii"),
            "width": image.width, "height": image.height,
            "display_id": display_id,
            "screenshot_method": screenshot_method,
            "captured_at": datetime.now(timezone.utc).isoformat(),
        }

    def get(self, snapshot_id: str, inspector: Inspector) -> Inspection:
        with self._lock:
            item = self._items.get(snapshot_id)
        if item is None or time.monotonic() - item.created > 1800:
            raise ValueError("画面快照已过期，请点击刷新")
        return item

    def clear(self) -> None:
        with self._lock:
            self._items.clear()

    def save_archive(self, snapshot_id: str, inspector: Inspector, directory: str | None) -> dict[str, str]:
        item = self.get(snapshot_id, inspector)
        if item.xml is None:
            raise ValueError("当前画面没有 XML，请先获取 hierarchy 快照")
        xml_data = item.xml.encode("utf-8")
        if len(xml_data) > 8 * 1024 * 1024 or item.image.width * item.image.height > 32_000_000:
            raise ValueError("当前快照超过导入限制：XML 上限 8 MiB，图片上限 3200 万像素")
        parent = archive_directory(directory)
        parent.mkdir(parents=True, exist_ok=True)
        folder = parent / f"capture_{datetime.now().strftime('%Y%m%d_%H%M%S_%f')}_display_{item.display_id}_{uuid.uuid4().hex[:8]}"
        folder.mkdir()
        created = []
        try:
            for name, content in (
                ("hierarchy.xml", xml_data),
                ("metadata.json", json.dumps({"display_id": item.display_id}, ensure_ascii=True).encode("utf-8")),
            ):
                path = folder / name
                with path.open("xb") as file:
                    created.append(path)
                    file.write(content)
            path = folder / "screen.png"
            with path.open("xb") as file:
                created.append(path)
                item.image.save(file, format="PNG")
                if file.tell() > 24 * 1024 * 1024:
                    raise ValueError("图片超过 24 MiB 导入限制")
        except (OSError, ValueError):
            for path in created:
                path.unlink()
            folder.rmdir()
            raise
        return {"directory": str(parent), "path": str(folder), "name": folder.name}

    def load_archive(self, inspector: Inspector, value: str) -> dict[str, Any]:
        image, xml, metadata = archive_files(value)
        if image.stat().st_size > 24 * 1024 * 1024 or xml.stat().st_size > 8 * 1024 * 1024 or metadata.stat().st_size > 4096:
            raise ValueError("分析快照文件超过导入大小限制")
        try:
            info = json.loads(metadata.read_text(encoding="utf-8"))
            if not isinstance(info, dict):
                raise ValueError("分析快照 metadata.json 格式无效")
            display_id = info["display_id"]
            if type(display_id) is not int or not 0 <= display_id <= 2147483647:
                raise ValueError("分析快照 Display ID 无效")
            xml_text = xml.read_text(encoding="utf-8")
            image_data = image.read_bytes()
        except (UnicodeError, KeyError, json.JSONDecodeError) as exc:
            raise ValueError(f"分析快照格式无效：{exc}") from exc
        result = self.import_files(inspector, image_data, xml_text, display_id)
        return {**result, "xml": xml_text}

    def search(self, snapshot_id: str, query: str, inspector: Inspector) -> list[dict[str, Any]]:
        item = self.get(snapshot_id, inspector)
        if item.xml is None:
            raise ValueError("当前为颜色快照，请点击 hierarchy 获取层级")
        return self.search_hierarchy(item.xml, item.hierarchy["elements"], query)

    @staticmethod
    def search_hierarchy(xml: str, elements: list[dict[str, Any]], query: str) -> list[dict[str, Any]]:
        query = query.strip()
        if query.startswith(("/", "(")):
            source = PageSource.parse(xml)
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
