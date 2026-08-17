"""OLED display shim.

Real backend: SparkFun Qwiic OLED 0.91" 128x32 (SSD1306) via luma.oled.
Simulate backend: framed text box on stdout.

Three text sizes so the 128x32 area is actually used:
  huge  (~22px) centered, for DENIED
  large (~15px) primary line, top half
  small (~11px) secondary line, bottom half

Falls back to PIL's default font if DejaVu isn't installed.
Renders only when the visible text changes, so a 4 Hz tick doesn't thrash I2C.
"""
from __future__ import annotations

import logging
import time
from typing import Any, Optional

logger = logging.getLogger(__name__)

LINE_HEIGHT = 8
CHAR_WIDTH = 6

_LARGE_MAX_CHARS = 14
_SMALL_MAX_CHARS = 20


class Display:
    def __init__(self):
        self._last_lines: Optional[list[tuple[str, str]]] = None

    def show(self, screen: str, **kwargs: Any) -> None:
        lines = render_lines(screen, kwargs)
        if lines == self._last_lines:
            return
        self._last_lines = lines
        self._render(lines)

    def _render(self, lines: list[tuple[str, str]]) -> None:
        raise NotImplementedError

    def close(self) -> None:
        pass


def _trunc(s: Optional[str], n: int) -> str:
    s = s or ""
    return s if len(s) <= n else s[: n - 1] + "."


def render_lines(screen: str, kwargs: dict) -> list[tuple[str, str]]:
    """(text, size) tuples for a screen. size in {huge, large, small}."""
    if screen == "idle":
        return [
            ("Press button", "large"),
            (_trunc(kwargs.get("station_display_name"), _SMALL_MAX_CHARS), "small"),
        ]

    if screen == "ready":
        station = _trunc(kwargs.get("station_display_name"), 12)
        remaining = int(kwargs.get("remaining_seconds") or 0)
        return [("Tap card", "large"), (f"{station}  {remaining}s", "small")]

    if screen == "denied":
        return [("DENIED", "huge")]

    if screen == "running":
        name = _trunc(kwargs.get("member_name"), _LARGE_MAX_CHARS)
        mm, ss = divmod(int(kwargs.get("remaining_seconds") or 0), 60)
        return [(name, "large"), (f"{mm:02d}:{ss:02d} left", "small")]

    if screen == "enrolling":
        return [
            ("ENROLL", "large"),
            (_trunc(kwargs.get("enrollment_name"), _SMALL_MAX_CHARS), "small"),
        ]

    if screen == "offline":
        return [("Sync error", "large"), ("using cache", "small")]

    return [(f"({screen})", "small")]


class _SimDisplay(Display):
    def __init__(self, width: int = 128, height: int = 32):
        super().__init__()
        self._width = max(20, width // CHAR_WIDTH)

    def _render(self, lines: list[tuple[str, str]]) -> None:
        marker = {"huge": "*", "large": "+", "small": " "}
        bar = "+" + "-" * (self._width + 2) + "+"
        print(bar)
        for text, size in lines:
            print(f"|{marker.get(size, ' ')}{text:<{self._width}}{marker.get(size, ' ')}|")
        print(bar)


class _OledDisplay(Display):
    def __init__(
        self, i2c_bus: int = 1, address: int = 0x3C, width: int = 128, height: int = 32
    ):
        from luma.core.interface.serial import i2c
        from luma.oled.device import ssd1306

        super().__init__()
        self._device = ssd1306(
            i2c(port=i2c_bus, address=address), width=width, height=height
        )
        self._width = width
        self._height = height
        self._fonts = _load_fonts()

    def _render(self, lines: list[tuple[str, str]]) -> None:
        from luma.core.render import canvas

        with canvas(self._device) as draw:
            if len(lines) == 1 and lines[0][1] == "huge":
                text, _ = lines[0]
                font = self._fonts["huge"]
                bbox = draw.textbbox((0, 0), text, font=font)
                x = max(0, (self._width - (bbox[2] - bbox[0])) // 2)
                y = max(0, (self._height - (bbox[3] - bbox[1])) // 2)
                draw.text((x, y), text, fill="white", font=font)
                return
            y = 0
            for text, size in lines:
                draw.text(
                    (0, y),
                    text,
                    fill="white",
                    font=self._fonts.get(size, self._fonts["small"]),
                )
                y = 17 if size == "large" else y + 12

    def close(self) -> None:
        try:
            self._device.cleanup()
        except Exception:
            pass


def _load_fonts():
    from PIL import ImageFont

    candidates = {
        "huge": ("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf", 22),
        "large": ("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf", 15),
        "small": ("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf", 11),
    }
    fonts = {}
    for name, (path, size) in candidates.items():
        try:
            fonts[name] = ImageFont.truetype(path, size)
        except (OSError, IOError):
            logger.warning("DejaVu font missing at %s; using default", path)
            fonts[name] = ImageFont.load_default()
    return fonts


def make(
    display_type: str,
    i2c_bus: int = 1,
    address: int = 0x3C,
    width: int = 128,
    height: int = 32,
) -> Display:
    if display_type == "simulate":
        return _SimDisplay(width, height)
    if display_type == "ssd1306":
        return _OledDisplay(i2c_bus, address, width, height)
    raise ValueError(f"unknown display type: {display_type!r}")


def _selftest() -> None:
    import sys

    d = make(sys.argv[1] if len(sys.argv) > 1 else "ssd1306")
    screens = [
        ("idle", {"station_display_name": "Laser Cutter 1"}),
        ("ready", {"station_display_name": "Laser Cutter 1", "remaining_seconds": 28}),
        ("denied", {}),
        ("running", {"member_name": "Alice", "remaining_seconds": 872}),
        ("enrolling", {"enrollment_name": "New Person"}),
    ]
    try:
        for name, kw in screens:
            print(f"--- {name} ---")
            d.show(name, **kw)
            time.sleep(2)
    finally:
        d.close()


if __name__ == "__main__":
    _selftest()
