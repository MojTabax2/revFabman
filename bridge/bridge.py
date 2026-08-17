"""Fabman v2 bridge runtime.

Local-first: every access decision reads the local SQLite cache, so a card tap
is instant and works with the network down. Supabase sync runs on a background
thread and can fail freely without affecting the loop.

Run:  python bridge.py [config.yaml]
"""
from __future__ import annotations

import logging
import signal
import sys
import threading
import time
from datetime import datetime, timedelta, timezone
from typing import Optional

from core import cache
from core import config as configlib
from core.config import Config
from core.state_machine import (
    Buzz,
    ButtonPressed,
    CardSwiped,
    CompleteEnrollment,
    EnrollmentRequested,
    LedMode,
    LogSessionEnd,
    LogSessionStart,
    Relay,
    SMConfig,
    ShowDisplay,
    State,
    Tick,
    initial,
    transition,
)
from core.sync import SupabaseClient, Syncer
from hardware import button as hw_button
from hardware import buzzer as hw_buzzer
from hardware import display as hw_display
from hardware import reader as hw_reader
from hardware import relay as hw_relay

logger = logging.getLogger("bridge")

TICK_HZ = 4
TICK_SECONDS = 1.0 / TICK_HZ
VERSION = "2.0"

# States where a card read means something. Everywhere else the read is
# consumed and discarded, so nothing queues up to fire later.
READER_ACTIVE_STATES = (State.READY, State.ENROLLING, State.RUNNING)


class Bridge:
    def __init__(self, config: Config):
        self.config = config
        self.db_path = config.storage.cache_path

        cache.init(self.db_path)

        hw = config.hardware
        self.reader = hw_reader.make(
            hw.reader.type,
            device_name=hw.reader.device_name,
            **(
                {}
                if hw.reader.type == "simulate"
                else {
                    "gap_seconds": hw.reader.gap_seconds,
                    "dedup_window_s": hw.reader.dedup_window_seconds,
                }
            ),
        )
        self.display = hw_display.make(
            hw.display.type,
            i2c_bus=hw.i2c_bus,
            address=hw.display.i2c_address,
            width=hw.display.width,
            height=hw.display.height,
        )
        self.button = hw_button.make(
            hw.button.type,
            gpio_pin=hw.button.gpio_pin,
            pull=hw.button.pull,
            led_pin=hw.button.led_pin,
        )
        self.buzzer = hw_buzzer.make(hw.buzzer.type, gpio_pin=hw.buzzer.gpio_pin)
        self.relay = hw_relay.make(
            hw.relay.type,
            gpio_pin=hw.relay.gpio_pin,
            active_high=hw.relay.active_high,
        )

        self.sm_config = self._build_sm_config()
        self.current_session_id: Optional[str] = None
        self.sync_wake = threading.Event()

        now = datetime.now(timezone.utc)
        self._recover_orphan_sessions(now)
        result = initial(now, self.sm_config)
        self.state = result.state
        self.state_data = result.state_data
        self._execute(result.actions, now)

    # -- config ---------------------------------------------------------------

    def _build_sm_config(self) -> SMConfig:
        """Behavioral settings come from the cached machine row when present."""
        m = cache.get_machine(self.db_path)
        if m is None:
            logger.warning(
                "No cached machine settings yet; using defaults until first sync"
            )
            return SMConfig(station_display_name=self.config.station.machine_name)
        return SMConfig(
            station_display_name=m.display_name,
            ready_timeout_s=m.ready_seconds,
            session_timeout_s=m.session_seconds,
            denied_display_s=m.denied_display_seconds,
            extend_s=m.extend_seconds,
            extend_cooldown_s=m.extend_cooldown_seconds,
            session_max_total_s=m.session_max_total_seconds,
            warning_remaining_s=m.warning_remaining_seconds,
        )

    def refresh_settings(self) -> None:
        """Adopt machine settings pulled by a sync. Safe to call any time --
        only applied while IDLE so a running session's rules can't shift."""
        if self.state != State.IDLE:
            return
        new_config = self._build_sm_config()
        if new_config != self.sm_config:
            logger.info("Applying updated machine settings")
            self.sm_config = new_config
            self.display.show(
                "idle", station_display_name=new_config.station_display_name
            )

    # -- main loop ------------------------------------------------------------

    def step(self, now: Optional[datetime] = None) -> None:
        now = now or datetime.now(timezone.utc)
        events = self._collect_events(now)
        if not events:
            events = [Tick(now=now)]
        for event in events:
            prev_state = self.state
            result = transition(self.state, self.state_data, event, self.sm_config)
            self.state = result.state
            self.state_data = result.state_data
            self._execute(result.actions, now)
            # Entering a reader-active state: drop anything in flight so a
            # stale read can't be consumed as the user's intended tap.
            if self.state != prev_state and self.state in READER_ACTIVE_STATES:
                self.reader.drain()

    def _collect_events(self, now: datetime) -> list:
        events: list = []

        # Always poll so nothing accumulates; only act on it where it matters.
        uid = self.reader.poll()
        if uid:
            if self.state in READER_ACTIVE_STATES:
                entry = cache.lookup_access(self.db_path, uid)
                events.append(
                    CardSwiped(
                        now=now,
                        uid=uid,
                        member_id=entry.member_id if entry else None,
                        member_name=entry.member_name if entry else None,
                        authorized=entry is not None,
                    )
                )
            else:
                logger.debug("Discarding read %s in state %s", uid, self.state.value)

        if self.button.was_pressed():
            events.append(ButtonPressed(now=now))

        if self.state == State.IDLE:
            pending = cache.get_active_enrollment(self.db_path, now)
            if pending:
                events.append(
                    EnrollmentRequested(
                        now=now,
                        pending_id=pending.id,
                        name=pending.member_name,
                        expires_at=pending.expires_at,
                    )
                )

        return events

    # -- action execution -----------------------------------------------------

    def _execute(self, actions: list, now: datetime) -> None:
        for action in actions:
            try:
                self._execute_one(action, now)
            except Exception:
                logger.exception("Failed to execute %r", action)

    def _execute_one(self, action, now: datetime) -> None:
        if isinstance(action, ShowDisplay):
            self.display.show(
                action.screen,
                member_name=action.member_name,
                enrollment_name=action.enrollment_name,
                remaining_seconds=action.remaining_seconds,
                station_display_name=action.station_display_name,
            )
        elif isinstance(action, Buzz):
            self.buzzer.play(action.pattern)
        elif isinstance(action, Relay):
            self.relay.set(action.on)
        elif isinstance(action, LedMode):
            self.button.set_led(action.mode)
        elif isinstance(action, LogSessionStart):
            self.current_session_id = cache.start_session(
                self.db_path, action.member_id, action.member_name, now
            )
            logger.info(
                "Session %s started for %s",
                self.current_session_id,
                action.member_name,
            )
        elif isinstance(action, LogSessionEnd):
            if self.current_session_id is not None:
                duration = cache.end_session(
                    self.db_path,
                    self.current_session_id,
                    now,
                    auto_stopped=action.auto_stopped,
                    extensions_seconds=action.extensions_seconds,
                )
                logger.info(
                    "Session %s ended (%ds, auto=%s, extensions=%ds)",
                    self.current_session_id,
                    duration,
                    action.auto_stopped,
                    action.extensions_seconds,
                )
                self.current_session_id = None
                self.sync_wake.set()
        elif isinstance(action, CompleteEnrollment):
            cache.capture_enrollment(self.db_path, action.pending_id, action.uid)
            logger.info(
                "Enrollment %s captured uid=%s", action.pending_id, action.uid
            )
            self.sync_wake.set()

    def _recover_orphan_sessions(self, now: datetime) -> None:
        """Close sessions left open by a crash/power cut so they still get logged."""
        for s in cache.list_active_sessions(self.db_path):
            logger.warning("Recovering orphan session %s", s.id)
            cache.end_session(self.db_path, s.id, now, auto_stopped=True)

    def close(self) -> None:
        try:
            self.relay.set(False)
        except Exception:
            pass
        try:
            self.button.set_led("off")
        except Exception:
            pass
        for component in (
            self.reader,
            self.display,
            self.button,
            self.buzzer,
            self.relay,
        ):
            try:
                component.close()
            except Exception:
                pass


def _sync_loop(
    bridge: Bridge, syncer: Syncer, stop: threading.Event, interval: int
) -> None:
    """Background sync. Never raises into the main loop."""
    while not stop.is_set():
        try:
            result = syncer.sync_once()
            if result.ok:
                logger.info(
                    "Sync ok: access=%d pushed=%d enrolled=%d",
                    result.access_pulled,
                    result.sessions_pushed,
                    result.enrollments_pushed,
                )
                bridge.refresh_settings()
            else:
                logger.warning("Sync incomplete: %s", "; ".join(result.errors))
        except Exception:
            logger.exception("Sync loop error")
        bridge.sync_wake.wait(timeout=interval)
        bridge.sync_wake.clear()


def main() -> int:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )

    config_path = sys.argv[1] if len(sys.argv) > 1 else "config.yaml"
    config = configlib.load(config_path)

    bridge = Bridge(config)
    stop = threading.Event()

    syncer = None
    if config.supabase.configured:
        client = SupabaseClient(
            config.supabase.url,
            config.supabase.service_key,
            timeout=config.supabase.request_timeout_seconds,
        )
        syncer = Syncer(
            client=client,
            db_path=config.storage.cache_path,
            machine_name=config.station.machine_name,
            bridge_name=config.station.bridge_name,
            version=VERSION,
        )
        threading.Thread(
            target=_sync_loop,
            args=(bridge, syncer, stop, config.supabase.sync_interval_seconds),
            daemon=True,
        ).start()
    else:
        logger.warning(
            "Supabase not configured (see secrets/env) -- running cache-only. "
            "Access list will not update and sessions will not upload."
        )

    def _signal(signum, _frame):
        logger.info("Signal %d received, shutting down", signum)
        stop.set()
        bridge.sync_wake.set()

    signal.signal(signal.SIGINT, _signal)
    signal.signal(signal.SIGTERM, _signal)

    logger.info(
        "Bridge %s started for machine %s (%d cached credentials)",
        config.station.bridge_name,
        config.station.machine_name,
        cache.count_access(config.storage.cache_path),
    )
    try:
        while not stop.is_set():
            start = time.monotonic()
            try:
                bridge.step()
            except Exception:
                logger.exception("step() error; continuing")
            time.sleep(max(0.0, TICK_SECONDS - (time.monotonic() - start)))
    finally:
        bridge.close()
        # Best-effort final flush so the last session isn't stranded locally.
        if syncer is not None:
            try:
                syncer.sync_once()
            except Exception:
                logger.debug("final sync failed", exc_info=True)
        logger.info("Bridge stopped")

    return 0


if __name__ == "__main__":
    sys.exit(main())
