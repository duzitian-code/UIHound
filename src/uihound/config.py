from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="UIHOUND_")

    host: str = "127.0.0.1"
    port: int = Field(default=8000, ge=1, le=65535)
    snapshot_dir: Path = Field(default_factory=lambda: Path.home() / "UiHound" / "screenshots")
    recording_dir: Path = Field(default_factory=lambda: Path.home() / "UiHound" / "recordings")
    log_dir: Path = Field(default_factory=lambda: Path.cwd() / "logs")
    open_browser: bool = True
    screenshot_quality: int = Field(default=90, ge=1, le=100)
    screen_stream_fps: int = Field(default=12, ge=1, le=60)
    screen_stream_quality: int = Field(default=80, ge=1, le=100)
    scrcpy_max_size: int = Field(default=1920, ge=0, le=8192)
    scrcpy_max_fps: int = Field(default=60, ge=1, le=120)
    scrcpy_bit_rate: int = Field(default=8_000_000, ge=100_000, le=100_000_000)


settings = Settings()
BASE_DIR = Path(__file__).resolve().parent
STATIC_DIR = BASE_DIR / "static"
SNAPSHOT_DIR = settings.snapshot_dir.expanduser().resolve()
RECORDING_DIR = settings.recording_dir.expanduser().resolve()
LOG_DIR = settings.log_dir.expanduser().resolve()
HOST = settings.host
PORT = settings.port
SCREENSHOT_QUALITY = settings.screenshot_quality
SCREEN_STREAM_FPS = settings.screen_stream_fps
SCREEN_STREAM_QUALITY = settings.screen_stream_quality
