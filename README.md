# UiHound · UI 猎犬

UiHound 是用于 Android 调试与自动化测试的网页工具。在浏览器中查看设备画面和 UI 节点、复制定位表达式、远程操作设备，并完成取色、截图与录屏。

## 功能

| 功能 | 说明 |
|---|---|
| 元素检查 | UI 节点树、节点虚线边界、选中高亮、属性与坐标展示 |
| 元素定位 | u2 selector / XPath 搜索与复制，优先生成唯一属性定位 |
| 远程操作 | scrcpy 实时画面，点击、拖动、长按、双指滚动、主页、返回、后台任务 |
| 颜色取样 | 像素放大镜、坐标、HEX、RGB、HSB、OpenCV HSV，多点记录与复制 |
| 截图 | 全屏、元素和区域截图，选区移动、缩放及坐标微调 |
| 录屏 | 录制设备画面，保存为无音频 WebM，支持视频预览 |
| 文件管理 | 截图与录屏列表、复制文件、删除单个或全部、打开保存目录 |
| 工作区 | 可调节分栏、白天 / 黑夜主题、偏好记忆 |

## 安装与启动

### 环境准备

- Python 3.10 或以上版本。
- Android Platform Tools，确保 `adb` 已加入 PATH。
- macOS、Windows 或 Linux；新版 Chrome 或 Edge，使用本机地址访问。
- Android 设备开启 USB / 无线调试，并接受调试授权。

在终端执行 `adb devices`，确认设备状态为 `device`。无线连接可先执行 `adb connect 地址:端口`。

### 安装

安装并启动：

```powershell
pip install uihound
uihound
```

服务启动后自动打开 `http://127.0.0.1:8000`。在网页中选择设备或填写设备地址，点击“连接设备”。

启动参数：

```powershell
# 指定端口
uihound --port 8080

# 不自动打开浏览器
uihound --no-browser
```

升级到新版本：

```powershell
pip install --upgrade uihound
```

升级后重启 `uihound`，刷新网页即可。

## 使用方法

### 检查元素：hierarchy

点击设备画面上方的 hierarchy 图标，获取当前截图和 UI 节点树。点击画面或树节点查看属性，复制定位表达式；也可在搜索框输入 selector 或 XPath：

```python
resourceId="com.example:id/title"
text="设置"
//*[@resource-id="com.example:id/title"]
```

XPath 优先使用当前层级中唯一的 `resource-id`、`content-desc`、`text`，没有可用唯一属性时才使用层级路径。

**hierarchy 是静态快照，设备页面变化后需点击刷新。** 动画结束、画面稳定后再获取，便于对齐画面和节点。画面上方的边框按钮可显示或隐藏节点虚线。

### 操作设备：remote

点击 remote 图标进入实时操作模式。鼠标点击对应轻触，按住对应长按，拖动或触摸板双指滚动对应滑动。侧栏提供主页、返回、后台任务等操作。

点击冻结按钮暂停网页画面，暂停期间禁用触摸，再次点击恢复。文本输入在“常用操作”页完成，支持 `Ctrl+Enter` 发送。

首次使用 remote 会下载 scrcpy-server v3.3.4。离线环境可预先下载同版本官方文件，并通过 `UIHOUND_SCRCPY_SERVER` 指定路径。

### 取色：color

点击 color 获取当前无损画面，移动鼠标查看像素与颜色值。方向键微调 1px，`Shift+方向键` 微调 10px；点击或按 Enter 保存取样。最新记录置顶，每项可单独复制。

刷新或切换画面会清除取样记录。OpenCV HSV 为范围换算值，边界颜色可能存在 1 个单位的舍入差异。

### 截图与文件管理

通过侧栏保存全屏截图，或选中元素后点击“元素截图”。区域截图先点击框选按钮，再拖出选区；可拖动选区、调整八个边角、输入坐标或用方向键微调。保存成功后自动清除选区。

hierarchy / color 截取当前静态画面；remote 全屏截图获取设备当前画面。截图不会包含节点边框。

在“截图”页修改保存目录、查看缩略图、复制或删除文件。**删除会真实移除磁盘文件，操作前会确认；删除全部仅针对当前列表，不递归删除子目录。**

“复制文件”后可在目标文件夹使用系统粘贴快捷键（Windows `Ctrl+V`，macOS `Command+V`）粘贴。“打开目录”直接打开保存位置。这两项需要浏览器和服务运行在同一台电脑；文件剪贴板复制支持 Windows 和 macOS，Linux 暂不支持。

### 录屏

点击侧栏圆形录屏按钮开始，再次点击停止并保存；非 remote 模式会自动连接 remote 后开始录制。

录屏默认保存在用户主目录下的 `UiHound\recordings` 文件夹。“录屏”页支持修改保存目录、播放、复制文件、删除和打开目录。录制内容不包含节点边框；冻结网页画面时仍会录制设备新帧。

切换模式或断开设备会停止并保存录屏。录屏接近 240 MiB 时自动停止。**关闭网页前请等待保存完成**；上传失败可在录屏页重试，强制关闭或刷新会丢失未保存的视频。

## 常用设置

截图和录屏页可选择对应根目录内的子目录。需要更换根目录或调整画质时，在启动前设置环境变量：

```powershell
$env:UIHOUND_SNAPSHOT_DIR = "D:\UiHoundData\snapshots"
$env:UIHOUND_RECORDING_DIR = "D:\UiHoundData\recordings"
$env:UIHOUND_SCRCPY_MAX_SIZE = "1920"
$env:UIHOUND_SCRCPY_MAX_FPS = "60"
$env:UIHOUND_SCRCPY_BIT_RATE = "8000000"
uihound
```

默认文件位置固定在用户主目录下，不受启动目录或安装目录影响：

| 文件 | Windows | macOS / Linux |
|---|---|---|
| 截图 | `%USERPROFILE%\UiHound\screenshots` | `~/UiHound/screenshots` |
| 录屏 | `%USERPROFILE%\UiHound\recordings` | `~/UiHound/recordings` |

环境变量可覆盖默认根目录。升级前保存的文件不会自动移动或删除；浏览器记住的路径若已不在当前允许的根目录内，会提示并切换到默认目录。

remote 默认最长边 1920、目标 60 fps、码率 8 Mbps；实际流畅度取决于设备与连接状况。

拖动分隔条调整设备、属性和节点树区域，顶部可重置布局。太阳 / 月亮按钮切换主题，浏览器会记住主题和分栏宽度。

## 自动化调用

| 请求 | 参数 |
|---|---|
| `GET /device/status` | 设备连接状态 |
| `GET /snapshot/full` | 可选 `save_dir`、`filename` |
| `GET /snapshot/element` | 必填 `selector`；可选 `save_dir`、`filename` |
| `GET /snapshot/region` | 必填 `x1`、`y1`、`x2`、`y2`；可选 `save_dir`、`filename` |

截图接口返回 `success`、`file_path`、`base64`、`timestamp`。`save_dir` 必须位于截图根目录内，`filename` 为 `.png` 文件名；同名文件不会覆盖。

服务用于本机调试，请勿直接暴露到不受信任的网络。
