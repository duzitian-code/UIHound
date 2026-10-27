"""User-initiated local file dialogs, isolated from ASGI worker threads."""

import base64
import json
import os
import subprocess
import sys
from pathlib import Path

from . import config


def choose_file(kind: str) -> dict:
    if kind not in {"image", "xml"}:
        raise ValueError("请选择图片或 XML")
    directory = config.IMPORT_DIR.resolve()
    directory.mkdir(parents=True, exist_ok=True)
    script = """
import json, sys, tkinter as tk
from tkinter import filedialog
root = tk.Tk()
root.withdraw()
root.attributes('-topmost', True)
try:
    types = [('Images', '*.png *.jpg *.jpeg *.webp')] if sys.argv[2] == 'image' else [('XML', '*.xml')]
    path = filedialog.askopenfilename(parent=root, initialdir=sys.argv[1],
        title='UiHound - Select ' + sys.argv[2], filetypes=types)
    print(json.dumps(path))
finally:
    root.destroy()
"""
    try:
        options = {}
        command = [sys.executable, "-c", script, str(directory), kind]
        if os.name == "nt":
            # Disable the Vista dialog's remembered-folder behavior; honor InitialDirectory on every invocation.
            windows_script = r"""
$ErrorActionPreference = 'Stop'
[Console]::OutputEncoding = New-Object System.Text.UTF8Encoding($false)
Add-Type -AssemblyName System.Windows.Forms
$dialog = New-Object System.Windows.Forms.OpenFileDialog
try {
    $dialog.AutoUpgradeEnabled = $false
    $dialog.RestoreDirectory = $true
    $dialog.InitialDirectory = $env:UIHOUND_PICKER_DIRECTORY
    $dialog.FileName = ''
    $dialog.Multiselect = $false
    $dialog.CheckFileExists = $true
    $dialog.Title = 'UiHound - Select ' + $env:UIHOUND_PICKER_KIND
    if ($env:UIHOUND_PICKER_KIND -eq 'image') {
        $dialog.Filter = 'Images (*.png;*.jpg;*.jpeg;*.webp)|*.png;*.jpg;*.jpeg;*.webp'
    } else {
        $dialog.Filter = 'XML (*.xml)|*.xml'
    }
    if ($dialog.ShowDialog() -eq [System.Windows.Forms.DialogResult]::OK) {
        ConvertTo-Json -Compress -InputObject $dialog.FileName
    } else {
        ConvertTo-Json -Compress -InputObject ''
    }
} finally {
    $dialog.Dispose()
}
"""
            command = ["powershell.exe", "-NoProfile", "-NonInteractive", "-STA", "-Command", windows_script]
            options = {
                "creationflags": subprocess.CREATE_NO_WINDOW,
                "env": {**os.environ, "UIHOUND_PICKER_DIRECTORY": str(directory), "UIHOUND_PICKER_KIND": kind},
            }
        result = subprocess.run(
            command,
            capture_output=True, text=True, encoding="utf-8", check=True, timeout=300,
            **options,
        )
    except subprocess.TimeoutExpired as exc:
        raise RuntimeError("文件选择窗口超时，请重新选择") from exc
    except subprocess.CalledProcessError as exc:
        raise RuntimeError(f"无法打开系统文件选择窗口，请确认服务运行在桌面会话：{exc.stderr.strip()}") from exc
    value = json.loads(result.stdout)
    if not value:
        return {"cancelled": True}
    path = Path(value)
    suffixes = {".png", ".jpg", ".jpeg", ".webp"} if kind == "image" else {".xml"}
    if not path.is_file() or path.suffix.lower() not in suffixes:
        raise ValueError("所选文件类型无效")
    limit = (24 if kind == "image" else 8) * 1024 * 1024
    with path.open("rb") as file:
        data = file.read(limit + 1)
    if len(data) > limit:
        raise ValueError(f"文件超过 {limit // 1024 // 1024} MiB 限制")
    return {
        "cancelled": False, "name": path.name, "directory": str(directory),
        "content": base64.b64encode(data).decode("ascii") if kind == "image" else data.decode("utf-8-sig"),
    }
