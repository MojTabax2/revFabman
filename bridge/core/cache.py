"""Local SQLite cache + outbox for one bridge.

Supabase is the source of truth; this file is a durable local mirror so the
bridge keeps working when the network is down:

  access      - uid -> member, for THIS machine only. Authorization reads this,
                never the network, so a card tap is always instant.
  machine     - the machine's tunables (timeouts), pulled from Supabase.
  enrollment  - the active pending enrollment for this machine, if any.
  sessions    - every session, with synced=0 until Supabase confirms it (outbox).

Session ids are UUIDs generated here so retried pushes upsert instead of
duplicating.
"""
from __future__ import annotations

import sqlite3
import uuid
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterator, Optional, Union

PathLike = Union[str, Path]

SCHEMA = """
CREATE TABLE IF NOT EXISTS access (
    uid TEXT PRIMARY KEY,
    member_id TEXT NOT NULL,
    member_name TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS machine (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    display_name TEXT NOT NULL,
    enabled INTEGER NOT NULL DEFAULT 1,
    ready_seconds INTEGER NOT NULL DEFAULT 30,
    session_seconds INTEGER NOT NULL DEFAULT 900,
    extend_seconds INTEGER NOT NULL DEFAULT 900,
    extend_cooldown_seconds INTEGER NOT NULL DEFAULT 10,
    session_max_total_seconds INTEGER NOT NULL DEFAULT 18000,
    warning_remaining_seconds INTEGER NOT NULL DEFAULT 60,
    enrollment_seconds INTEGER NOT NULL DEFAULT 60,
    denied_display_seconds INTEGER NOT NULL DEFAULT 2
);

CREATE TABLE IF NOT EXISTS enrollment (
    id TEXT PRIMARY KEY,
    member_name TEXT NOT NULL,
    member_id TEXT,
    expires_at TEXT NOT NULL,
    captured_uid TEXT,
    pushed INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS sessions (
    id TEXT PRIMARY KEY,
    member_id TEXT NOT NULL,
    member_name TEXT,
    started_at TEXT NOT NULL,
    ended_at TEXT,
    duration_seconds INTEGER,
    extensions_seconds INTEGER NOT NULL DEFAULT 0,
    auto_stopped INTEGER NOT NULL DEFAULT 0,
    synced INTEGER NOT NULL DEFAULT 0,
    attempts INTEGER NOT NULL DEFAULT 0,
    last_error TEXT
);

CREATE TABLE IF NOT EXISTS meta (
    key TEXT PRIMARY KEY,
    value TEXT
);

CREATE INDEX IF NOT EXISTS sessions_unsynced_idx ON sessions(synced, id);
"""


@dataclass
class AccessEntry:
    uid: str
    member_id: str
    member_name: str


@dataclass
class MachineSettings:
    id: str
    name: str
    display_name: str
    enabled: bool
    ready_seconds: int
    session_seconds: int
    extend_seconds: int
    extend_cooldown_seconds: int
    session_max_total_seconds: int
    warning_remaining_seconds: int
    enrollment_seconds: int
    denied_display_seconds: int

    @classmethod
    def from_row(cls, row: sqlite3.Row) -> "MachineSettings":
        return cls(
            id=row["id"],
            name=row["name"],
            display_name=row["display_name"],
            enabled=bool(row["enabled"]),
            ready_seconds=row["ready_seconds"],
            session_seconds=row["session_seconds"],
            extend_seconds=row["extend_seconds"],
            extend_cooldown_seconds=row["extend_cooldown_seconds"],
            session_max_total_seconds=row["session_max_total_seconds"],
            warning_remaining_seconds=row["warning_remaining_seconds"],
            enrollment_seconds=row["enrollment_seconds"],
            denied_display_seconds=row["denied_display_seconds"],
        )


@dataclass
class PendingEnrollment:
    id: str
    member_name: str
    member_id: Optional[str]
    expires_at: datetime
    captured_uid: Optional[str]
    pushed: bool


@dataclass
class CachedSession:
    id: str
    member_id: str
    member_name: Optional[str]
    started_at: datetime
    ended_at: Optional[datetime]
    duration_seconds: Optional[int]
    extensions_seconds: int
    auto_stopped: bool
    synced: bool
    attempts: int


def fmt_dt(dt: datetime) -> str:
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc).isoformat(timespec="seconds")


def parse_dt(s: str) -> datetime:
    # Postgres/PostgREST may hand back '...+00:00', '...Z', or microseconds.
    s = s.strip()
    if s.endswith("Z"):
        s = s[:-1] + "+00:00"
    dt = datetime.fromisoformat(s)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt


@contextmanager
def _connect(db_path: PathLike) -> Iterator[sqlite3.Connection]:
    conn = sqlite3.connect(str(db_path), isolation_level=None)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode = WAL")
    conn.execute("PRAGMA synchronous = NORMAL")
    try:
        yield conn
    finally:
        conn.close()


def init(db_path: PathLike) -> None:
    with _connect(db_path) as conn:
        conn.executescript(SCHEMA)


# ---------------------------------------------------------------------------
# Access list (authorization -- the hot path, always local)
# ---------------------------------------------------------------------------

def replace_access(db_path: PathLike, entries: list[AccessEntry]) -> None:
    """Atomically swap in a fresh access list from Supabase.

    Only called with a full, successful pull -- a failed pull leaves the old
    list intact so the bridge keeps honoring the last known-good state.
    """
    with _connect(db_path) as conn:
        conn.execute("BEGIN")
        try:
            conn.execute("DELETE FROM access")
            conn.executemany(
                "INSERT OR REPLACE INTO access (uid, member_id, member_name) "
                "VALUES (?, ?, ?)",
                [(e.uid, e.member_id, e.member_name) for e in entries],
            )
            conn.execute("COMMIT")
        except Exception:
            conn.execute("ROLLBACK")
            raise


def lookup_access(db_path: PathLike, uid: str) -> Optional[AccessEntry]:
    with _connect(db_path) as conn:
        row = conn.execute(
            "SELECT uid, member_id, member_name FROM access WHERE uid = ?", (uid,)
        ).fetchone()
        if row is None:
            return None
        return AccessEntry(
            uid=row["uid"], member_id=row["member_id"], member_name=row["member_name"]
        )


def count_access(db_path: PathLike) -> int:
    with _connect(db_path) as conn:
        return conn.execute("SELECT COUNT(*) AS n FROM access").fetchone()["n"]


# ---------------------------------------------------------------------------
# Machine settings
# ---------------------------------------------------------------------------

def upsert_machine(db_path: PathLike, settings: MachineSettings) -> None:
    with _connect(db_path) as conn:
        conn.execute("DELETE FROM machine")
        conn.execute(
            """INSERT INTO machine (
                   id, name, display_name, enabled, ready_seconds, session_seconds,
                   extend_seconds, extend_cooldown_seconds, session_max_total_seconds,
                   warning_remaining_seconds, enrollment_seconds, denied_display_seconds
               ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                settings.id,
                settings.name,
                settings.display_name,
                1 if settings.enabled else 0,
                settings.ready_seconds,
                settings.session_seconds,
                settings.extend_seconds,
                settings.extend_cooldown_seconds,
                settings.session_max_total_seconds,
                settings.warning_remaining_seconds,
                settings.enrollment_seconds,
                settings.denied_display_seconds,
            ),
        )


def get_machine(db_path: PathLike) -> Optional[MachineSettings]:
    with _connect(db_path) as conn:
        row = conn.execute("SELECT * FROM machine LIMIT 1").fetchone()
        return MachineSettings.from_row(row) if row else None


# ---------------------------------------------------------------------------
# Enrollment
# ---------------------------------------------------------------------------

def set_pending_enrollment(
    db_path: PathLike,
    enrollment_id: str,
    member_name: str,
    expires_at: datetime,
    member_id: Optional[str] = None,
) -> None:
    """Store the active pending enrollment (at most one per bridge)."""
    with _connect(db_path) as conn:
        conn.execute("DELETE FROM enrollment WHERE captured_uid IS NULL")
        conn.execute(
            "INSERT OR REPLACE INTO enrollment "
            "(id, member_name, member_id, expires_at, captured_uid, pushed) "
            "VALUES (?, ?, ?, ?, NULL, 0)",
            (enrollment_id, member_name, member_id, fmt_dt(expires_at)),
        )


def get_active_enrollment(
    db_path: PathLike, now: datetime
) -> Optional[PendingEnrollment]:
    with _connect(db_path) as conn:
        row = conn.execute(
            "SELECT * FROM enrollment WHERE captured_uid IS NULL AND expires_at > ? "
            "ORDER BY expires_at DESC LIMIT 1",
            (fmt_dt(now),),
        ).fetchone()
        if row is None:
            return None
        return PendingEnrollment(
            id=row["id"],
            member_name=row["member_name"],
            member_id=row["member_id"],
            expires_at=parse_dt(row["expires_at"]),
            captured_uid=row["captured_uid"],
            pushed=bool(row["pushed"]),
        )


def capture_enrollment(db_path: PathLike, enrollment_id: str, uid: str) -> None:
    """Record the tapped uid locally; sync pushes it to Supabase."""
    with _connect(db_path) as conn:
        conn.execute(
            "UPDATE enrollment SET captured_uid = ?, pushed = 0 WHERE id = ?",
            (uid, enrollment_id),
        )


def list_captured_unpushed(db_path: PathLike) -> list[PendingEnrollment]:
    with _connect(db_path) as conn:
        rows = conn.execute(
            "SELECT * FROM enrollment WHERE captured_uid IS NOT NULL AND pushed = 0"
        ).fetchall()
        return [
            PendingEnrollment(
                id=r["id"],
                member_name=r["member_name"],
                member_id=r["member_id"],
                expires_at=parse_dt(r["expires_at"]),
                captured_uid=r["captured_uid"],
                pushed=bool(r["pushed"]),
            )
            for r in rows
        ]


def mark_enrollment_pushed(db_path: PathLike, enrollment_id: str) -> None:
    with _connect(db_path) as conn:
        conn.execute("UPDATE enrollment SET pushed = 1 WHERE id = ?", (enrollment_id,))


def clear_enrollment(db_path: PathLike, enrollment_id: str) -> None:
    with _connect(db_path) as conn:
        conn.execute("DELETE FROM enrollment WHERE id = ?", (enrollment_id,))


def clear_stale_enrollments(db_path: PathLike, now: datetime) -> int:
    """Drop expired, never-captured enrollments."""
    with _connect(db_path) as conn:
        cur = conn.execute(
            "DELETE FROM enrollment WHERE captured_uid IS NULL AND expires_at <= ?",
            (fmt_dt(now),),
        )
        return cur.rowcount


# ---------------------------------------------------------------------------
# Sessions (write locally first, then push -- the outbox)
# ---------------------------------------------------------------------------

def start_session(
    db_path: PathLike,
    member_id: str,
    member_name: Optional[str],
    now: datetime,
) -> str:
    """Create a session locally and return its client-generated UUID."""
    session_id = str(uuid.uuid4())
    with _connect(db_path) as conn:
        conn.execute(
            "INSERT INTO sessions (id, member_id, member_name, started_at) "
            "VALUES (?, ?, ?, ?)",
            (session_id, member_id, member_name, fmt_dt(now)),
        )
    return session_id


def end_session(
    db_path: PathLike,
    session_id: str,
    now: datetime,
    auto_stopped: bool = False,
    extensions_seconds: int = 0,
) -> int:
    with _connect(db_path) as conn:
        row = conn.execute(
            "SELECT started_at FROM sessions WHERE id = ?", (session_id,)
        ).fetchone()
        if row is None:
            raise ValueError(f"session {session_id} not found")
        started = parse_dt(row["started_at"])
        duration = max(0, int((now - started).total_seconds()))
        conn.execute(
            """UPDATE sessions
                  SET ended_at = ?, duration_seconds = ?, auto_stopped = ?,
                      extensions_seconds = ?, synced = 0
                WHERE id = ?""",
            (
                fmt_dt(now),
                duration,
                1 if auto_stopped else 0,
                extensions_seconds,
                session_id,
            ),
        )
        return duration


def list_active_sessions(db_path: PathLike) -> list[CachedSession]:
    with _connect(db_path) as conn:
        rows = conn.execute(
            "SELECT * FROM sessions WHERE ended_at IS NULL ORDER BY started_at"
        ).fetchall()
        return [_session_from_row(r) for r in rows]


def list_unsynced_sessions(db_path: PathLike, limit: int = 50) -> list[CachedSession]:
    """Ended sessions awaiting confirmation from Supabase."""
    with _connect(db_path) as conn:
        rows = conn.execute(
            "SELECT * FROM sessions WHERE synced = 0 AND ended_at IS NOT NULL "
            "ORDER BY attempts ASC, started_at ASC LIMIT ?",
            (limit,),
        ).fetchall()
        return [_session_from_row(r) for r in rows]


def mark_session_synced(db_path: PathLike, session_id: str) -> None:
    with _connect(db_path) as conn:
        conn.execute(
            "UPDATE sessions SET synced = 1, last_error = NULL WHERE id = ?",
            (session_id,),
        )


def mark_session_failed(db_path: PathLike, session_id: str, error: str) -> None:
    with _connect(db_path) as conn:
        conn.execute(
            "UPDATE sessions SET attempts = attempts + 1, last_error = ? WHERE id = ?",
            (error[:500], session_id),
        )


def prune_synced_sessions(db_path: PathLike, keep: int = 500) -> int:
    """Trim old synced rows so the cache can't grow forever."""
    with _connect(db_path) as conn:
        cur = conn.execute(
            """DELETE FROM sessions
                WHERE synced = 1
                  AND id NOT IN (
                      SELECT id FROM sessions WHERE synced = 1
                      ORDER BY started_at DESC LIMIT ?
                  )""",
            (keep,),
        )
        return cur.rowcount


def _session_from_row(row: sqlite3.Row) -> CachedSession:
    return CachedSession(
        id=row["id"],
        member_id=row["member_id"],
        member_name=row["member_name"],
        started_at=parse_dt(row["started_at"]),
        ended_at=parse_dt(row["ended_at"]) if row["ended_at"] else None,
        duration_seconds=row["duration_seconds"],
        extensions_seconds=row["extensions_seconds"],
        auto_stopped=bool(row["auto_stopped"]),
        synced=bool(row["synced"]),
        attempts=row["attempts"],
    )


# ---------------------------------------------------------------------------
# Meta (last sync time, etc.)
# ---------------------------------------------------------------------------

def set_meta(db_path: PathLike, key: str, value: str) -> None:
    with _connect(db_path) as conn:
        conn.execute(
            "INSERT OR REPLACE INTO meta (key, value) VALUES (?, ?)", (key, value)
        )


def get_meta(db_path: PathLike, key: str) -> Optional[str]:
    with _connect(db_path) as conn:
        row = conn.execute("SELECT value FROM meta WHERE key = ?", (key,)).fetchone()
        return row["value"] if row else None
