from __future__ import annotations

import pytest

from hardware import reader
from hardware.reader import ENTER, KEYCODE_TO_CHAR, KeystrokeFramer


# ---------------------------------------------------------------------------
# Keycode map -- must decode the hex our OMNIKEY actually emits.
# ---------------------------------------------------------------------------

def _decode(codes: list[int]) -> str:
    return "".join(KEYCODE_TO_CHAR[c] for c in codes)


def test_keycode_map_decodes_digits():
    # number-row 1..9 then 0
    assert _decode([2, 3, 4, 5, 6, 7, 8, 9, 10, 11]) == "1234567890"


def test_keycode_map_decodes_hex_letters():
    # a b c d e f
    assert _decode([30, 48, 46, 32, 18, 33]) == "abcdef"


def test_keycode_map_decodes_a_real_badge():
    """The actual value captured from a work ID: 043f6afba2"""
    codes = [11, 5, 4, 33, 7, 30, 33, 48, 30, 3]
    assert _decode(codes) == "043f6afba2"


def test_keycode_map_enter_variants():
    assert KEYCODE_TO_CHAR[28] == ENTER
    assert KEYCODE_TO_CHAR[96] == ENTER


def test_keycode_map_keypad_digits():
    assert _decode([82, 79, 80, 81, 75, 76, 77, 71, 72, 73]) == "0123456789"


# ---------------------------------------------------------------------------
# Framing -- our reader sends NO terminator, so timing does the work.
# ---------------------------------------------------------------------------

def _feed(framer: KeystrokeFramer, text: str, start: float, step: float = 0.005):
    """Feed characters as a burst, returning everything completed."""
    out = []
    ts = start
    for ch in text:
        out.extend(framer.feed(ch, ts))
        ts += step
    return out, ts


def test_burst_with_no_terminator_completes_on_idle():
    f = KeystrokeFramer(gap_seconds=0.15)
    completed, ts = _feed(f, "043f6afba2", start=1.0)
    # Nothing completes while characters are still arriving.
    assert completed == []
    # Still nothing if we check before the gap elapses.
    assert f.flush_if_idle(ts + 0.05) == []
    # Once the reader goes quiet, the read finalizes.
    assert f.flush_if_idle(ts + 0.2) == ["043f6afba2"]


def test_two_taps_separated_by_gap_yield_two_uids():
    f = KeystrokeFramer(gap_seconds=0.15)
    _, ts = _feed(f, "043f6afba2", start=1.0)
    # Second tap starts well after the gap; feeding it finalizes the first.
    completed, ts2 = _feed(f, "043f6b3ee2", start=ts + 3.0)
    assert completed == ["043f6afba2"]
    assert f.flush_if_idle(ts2 + 0.2) == ["043f6b3ee2"]


def test_three_taps_of_same_card_yield_three_reads():
    """Matches the captured behaviour: 043f6afba2 x3 with no terminators."""
    f = KeystrokeFramer(gap_seconds=0.15)
    uids = []
    ts = 1.0
    for _ in range(3):
        completed, ts = _feed(f, "043f6afba2", start=ts + 2.0)
        uids.extend(completed)
    uids.extend(f.flush_if_idle(ts + 0.2))
    assert uids == ["043f6afba2"] * 3


def test_enter_terminator_completes_immediately():
    """If the reader is later configured to append Enter, that ends a read too."""
    f = KeystrokeFramer(gap_seconds=0.15)
    completed, ts = _feed(f, "043f6afba2", start=1.0)
    assert completed == []
    assert f.feed(ENTER, ts) == ["043f6afba2"]
    # Buffer is empty afterwards.
    assert f.flush_if_idle(ts + 1.0) == []


def test_enter_on_empty_buffer_is_noop():
    f = KeystrokeFramer()
    assert f.feed(ENTER, 1.0) == []


def test_max_length_guard_forces_completion():
    f = KeystrokeFramer(gap_seconds=0.15, max_length=4)
    completed, _ = _feed(f, "abcdef", start=1.0)
    # First 4 chars flush as one read; the rest stay buffered.
    assert completed == ["abcd"]


def test_reset_discards_partial_read():
    f = KeystrokeFramer(gap_seconds=0.15)
    _feed(f, "043f6a", start=1.0)
    f.reset()
    assert f.flush_if_idle(100.0) == []


def test_flush_on_empty_framer_is_noop():
    f = KeystrokeFramer()
    assert f.flush_if_idle(123.0) == []


# ---------------------------------------------------------------------------
# Simulate backend (used by bridge tests on any OS)
# ---------------------------------------------------------------------------

def test_sim_reader_returns_none_when_empty(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    r = reader.make("simulate")
    assert r.poll() is None


def test_sim_reader_returns_queued_uids(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    r = reader.make("simulate")
    reader.SIM_INPUT_FILE.write_text("043f6afba2\n043f6b3ee2\n")
    assert r.poll() == "043f6afba2"
    assert r.poll() == "043f6b3ee2"
    assert r.poll() is None


def test_sim_reader_drain_discards_all(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    r = reader.make("simulate")
    reader.SIM_INPUT_FILE.write_text("a\nb\nc\n")
    assert r.drain() == 3
    assert r.poll() is None


def test_make_rejects_unknown_type():
    with pytest.raises(ValueError):
        reader.make("bogus")
