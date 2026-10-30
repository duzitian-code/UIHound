# UiHound · UI 猎犬

UiHound 是用于 Android 调试与自动化测试的本地网页工具。它可以查看设备画面和 UI 节点、查找元素定位信息，并提供远程操作、取色、截图、录屏和离线分析功能。

## 功能

- **元素检查**：查看 UI 节点树、元素属性、坐标和画面高亮。
- **元素定位**：搜索并复制 u2 selector 或 XPath。
- **设备操作**：查看实时设备画面，执行点击、滑动、长按、主页、返回等操作。
- **颜色取样**：查看像素坐标及 HEX、RGB、HSB、HSV 颜色值。
- **截图与录屏**：保存设备全屏、元素或区域截图，并录制设备画面。
- **文件管理**：查看、复制和管理截图及录屏文件。
- **多屏设备**：选择 Android Display，检查对应屏幕并进行操作。
- **离线分析**：导入图片和 UI XML，无需连接设备即可检查节点和画面。

## 环境准备

- Python 3.10 或更高版本。
- Android Platform Tools，并确保 `adb` 已加入系统 PATH。
- Android 设备已开启 USB 或无线调试，并接受调试授权。
- macOS、Windows 或 Linux；建议使用新版 Chrome 或 Edge。

连接设备前，可在终端运行以下命令确认 ADB 能识别设备：

```shell
adb devices
```

设备状态应为 `device`。无线调试可先运行：

```shell
adb connect 地址:端口
```

## 安装与启动

从 PyPI 安装并启动：

```shell
pip install uihound
uihound
```

服务启动后会自动打开浏览器，默认地址为 `http://127.0.0.1:8000`。在网页顶部选择或输入 ADB 设备，然后点击“连接设备”。

### 启动选项

```shell
# 指定端口
uihound --port 8080

# 不自动打开浏览器
uihound --no-browser

# 查看版本
uihound --version
```

升级到最新版本：

```shell
pip install --upgrade uihound
```

升级后重启 UiHound 服务并刷新网页。

## 从源码运行

克隆项目后，在项目根目录执行：

```shell
python -m pip install .
uihound
```

## 网络安全

UiHound 默认仅供本机调试。请勿将服务直接暴露到不受信任的网络。
