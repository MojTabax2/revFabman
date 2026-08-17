"""Pushbutton shim (Adafruit 559 metal button: NO1 -> GPIO, C1 -> GND, LED ring).

Real backend: gpiozero Button + PWMLED. Needs the lgpio pin factory on
Bookworm -- RPi.GPIO can't do edge detection there.

Simulate backend: reads "press" tokens from _sim/button_in.txt.

was_pressed() is edge-buffered: True if a press happened since the last call.
"""
from __future__ import annotations

import logging
import time
from pathlib import Path
from threading import Lock
from typing import Optional

logger = logging.getLogger(__name__)

SIM_INPUT_FILE = Path("_sim/button_in.txt")

_LED_LEVELS = {"off": 0.0, "dim": 0.15, "solid": 1.0}


class Button:
    def was_pressed(self) -> bool:
        raise NotImplementedError

    def set_led(self, mode: str) -> None:
        raise NotImplementedError

    def close(self) -> None:
        pass


class _SimButton(Button):
    def __init__(self):
        SIM_INPUT_FILE.parent.mkdir(parents=True, exist_ok=True)
        if not SIM_INPUT_FILE.exists():
            SIM_INPUT_FILE.write_text("")
        self._led_mode = "off"

    def was_pressed(self) -> bool:
        try:
            content = SIM_INPUT_FILE.read_text()
        except FileNotFoundError:
            return False
        if not content.strip():
            return False
        SIM_INPUT_FILE.write_text("")
        logger.info("SIM button -> press")
        return True

    def set_led(self, mode: str) -> None:
        if mode not in _LED_LEVELS:
            raise ValueError(f"unknown LED mode {mode!r}")
        if mode != self._led_mode:
            logger.info("SIM button LED -> %s", mode)
            self._led_mode = mode


class _GpioButton(Button):
    def __init__(self, pin: int, pull_up: bool = True, led_pin: Optional[int] = None):
        from gpiozero import Button as GZButton

        self._lock = Lock()
        self._pressed = False
        self._btn = GZButton(pin, pull_up=pull_up, bounce_time=0.05)
        self._btn.when_pressed = self._on_press

        self._led = None
        if led_pin is not None:
            from gpiozero import PWMLED

            self._led = PWMLED(led_pin)
            self._led.value = 0.0

    def _on_press(self) -> None:
        with self._lock:
            self._pressed = True

    def was_pressed(self) -> bool:
        with self._lock:
            if self._pressed:
                self._pressed = False
                return True
            return False

    def set_led(self, mode: str) -> None:
        if self._led is None:
            return
        if mode not in _LED_LEVELS:
            raise ValueError(f"unknown LED mode {mode!r}")
        self._led.value = _LED_LEVELS[mode]

    def close(self) -> None:
        try:
            self._btn.close()
        except Exception:
            pass
        if self._led:
            try:
                self._led.close()
            except Exception:
                pass


def make(
    button_type: str = "gpio",
    gpio_pin: int = 24,
    pull: str = "up",
    led_pin: Optional[int] = 16,
) -> Button:
    if button_type == "simulate":
        return _SimButton()
    try:
        return _GpioButton(gpio_pin, pull_up=(pull == "up"), led_pin=led_pin)
    except (ImportError, RuntimeError) as e:
        logger.warning("GPIO unavailable (%s); using simulate button", e)
        return _SimButton()


def _selftest() -> None:
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    import sys

    b = make(sys.argv[1] if len(sys.argv) > 1 else "gpio")
    print(f"Polling button 30s. Press it (or write 'press' to {SIM_INPUT_FILE}).")
    b.set_led("dim")
    end = time.time() + 30
    try:
        while time.time() < end:
            if b.was_pressed():
                print("  press!")
                b.set_led("solid")
                time.sleep(0.5)
                b.set_led("dim")
            time.sleep(0.05)
    except KeyboardInterrupt:
        pass
    finally:
        b.set_led("off")
        b.close()


if __name__ == "__main__":
    _selftest()
