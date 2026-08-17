from __future__ import annotations

from datetime import timedelta

import pytest

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
    StateData,
    Tick,
    initial,
    transition,
)

CONFIG = SMConfig(
    station_display_name="Laser Cutter 1",
    ready_timeout_s=30,
    session_timeout_s=900,
    denied_display_s=2,
    extend_s=900,
    extend_cooldown_s=10,
    session_max_total_s=18000,
    warning_remaining_s=60,
)

ALICE = "aaaaaaaa-0000-0000-0000-000000000001"
BOB = "bbbbbbbb-0000-0000-0000-000000000002"


def find(actions, t):
    matches = [a for a in actions if isinstance(a, t)]
    assert matches, f"expected a {t.__name__} in {actions}"
    return matches[0]


def has(actions, target):
    return any(a == target for a in actions)


def _to_ready(now):
    r0 = initial(now, CONFIG)
    return transition(r0.state, r0.state_data, ButtonPressed(now=now), CONFIG)


def _to_running(now):
    r = _to_ready(now)
    swipe = CardSwiped(
        now=now, uid="043f6afba2", member_id=ALICE, member_name="Alice", authorized=True
    )
    return transition(r.state, r.state_data, swipe, CONFIG)


# ---------------------------------------------------------------------------
# initial / IDLE
# ---------------------------------------------------------------------------

def test_initial_state_is_idle_and_safe(now):
    r = initial(now, CONFIG)
    assert r.state == State.IDLE
    assert has(r.actions, Relay(on=False))
    assert has(r.actions, LedMode("dim"))
    assert find(r.actions, ShowDisplay).screen == "idle"


def test_idle_card_swipe_does_nothing(now):
    r0 = initial(now, CONFIG)
    swipe = CardSwiped(
        now=now, uid="043f6afba2", member_id=ALICE, member_name="Alice", authorized=True
    )
    r = transition(r0.state, r0.state_data, swipe, CONFIG)
    assert r.state == State.IDLE
    assert r.actions == []


def test_idle_button_enters_ready(now):
    r = _to_ready(now)
    assert r.state == State.READY
    assert has(r.actions, LedMode("solid"))
    show = find(r.actions, ShowDisplay)
    assert show.screen == "ready"
    assert show.remaining_seconds == 30


def test_idle_tick_is_quiet(now):
    r0 = initial(now, CONFIG)
    r = transition(r0.state, r0.state_data, Tick(now=now + timedelta(seconds=5)), CONFIG)
    assert r.state == State.IDLE
    assert r.actions == []


# ---------------------------------------------------------------------------
# READY
# ---------------------------------------------------------------------------

def test_ready_authorized_card_starts_session(now):
    r = _to_running(now)
    assert r.state == State.RUNNING
    assert has(r.actions, Relay(on=True))
    assert has(r.actions, Buzz("start"))
    start = find(r.actions, LogSessionStart)
    assert start.member_id == ALICE
    assert start.member_name == "Alice"
    show = find(r.actions, ShowDisplay)
    assert show.screen == "running"
    assert show.remaining_seconds == 900


def test_ready_unauthorized_card_denies(now):
    r = _to_ready(now)
    swipe = CardSwiped(
        now=now + timedelta(seconds=2),
        uid="unknown",
        member_id=None,
        member_name=None,
        authorized=False,
    )
    r2 = transition(r.state, r.state_data, swipe, CONFIG)
    assert r2.state == State.DENIED
    assert has(r2.actions, Buzz("denied"))
    assert find(r2.actions, ShowDisplay).screen == "denied"


def test_ready_button_cancels(now):
    r = _to_ready(now)
    r2 = transition(
        r.state, r.state_data, ButtonPressed(now=now + timedelta(seconds=3)), CONFIG
    )
    assert r2.state == State.IDLE
    assert find(r2.actions, ShowDisplay).screen == "idle"


def test_ready_counts_down_then_times_out(now):
    r = _to_ready(now)
    mid = transition(
        r.state, r.state_data, Tick(now=now + timedelta(seconds=29)), CONFIG
    )
    assert mid.state == State.READY
    assert find(mid.actions, ShowDisplay).remaining_seconds == 1

    out = transition(
        r.state, r.state_data, Tick(now=now + timedelta(seconds=30)), CONFIG
    )
    assert out.state == State.IDLE


# ---------------------------------------------------------------------------
# DENIED
# ---------------------------------------------------------------------------

def test_denied_clears_after_display_window(now):
    r = _to_ready(now)
    denied = transition(
        r.state,
        r.state_data,
        CardSwiped(
            now=now + timedelta(seconds=1),
            uid="x",
            member_id=None,
            member_name=None,
            authorized=False,
        ),
        CONFIG,
    )
    still = transition(
        denied.state, denied.state_data, Tick(now=now + timedelta(seconds=2)), CONFIG
    )
    assert still.state == State.DENIED
    cleared = transition(
        denied.state, denied.state_data, Tick(now=now + timedelta(seconds=4)), CONFIG
    )
    assert cleared.state == State.IDLE


# ---------------------------------------------------------------------------
# RUNNING: stop, timeout, extend
# ---------------------------------------------------------------------------

def test_running_button_stops_session(now):
    r = _to_running(now)
    r2 = transition(
        r.state, r.state_data, ButtonPressed(now=now + timedelta(minutes=2)), CONFIG
    )
    assert r2.state == State.IDLE
    assert has(r2.actions, Relay(on=False))
    assert has(r2.actions, Buzz("stop"))
    end = find(r2.actions, LogSessionEnd)
    assert end.auto_stopped is False
    assert end.extensions_seconds == 0


def test_running_auto_stops_at_timeout(now):
    r = _to_running(now)
    r2 = transition(
        r.state, r.state_data, Tick(now=now + timedelta(seconds=900)), CONFIG
    )
    assert r2.state == State.IDLE
    assert has(r2.actions, Buzz("timeout"))
    assert find(r2.actions, LogSessionEnd).auto_stopped is True


def test_running_ignores_other_members_card(now):
    r = _to_running(now)
    r2 = transition(
        r.state,
        r.state_data,
        CardSwiped(
            now=now + timedelta(seconds=30),
            uid="043f6b3ee2",
            member_id=BOB,
            member_name="Bob",
            authorized=True,
        ),
        CONFIG,
    )
    assert r2.state == State.RUNNING
    assert r2.actions == []
    assert r2.state_data.session_extensions_s == 0


def test_running_ignores_unknown_card(now):
    r = _to_running(now)
    r2 = transition(
        r.state,
        r.state_data,
        CardSwiped(
            now=now + timedelta(seconds=30),
            uid="nope",
            member_id=None,
            member_name=None,
            authorized=False,
        ),
        CONFIG,
    )
    assert r2.state == State.RUNNING
    assert r2.actions == []


def test_active_member_retap_extends(now):
    r = _to_running(now)
    r2 = transition(
        r.state,
        r.state_data,
        CardSwiped(
            now=now + timedelta(seconds=60),
            uid="043f6afba2",
            member_id=ALICE,
            member_name="Alice",
            authorized=True,
        ),
        CONFIG,
    )
    assert r2.state == State.RUNNING
    assert r2.state_data.session_extensions_s == 900
    assert has(r2.actions, Buzz("success"))
    # 900 base + 900 extension - 60 elapsed
    assert find(r2.actions, ShowDisplay).remaining_seconds == 1740


def test_extend_cooldown_blocks_rapid_retaps(now):
    r = _to_running(now)
    first = transition(
        r.state,
        r.state_data,
        CardSwiped(
            now=now + timedelta(seconds=60),
            uid="u",
            member_id=ALICE,
            member_name="Alice",
            authorized=True,
        ),
        CONFIG,
    )
    blocked = transition(
        first.state,
        first.state_data,
        CardSwiped(
            now=now + timedelta(seconds=65),
            uid="u",
            member_id=ALICE,
            member_name="Alice",
            authorized=True,
        ),
        CONFIG,
    )
    assert blocked.state_data.session_extensions_s == 900
    assert blocked.actions == []

    allowed = transition(
        first.state,
        first.state_data,
        CardSwiped(
            now=now + timedelta(seconds=75),
            uid="u",
            member_id=ALICE,
            member_name="Alice",
            authorized=True,
        ),
        CONFIG,
    )
    assert allowed.state_data.session_extensions_s == 1800


def test_extend_grants_partial_up_to_cap(now):
    """Near the 5 hr ceiling we grant only what's left, not a full block."""
    near_cap = StateData(
        entered_at=now,
        current_member_id=ALICE,
        current_member_name="Alice",
        session_extensions_s=17100 - 60,  # 60s of headroom remains
    )
    r = transition(
        State.RUNNING,
        near_cap,
        CardSwiped(
            now=now + timedelta(seconds=1),
            uid="u",
            member_id=ALICE,
            member_name="Alice",
            authorized=True,
        ),
        CONFIG,
    )
    assert r.state_data.session_extensions_s == 17100
    assert has(r.actions, Buzz("success"))


def test_extend_at_cap_buzzes_limit(now):
    at_cap = StateData(
        entered_at=now,
        current_member_id=ALICE,
        current_member_name="Alice",
        session_extensions_s=17100,
    )
    r = transition(
        State.RUNNING,
        at_cap,
        CardSwiped(
            now=now + timedelta(seconds=1),
            uid="u",
            member_id=ALICE,
            member_name="Alice",
            authorized=True,
        ),
        CONFIG,
    )
    assert r.state_data.session_extensions_s == 17100
    assert has(r.actions, Buzz("limit"))
    assert not has(r.actions, Buzz("success"))


def test_extended_session_uses_new_total_for_auto_stop(now):
    r = _to_running(now)
    extended = transition(
        r.state,
        r.state_data,
        CardSwiped(
            now=now + timedelta(seconds=60),
            uid="u",
            member_id=ALICE,
            member_name="Alice",
            authorized=True,
        ),
        CONFIG,
    )
    # Original expiry passes without stopping.
    still = transition(
        extended.state,
        extended.state_data,
        Tick(now=now + timedelta(seconds=901)),
        CONFIG,
    )
    assert still.state == State.RUNNING
    # New expiry (1800s) does stop it.
    stopped = transition(
        extended.state,
        extended.state_data,
        Tick(now=now + timedelta(seconds=1800)),
        CONFIG,
    )
    assert stopped.state == State.IDLE
    assert find(stopped.actions, LogSessionEnd).extensions_seconds == 900


# ---------------------------------------------------------------------------
# RUNNING: warning buzz
# ---------------------------------------------------------------------------

def test_warning_fires_once_near_expiry(now):
    r = _to_running(now)
    warned = transition(
        r.state, r.state_data, Tick(now=now + timedelta(seconds=841)), CONFIG
    )
    assert has(warned.actions, Buzz("warning"))
    assert warned.state_data.warning_played is True

    again = transition(
        warned.state, warned.state_data, Tick(now=now + timedelta(seconds=842)), CONFIG
    )
    assert not has(again.actions, Buzz("warning"))


def test_extend_rearms_the_warning(now):
    r = _to_running(now)
    warned = transition(
        r.state, r.state_data, Tick(now=now + timedelta(seconds=841)), CONFIG
    )
    extended = transition(
        warned.state,
        warned.state_data,
        CardSwiped(
            now=now + timedelta(seconds=842),
            uid="u",
            member_id=ALICE,
            member_name="Alice",
            authorized=True,
        ),
        CONFIG,
    )
    assert extended.state_data.warning_played is False
    # And it fires again as the new expiry approaches.
    rewarned = transition(
        extended.state,
        extended.state_data,
        Tick(now=now + timedelta(seconds=1745)),
        CONFIG,
    )
    assert has(rewarned.actions, Buzz("warning"))


# ---------------------------------------------------------------------------
# ENROLLING
# ---------------------------------------------------------------------------

def test_enrollment_request_shows_prompt(now):
    r0 = initial(now, CONFIG)
    r = transition(
        r0.state,
        r0.state_data,
        EnrollmentRequested(
            now=now,
            pending_id="e1",
            name="New Person",
            expires_at=now + timedelta(seconds=60),
        ),
        CONFIG,
    )
    assert r.state == State.ENROLLING
    show = find(r.actions, ShowDisplay)
    assert show.screen == "enrolling"
    assert show.enrollment_name == "New Person"


def test_enrolling_card_captures_uid(now):
    r0 = initial(now, CONFIG)
    enrolling = transition(
        r0.state,
        r0.state_data,
        EnrollmentRequested(
            now=now, pending_id="e1", name="X", expires_at=now + timedelta(seconds=60)
        ),
        CONFIG,
    )
    captured = transition(
        enrolling.state,
        enrolling.state_data,
        CardSwiped(
            now=now + timedelta(seconds=5),
            uid="043f6afba2",
            member_id=None,
            member_name=None,
            authorized=False,
        ),
        CONFIG,
    )
    assert captured.state == State.IDLE
    ce = find(captured.actions, CompleteEnrollment)
    assert ce.pending_id == "e1"
    assert ce.uid == "043f6afba2"
    assert has(captured.actions, Buzz("enroll"))


def test_enrollment_expires(now):
    r0 = initial(now, CONFIG)
    enrolling = transition(
        r0.state,
        r0.state_data,
        EnrollmentRequested(
            now=now, pending_id="e1", name="X", expires_at=now + timedelta(seconds=60)
        ),
        CONFIG,
    )
    r = transition(
        enrolling.state,
        enrolling.state_data,
        Tick(now=now + timedelta(seconds=60)),
        CONFIG,
    )
    assert r.state == State.IDLE


def test_repeated_same_enrollment_request_is_idle(now):
    """Sync re-reports the same pending row every cycle; don't re-render."""
    r0 = initial(now, CONFIG)
    first = transition(
        r0.state,
        r0.state_data,
        EnrollmentRequested(
            now=now, pending_id="e1", name="X", expires_at=now + timedelta(seconds=60)
        ),
        CONFIG,
    )
    again = transition(
        first.state,
        first.state_data,
        EnrollmentRequested(
            now=now + timedelta(seconds=5),
            pending_id="e1",
            name="X",
            expires_at=now + timedelta(seconds=60),
        ),
        CONFIG,
    )
    assert again.state == State.ENROLLING
    assert again.actions == []


def test_new_enrollment_supersedes_previous(now):
    r0 = initial(now, CONFIG)
    first = transition(
        r0.state,
        r0.state_data,
        EnrollmentRequested(
            now=now, pending_id="e1", name="Alice", expires_at=now + timedelta(seconds=60)
        ),
        CONFIG,
    )
    second = transition(
        first.state,
        first.state_data,
        EnrollmentRequested(
            now=now + timedelta(seconds=5),
            pending_id="e2",
            name="Bob",
            expires_at=now + timedelta(seconds=65),
        ),
        CONFIG,
    )
    assert second.state_data.pending_enrollment_id == "e2"
    assert find(second.actions, ShowDisplay).enrollment_name == "Bob"
