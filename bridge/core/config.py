"""Bridge config loader.

config.yaml holds only what's local to this box: identity, hardware wiring,
and how to reach Supabase. Behavioral tunables (timeouts, extension rules)
live in Supabase on the machines row so they can be changed centrally.

Supabase credentials come from the environment or secrets/env, never the YAML.
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional, Union

import yaml

PathLike = Union[str, Path]


@dataclass
class StationConfig:
    machine_name: str          # must match machines.name in Supabase
    bridge_name: str           # must be unique per Pi


@dataclass
class ReaderConfig:
    type: str = "usb_keyboard"       # usb_keyboard | simulate
    device_name: str = "OMNIKEY"     # evdev name substring
    gap_seconds: float = 0.15        # keystroke-burst framing gap
    dedup_window_seconds: float = 2.0


@dataclass
class DisplayConfig:
    type: str = "ssd1306"            # ssd1306 | simulate
    i2c_address: int = 0x3C
    width: int = 128
    height: int = 32


@dataclass
class ButtonConfig:
    type: str = "gpio"               # gpio | simulate
    gpio_pin: int = 24
    pull: str = "up"
    led_pin: Optional[int] = 16


@dataclass
class BuzzerConfig:
    type: str = "gpio"
    gpio_pin: int = 13


@dataclass
class RelayConfig:
    type: str = "gpio"
    gpio_pin: int = 26
    active_high: bool = True


@dataclass
class HardwareConfig:
    i2c_bus: int = 1
    reader: ReaderConfig = field(default_factory=ReaderConfig)
    display: DisplayConfig = field(default_factory=DisplayConfig)
    button: ButtonConfig = field(default_factory=ButtonConfig)
    buzzer: BuzzerConfig = field(default_factory=BuzzerConfig)
    relay: RelayConfig = field(default_factory=RelayConfig)


@dataclass
class SupabaseConfig:
    url: str = ""
    service_key: str = ""
    sync_interval_seconds: int = 30
    request_timeout_seconds: int = 10

    @property
    def configured(self) -> bool:
        return bool(self.url and self.service_key)


@dataclass
class StorageConfig:
    cache_path: str = "cache.db"


@dataclass
class Config:
    station: StationConfig
    hardware: HardwareConfig = field(default_factory=HardwareConfig)
    supabase: SupabaseConfig = field(default_factory=SupabaseConfig)
    storage: StorageConfig = field(default_factory=StorageConfig)


def _load_env_file(path: Path) -> dict[str, str]:
    """Minimal KEY=VALUE parser for secrets/env (no python-dotenv needed)."""
    values: dict[str, str] = {}
    if not path.exists():
        return values
    for line in path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        values[key.strip()] = value.strip().strip('"').strip("'")
    return values


def load(path: PathLike = "config.yaml") -> Config:
    raw = yaml.safe_load(Path(path).read_text()) or {}

    station_raw = raw.get("station") or {}
    machine_name = station_raw.get("machine_name")
    if not machine_name:
        raise ValueError("config.yaml: station.machine_name is required")
    station = StationConfig(
        machine_name=machine_name,
        bridge_name=station_raw.get("bridge_name") or f"bridge-{machine_name}",
    )

    hw_raw = raw.get("hardware") or {}
    hardware = HardwareConfig(
        i2c_bus=hw_raw.get("i2c_bus", 1),
        reader=ReaderConfig(**(hw_raw.get("reader") or {})),
        display=DisplayConfig(**(hw_raw.get("display") or {})),
        button=ButtonConfig(**(hw_raw.get("button") or {})),
        buzzer=BuzzerConfig(**(hw_raw.get("buzzer") or {})),
        relay=RelayConfig(**(hw_raw.get("relay") or {})),
    )

    sb_raw = raw.get("supabase") or {}
    env_path = Path(sb_raw.get("env_file", "secrets/env"))
    env = _load_env_file(env_path)
    supabase = SupabaseConfig(
        # Real environment wins over the secrets file, so systemd/CI can override.
        url=os.environ.get("SUPABASE_URL") or env.get("SUPABASE_URL", ""),
        service_key=os.environ.get("SUPABASE_SERVICE_KEY")
        or env.get("SUPABASE_SERVICE_KEY", ""),
        sync_interval_seconds=sb_raw.get("sync_interval_seconds", 30),
        request_timeout_seconds=sb_raw.get("request_timeout_seconds", 10),
    )

    storage = StorageConfig(**(raw.get("storage") or {}))

    return Config(
        station=station, hardware=hardware, supabase=supabase, storage=storage
    )
