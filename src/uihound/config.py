from pathlib import Path
import json
import os
import tempfile
import threading

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="UIHOUND_")

    host: str = "127.0.0.1"
    port: int = Field(default=8000, ge=1, le=65535)
    project_dir: Path = Field(default_factory=lambda: Path.home() / "UiHound")
    snapshot_dir: Path = Field(default_factory=lambda: Path.home() / "UiHound" / "screenshots")
    recording_dir: Path = Field(default_factory=lambda: Path.home() / "UiHound" / "recordings")
    inspection_dir: Path | None = None
    import_dir: Path | None = None
    log_dir: Path = Field(default_factory=lambda: Path.cwd() / "logs")
    open_browser: bool = True
    screenshot_quality: int = Field(default=90, ge=1, le=100)
    screen_stream_fps: int = Field(default=12, ge=1, le=60)
    screen_stream_quality: int = Field(default=80, ge=1, le=100)
    scrcpy_max_size: int = Field(default=1920, ge=0, le=8192)
    scrcpy_max_fps: int = Field(default=60, ge=1, le=120)
    scrcpy_bit_rate: int = Field(default=8_000_000, ge=100_000, le=100_000_000)


settings = Settings()
PREFERENCES_FILE = Path.home() / "UiHound" / "settings.json"
_path_lock = threading.Lock()
_path_overrides: dict[str, str] = {}
_directory_names = {
    "snapshot_directory": ("snapshot_dir", "screenshots"),
    "recording_directory": ("recording_dir", "recordings"),
    "inspection_directory": ("inspection_dir", "snapshots"),
}


def resolve_directories(values: dict[str, str]) -> dict[str, str]:
    project = Path(values.get("project_directory") or settings.project_dir).expanduser()
    if not project.is_absolute():
        raise ValueError("项目根目录必须是绝对路径")
    result = {"project_directory": str(project.resolve())}
    for key, (field, child) in _directory_names.items():
        value = values.get(key)
        if key not in values and field in settings.model_fields_set:
            value = getattr(settings, field)
        path = Path(value).expanduser() if value else project / child
        if not path.is_absolute():
            path = project / path
        result[key] = str(path.resolve())
    value = values.get("import_directory")
    if "import_directory" not in values and "import_dir" in settings.model_fields_set:
        value = settings.import_dir
    path = Path(value).expanduser() if value else Path(result["inspection_directory"])
    result["import_directory"] = str((path if path.is_absolute() else project / path).resolve())
    screenshots = Path(result["snapshot_directory"])
    inspections = Path(result["inspection_directory"])
    if screenshots == inspections or screenshots in inspections.parents or inspections in screenshots.parents:
        raise ValueError("截图目录和快照目录必须独立，不能相同或互为子目录")
    return result


def _apply_directories(values: dict[str, str]) -> None:
    global PROJECT_DIR, SNAPSHOT_DIR, RECORDING_DIR, INSPECTION_DIR, IMPORT_DIR, _path_overrides
    paths = resolve_directories(values)
    PROJECT_DIR = Path(paths["project_directory"])
    SNAPSHOT_DIR = Path(paths["snapshot_directory"])
    RECORDING_DIR = Path(paths["recording_directory"])
    INSPECTION_DIR = Path(paths["inspection_directory"])
    IMPORT_DIR = Path(paths["import_directory"])
    _path_overrides = dict(values)


def directory_settings() -> dict:
    return {
        "project_directory": str(PROJECT_DIR), "snapshot_directory": str(SNAPSHOT_DIR),
        "recording_directory": str(RECORDING_DIR), "inspection_directory": str(INSPECTION_DIR),
        "import_directory": str(IMPORT_DIR), "overrides": dict(_path_overrides),
    }


def save_directories(values: dict[str, str]) -> dict:
    allowed = {"project_directory", "import_directory", *_directory_names}
    if set(values) - allowed:
        raise ValueError("目录设置包含未知字段")
    paths = resolve_directories(values)
    for value in paths.values():
        Path(value).mkdir(parents=True, exist_ok=True)
    with _path_lock:
        PREFERENCES_FILE.parent.mkdir(parents=True, exist_ok=True)
        fd, temporary = tempfile.mkstemp(prefix="settings-", suffix=".tmp", dir=PREFERENCES_FILE.parent)
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as file:
                json.dump(values, file, ensure_ascii=False, indent=2)
            os.replace(temporary, PREFERENCES_FILE)
        finally:
            if os.path.exists(temporary):
                os.unlink(temporary)
        _apply_directories(values)
    return directory_settings()


if PREFERENCES_FILE.exists():
    with PREFERENCES_FILE.open(encoding="utf-8") as file:
        saved = json.load(file)
    if not isinstance(saved, dict) or any(not isinstance(value, str) for value in saved.values()):
        raise ValueError(f"目录设置文件格式无效：{PREFERENCES_FILE}")
    _apply_directories(saved)
else:
    _apply_directories({})
BASE_DIR = Path(__file__).resolve().parent
STATIC_DIR = BASE_DIR / "static"
LOG_DIR = settings.log_dir.expanduser().resolve()
HOST = settings.host
PORT = settings.port
SCREENSHOT_QUALITY = settings.screenshot_quality
SCREEN_STREAM_FPS = settings.screen_stream_fps
SCREEN_STREAM_QUALITY = settings.screen_stream_quality
