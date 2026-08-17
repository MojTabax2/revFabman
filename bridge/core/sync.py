"""Supabase sync for one bridge.

Talks to Supabase's PostgREST API over plain HTTPS (requests) instead of the
supabase SDK: fewer deps on the Pi and explicit control over timeouts, which
matters because nothing here may ever stall the 4 Hz bridge loop.

Design rules:
  * Sync NEVER runs on the card-tap path. It runs in a background thread.
  * Every call has a short timeout and every failure is caught. A sync error
    logs and returns; it never propagates into the bridge loop.
  * A failed pull leaves the existing local cache untouched, so the bridge
    keeps honoring the last known-good access list.
  * Session pushes are upserts on a client-generated UUID, so retrying after
    an outage can't duplicate rows.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Optional

import requests

from core import cache
from core.cache import AccessEntry, MachineSettings

logger = logging.getLogger(__name__)

DEFAULT_TIMEOUT = 10  # seconds; per-request


class SyncError(Exception):
    pass


@dataclass
class SyncResult:
    access_pulled: int = 0
    sessions_pushed: int = 0
    sessions_failed: int = 0
    enrollments_pulled: int = 0
    enrollments_pushed: int = 0
    machine_pulled: bool = False
    errors: Optional[list[str]] = None

    def __post_init__(self):
        if self.errors is None:
            self.errors = []

    @property
    def ok(self) -> bool:
        return not self.errors


class SupabaseClient:
    """Thin PostgREST wrapper (only the verbs this bridge needs)."""

    def __init__(self, url: str, service_key: str, timeout: int = DEFAULT_TIMEOUT):
        self.rest_url = url.rstrip("/") + "/rest/v1"
        self.timeout = timeout
        self._session = requests.Session()
        self._session.headers.update(
            {
                "apikey": service_key,
                "Authorization": f"Bearer {service_key}",
                "Content-Type": "application/json",
            }
        )

    def select(
        self, table: str, params: Optional[dict[str, str]] = None
    ) -> list[dict[str, Any]]:
        r = self._session.get(
            f"{self.rest_url}/{table}", params=params or {}, timeout=self.timeout
        )
        r.raise_for_status()
        return r.json()

    def upsert(
        self,
        table: str,
        rows: list[dict[str, Any]],
        on_conflict: Optional[str] = None,
    ) -> list[dict[str, Any]]:
        params = {}
        if on_conflict:
            params["on_conflict"] = on_conflict
        r = self._session.post(
            f"{self.rest_url}/{table}",
            params=params,
            json=rows,
            headers={"Prefer": "resolution=merge-duplicates,return=representation"},
            timeout=self.timeout,
        )
        r.raise_for_status()
        return r.json() if r.text else []

    def patch(
        self, table: str, params: dict[str, str], values: dict[str, Any]
    ) -> list[dict[str, Any]]:
        r = self._session.patch(
            f"{self.rest_url}/{table}",
            params=params,
            json=values,
            headers={"Prefer": "return=representation"},
            timeout=self.timeout,
        )
        r.raise_for_status()
        return r.json() if r.text else []

    def insert(self, table: str, row: dict[str, Any]) -> list[dict[str, Any]]:
        r = self._session.post(
            f"{self.rest_url}/{table}",
            json=row,
            headers={"Prefer": "return=representation"},
            timeout=self.timeout,
        )
        r.raise_for_status()
        return r.json() if r.text else []


class Syncer:
    """Owns one bridge's sync cycle against Supabase."""

    def __init__(
        self,
        client: SupabaseClient,
        db_path,
        machine_name: str,
        bridge_name: str,
        version: str = "2.0",
    ):
        self.client = client
        self.db_path = db_path
        self.machine_name = machine_name
        self.bridge_name = bridge_name
        self.version = version
        self._machine_id: Optional[str] = None
        self._bridge_id: Optional[str] = None

    # -- ids -----------------------------------------------------------------

    def resolve_machine_id(self) -> str:
        if self._machine_id:
            return self._machine_id
        rows = self.client.select(
            "machines", {"name": f"eq.{self.machine_name}", "select": "id"}
        )
        if not rows:
            raise SyncError(
                f"machine {self.machine_name!r} not found in Supabase; "
                "create it in the admin app first"
            )
        self._machine_id = rows[0]["id"]
        return self._machine_id

    def resolve_bridge_id(self) -> str:
        """Find (or register) this bridge's row."""
        if self._bridge_id:
            return self._bridge_id
        rows = self.client.select(
            "bridges", {"name": f"eq.{self.bridge_name}", "select": "id"}
        )
        if rows:
            self._bridge_id = rows[0]["id"]
        else:
            created = self.client.insert(
                "bridges",
                {
                    "name": self.bridge_name,
                    "machine_id": self.resolve_machine_id(),
                    "version": self.version,
                },
            )
            self._bridge_id = created[0]["id"]
            logger.info("Registered bridge %r", self.bridge_name)
        return self._bridge_id

    # -- pulls ---------------------------------------------------------------

    def pull_machine(self) -> MachineSettings:
        rows = self.client.select("machines", {"name": f"eq.{self.machine_name}"})
        if not rows:
            raise SyncError(f"machine {self.machine_name!r} not found")
        m = rows[0]
        self._machine_id = m["id"]
        settings = MachineSettings(
            id=m["id"],
            name=m["name"],
            display_name=m["display_name"],
            enabled=bool(m["enabled"]),
            ready_seconds=m["ready_seconds"],
            session_seconds=m["session_seconds"],
            extend_seconds=m["extend_seconds"],
            extend_cooldown_seconds=m["extend_cooldown_seconds"],
            session_max_total_seconds=m["session_max_total_seconds"],
            warning_remaining_seconds=m["warning_remaining_seconds"],
            enrollment_seconds=m["enrollment_seconds"],
            denied_display_seconds=m["denied_display_seconds"],
        )
        cache.upsert_machine(self.db_path, settings)
        return settings

    def pull_access(self) -> int:
        """Refresh the local access list for this machine.

        Only swaps the cache in after a fully successful fetch.
        """
        machine_id = self.resolve_machine_id()
        rows = self.client.select(
            "v_machine_access",
            {"machine_id": f"eq.{machine_id}", "select": "uid,member_id,member_name"},
        )
        entries = [
            AccessEntry(
                uid=r["uid"], member_id=r["member_id"], member_name=r["member_name"]
            )
            for r in rows
            if r.get("uid")
        ]
        cache.replace_access(self.db_path, entries)
        return len(entries)

    def pull_enrollment(self, now: datetime) -> int:
        """Fetch a pending enrollment targeted at this machine."""
        machine_id = self.resolve_machine_id()
        rows = self.client.select(
            "pending_enrollments",
            {
                "machine_id": f"eq.{machine_id}",
                "status": "eq.pending",
                "expires_at": f"gt.{cache.fmt_dt(now)}",
                "order": "requested_at.desc",
                "limit": "1",
            },
        )
        if not rows:
            cache.clear_stale_enrollments(self.db_path, now)
            return 0
        e = rows[0]
        cache.set_pending_enrollment(
            self.db_path,
            enrollment_id=e["id"],
            member_name=e["member_name"],
            expires_at=cache.parse_dt(e["expires_at"]),
            member_id=e.get("member_id"),
        )
        return 1

    # -- pushes --------------------------------------------------------------

    def push_sessions(self) -> tuple[int, int]:
        """Upsert ended-but-unsynced sessions. Returns (pushed, failed)."""
        pending = cache.list_unsynced_sessions(self.db_path)
        if not pending:
            return (0, 0)

        machine_id = self.resolve_machine_id()
        bridge_id = self.resolve_bridge_id()
        pushed = failed = 0

        for s in pending:
            row = {
                "id": s.id,
                "member_id": s.member_id,
                "machine_id": machine_id,
                "bridge_id": bridge_id,
                "started_at": cache.fmt_dt(s.started_at),
                "ended_at": cache.fmt_dt(s.ended_at) if s.ended_at else None,
                "duration_seconds": s.duration_seconds,
                "extensions_seconds": s.extensions_seconds,
                "auto_stopped": s.auto_stopped,
            }
            try:
                self.client.upsert("sessions", [row], on_conflict="id")
                cache.mark_session_synced(self.db_path, s.id)
                pushed += 1
            except Exception as e:
                logger.warning("Session %s push failed: %s", s.id, e)
                cache.mark_session_failed(self.db_path, s.id, str(e))
                failed += 1
                break  # network is likely down; stop hammering it
        return (pushed, failed)

    def push_enrollments(self, now: datetime) -> int:
        """Send captured enrollment taps to Supabase.

        Creates the member (or re-badges/reactivates an existing one), then
        marks the pending row captured.
        """
        captured = cache.list_captured_unpushed(self.db_path)
        if not captured:
            return 0

        pushed = 0
        for e in captured:
            try:
                member_id = self._upsert_member_for_enrollment(e)
                self.client.patch(
                    "pending_enrollments",
                    {"id": f"eq.{e.id}"},
                    {
                        "captured_uid": e.captured_uid,
                        "captured_member_id": member_id,
                        "status": "captured",
                    },
                )
                cache.mark_enrollment_pushed(self.db_path, e.id)
                pushed += 1
                logger.info(
                    "Enrollment %s pushed: %s -> member %s",
                    e.id,
                    e.captured_uid,
                    member_id,
                )
            except Exception as ex:
                logger.warning("Enrollment %s push failed: %s", e.id, ex)
                break
        return pushed

    def _upsert_member_for_enrollment(self, e) -> str:
        """Resolve the enrollment to a member id.

        Order matters: an existing badge wins (re-tapping a known card updates
        that member rather than creating a duplicate), then an explicit
        member_id (re-badging), then create fresh.
        """
        existing = self.client.select(
            "members", {"rfid_uid": f"eq.{e.captured_uid}", "select": "id"}
        )
        if existing:
            member_id = existing[0]["id"]
            # Reactivate + adopt the name the admin just typed.
            self.client.patch(
                "members",
                {"id": f"eq.{member_id}"},
                {"active": True, "name": e.member_name},
            )
            return member_id

        if e.member_id:
            self.client.patch(
                "members",
                {"id": f"eq.{e.member_id}"},
                {"rfid_uid": e.captured_uid, "active": True},
            )
            return e.member_id

        created = self.client.upsert(
            "members",
            [{"name": e.member_name, "rfid_uid": e.captured_uid, "active": True}],
        )
        return created[0]["id"]

    def heartbeat(self, now: datetime, ip: Optional[str] = None) -> None:
        bridge_id = self.resolve_bridge_id()
        values: dict[str, Any] = {
            "last_seen_at": cache.fmt_dt(now),
            "version": self.version,
        }
        if ip:
            values["last_ip"] = ip
        self.client.patch("bridges", {"id": f"eq.{bridge_id}"}, values)

    # -- one full cycle ------------------------------------------------------

    def sync_once(self, now: Optional[datetime] = None) -> SyncResult:
        """Run a full sync cycle. Never raises -- errors land in the result."""
        now = now or datetime.now(timezone.utc)
        result = SyncResult()

        # Push first: locally-produced facts are what we'd hate to lose.
        try:
            pushed, failed = self.push_sessions()
            result.sessions_pushed = pushed
            result.sessions_failed = failed
        except Exception as e:
            result.errors.append(f"push_sessions: {e}")

        try:
            result.enrollments_pushed = self.push_enrollments(now)
        except Exception as e:
            result.errors.append(f"push_enrollments: {e}")

        try:
            self.pull_machine()
            result.machine_pulled = True
        except Exception as e:
            result.errors.append(f"pull_machine: {e}")

        try:
            result.access_pulled = self.pull_access()
        except Exception as e:
            result.errors.append(f"pull_access: {e}")

        try:
            result.enrollments_pulled = self.pull_enrollment(now)
        except Exception as e:
            result.errors.append(f"pull_enrollment: {e}")

        try:
            self.heartbeat(now)
        except Exception as e:
            result.errors.append(f"heartbeat: {e}")

        if result.ok:
            cache.set_meta(self.db_path, "last_sync_at", cache.fmt_dt(now))
        try:
            cache.prune_synced_sessions(self.db_path)
        except Exception:
            logger.debug("prune failed", exc_info=True)

        return result
