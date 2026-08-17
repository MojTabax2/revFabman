"""Piezo buzzer shim.

Real backend: gpiozero PWMOutputDevice.
Simulate backend: logs the pattern name.

Patterns are (freq_hz | None, duration_s) steps; None means a silent gap.
Playback runs on a worker thread so the bridge loop never blocks on audio.
"""
from __future__ import annotations

import logging
import queue
import threading
import time
from typing import Optional

logger = logging.getLogger(__name__)

# Frequencies live in the 2.6-4.4 kHz band because the PS1240 piezo resonates
# around 4 kHz and is only efficient from roughly 2-10 kHz. Earlier versions used
# 400-1100 Hz, which is well below resonance and audibly much quieter for the
# same drive voltage.
#
# What distinguishes one pattern from another is mostly rhythm (how many pulses,
# how long) plus the direction of pitch change -- both preserved here. The most
# important cues (success, warning) sit closest to resonance so they carry over
# machine noise.
#
# Use `python -m hardware.buzzer sweep` to find the loudest frequency for your
# specific unit and mounting; resonance shifts once the element is fixed to a
# panel or enclosure.
_LOW = 2600
_MID = 3200
_WARN = 3800
_HIGH = 4000
_TOP = 4400

PATTERNS: dict[str, list[tuple[Optional[int], float]]] = {
    "success": [(_HIGH, 0.10)],
    "denied":  [(_LOW, 0.10), (None, 0.08), (_LOW, 0.10)],
    "start":   [(_MID, 0.08), (_HIGH, 0.12)],
    "stop":    [(_HIGH, 0.08), (_MID, 0.12)],
    "enroll":  [(_MID, 0.06), (_WARN, 0.06), (_TOP, 0.10)],
    "timeout": [(_LOW, 0.15), (None, 0.10), (_LOW, 0.15), (None, 0.10), (_LOW, 0.15)],
    "warning": [(_WARN, 0.10), (None, 0.10), (_WARN, 0.10), (None, 0.10), (_WARN, 0.10)],
    "limit":   [(3000, 0.20)],
}


class Buzzer:
    def play(self, pattern: str) -> None:
        raise NotImplementedError

    def close(self) -> None:
        pass


class _SimBuzzer(Buzzer):
    def play(self, pattern: str) -> None:
        if pattern not in PATTERNS:
            raise ValueError(f"unknown buzzer pattern {pattern!r}")
        logger.info("SIM buzzer -> %s", pattern)


class _PwmBuzzer(Buzzer):
    """Queues patterns to a worker thread; play() returns immediately."""

    def __init__(self, pin: int):
        from gpiozero import PWMOutputDevice

        self._dev = PWMOutputDevice(pin, frequency=1000, initial_value=0)
        self._queue: queue.Queue[str] = queue.Queue()
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._worker, daemon=True)
        self._thread.start()

    def _worker(self) -> None:
        while not self._stop.is_set():
            try:
                pattern = self._queue.get(timeout=0.2)
            except queue.Empty:
                continue
            for freq, dur in PATTERNS.get(pattern, []):
                if self._stop.is_set():
                    break
                if freq is None:
                    self._dev.value = 0
                else:
                    self._dev.frequency = freq
                    self._dev.value = 0.5
                time.sleep(dur)
            self._dev.value = 0

    def play(self, pattern: str) -> None:
        if pattern not in PATTERNS:
            raise ValueError(f"unknown buzzer pattern {pattern!r}")
        self._queue.put(pattern)

    def close(self) -> None:
        self._stop.set()
        self._thread.join(timeout=1.0)
        try:
            self._dev.value = 0
            self._dev.close()
        except Exception:
            pass


def make(buzzer_type: str = "gpio", gpio_pin: int = 13) -> Buzzer:
    if buzzer_type == "simulate":
        return _SimBuzzer()
    try:
        return _PwmBuzzer(gpio_pin)
    except (ImportError, RuntimeError) as e:
        logger.warning("GPIO unavailable (%s); using simulate buzzer", e)
        return _SimBuzzer()


def sweep(pin: int = 13, start_hz: int = 1000, end_hz: int = 6000, step_hz: int = 250) -> None:
    """Play a rising series of tones so you can hear which is loudest.

    A piezo's resonant peak is sharp and moves once the element is mounted, so
    the only reliable way to tune it is by ear on the finished assembly. Note
    the loudest frequency, then set the constants at the top of this file.
    """
    from gpiozero import PWMOutputDevice

    dev = PWMOutputDevice(pin, frequency=start_hz, initial_value=0)
    print(f"Sweeping {start_hz}-{end_hz} Hz on GPIO {pin}. Listen for the peak.")
    try:
        for freq in range(start_hz, end_hz + 1, step_hz):
            print(f"  {freq} Hz", flush=True)
            dev.frequency = freq
            dev.value = 0.5
            time.sleep(0.6)
            dev.value = 0
            time.sleep(0.15)
    except KeyboardInterrupt:
        pass
    finally:
        dev.value = 0
        dev.close()
    print("\nLoudest frequency -> set _HIGH near it, and the others relative.")


def _selftest() -> None:
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    import sys

    arg = sys.argv[1] if len(sys.argv) > 1 else "gpio"

    if arg == "sweep":
        sweep()
        return

    b = make(arg)
    try:
        for name in PATTERNS:
            print(f"playing: {name}")
            b.play(name)
            time.sleep(1.0)
    finally:
        b.close()


if __name__ == "__main__":
    _selftest()
