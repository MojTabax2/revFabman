"""Pure-logic state machine for the Fabman bridge (v2).

No DB, network, or hardware imports. bridge.py is responsible for:
  * resolving a tapped uid against the local cache and building CardSwiped
  * calling transition() per event and on a periodic Tick (~4 Hz)
  * executing the returned Actions against hardware and the local cache

Workflow is button-first. The reader is only *consulted* in READY, ENROLLING,
and RUNNING (where only the active member may extend), which keeps stray reads
from doing anything in IDLE.

  IDLE ──(button)──► READY ──(card+auth)──► RUNNING ──(button or timeout)──► IDLE
                       ├──(card+denied)──► DENIED ──► IDLE
                       ├──(button cancel)──► IDLE
                       └──(ready timeout)──► IDLE

  RUNNING: active member re-tap extends the session (cooldown + hard cap),
           and a warning buzz fires once as expiry approaches.

  IDLE ──(pending enrollment)──► ENROLLING ──(card)──► IDLE (uid captured)
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Optional, Union


class State(Enum):
    IDLE = "idle"
    READY = "ready"
    DENIED = "denied"
    RUNNING = "running"
    ENROLLING = "enrolling"


# ---------- Events ----------

@dataclass
class CardSwiped:
    now: datetime
    uid: str
    member_id: Optional[str]
    member_name: Optional[str]
    authorized: bool


@dataclass
class ButtonPressed:
    now: datetime


@dataclass
class Tick:
    now: datetime


@dataclass
class EnrollmentRequested:
    now: datetime
    pending_id: str
    name: str
    expires_at: datetime


Event = Union[CardSwiped, ButtonPressed, Tick, EnrollmentRequested]


# ---------- Actions ----------

@dataclass
class ShowDisplay:
    screen: str  # idle, ready, denied, running, enrolling
    member_name: Optional[str] = None
    enrollment_name: Optional[str] = None
    remaining_seconds: Optional[int] = None
    station_display_name: Optional[str] = None


@dataclass
class Buzz:
    pattern: str  # success, denied, start, stop, enroll, timeout, warning, limit


@dataclass
class Relay:
    on: bool


@dataclass
class LedMode:
    mode: str  # off, dim, solid


@dataclass
class LogSessionStart:
    member_id: str
    member_name: Optional[str]


@dataclass
class LogSessionEnd:
    auto_stopped: bool
    extensions_seconds: int


@dataclass
class CompleteEnrollment:
    pending_id: str
    uid: str


Action = Union[
    ShowDisplay, Buzz, Relay, LedMode, LogSessionStart, LogSessionEnd, CompleteEnrollment
]


# ---------- State data ----------

@dataclass
class StateData:
    entered_at: datetime
    current_member_id: Optional[str] = None
    current_member_name: Optional[str] = None
    pending_enrollment_id: Optional[str] = None
    pending_enrollment_name: Optional[str] = None
    enrollment_expires_at: Optional[datetime] = None
    session_extensions_s: int = 0
    warning_played: bool = False
    last_extend_at: Optional[datetime] = None


# ---------- Config ----------

@dataclass
class SMConfig:
    station_display_name: str
    ready_timeout_s: int = 30
    session_timeout_s: int = 900
    denied_display_s: int = 2
    extend_s: int = 900
    extend_cooldown_s: int = 10
    session_max_total_s: int = 18000
    warning_remaining_s: int = 60


@dataclass
class TransitionResult:
    state: State
    state_data: StateData
    actions: list[Action] = field(default_factory=list)


def initial(now: datetime, config: SMConfig) -> TransitionResult:
    return TransitionResult(
        state=State.IDLE,
        state_data=StateData(entered_at=now),
        actions=_idle_actions(config),
    )


def transition(
    state: State, sd: StateData, event: Event, config: SMConfig
) -> TransitionResult:
    now = event.now

    if state == State.IDLE:
        if isinstance(event, ButtonPressed):
            return TransitionResult(
                state=State.READY,
                state_data=StateData(entered_at=now),
                actions=[
                    LedMode("solid"),
                    ShowDisplay(
                        screen="ready",
                        station_display_name=config.station_display_name,
                        remaining_seconds=config.ready_timeout_s,
                    ),
                ],
            )
        if isinstance(event, EnrollmentRequested):
            return _enter_enrolling(event)
        # Card swipes in IDLE are deliberately inert.
        return _stay(state, sd)

    if state == State.READY:
        if isinstance(event, CardSwiped):
            if event.authorized:
                return TransitionResult(
                    state=State.RUNNING,
                    state_data=StateData(
                        entered_at=now,
                        current_member_id=event.member_id,
                        current_member_name=event.member_name,
                    ),
                    actions=[
                        Relay(on=True),
                        Buzz("start"),
                        LogSessionStart(
                            member_id=event.member_id, member_name=event.member_name
                        ),
                        LedMode("solid"),
                        ShowDisplay(
                            screen="running",
                            member_name=event.member_name,
                            remaining_seconds=config.session_timeout_s,
                        ),
                    ],
                )
            return TransitionResult(
                state=State.DENIED,
                state_data=StateData(entered_at=now),
                actions=[Buzz("denied"), LedMode("dim"), ShowDisplay(screen="denied")],
            )
        if isinstance(event, ButtonPressed):
            return _to_idle(now, config)
        if isinstance(event, Tick):
            elapsed = (now - sd.entered_at).total_seconds()
            if elapsed >= config.ready_timeout_s:
                return _to_idle(now, config)
            return TransitionResult(
                state=State.READY,
                state_data=sd,
                actions=[
                    ShowDisplay(
                        screen="ready",
                        station_display_name=config.station_display_name,
                        remaining_seconds=max(
                            0, int(config.ready_timeout_s - elapsed)
                        ),
                    )
                ],
            )
        return _stay(state, sd)

    if state == State.DENIED:
        if isinstance(event, Tick):
            if (now - sd.entered_at).total_seconds() >= config.denied_display_s:
                return _to_idle(now, config)
        return _stay(state, sd)

    if state == State.RUNNING:
        total_s = config.session_timeout_s + sd.session_extensions_s

        if isinstance(event, CardSwiped):
            # Only the active member may extend; anyone else is ignored outright.
            if event.member_id != sd.current_member_id:
                return _stay(state, sd)
            if sd.last_extend_at is not None:
                if (now - sd.last_extend_at).total_seconds() < config.extend_cooldown_s:
                    return _stay(state, sd)
            capacity = config.session_max_total_s - total_s
            if capacity <= 0:
                return TransitionResult(
                    state=State.RUNNING, state_data=sd, actions=[Buzz("limit")]
                )
            granted = min(config.extend_s, capacity)
            new_extensions = sd.session_extensions_s + granted
            elapsed = (now - sd.entered_at).total_seconds()
            remaining = max(
                0, int(config.session_timeout_s + new_extensions - elapsed)
            )
            return TransitionResult(
                state=State.RUNNING,
                state_data=StateData(
                    entered_at=sd.entered_at,
                    current_member_id=sd.current_member_id,
                    current_member_name=sd.current_member_name,
                    session_extensions_s=new_extensions,
                    warning_played=False,  # re-arm for the new expiry
                    last_extend_at=now,
                ),
                actions=[
                    Buzz("success"),
                    ShowDisplay(
                        screen="running",
                        member_name=sd.current_member_name,
                        remaining_seconds=remaining,
                    ),
                ],
            )

        if isinstance(event, ButtonPressed):
            return _stop_session(now, sd, auto=False, config=config)

        if isinstance(event, Tick):
            elapsed = (now - sd.entered_at).total_seconds()
            if elapsed >= total_s:
                return _stop_session(now, sd, auto=True, config=config)
            remaining = max(0, int(total_s - elapsed))

            actions: list[Action] = []
            new_sd = sd
            if not sd.warning_played and remaining <= config.warning_remaining_s:
                actions.append(Buzz("warning"))
                new_sd = StateData(
                    entered_at=sd.entered_at,
                    current_member_id=sd.current_member_id,
                    current_member_name=sd.current_member_name,
                    session_extensions_s=sd.session_extensions_s,
                    warning_played=True,
                    last_extend_at=sd.last_extend_at,
                )
            actions.append(
                ShowDisplay(
                    screen="running",
                    member_name=sd.current_member_name,
                    remaining_seconds=remaining,
                )
            )
            return TransitionResult(
                state=State.RUNNING, state_data=new_sd, actions=actions
            )

        return _stay(state, sd)

    if state == State.ENROLLING:
        if isinstance(event, CardSwiped):
            return TransitionResult(
                state=State.IDLE,
                state_data=StateData(entered_at=now),
                actions=[
                    CompleteEnrollment(
                        pending_id=sd.pending_enrollment_id, uid=event.uid
                    ),
                    Buzz("enroll"),
                ]
                + _idle_actions(config),
            )
        if isinstance(event, Tick):
            if sd.enrollment_expires_at and now >= sd.enrollment_expires_at:
                return _to_idle(now, config)
            return _stay(state, sd)
        if isinstance(event, EnrollmentRequested):
            # Same enrollment we're already showing: nothing to do.
            if event.pending_id == sd.pending_enrollment_id:
                return _stay(state, sd)
            return _enter_enrolling(event)
        return _stay(state, sd)

    return _stay(state, sd)


def _idle_actions(config: SMConfig) -> list[Action]:
    return [
        Relay(on=False),
        LedMode("dim"),
        ShowDisplay(
            screen="idle", station_display_name=config.station_display_name
        ),
    ]


def _stay(state: State, sd: StateData) -> TransitionResult:
    return TransitionResult(state=state, state_data=sd, actions=[])


def _to_idle(now: datetime, config: SMConfig) -> TransitionResult:
    return TransitionResult(
        state=State.IDLE,
        state_data=StateData(entered_at=now),
        actions=_idle_actions(config),
    )


def _stop_session(
    now: datetime, sd: StateData, auto: bool, config: SMConfig
) -> TransitionResult:
    return TransitionResult(
        state=State.IDLE,
        state_data=StateData(entered_at=now),
        actions=[
            Relay(on=False),
            LogSessionEnd(
                auto_stopped=auto, extensions_seconds=sd.session_extensions_s
            ),
            Buzz("timeout" if auto else "stop"),
            LedMode("dim"),
            ShowDisplay(
                screen="idle", station_display_name=config.station_display_name
            ),
        ],
    )


def _enter_enrolling(event: EnrollmentRequested) -> TransitionResult:
    return TransitionResult(
        state=State.ENROLLING,
        state_data=StateData(
            entered_at=event.now,
            pending_enrollment_id=event.pending_id,
            pending_enrollment_name=event.name,
            enrollment_expires_at=event.expires_at,
        ),
        actions=[ShowDisplay(screen="enrolling", enrollment_name=event.name)],
    )
