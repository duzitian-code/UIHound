import ast
import base64
import io
import re
import json
import unicodedata
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
import xml.etree.ElementTree as ET

import uiautomator2 as u2
from PIL import Image

from . import config


class Inspector:
    def __init__(self) -> None:
        self.device: Any = None
        self.serial: str | None = None

    def adb_devices(self) -> list[dict[str, str]]:
        import subprocess

        try:
            result = subprocess.run(
                ["adb", "devices"], capture_output=True, text=True, timeout=10, check=False,
            )
        except FileNotFoundError as exc:
            raise RuntimeError("ADB 未安装或未配置 PATH，请安装 Android Platform Tools") from exc
        except subprocess.TimeoutExpired as exc:
            raise RuntimeError("ADB 扫描超时，请检查设备连接") from exc
        if result.returncode:
            raise RuntimeError(result.stderr.strip() or "adb devices 执行失败")
        devices = []
        for line in result.stdout.splitlines()[1:]:
            parts = line.strip().split()
            if len(parts) >= 2 and parts[1] == "device":
                devices.append({"serial": parts[0], "state": parts[1]})
        return devices

    def connect(self, serial: str) -> dict[str, Any]:
        serial = serial.strip()
        if not serial:
            raise ValueError("请输入设备序列号或网络 ADB 地址")
        self.device = u2.connect(serial)
        self.serial = serial
        status = self.status()
        if not status["connected"]:
            self.device = None
            self.serial = None
            raise RuntimeError(status.get("error") or "无法读取设备状态")
        return status

    def status(self) -> dict[str, Any]:
        if self.device is None:
            return {"connected": False, "serial": None, "uiautomator2": False, "device_serial": None, "u2_ready": False}
        try:
            info = self.device.info
            return {
                "connected": True,
                "serial": self.serial,
                "uiautomator2": True,
                "device_serial": self.serial,
                "u2_ready": True,
                "info": {
                    key: info.get(key)
                    for key in ("productName", "model", "brand", "screen")
                    if info.get(key) is not None
                },
            }
        except Exception as exc:
            return {
                "connected": False,
                "serial": self.serial,
                "uiautomator2": False,
                "device_serial": self.serial,
                "u2_ready": False,
                "error": str(exc),
            }

    def disconnect(self) -> dict[str, str]:
        self.device = None
        self.serial = None
        return {"message": "设备已断开"}

    def require_device(self) -> Any:
        if self.device is None:
            raise RuntimeError("尚未连接设备")
        return self.device

    def screenshot(self) -> Image.Image:
        image = self.require_device().screenshot()
        if not isinstance(image, Image.Image):
            raise RuntimeError("设备未返回有效截图")
        return image

    @staticmethod
    def _bounds(value: Any) -> dict[str, int] | None:
        if isinstance(value, (tuple, list)) and len(value) == 4:
            try:
                left, top, right, bottom = map(int, value)
                return {"left": left, "top": top, "right": right, "bottom": bottom}
            except (TypeError, ValueError):
                return None
        if isinstance(value, dict):
            try:
                left, top = int(value["left"]), int(value["top"])
                right, bottom = int(value["right"]), int(value["bottom"])
                return {"left": left, "top": top, "right": right, "bottom": bottom}
            except (KeyError, TypeError, ValueError):
                pass
        if isinstance(value, str):
            match = re.fullmatch(r"\[(\d+),(\d+)]\[(\d+),(\d+)]", value)
            if match:
                left, top, right, bottom = map(int, match.groups())
                return {"left": left, "top": top, "right": right, "bottom": bottom}
        return None

    @staticmethod
    def _with_geometry(attrs: dict[str, Any], bounds: Any) -> dict[str, Any]:
        box = Inspector._bounds(bounds or attrs.get("bounds"))
        result = dict(attrs)
        if box:
            result["bounds"] = box
            result["x"] = box["left"]
            result["y"] = box["top"]
            result["width"] = box["right"] - box["left"]
            result["height"] = box["bottom"] - box["top"]
            result["centerX"] = (box["left"] + box["right"]) / 2
            result["centerY"] = (box["top"] + box["bottom"]) / 2
        return result

    def hierarchy(self, xml: str | None = None) -> dict[str, Any]:
        if xml is None:
            xml = self.require_device().dump_hierarchy()
        root = ET.fromstring(xml)
        elements = []
        all_nodes = []

        def build_tree(node: ET.Element, xpath: str, index: int) -> dict[str, Any]:
            attrs = self._normalize_attrs(dict(node.attrib))
            bounds = self._bounds(attrs.get("bounds"))
            item = self._with_geometry(attrs, bounds)
            item["index"] = index
            item["nodeName"] = node.tag
            item["xpath"] = xpath
            all_nodes.append(item)
            if bounds:
                elements.append(item)

            children = []
            next_index = index + 1
            for position, child in enumerate(node, 1):
                # u2 renames XML node tags to class names; wildcard positions work in both trees.
                child_path = f"{xpath}/*[{position}]"
                children.append(build_tree(child, child_path, next_index))
                next_index += self._node_count(child)
            item["children"] = children
            return item

        tree = build_tree(root, f"/{root.tag}[1]", 0)
        self._attach_unique_selectors(all_nodes)
        visible_nodes = set()

        def retain_primary_display(item: dict[str, Any], inherited_display: str = "0") -> bool:
            display = str(item.get("display-id", inherited_display))
            if display != "0":
                return False
            visible_nodes.add(id(item))
            item["children"] = [
                child for child in item["children"] if retain_primary_display(child, display)
            ]
            return True

        # Keep original XML positions and globally unique selectors for native u2 lookup.
        if not retain_primary_display(tree):
            tree = None
        elements = [item for item in elements if id(item) in visible_nodes]
        return {"elements": elements, "tree": tree, "empty": not elements, "node_count": len(visible_nodes)}

    @staticmethod
    def _usable_selector_value(value: Any) -> bool:
        if not isinstance(value, str):
            return False
        candidate = value.strip()
        if not candidate or any(unicodedata.category(char) == "Cc" for char in candidate):
            return False
        if "\ufffd" in candidate or any(marker in candidate for marker in ("锟斤拷", "烫烫烫", "ï¿½")):
            return False
        if re.fullmatch(r"[?*._-]+", candidate):
            return False
        placeholders = {
            "null", "none", "undefined", "unknown", "placeholder", "n/a",
            "未设置", "未知", "未命名", "占位符", "乱码",
        }
        if candidate.casefold() in placeholders:
            return False
        if (
            candidate.startswith("<") and candidate.endswith(">")
            or candidate.startswith("{{") and candidate.endswith("}}")
            or candidate.startswith("${") and candidate.endswith("}")
        ):
            return False
        return True

    @staticmethod
    def _selector_expression(key: str, value: str) -> str:
        return f"{key}={json.dumps(value, ensure_ascii=False)}"

    @staticmethod
    def _xpath_literal(value: str) -> str:
        if '"' not in value:
            return f'"{value}"'
        if "'" not in value:
            return f"'{value}'"
        # XPath 1.0 has no backslash escapes; join literals when both quotes occur.
        return "concat(" + ", '\"', ".join(f'"{part}"' for part in value.split('"')) + ")"

    def _attach_unique_selectors(self, elements: list[dict[str, Any]]) -> None:
        precedence = (
            ("resourceId", "resourceId"),
            ("text", "text"),
            ("contentDescription", "description"),
            ("className", "className"),
        )
        counts: dict[tuple[str, str], int] = {}
        for element in elements:
            for attribute, _ in precedence:
                value = element.get(attribute)
                if self._usable_selector_value(value):
                    key = (attribute, value)
                    counts[key] = counts.get(key, 0) + 1

        for element in elements:
            element["u2Selector"] = None
            for attribute, selector_key in precedence:
                value = element.get(attribute)
                if not self._usable_selector_value(value):
                    continue
                if counts.get((attribute, value)) == 1:
                    element["u2Selector"] = self._selector_expression(selector_key, value)
                    break
            for attribute, xml_attribute in (
                ("resourceId", "resource-id"),
                ("contentDescription", "content-desc"),
                ("text", "text"),
            ):
                value = element.get(attribute)
                if self._usable_selector_value(value) and counts.get((attribute, value)) == 1:
                    element["xpath"] = f"//*[@{xml_attribute}={self._xpath_literal(value)}]"
                    break

    @staticmethod
    def _node_count(node: ET.Element) -> int:
        return 1 + sum(Inspector._node_count(child) for child in list(node))

    @staticmethod
    def _normalize_attrs(attrs: dict[str, Any]) -> dict[str, Any]:
        aliases = {
            "package": "packageName",
            "class": "className",
            "resource-id": "resourceId",
            "resourceName": "resourceId",
            "content-desc": "contentDescription",
            "long-clickable": "longClickable",
            "visible-to-user": "visibleToUser",
        }
        normalized = {aliases.get(key, key): value for key, value in attrs.items()}
        if "resourceId" in normalized:
            normalized["resourceName"] = normalized["resourceId"]
        boolean_fields = {
            "enabled", "clickable", "checkable", "checked", "longClickable",
            "scrollable", "focusable", "focused", "selected", "password",
            "visibleToUser",
        }
        for key, value in tuple(normalized.items()):
            if key in boolean_fields and value in ("true", "false"):
                normalized[key] = value == "true"
        return normalized

    @staticmethod
    def _xpath_for_node(root: ET.Element, target: ET.Element) -> str:
        path: list[str] = []
        current = target
        while current is not root:
            parent = next((candidate for candidate in root.iter() if current in list(candidate)), None)
            if parent is None:
                break
            siblings = [child for child in list(parent) if child.tag == current.tag]
            position = siblings.index(current) + 1
            path.append(f"{current.tag}[{position}]")
            current = parent
        path.append(f"{root.tag}[1]")
        return "/" + "/".join(reversed(path))

    def native_selector(self, query: str) -> Any:
        device = self.require_device()
        query = query.strip()
        if not query:
            raise ValueError("请输入 u2 selector 或 XPath")
        if query.startswith("/") or query.startswith("("):
            return device.xpath(query)
        return device(**self.parse_selector(query))

    @staticmethod
    def parse_selector(query: str) -> dict[str, Any]:
        try:
            expression = ast.parse(f"dict({query})", mode="eval").body
            if (
                not isinstance(expression, ast.Call) or expression.args
                or not isinstance(expression.func, ast.Name) or expression.func.id != "dict"
            ):
                raise ValueError
            if any(keyword.arg is None for keyword in expression.keywords):
                raise ValueError
            selector = {
                keyword.arg: ast.literal_eval(keyword.value)
                for keyword in expression.keywords
            }
            if len(selector) != len(expression.keywords):
                raise ValueError
        except (SyntaxError, ValueError, TypeError) as exc:
            raise ValueError('selector 格式示例：text="设置" 或 resourceId="com.app:id/title"') from exc
        allowed = {
            "text", "textContains", "textMatches", "className", "classNameMatches",
            "description", "descriptionContains", "descriptionMatches", "resourceId",
            "resourceIdMatches", "packageName", "packageNameMatches", "checkable",
            "checked", "clickable", "longClickable", "scrollable", "enabled",
            "focusable", "focused", "selected", "instance",
        }
        if not selector or not set(selector).issubset(allowed):
            raise ValueError("selector 为空或包含不支持的属性")
        if any(not isinstance(value, (str, bool, int)) for value in selector.values()):
            raise ValueError("selector 属性值必须是字符串、布尔值或整数")
        return selector

    def _resolve(self, query: str) -> list[dict[str, Any]]:
        selector_object = self.native_selector(query)
        if query.strip().startswith(("/", "(")):
            xpath_result = selector_object
            if not callable(getattr(xpath_result, "all", None)):
                raise RuntimeError("uiautomator2 XPath selector 没有提供 all() 查询接口")
            matched = xpath_result.all()
            results = []
            for element in matched:
                attrs = self._normalize_attrs(dict(getattr(element, "attrib", {}) or {}))
                bounds = getattr(element, "bounds", None)
                result = self._with_geometry(attrs, bounds)
                get_xpath = getattr(element, "get_xpath", None)
                if callable(get_xpath):
                    result["xpath"] = get_xpath()
                results.append(result)
            return results

        elements = [
            selector_object[index]
            for index in range(selector_object.count)
        ]
        results = []
        for element in elements:
            info = self._normalize_attrs(dict(element.info))
            results.append(self._with_geometry(info, info.get("bounds")))
        return results

    def search(self, query: str) -> list[dict[str, Any]]:
        matches = self._resolve(query)
        hierarchy = self.hierarchy()["elements"]
        for match in matches:
            match_bounds = self._bounds(match.get("bounds"))
            exact = next((item for item in hierarchy if item.get("xpath") == match.get("xpath")), None)
            if exact is None and match_bounds:
                exact = next(
                    (
                        item for item in hierarchy
                        if item.get("bounds") == match_bounds
                        and all(
                            not match.get(key) or item.get(key) == match.get(key)
                            for key in ("resourceId", "text", "contentDescription", "className")
                        )
                    ),
                    None,
                )
            if exact:
                match["xpath"] = exact["xpath"]
                match["u2Selector"] = exact["u2Selector"]
        return matches

    def element_for_point(self, x: int, y: int) -> dict[str, Any]:
        candidates = [
            item for item in self.hierarchy()["elements"]
            if item["bounds"]["left"] <= x <= item["bounds"]["right"]
            and item["bounds"]["top"] <= y <= item["bounds"]["bottom"]
        ]
        if not candidates:
            raise LookupError("该坐标位置没有可探查的 UI 元素")
        return min(candidates, key=lambda item: item["width"] * item["height"])

    def save_snapshot(
        self,
        kind: str,
        custom_path: str | None = None,
        selector: str | None = None,
        region: dict[str, int] | None = None,
        filename: str | None = None,
        image: Image.Image | None = None,
    ) -> dict[str, Any]:
        directory = self._snapshot_directory(custom_path)
        if filename is not None:
            if (
                not re.fullmatch(r'[^<>:"/\\|?*\x00-\x1f]+\.png', filename, re.IGNORECASE)
                or filename.startswith(".")
                or filename.rstrip(" .") != filename
                or filename.split(".")[0].upper() in {"CON", "PRN", "AUX", "NUL", *(f"COM{i}" for i in range(10)), *(f"LPT{i}" for i in range(10))}
            ):
                raise ValueError("filename 必须是不含路径的有效 PNG 文件名")
        metadata: dict[str, Any] = {}
        captured_image = image is not None
        image = image if image is not None else self.screenshot()
        if kind == "element":
            if captured_image and region is not None:
                bounds = self._bounds([region["x1"], region["y1"], region["x2"], region["y2"]])
            else:
                if not selector:
                    raise ValueError("元素截图需要 selector 或 XPath")
                elements = self._resolve(selector)
                if not elements:
                    raise LookupError("未找到匹配元素")
                bounds = self._bounds(elements[0].get("bounds"))
            if not bounds:
                raise ValueError("匹配元素没有有效边界")
            if (
                bounds["left"] < 0
                or bounds["top"] < 0
                or bounds["right"] > image.width
                or bounds["bottom"] > image.height
                or bounds["right"] <= bounds["left"]
                or bounds["bottom"] <= bounds["top"]
            ):
                raise ValueError("匹配元素边界超出屏幕范围")
            image = image.crop(
                (bounds["left"], bounds["top"], bounds["right"], bounds["bottom"])
            )
            metadata["bounds"] = bounds
        elif kind == "region":
            if not region:
                raise ValueError("区域截图需要 x1、y1、x2、y2")
            x1, x2 = sorted((int(region["x1"]), int(region["x2"])))
            y1, y2 = sorted((int(region["y1"]), int(region["y2"])))
            if x1 == x2 or y1 == y2:
                raise ValueError("框选区域不能为空")
            if x1 < 0 or y1 < 0 or x2 > image.width or y2 > image.height:
                raise ValueError("框选区域超出屏幕范围")
            image = image.crop((x1, y1, x2, y2))
            metadata["region"] = {"x1": x1, "y1": y1, "x2": x2, "y2": y2}
        elif kind != "full":
            raise ValueError("不支持的截图类型")

        directory.mkdir(parents=True, exist_ok=True)
        filename = filename or f"{'elem' if kind == 'element' else kind}_{datetime.now().strftime('%Y%m%d_%H%M%S_%f')}.png"
        destination = directory / filename
        if destination.resolve().parent != directory.resolve():
            raise ValueError("截图文件路径超出保存目录")
        buffer = io.BytesIO()
        image.save(buffer, format="PNG")
        try:
            with destination.open("xb") as output:
                output.write(buffer.getvalue())
        except FileExistsError as exc:
            raise ValueError("截图文件已存在，请使用其他文件名") from exc
        encoded = base64.b64encode(buffer.getvalue()).decode("ascii")
        path = str(destination.resolve())
        return {
            "success": True, "file_path": path, "base64": encoded,
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "path": path, "image": encoded, **metadata,
        }

    @staticmethod
    def _snapshot_directory(custom_path: str | None) -> Path:
        root = config.SNAPSHOT_DIR.resolve()
        if not custom_path:
            return root
        requested = Path(custom_path).expanduser()
        candidate = requested.resolve() if requested.is_absolute() else (root / requested).resolve()
        if candidate != root and root not in candidate.parents:
            raise ValueError("保存路径必须位于 snapshots 目录内")
        return candidate


inspector = Inspector()
