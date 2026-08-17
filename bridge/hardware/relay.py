"""Mains relay shim (SparkFun IoT Power Relay).

Real backend: gpiozero OutputDevice driving the relay's control input.
Simulate backend: logs ON/OFF transitions.
"""
from __future__ import annotations

import logging
import time

logger = logging.getLogger(__name__)


class Relay:
    def set(self, on: bool) -> None:
        raise NotImplementedError

    def close(self) -> None:
        pass


class _SimRelay(Relay):
    def __init__(self):
        self._on = False

    def set(self, on: bool) -> None:
        if on != self._on:
            logger.info("SIM relay -> %s", "ON" if on else "OFF")
            self._on = on


class _GpioRelay(Relay):
    def __init__(self, pin: int, active_high: bool = True):
        from gpiozero import OutputDevice

        self._dev = OutputDevice(pin, active_high=active_high, initial_value=False)

    def set(self, on: bool) -> None:
        if on:
            self._dev.on()
        else:
            self._dev.off()

    def close(self) -> None:
        try:
            self._dev.off()
            self._dev.close()
        except Exception:
            pass


def make(relay_type: str = "gpio", gpio_pin: int = 26, active_high: bool = True) -> Relay:
    if relay_type == "simulate":
        return _SimRelay()
    try:
        return _GpioRelay(gpio_pin, active_high)
    except (ImportError, RuntimeError) as e:
        logger.warning("GPIO unavailable (%s); using simulate relay", e)
        return _SimRelay()


def _selftest() -> None:
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    import sys

    r = make(sys.argv[1] if len(sys.argv) > 1 else "gpio")
    try:
        for _ in range(3):
            print("relay ON")
            r.set(True)
            time.sleep(1)
            print("relay OFF")
            r.set(False)
            time.sleep(1)
    finally:
        r.close()


if __name__ == "__main__":
    _selftest()
