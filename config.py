"""
config.py
=========
設定持久化 (JSON)。存放：選取區域、擷取參數、輸出目錄等。

座標與尺寸皆為「物理像素」。
"""
from __future__ import annotations

import json
import os
from dataclasses import dataclass, asdict, field
from typing import Optional

CONFIG_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "config.json")

DEFAULT_OUTPUT_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "screenshots")


@dataclass
class Region:
    """選取區域，物理像素。左/上/寬/高。"""
    left: int = 0
    top: int = 0
    width: int = 0
    height: int = 0

    @property
    def right(self) -> int:
        return self.left + self.width

    @property
    def bottom(self) -> int:
        return self.top + self.height

    def is_valid(self) -> bool:
        return self.width > 0 and self.height > 0

    def __iter__(self):
        return iter((self.left, self.top, self.width, self.height))


@dataclass
class Config:
    region: Region = field(default_factory=Region)
    # 擷取模式："interval" (秒) 或 "fps" (每秒幀數)
    capture_mode: str = "interval"
    interval_seconds: float = 5.0
    fps: int = 10
    # 0 = 無限（直到手動停止）；>0 則為張數上限 / 或秒數上限
    max_frames: int = 0
    max_seconds: float = 0.0
    output_dir: str = DEFAULT_OUTPUT_DIR
    file_format: str = "png"          # png / jpg
    jpeg_quality: int = 90
    file_prefix: str = "capture"
    snap_enabled: bool = True
    snap_distance: int = 8

    def save(self, path: str = CONFIG_PATH) -> None:
        data = asdict(self)
        try:
            with open(path, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
        except OSError:
            pass

    @classmethod
    def load(cls, path: str = CONFIG_PATH) -> "Config":
        cfg = cls()
        try:
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
        except (OSError, ValueError):
            data = {}
        if isinstance(data, dict):
            if isinstance(data.get("region"), dict):
                r = data["region"]
                cfg.region = Region(
                    int(r.get("left", 0)),
                    int(r.get("top", 0)),
                    int(r.get("width", 0)),
                    int(r.get("height", 0)),
                )
            for key in (
                "capture_mode", "interval_seconds", "fps", "max_frames",
                "max_seconds", "output_dir", "file_format", "jpeg_quality",
                "file_prefix", "snap_enabled", "snap_distance",
            ):
                if key in data:
                    setattr(cfg, key, data[key])
        return cfg
