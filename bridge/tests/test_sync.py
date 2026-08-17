from __future__ import annotations

from datetime import timedelta

import pytest

from core import cache
from core.cache import AccessEntry
from core.sync import SyncError, Syncer

MACHINE_ID = "11111111-1111-1111-1111-111111111111"
BRIDGE_ID = "22222222-2222-2222-2222-222222222222"


class FakeClient:
    """Stands in for Supabase/PostgREST. Records calls, serves canned rows."""

    def __init__(self):
        self.tables: dict[str, list[dict]] = {
            "machines": [_machine_row()],
            "bridges": [{"id": BRIDGE_ID, "name": "bridge-laser-1"}],
            "v_machine_access": [],
            "pending_enrollments": [],
            "members": [],
            "sessions": [],
        }
        self.upserts: list[tuple[str, list[dict]]] = []
        self.patches: list[tuple[str, dict, dict]] = []
        self.inserts: list[tuple[str, dict]] = []
        self.fail_on: set[str] = set()      # table names that raise
        self.fail_times: dict[str, int] = {}  # table -> remaining failures

    def _maybe_fail(self, table: str):
        if table in self.fail_on:
            raise RuntimeError(f"simulated network failure on {table}")
        remaining = self.fail_times.get(table, 0)
        if remaining > 0:
            self.fail_times[table] = remaining - 1
            raise RuntimeError(f"simulated transient failure on {table}")

    def select(self, table, params=None):
        self._maybe_fail(table)
        rows = self.tables.get(table, [])
        params = params or {}
        out = []
        for row in rows:
            match = True
            for key, val in params.items():
                if key in ("select", "order", "limit"):
                    continue
                if isinstance(val, str) and val.startswith("eq."):
                    want = val[3:]
                    have = row.get(key)
                    if str(have) != want:
                        match = False
                        break
                elif isinstance(val, str) and val.startswith("gt."):
                    # Coarse but fine for these tests.
                    continue
            if match:
                out.append(row)
        if "limit" in (params or {}):
            out = out[: int(params["limit"])]
        return out

    def upsert(self, table, rows, on_conflict=None):
        self._maybe_fail(table)
        self.upserts.append((table, rows))
        out = []
        for r in rows:
            row = dict(r)
            row.setdefault("id", f"generated-{table}-{len(self.tables[table])}")
            self.tables.setdefault(table, []).append(row)
            out.append(row)
        return out

    def patch(self, table, params, values):
        self._maybe_fail(table)
        self.patches.append((table, params, values))
        return [{"id": params.get("id", "").replace("eq.", ""), **values}]

    def insert(self, table, row):
        self._maybe_fail(table)
        self.inserts.append((table, row))
        created = dict(row)
        created.setdefault("id", BRIDGE_ID)
        self.tables.setdefault(table, []).append(created)
        return [created]


def _machine_row():
    return {
        "id": MACHINE_ID,
        "name": "laser-1",
        "display_name": "Laser Cutter 1",
        "enabled": True,
        "ready_seconds": 30,
        "session_seconds": 900,
        "extend_seconds": 900,
        "extend_cooldown_seconds": 10,
        "session_max_total_seconds": 18000,
        "warning_remaining_seconds": 60,
        "enrollment_seconds": 60,
        "denied_display_seconds": 2,
    }


@pytest.fixture
def client():
    return FakeClient()


@pytest.fixture
def syncer(client, db_path):
    return Syncer(
        client=client,
        db_path=db_path,
        machine_name="laser-1",
        bridge_name="bridge-laser-1",
    )


# ---------------------------------------------------------------------------
# id resolution
# ---------------------------------------------------------------------------

def test_resolve_machine_id(syncer):
    assert syncer.resolve_machine_id() == MACHINE_ID


def test_resolve_machine_id_missing_raises(client, db_path):
    client.tables["machines"] = []
    s = Syncer(client, db_path, "nope", "bridge-x")
    with pytest.raises(SyncError):
        s.resolve_machine_id()


def test_resolve_bridge_id_finds_existing(syncer, client):
    assert syncer.resolve_bridge_id() == BRIDGE_ID
    assert client.inserts == []


def test_resolve_bridge_id_registers_when_missing(client, db_path):
    client.tables["bridges"] = []
    s = Syncer(client, db_path, "laser-1", "bridge-new")
    bridge_id = s.resolve_bridge_id()
    assert bridge_id
    assert len(client.inserts) == 1
    table, row = client.inserts[0]
    assert table == "bridges"
    assert row["name"] == "bridge-new"
    assert row["machine_id"] == MACHINE_ID


# ---------------------------------------------------------------------------
# pulls
# ---------------------------------------------------------------------------

def test_pull_machine_caches_settings(syncer, db_path):
    settings = syncer.pull_machine()
    assert settings.display_name == "Laser Cutter 1"
    cached = cache.get_machine(db_path)
    assert cached is not None
    assert cached.session_seconds == 900


def test_pull_machine_picks_up_changed_tunables(syncer, client, db_path):
    client.tables["machines"][0]["session_seconds"] = 1800
    client.tables["machines"][0]["warning_remaining_seconds"] = 120
    syncer.pull_machine()
    cached = cache.get_machine(db_path)
    assert cached.session_seconds == 1800
    assert cached.warning_remaining_seconds == 120


def test_pull_access_populates_cache(syncer, client, db_path):
    client.tables["v_machine_access"] = [
        {
            "machine_id": MACHINE_ID,
            "uid": "043f6afba2",
            "member_id": "m1",
            "member_name": "Alice",
        },
        {
            "machine_id": MACHINE_ID,
            "uid": "043f6b3ee2",
            "member_id": "m2",
            "member_name": "Bob",
        },
    ]
    assert syncer.pull_access() == 2
    assert cache.lookup_access(db_path, "043f6afba2").member_name == "Alice"


def test_pull_access_skips_rows_without_uid(syncer, client, db_path):
    client.tables["v_machine_access"] = [
        {"machine_id": MACHINE_ID, "uid": None, "member_id": "m1", "member_name": "A"},
        {"machine_id": MACHINE_ID, "uid": "B", "member_id": "m2", "member_name": "B"},
    ]
    assert syncer.pull_access() == 1


def test_failed_pull_leaves_previous_access_intact(syncer, client, db_path):
    """The core offline guarantee: a network failure must not wipe access."""
    cache.replace_access(db_path, [AccessEntry("043f6afba2", "m1", "Alice")])
    client.fail_on.add("v_machine_access")
    with pytest.raises(RuntimeError):
        syncer.pull_access()
    assert cache.lookup_access(db_path, "043f6afba2") is not None


def test_pull_enrollment_stores_pending(syncer, client, db_path, now):
    client.tables["pending_enrollments"] = [
        {
            "id": "e1",
            "machine_id": MACHINE_ID,
            "member_name": "Newbie",
            "member_id": None,
            "status": "pending",
            "expires_at": cache.fmt_dt(now + timedelta(seconds=60)),
        }
    ]
    assert syncer.pull_enrollment(now) == 1
    active = cache.get_active_enrollment(db_path, now)
    assert active.member_name == "Newbie"


def test_pull_enrollment_none_clears_stale(syncer, db_path, now):
    cache.set_pending_enrollment(db_path, "old", "Old", now - timedelta(seconds=10))
    assert syncer.pull_enrollment(now) == 0
    assert cache.get_active_enrollment(db_path, now) is None


# ---------------------------------------------------------------------------
# session push
# ---------------------------------------------------------------------------

def test_push_sessions_upserts_and_marks_synced(syncer, client, db_path, now):
    sid = cache.start_session(db_path, "m1", "Alice", now)
    cache.end_session(db_path, sid, now + timedelta(seconds=60))

    pushed, failed = syncer.push_sessions()
    assert (pushed, failed) == (1, 0)
    assert cache.list_unsynced_sessions(db_path) == []

    table, rows = client.upserts[0]
    assert table == "sessions"
    assert rows[0]["id"] == sid           # client-generated id preserved
    assert rows[0]["machine_id"] == MACHINE_ID
    assert rows[0]["bridge_id"] == BRIDGE_ID
    assert rows[0]["duration_seconds"] == 60


def test_push_sessions_noop_when_empty(syncer):
    assert syncer.push_sessions() == (0, 0)


def test_push_sessions_failure_keeps_session_queued(syncer, client, db_path, now):
    sid = cache.start_session(db_path, "m1", "Alice", now)
    cache.end_session(db_path, sid, now + timedelta(seconds=60))
    client.fail_on.add("sessions")

    pushed, failed = syncer.push_sessions()
    assert (pushed, failed) == (0, 1)
    queued = cache.list_unsynced_sessions(db_path)
    assert len(queued) == 1
    assert queued[0].attempts == 1


def test_push_sessions_retries_successfully_after_outage(syncer, client, db_path, now):
    sid = cache.start_session(db_path, "m1", "Alice", now)
    cache.end_session(db_path, sid, now + timedelta(seconds=60))
    client.fail_times["sessions"] = 1

    assert syncer.push_sessions() == (0, 1)
    assert syncer.push_sessions() == (1, 0)
    assert cache.list_unsynced_sessions(db_path) == []


def test_push_sessions_stops_at_first_failure(syncer, client, db_path, now):
    """Don't hammer a down network with the whole backlog."""
    for i in range(5):
        sid = cache.start_session(db_path, "m1", "Alice", now + timedelta(minutes=i))
        cache.end_session(db_path, sid, now + timedelta(minutes=i, seconds=30))
    client.fail_on.add("sessions")
    pushed, failed = syncer.push_sessions()
    assert pushed == 0
    assert failed == 1  # bailed after the first
    assert len(cache.list_unsynced_sessions(db_path)) == 5


def test_offline_backlog_all_flushes_when_network_returns(syncer, db_path, now):
    for i in range(4):
        sid = cache.start_session(db_path, "m1", "Alice", now + timedelta(minutes=i))
        cache.end_session(db_path, sid, now + timedelta(minutes=i, seconds=30))
    pushed, failed = syncer.push_sessions()
    assert (pushed, failed) == (4, 0)
    assert cache.list_unsynced_sessions(db_path) == []


# ---------------------------------------------------------------------------
# enrollment push
# ---------------------------------------------------------------------------

def test_push_enrollment_creates_new_member(syncer, client, db_path, now):
    cache.set_pending_enrollment(db_path, "e1", "Newbie", now + timedelta(seconds=60))
    cache.capture_enrollment(db_path, "e1", "043f6afba2")

    assert syncer.push_enrollments(now) == 1
    member_upserts = [u for u in client.upserts if u[0] == "members"]
    assert len(member_upserts) == 1
    assert member_upserts[0][1][0]["rfid_uid"] == "043f6afba2"
    assert member_upserts[0][1][0]["name"] == "Newbie"
    # pending row marked captured
    pe_patches = [p for p in client.patches if p[0] == "pending_enrollments"]
    assert pe_patches[0][2]["status"] == "captured"
    assert cache.list_captured_unpushed(db_path) == []


def test_push_enrollment_existing_uid_reactivates_and_renames(
    syncer, client, db_path, now
):
    """Re-enrolling a known badge updates that member instead of duplicating."""
    client.tables["members"] = [
        {"id": "existing-1", "rfid_uid": "043f6afba2", "name": "Old Name", "active": False}
    ]
    cache.set_pending_enrollment(db_path, "e1", "New Name", now + timedelta(seconds=60))
    cache.capture_enrollment(db_path, "e1", "043f6afba2")

    assert syncer.push_enrollments(now) == 1
    member_patches = [p for p in client.patches if p[0] == "members"]
    assert len(member_patches) == 1
    _, params, values = member_patches[0]
    assert params["id"] == "eq.existing-1"
    assert values["active"] is True
    assert values["name"] == "New Name"
    # No new member created
    assert [u for u in client.upserts if u[0] == "members"] == []


def test_push_enrollment_rebadges_existing_member(syncer, client, db_path, now):
    """member_id set on the pending row = assign a new card to that person."""
    cache.set_pending_enrollment(
        db_path,
        "e1",
        "Alice",
        now + timedelta(seconds=60),
        member_id="member-42",
    )
    cache.capture_enrollment(db_path, "e1", "NEWCARD")

    assert syncer.push_enrollments(now) == 1
    member_patches = [p for p in client.patches if p[0] == "members"]
    _, params, values = member_patches[0]
    assert params["id"] == "eq.member-42"
    assert values["rfid_uid"] == "NEWCARD"


def test_push_enrollment_failure_keeps_it_queued(syncer, client, db_path, now):
    cache.set_pending_enrollment(db_path, "e1", "Newbie", now + timedelta(seconds=60))
    cache.capture_enrollment(db_path, "e1", "AAA")
    client.fail_on.add("members")

    assert syncer.push_enrollments(now) == 0
    assert len(cache.list_captured_unpushed(db_path)) == 1


def test_push_enrollments_noop_when_none(syncer, now):
    assert syncer.push_enrollments(now) == 0


# ---------------------------------------------------------------------------
# heartbeat + full cycle
# ---------------------------------------------------------------------------

def test_heartbeat_patches_bridge(syncer, client, now):
    syncer.heartbeat(now, ip="10.0.0.5")
    bridge_patches = [p for p in client.patches if p[0] == "bridges"]
    assert len(bridge_patches) == 1
    _, params, values = bridge_patches[0]
    assert params["id"] == f"eq.{BRIDGE_ID}"
    assert values["last_ip"] == "10.0.0.5"
    assert "last_seen_at" in values


def test_sync_once_happy_path(syncer, client, db_path, now):
    client.tables["v_machine_access"] = [
        {
            "machine_id": MACHINE_ID,
            "uid": "043f6afba2",
            "member_id": "m1",
            "member_name": "Alice",
        }
    ]
    sid = cache.start_session(db_path, "m1", "Alice", now)
    cache.end_session(db_path, sid, now + timedelta(seconds=60))

    result = syncer.sync_once(now)
    assert result.ok
    assert result.sessions_pushed == 1
    assert result.access_pulled == 1
    assert result.machine_pulled is True
    assert cache.get_meta(db_path, "last_sync_at") is not None


def test_sync_once_never_raises_on_total_network_failure(syncer, client, db_path, now):
    """A completely offline cycle reports errors but must not throw."""
    for table in ("machines", "bridges", "v_machine_access", "pending_enrollments",
                  "sessions", "members"):
        client.fail_on.add(table)
    result = syncer.sync_once(now)
    assert not result.ok
    assert len(result.errors) > 0
    assert cache.get_meta(db_path, "last_sync_at") is None


def test_sync_once_offline_preserves_access_for_offline_auth(
    syncer, client, db_path, now
):
    cache.replace_access(db_path, [AccessEntry("043f6afba2", "m1", "Alice")])
    for table in ("machines", "bridges", "v_machine_access", "pending_enrollments"):
        client.fail_on.add(table)
    syncer.sync_once(now)
    # Still authorized offline.
    assert cache.lookup_access(db_path, "043f6afba2").member_name == "Alice"


def test_sync_once_partial_failure_still_does_the_rest(syncer, client, db_path, now):
    client.fail_on.add("pending_enrollments")
    result = syncer.sync_once(now)
    assert not result.ok
    assert result.machine_pulled is True          # unaffected work still ran
    assert any("pull_enrollment" in e for e in result.errors)
