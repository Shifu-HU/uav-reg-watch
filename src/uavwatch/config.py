"""配置加载与路径解析。"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

PKG_DIR = Path(__file__).resolve().parent


def _detect_root() -> Path:
    """解析项目根目录，兼容源码运行与 PyInstaller 打包成 exe 运行。

    - 源码运行:  src/uavwatch/config.py -> 上溯三级 = 项目根
    - exe 运行:  内置资源在 sys._MEIPASS(临时解包目录)，但配置和数据必须
                 放在 exe 同目录，否则用户改的设置和抓到的数据重启就丢。
    """
    import sys
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return PKG_DIR.parent.parent


PROJECT_ROOT = _detect_root()


def bundled_root() -> Path:
    """只读内置资源目录(PyInstaller 解包目录)，非打包时为项目根。"""
    import sys
    meipass = getattr(sys, "_MEIPASS", None)
    return Path(meipass) if meipass else PROJECT_ROOT


def _resolve(path_str: str) -> Path:
    """把配置里的相对路径解析为绝对路径。

    可写路径(data/) 相对 exe 同目录；只读资源相对内置目录。
    """
    p = Path(path_str)
    if p.is_absolute():
        return p
    if p.parts and p.parts[0] == "data":
        return PROJECT_ROOT / p
    bundled = bundled_root() / p
    return bundled if bundled.exists() else (PROJECT_ROOT / p)


@dataclass
class LocationConfig:
    city: str = ""
    province: str = ""
    # 区县 —— 设置页的地区选择器有第三级，_save_settings 会写进
    # config；没有这个字段 load_config 会直接 TypeError 炸掉。
    district: str = ""
    keep_national: bool = True
    also_keep: list[str] = field(default_factory=list)


@dataclass
class LLMConfig:
    provider: str = "ollama"
    host: str = "http://127.0.0.1:11434"
    model: str = "qwen3.5:0.8b"
    fallback_models: list[str] = field(default_factory=list)
    concurrency: int = 2
    timeout: int = 90
    think: bool = False
    summary_max_chars: int = 120


@dataclass
class ScheduleConfig:
    daily_time: str = "07:30"
    min_daily_items: int = 200
    max_items_per_run: int = 600
    catchup_after_hours: int = 20
    request_delay: float = 0.6


@dataclass
class DedupConfig:
    title_similarity: float = 0.86
    content_hash_chars: int = 2000


@dataclass
class NotifyConfig:
    desktop: bool = True
    brief_dir: str = "data/briefs"
    brief_format: str = "both"


@dataclass
class Config:
    raw: dict[str, Any]
    path: Path

    # --- 路径 ---
    @property
    def data_dir(self) -> Path:
        d = _resolve(self.raw.get("app", {}).get("data_dir", "data"))
        d.mkdir(parents=True, exist_ok=True)
        return d

    @property
    def db_path(self) -> Path:
        return self.data_dir / "uavwatch.sqlite3"

    @property
    def state_path(self) -> Path:
        return self.data_dir / "state.json"

    @property
    def log_dir(self) -> Path:
        d = self.data_dir / "logs"
        d.mkdir(parents=True, exist_ok=True)
        return d

    # --- 分节 ---
    @property
    def location(self) -> LocationConfig:
        return LocationConfig(**{**LocationConfig().__dict__, **self.raw.get("location", {})})

    @property
    def llm(self) -> LLMConfig:
        return LLMConfig(**{**LLMConfig().__dict__, **self.raw.get("llm", {})})

    @property
    def schedule(self) -> ScheduleConfig:
        return ScheduleConfig(**{**ScheduleConfig().__dict__, **self.raw.get("schedule", {})})

    @property
    def dedup(self) -> DedupConfig:
        return DedupConfig(**{**DedupConfig().__dict__, **self.raw.get("dedup", {})})

    @property
    def notify(self) -> NotifyConfig:
        return NotifyConfig(**{**NotifyConfig().__dict__, **self.raw.get("notify", {})})

    @property
    def sources(self) -> dict[str, Any]:
        return self.raw.get("sources", {})

    @property
    def crawler(self) -> dict[str, Any]:
        """抓取策略 —— 第三方来源配额、核验开关等。"""
        return self.raw.get("crawler", {})

    @property
    def devices(self) -> list[dict[str, Any]]:
        """用户登记的无人机列表。空列表表示不过滤。"""
        d = self.raw.get("devices")
        return d if isinstance(d, list) else []

    def set_devices(self, devices: list[dict[str, Any]]) -> None:
        """保存设备列表并落盘。"""
        self.raw["devices"] = devices
        self.save()

    def save(self) -> None:
        self.path.write_text(
            yaml.safe_dump(self.raw, allow_unicode=True, sort_keys=False),
            encoding="utf-8",
        )

    def update(self, section: str, **kwargs: Any) -> None:
        """更新配置中的某一节并落盘。"""
        self.raw.setdefault(section, {}).update(kwargs)
        self.save()


DEFAULT_CONFIG_NAME = "config.yaml"


def load_config(path: str | os.PathLike[str] | None = None) -> Config:
    """加载配置。

    查找顺序(exe 运行时尤其重要):
      1. 显式传入的路径
      2. exe 同目录 / 项目根目录的 config.yaml  <- 用户可编辑
      3. 打包内置的 config.yaml                  <- 首次运行时复制出来
    """
    if path:
        cfg_path = Path(path)
    else:
        cfg_path = PROJECT_ROOT / DEFAULT_CONFIG_NAME
        if not cfg_path.exists():
            bundled = bundled_root() / DEFAULT_CONFIG_NAME
            if bundled.exists() and bundled != cfg_path:
                # 首次运行: 把内置配置复制到 exe 同目录，之后用户可自行修改
                try:
                    cfg_path.parent.mkdir(parents=True, exist_ok=True)
                    cfg_path.write_text(bundled.read_text(encoding="utf-8"),
                                        encoding="utf-8")
                except Exception:            # noqa: BLE001
                    cfg_path = bundled
    if not cfg_path.exists():
        raise FileNotFoundError(f"配置文件不存在: {cfg_path}")
    raw = yaml.safe_load(cfg_path.read_text(encoding="utf-8")) or {}
    return Config(raw=raw, path=cfg_path)
