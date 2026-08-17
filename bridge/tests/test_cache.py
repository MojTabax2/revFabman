from __future__ import annotations

from datetime import timedelta

import pytest

from core import cache
from core.cache import AccessEntry


# ---------------------------------------------------------------------------
# Schema / access list
# ---------------------------------------------------------------------------

def test_init_is_idempotent(db_path):
    cache.init(db_path)
    cache.init(db_path)


def test_replace_access_and_lookup(db_path):
    cache.replace_access(
        db_path,
        [
            AccessEntry("043f6afba2", "m1", "Alice"),
            AccessEntry("043f6b3ee2", "m2", "Bob"),
        ],
    )
    hit = cache.lookup_access(db_path, "043f6afba2")
    assert hit is not None
    assert hit.member_name == "Alice"
    assert cache.lookup_access(db_path, "043f6b3ee2").member_id == "m2"
    assert cache.lookup_access(db_path, "unknown") is None
    assert cache.count_access(db_path) == 2


def test_replace_access_swaps_wholesale(db_path):
    """A revoked member must disappear after the next successful pull."""
    cache.replace_access(
        db_path, [AccessEntry("A", "m1", "Alice"), AccessEntry("B", "m2", "Bob")]
    )
    cache.replace_access(db_path, [AccessEntry("A", "m1", "Alice")])
    assert cache.lookup_access(db_path, "A") is not None
    assert cache.lookup_access(db_path, "B") is None
    assert cache.count_access(db_path) == 1


def test_replace_access_with_empty_list_clears(db_path):
    cache.replace_access(db_path, [AccessEntry("A", "m1", "Alice")])
    cache.replace_access(db_path, [])
    assert cache.count_access(db_path) == 0


# ---------------------------------------------------------------------------
# Machine settings
# ---------------------------------------------------------------------------

def test_machine_roundtrip(db_path, machine_settings):
    assert cache.get_machine(db_path) is None
    cache.upsert_machine(db_path, machine_settings)
    got = cache.get_machine(db_path)
    assert got.name == "laser-1"
    assert got.display_name == "Laser Cutter 1"
    assert got.session_seconds == 900
    assert got.enabled is True


def test_machine_upsert_replaces(db_path, machine_settings):
    cache.upsert_machine(db_path, machine_settings)
    machine_settings.session_seconds = 1800
    machine_settings.display_name = "Laser Cutter 1 (renamed)"
    cache.upsert_machine(db_path, machine_settings)
    got = cache.get_machine(db_path)
    assert got.session_seconds == 1800
    assert got.display_name == "Laser Cutter 1 (renamed)"


# ---------------------------------------------------------------------------
# Enrollment
# ---------------------------------------------------------------------------

def test_pending_enrollment_roundtrip(db_path, now):
    cache.set_pending_enrollment(db_path, "e1", "Newbie", now + timedelta(seconds=60))
    active = cache.get_active_enrollment(db_path, now)
    assert active is not None
    assert active.member_name == "Newbie"
    assert active.captured_uid is None


def test_pending_enrollment_respects_expiry(db_path, now):
    cache.set_pending_enrollment(db_path, "e1", "Newbie", now + timedelta(seconds=60))
    assert cache.get_active_enrollment(db_path, now) is not None
    assert cache.get_active_enrollment(db_path, now + timedelta(seconds=120)) is None


def test_new_pending_supersedes_previous(db_path, now):
    cache.set_pending_enrollment(db_path, "e1", "Alice", now + timedelta(seconds=60))
    cache.set_pending_enrollment(db_path, "e2", "Bob", now + timedelta(seconds=60))
    active = cache.get_active_enrollment(db_path, now)
    assert active.id == "e2"
    assert active.member_name == "Bob"


def test_capture_enrollment_marks_unpushed(db_path, now):
    cache.set_pending_enrollment(db_path, "e1", "Newbie", now + timedelta(seconds=60))
    cache.capture_enrollment(db_path, "e1", "043f6afba2")
    # No longer "active" (it has a uid now)
    assert cache.get_active_enrollment(db_path, now) is None
    unpushed = cache.list_captured_unpushed(db_path)
    assert len(unpushed) == 1
    assert unpushed[0].captured_uid == "043f6afba2"
    assert unpushed[0].pushed is False


def test_mark_enrollment_pushed(db_path, now):
    cache.set_pending_enrollment(db_path, "e1", "Newbie", now + timedelta(seconds=60))
    cache.capture_enrollment(db_path, "e1", "AAA")
    cache.mark_enrollment_pushed(db_path, "e1")
    assert cache.list_captured_unpushed(db_path) == []


def test_clear_stale_enrollments(db_path, now):
    cache.set_pending_enrollment(db_path, "e1", "Newbie", now + timedelta(seconds=10))
    removed = cache.clear_stale_enrollments(db_path, now + timedelta(seconds=60))
    assert removed == 1
    assert cache.get_active_enrollment(db_path, now + timedelta(seconds=60)) is None


def test_clear_stale_keeps_captured_but_unpushed(db_path, now):
    """An expired-but-captured enrollment must survive so it can still push."""
    cache.set_pending_enrollment(db_path, "e1", "Newbie", now + timedelta(seconds=10))
    cache.capture_enrollment(db_path, "e1", "AAA")
    cache.clear_stale_enrollments(db_path, now + timedelta(seconds=60))
    assert len(cache.list_captured_unpushed(db_path)) == 1


# ---------------------------------------------------------------------------
# Sessions / outbox
# ---------------------------------------------------------------------------

def test_session_lifecycle(db_path, now):
    sid = cache.start_session(db_path, "m1", "Alice", now)
    assert sid  # client-generated uuid
    assert len(cache.list_active_sessions(db_path)) == 1

    duration = cache.end_session(db_path, sid, now + timedelta(seconds=125))
    assert duration == 125
    assert cache.list_active_sessions(db_path) == []

    unsynced = cache.list_unsynced_sessions(db_path)
    assert len(unsynced) == 1
    assert unsynced[0].id == sid
    assert unsynced[0].duration_seconds == 125
    assert unsynced[0].member_name == "Alice"


def test_session_ids_are_unique(db_path, now):
    a = cache.start_session(db_path, "m1", "Alice", now)
    cache.end_session(db_path, a, now)
    b = cache.start_session(db_path, "m1", "Alice", now)
    assert a != b


def test_end_session_records_extensions_and_auto_stop(db_path, now):
    sid = cache.start_session(db_path, "m1", "Alice", now)
    cache.end_session(
        db_path,
        sid,
        now + timedelta(minutes=30),
        auto_stopped=True,
        extensions_seconds=900,
    )
    s = cache.list_unsynced_sessions(db_path)[0]
    assert s.auto_stopped is True
    assert s.extensions_seconds == 900
    assert s.duration_seconds == 1800


def test_end_unknown_session_raises(db_path, now):
    with pytest.raises(ValueError):
        cache.end_session(db_path, "nope", now)


def test_active_session_not_in_unsynced(db_path, now):
    """A still-running session must not be pushed."""
    cache.start_session(db_path, "m1", "Alice", now)
    assert cache.list_unsynced_sessions(db_path) == []


def test_mark_session_synced_and_failed(db_path, now):
    sid = cache.start_session(db_path, "m1", "Alice", now)
    cache.end_session(db_path, sid, now + timedelta(seconds=60))

    cache.mark_session_failed(db_path, sid, "network down")
    cache.mark_session_failed(db_path, sid, "network down")
    assert cache.list_unsynced_sessions(db_path)[0].attempts == 2

    cache.mark_session_synced(db_path, sid)
    assert cache.list_unsynced_sessions(db_path) == []


def test_prune_synced_sessions_keeps_recent(db_path, now):
    for i in range(10):
        sid = cache.start_session(db_path, "m1", "Alice", now + timedelta(minutes=i))
        cache.end_session(db_path, sid, now + timedelta(minutes=i, seconds=30))
        cache.mark_session_synced(db_path, sid)
    removed = cache.prune_synced_sessions(db_path, keep=3)
    assert removed == 7


def test_prune_never_removes_unsynced(db_path, now):
    for i in range(5):
        sid = cache.start_session(db_path, "m1", "Alice", now + timedelta(minutes=i))
        cache.end_session(db_path, sid, now + timedelta(minutes=i, seconds=30))
    removed = cache.prune_synced_sessions(db_path, keep=1)
    assert removed == 0
    assert len(cache.list_unsynced_sessions(db_path)) == 5


# ---------------------------------------------------------------------------
# Meta + datetime helpers
# ---------------------------------------------------------------------------

def test_meta_roundtrip(db_path):
    assert cache.get_meta(db_path, "last_sync_at") is None
    cache.set_meta(db_path, "last_sync_at", "2026-03-10T15:00:00+00:00")
    assert cache.get_meta(db_path, "last_sync_at") == "2026-03-10T15:00:00+00:00"
    cache.set_meta(db_path, "last_sync_at", "later")
    assert cache.get_meta(db_path, "last_sync_at") == "later"


def test_parse_dt_handles_z_suffix():
    dt = cache.parse_dt("2026-03-10T15:00:00Z")
    assert dt.tzinfo is not None
    assert dt.hour == 15


def test_parse_dt_handles_offset_and_microseconds():
    dt = cache.parse_dt("2026-03-10T15:00:00.123456+00:00")
    assert dt.year == 2026


def test_fmt_dt_assumes_utc_for_naive():
    from datetime import datetime as _dt

    s = cache.fmt_dt(_dt(2026, 3, 10, 15, 0, 0))
    assert s.endswith("+00:00")
