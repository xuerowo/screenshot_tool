"""
config.py
=========
設定持久化 (JSON)。存放：多個定點區域、擷取參數、輸出目錄等。

座標與尺寸皆為「物理像素」。
"""
from __future__ import annotations

import json
import os
from dataclasses import dataclass, asdict, field
from typing import List, Optional

CONFIG_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "config.json")

DEFAULT_OUTPUT_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "screenshots")

# 預設檔名範本（與舊版固定檔名相同）；代號說明見 cap_engine.FILENAME_TOKENS
DEFAULT_FILENAME_TEMPLATE = "{前綴}_{名稱}_{日期}_{時間}_{毫秒}_{輪次}"


@dataclass
class Region:
    """選取區域，物理像素。左/上/寬/高 + 名稱（用於檔名）。"""
    left: int = 0
    top: int = 0
    width: int = 0
    height: int = 0
    name: str = ""

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

    @classmethod
    def from_dict(cls, r: dict) -> "Region":
        return cls(
            int(r.get("left", 0)),
            int(r.get("top", 0)),
            int(r.get("width", 0)),
            int(r.get("height", 0)),
            str(r.get("name", "")),
        )


def default_region_name(index: int) -> str:
    """第 index 個（0 起算）定點的預設名稱：P1、P2…"""
    return f"P{index + 1}"


@dataclass
class Config:
    # 多個定點區域；每次擷取會依序抓取所有有效區域
    regions: List[Region] = field(default_factory=list)
    # 擷取模式："interval" (秒)、"fps" (每秒幀數) 或 "page" (翻頁連拍)
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
    # 檔名範本（不含副檔名），可用 / 建立子資料夾
    filename_template: str = DEFAULT_FILENAME_TEMPLATE
    snap_enabled: bool = True
    snap_distance: int = 8
    # 翻頁連拍：先拍當前頁，之後每輪「按 page_key → 等 page_wait 秒 → 拍所有定點」
    page_key: str = "left"            # 見 winapi.PAGE_KEYS
    page_wait: float = 1.0
    page_countdown: float = 3.0       # 開始前倒數（讓使用者點回要翻頁的視窗）
    # 迷你工具列上次的位置 [x, y]（邏輯像素）；空 = 預設位置
    mini_pos: List[int] = field(default_factory=list)

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
            if isinstance(data.get("regions"), list):
                cfg.regions = [
                    Region.from_dict(r) for r in data["regions"] if isinstance(r, dict)
                ]
            elif isinstance(data.get("region"), dict):
                # 舊版單一區域設定 → 轉成清單
                cfg.regions = [Region.from_dict(data["region"])]
            for i, r in enumerate(cfg.regions):
                if not r.name:
                    r.name = default_region_name(i)
            for key in (
                "capture_mode", "interval_seconds", "fps", "max_frames",
                "max_seconds", "output_dir", "file_format", "jpeg_quality",
                "file_prefix", "filename_template", "snap_enabled", "snap_distance", "mini_pos",
                "page_key", "page_wait", "page_countdown",
            ):
                if key in data:
                    setattr(cfg, key, data[key])
        return cfg
