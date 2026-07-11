"""跨平台路径、平台探测与用户配置。"""
from __future__ import annotations

import json
import os
import platform
import sys
from dataclasses import dataclass, field
from pathlib import Path

APP_NAME = "ytb2bili"


def _base_dirs() -> tuple[Path, Path]:
    """返回 (data_dir, config_dir)，遵循各平台惯例。"""
    home = Path.home()
    system = platform.system()
    if system == "Windows":
        base = Path(os.environ.get("LOCALAPPDATA", home / "AppData" / "Local"))
        data = base / APP_NAME
        config = data
    elif system == "Darwin":
        data = home / "Library" / "Application Support" / APP_NAME
        config = data
    else:  # Linux / *nix
        data = Path(os.environ.get("XDG_DATA_HOME", home / ".local" / "share")) / APP_NAME
        config = Path(os.environ.get("XDG_CONFIG_HOME", home / ".config")) / APP_NAME
    return data, config


DATA_DIR, CONFIG_DIR = _base_dirs()
BIN_DIR = DATA_DIR / "bin"
CONFIG_FILE = CONFIG_DIR / "config.json"


def ensure_dirs() -> None:
    for d in (DATA_DIR, CONFIG_DIR, BIN_DIR):
        d.mkdir(parents=True, exist_ok=True)


@dataclass
class Config:
    """用户可持久化的默认设置。存于 config.json。"""

    cookies_file: str = str(DATA_DIR / "cookies.json")
    download_dir: str = str(Path.home() / "ytb2bili-downloads")
    default_tid: int = 17  # 游戏 → 单机游戏
    default_copyright: int = 2  # 1=自制 2=转载
    cookies_from_browser: str = ""  # 例如 "chrome"、"edge"、"firefox"、"safari"
    youtube_cookies: str = ""  # YouTube cookie 文件(Netscape 格式)，优先于浏览器读取，避免钥匙串弹窗
    quality: int = 1080
    proxy: str = ""  # yt-dlp 下载用的代理，如 http://127.0.0.1:10808（B 站投稿始终直连）
    biliup_line: str = ""  # 空=自动测速选线；可选 bda2/ws/qn/bldsa/tx/txa/bda/alia
    extra: dict = field(default_factory=dict)

    @classmethod
    def load(cls) -> "Config":
        cfg = cls()
        if CONFIG_FILE.exists():
            try:
                data = json.loads(CONFIG_FILE.read_text(encoding="utf-8"))
                for k, v in data.items():
                    if hasattr(cfg, k):
                        setattr(cfg, k, v)
                    else:
                        cfg.extra[k] = v
            except (json.JSONDecodeError, OSError):
                pass
        return cfg

    def save(self) -> None:
        ensure_dirs()
        payload = {
            "cookies_file": self.cookies_file,
            "download_dir": self.download_dir,
            "default_tid": self.default_tid,
            "default_copyright": self.default_copyright,
            "cookies_from_browser": self.cookies_from_browser,
            "youtube_cookies": self.youtube_cookies,
            "quality": self.quality,
            "proxy": self.proxy,
            "biliup_line": self.biliup_line,
            **self.extra,
        }
        CONFIG_FILE.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
        )

    def as_dict(self) -> dict:
        return {
            "cookies_file": self.cookies_file,
            "download_dir": self.download_dir,
            "default_tid": self.default_tid,
            "default_copyright": self.default_copyright,
            "cookies_from_browser": self.cookies_from_browser,
            "youtube_cookies": self.youtube_cookies,
            "quality": self.quality,
            "proxy": self.proxy,
            "biliup_line": self.biliup_line,
            "config_file": str(CONFIG_FILE),
            "data_dir": str(DATA_DIR),
        }


def platform_tag() -> str:
    return f"{platform.system().lower()}/{platform.machine().lower()}/py{sys.version_info.major}.{sys.version_info.minor}"
