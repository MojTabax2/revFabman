from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

import pytest

from core import cache


@pytest.fixture
def db_path(tmp_path: Path) -> Path:
    p = tmp_path / "cache.db"
    cache.init(p)
    return p


@pytest.fixture
def now() -> datetime:
    return datetime(2026, 3, 10, 15, 0, 0, tzinfo=timezone.utc)


@pytest.fixture
def machine_settings() -> cache.MachineSettings:
    return cache.MachineSettings(
        id="11111111-1111-1111-1111-111111111111",
        name="laser-1",
        display_name="Laser Cutter 1",
        enabled=True,
        ready_seconds=30,
        session_seconds=900,
        extend_seconds=900,
        extend_cooldown_seconds=10,
        session_max_total_seconds=18000,
        warning_remaining_seconds=60,
        enrollment_seconds=60,
        denied_display_seconds=2,
    )
