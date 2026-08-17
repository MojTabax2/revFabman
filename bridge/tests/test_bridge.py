from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest
import yaml

import bridge as bridge_module
from core import cache
from core import config as configlib
from core.cache import AccessEntry
from core.state_machine import State
from hardware import button as hw_button
from hardware import reader as hw_reader

ALICE = "aaaaaaaa-0000-0000-0000-000000000001"
BOB = "bbbbbbbb-0000-0000-0000-000000000002"
ALICE_UID = "043f6afba2"
BOB_UID = "043f6b3ee2"


def _write_config(tmp_path, cache_path):
    cfg = {
        "station": {"machine_name": "laser-1", "bridge_name": "bridge-laser-1"},
        "hardware": {
            "i2c_bus": 1,
            "reader": {"type": "simulate"},
            "display": {"type": "simulate"},
            "button": {"type": "simulate"},
            "buzzer": {"type": "simulate"},
            "relay": {"type": "simulate"},
        },
        # No supabase creds -> cache-only mode, which is what we want in tests.
        "supabase": {"env_file": str(tmp_path / "no-such-env")},
        "storage": {"cache_path": str(cache_path)},
    }
    path = tmp_path / "config.yaml"
    path.write_text(yaml.safe_dump(cfg))
    return path


@pytest.fixture
def setup(monkeypatch, tmp_path):
    """A bridge in full-simulate mode with Alice cached as authorized."""
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("SUPABASE_URL", raising=False)
    monkeypatch.delenv("SUPABASE_SERVICE_KEY", raising=False)

    cache_path = tmp_path / "cache.db"
    cache.init(cache_path)
    cache.upsert_machine(
        cache_path,
        cache.MachineSettings(
            id="m-1",
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
        ),
    )
    cache.replace_access(cache_path, [AccessEntry(ALICE_UID, ALICE, "Alice")])

    config = configlib.load(str(_write_config(tmp_path, cache_path)))
    return config, cache_path


def _tap(uid: str):
    hw_reader.SIM_INPUT_FILE.parent.mkdir(parents=True, exist_ok=True)
    hw_reader.SIM_INPUT_FILE.write_text(f"{uid}\n")


def _press():
    hw_button.SIM_INPUT_FILE.parent.mkdir(parents=True, exist_ok=True)
    hw_button.SIM_INPUT_FILE.write_text("press\n")


# ---------------------------------------------------------------------------
# Construction
# ---------------------------------------------------------------------------

def test_bridge_starts_idle_with_cached_settings(setup):
    config, _ = setup
    b = bridge_module.Bridge(config)
    try:
        assert b.state == State.IDLE
        # Pulled the display name out of the cached machine row.
        assert b.sm_config.station_display_name == "Laser Cutter 1"
        assert b.sm_config.session_timeout_s == 900
    finally:
        b.close()


def test_bridge_uses_defaults_when_machine_not_cached(monkeypatch, tmp_path):
    """First boot before any sync: still runs, just with defaults."""
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("SUPABASE_URL", raising=False)
    monkeypatch.delenv("SUPABASE_SERVICE_KEY", raising=False)
    cache_path = tmp_path / "cache.db"
    cache.init(cache_path)
    config = configlib.load(str(_write_config(tmp_path, cache_path)))
    b = bridge_module.Bridge(config)
    try:
        assert b.state == State.IDLE
        assert b.sm_config.station_display_name == "laser-1"
        assert b.sm_config.session_timeout_s == 900
    finally:
        b.close()


# ---------------------------------------------------------------------------
# Full happy path
# ---------------------------------------------------------------------------

def test_full_flow_button_tap_run_stop(setup):
    config, cache_path = setup
    b = bridge_module.Bridge(config)
    try:
        _press()
        b.step()
        assert b.state == State.READY

        _tap(ALICE_UID)
        b.step()
        assert b.state == State.RUNNING
        assert b.current_session_id is not None
        assert len(cache.list_active_sessions(cache_path)) == 1

        _press()
        b.step()
        assert b.state == State.IDLE
        assert b.current_session_id is None
        assert cache.list_active_sessions(cache_path) == []

        queued = cache.list_unsynced_sessions(cache_path)
        assert len(queued) == 1
        assert queued[0].member_id == ALICE
        assert queued[0].member_name == "Alice"
    finally:
        b.close()


def test_authorization_uses_local_cache_only(setup):
    """No network configured at all, yet access still works."""
    config, _ = setup
    assert not config.supabase.configured
    b = bridge_module.Bridge(config)
    try:
        _press()
        b.step()
        _tap(ALICE_UID)
        b.step()
        assert b.state == State.RUNNING
    finally:
        b.close()


def test_unknown_card_is_denied(setup):
    config, cache_path = setup
    b = bridge_module.Bridge(config)
    try:
        _press()
        b.step()
        _tap("cafebabe00")
        b.step()
        assert b.state == State.DENIED
        assert cache.list_active_sessions(cache_path) == []
    finally:
        b.close()


def test_revoked_member_is_denied_after_cache_update(setup):
    """Revoking in Supabase lands as an access-list swap; next tap is denied."""
    config, cache_path = setup
    b = bridge_module.Bridge(config)
    try:
        cache.replace_access(cache_path, [])  # simulates a pull after revocation
        _press()
        b.step()
        _tap(ALICE_UID)
        b.step()
        assert b.state == State.DENIED
    finally:
        b.close()


# ---------------------------------------------------------------------------
# Reader gating / stray reads
# ---------------------------------------------------------------------------

def test_tap_in_idle_is_discarded_and_does_not_leak(setup):
    """A stray read while IDLE must not survive to affect the next READY."""
    config, cache_path = setup
    b = bridge_module.Bridge(config)
    try:
        _tap("cafebabe00")   # unknown card tapped at idle
        b.step()
        assert b.state == State.IDLE
        assert cache.list_active_sessions(cache_path) == []

        # Button now; the earlier read must not produce a DENIED.
        _press()
        b.step()
        assert b.state == State.READY
        b.step()
        assert b.state == State.READY

        _tap(ALICE_UID)
        b.step()
        assert b.state == State.RUNNING
    finally:
        b.close()


def test_ready_timeout_returns_to_idle(setup):
    config, _ = setup
    b = bridge_module.Bridge(config)
    try:
        t0 = datetime.now(timezone.utc)
        _press()
        b.step(now=t0)
        assert b.state == State.READY
        b.step(now=t0 + timedelta(seconds=30))
        assert b.state == State.IDLE
    finally:
        b.close()


# ---------------------------------------------------------------------------
# Extension + auto-stop
# ---------------------------------------------------------------------------

def test_active_member_retap_extends_session(setup):
    config, _ = setup
    b = bridge_module.Bridge(config)
    try:
        t0 = datetime.now(timezone.utc)
        _press()
        b.step(now=t0)
        _tap(ALICE_UID)
        b.step(now=t0)
        assert b.state == State.RUNNING

        _tap(ALICE_UID)
        b.step(now=t0 + timedelta(seconds=60))
        assert b.state == State.RUNNING
        assert b.state_data.session_extensions_s == 900
    finally:
        b.close()


def test_other_member_retap_does_not_extend(setup):
    config, cache_path = setup
    cache.replace_access(
        cache_path,
        [AccessEntry(ALICE_UID, ALICE, "Alice"), AccessEntry(BOB_UID, BOB, "Bob")],
    )
    b = bridge_module.Bridge(config)
    try:
        t0 = datetime.now(timezone.utc)
        _press()
        b.step(now=t0)
        _tap(ALICE_UID)
        b.step(now=t0)
        assert b.state == State.RUNNING

        _tap(BOB_UID)
        b.step(now=t0 + timedelta(seconds=60))
        assert b.state == State.RUNNING
        assert b.state_data.session_extensions_s == 0
    finally:
        b.close()


def test_auto_stop_records_session(setup):
    config, cache_path = setup
    b = bridge_module.Bridge(config)
    try:
        t0 = datetime.now(timezone.utc)
        _press()
        b.step(now=t0)
        _tap(ALICE_UID)
        b.step(now=t0)
        b.step(now=t0 + timedelta(seconds=900))
        assert b.state == State.IDLE
        s = cache.list_unsynced_sessions(cache_path)[0]
        assert s.auto_stopped is True
        assert s.duration_seconds >= 900
    finally:
        b.close()


def test_extended_session_records_extension_seconds(setup):
    config, cache_path = setup
    b = bridge_module.Bridge(config)
    try:
        t0 = datetime.now(timezone.utc)
        _press()
        b.step(now=t0)
        _tap(ALICE_UID)
        b.step(now=t0)
        _tap(ALICE_UID)
        b.step(now=t0 + timedelta(seconds=60))
        _press()
        b.step(now=t0 + timedelta(seconds=120))
        assert b.state == State.IDLE
        s = cache.list_unsynced_sessions(cache_path)[0]
        assert s.extensions_seconds == 900
    finally:
        b.close()


# ---------------------------------------------------------------------------
# Enrollment
# ---------------------------------------------------------------------------

def test_enrollment_captures_tapped_uid(setup):
    config, cache_path = setup
    now = datetime.now(timezone.utc)
    cache.set_pending_enrollment(
        cache_path, "e1", "Newbie", now + timedelta(seconds=60)
    )

    b = bridge_module.Bridge(config)
    try:
        b.step()
        assert b.state == State.ENROLLING

        _tap("0499887766")
        b.step()
        assert b.state == State.IDLE

        captured = cache.list_captured_unpushed(cache_path)
        assert len(captured) == 1
        assert captured[0].captured_uid == "0499887766"
        assert captured[0].member_name == "Newbie"
    finally:
        b.close()


def test_stale_read_does_not_hijack_enrollment(setup):
    """Regression from v1: a read in flight when ENROLLING begins must be
    dropped, not captured as the member's badge."""
    config, cache_path = setup
    now = datetime.now(timezone.utc)
    _tap("deadbeef00")  # stale read sitting in the reader
    cache.set_pending_enrollment(
        cache_path, "e1", "Newbie", now + timedelta(seconds=60)
    )

    b = bridge_module.Bridge(config)
    try:
        b.step()
        assert b.state == State.ENROLLING
        assert cache.list_captured_unpushed(cache_path) == []

        _tap("0499887766")
        b.step()
        captured = cache.list_captured_unpushed(cache_path)
        assert len(captured) == 1
        assert captured[0].captured_uid == "0499887766"
    finally:
        b.close()


def test_enrollment_expiry_returns_to_idle(setup):
    config, cache_path = setup
    t0 = datetime.now(timezone.utc)
    cache.set_pending_enrollment(cache_path, "e1", "Newbie", t0 + timedelta(seconds=60))

    b = bridge_module.Bridge(config)
    try:
        b.step(now=t0)
        assert b.state == State.ENROLLING
        b.step(now=t0 + timedelta(seconds=61))
        assert b.state == State.IDLE
        assert cache.list_captured_unpushed(cache_path) == []
    finally:
        b.close()


# ---------------------------------------------------------------------------
# Recovery + settings refresh
# ---------------------------------------------------------------------------

def test_orphan_session_closed_on_startup(setup):
    """A power cut mid-session shouldn't leave a session open forever."""
    config, cache_path = setup
    past = datetime.now(timezone.utc) - timedelta(minutes=10)
    cache.start_session(cache_path, ALICE, "Alice", past)

    b = bridge_module.Bridge(config)
    try:
        assert cache.list_active_sessions(cache_path) == []
        queued = cache.list_unsynced_sessions(cache_path)
        assert len(queued) == 1
        assert queued[0].auto_stopped is True
    finally:
        b.close()


def test_refresh_settings_applies_while_idle(setup):
    config, cache_path = setup
    b = bridge_module.Bridge(config)
    try:
        m = cache.get_machine(cache_path)
        m.session_seconds = 1800
        m.display_name = "Laser Cutter 1 (updated)"
        cache.upsert_machine(cache_path, m)

        b.refresh_settings()
        assert b.sm_config.session_timeout_s == 1800
        assert b.sm_config.station_display_name == "Laser Cutter 1 (updated)"
    finally:
        b.close()


def test_refresh_settings_deferred_while_running(setup):
    """Changing the rules mid-session would move the goalposts under the user."""
    config, cache_path = setup
    b = bridge_module.Bridge(config)
    try:
        _press()
        b.step()
        _tap(ALICE_UID)
        b.step()
        assert b.state == State.RUNNING

        m = cache.get_machine(cache_path)
        m.session_seconds = 60
        cache.upsert_machine(cache_path, m)
        b.refresh_settings()
        assert b.sm_config.session_timeout_s == 900  # unchanged mid-session

        _press()
        b.step()
        assert b.state == State.IDLE
        b.refresh_settings()
        assert b.sm_config.session_timeout_s == 60   # applied once idle
    finally:
        b.close()
