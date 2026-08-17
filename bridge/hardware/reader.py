"""RFID reader shim.

Real backend: HID OMNIKEY 5427CK in keyboard-wedge mode, read via evdev.
The reader enumerates as a USB keyboard and "types" the card id. We grab the
input device *exclusively* so card numbers never leak to the Pi's console.

Our unit emits a fixed-length hex string with NO terminator (verified: one tap
gives e.g. '043f6afba2', three taps concatenate with no newline). So reads are
framed by keystroke timing -- a burst of characters followed by an idle gap is
one card read. An Enter also ends a read, in case the reader is later
configured to send one.

Simulate backend: reads UIDs from _sim/reader_in.txt (one per line, top consumed).
"""
from __future__ import annotations

import logging
import threading
import time
from pathlib import Path
from queue import Empty, Queue
from typing import Optional

logger = logging.getLogger(__name__)

SIM_INPUT_FILE = Path("_sim/reader_in.txt")

# A card's characters arrive within a few ms of each other; taps are seconds
# apart. 150 ms cleanly separates them.
DEFAULT_GAP_SECONDS = 0.15
# Safety valve so a stuck key can't grow the buffer without bound.
MAX_UID_LENGTH = 64
# Drop a repeat of the same uid inside this window (card left sitting on the reader).
DEDUP_WINDOW_S = 2.0

ENTER = "\n"

# Linux input event codes (stable kernel ABI -- hardcoded so this map is
# testable without evdev installed). See linux/input-event-codes.h
KEYCODE_TO_CHAR: dict[int, str] = {
    # number row
    2: "1", 3: "2", 4: "3", 5: "4", 6: "5",
    7: "6", 8: "7", 9: "8", 10: "9", 11: "0",
    # letters
    16: "q", 17: "w", 18: "e", 19: "r", 20: "t",
    21: "y", 22: "u", 23: "i", 24: "o", 25: "p",
    30: "a", 31: "s", 32: "d", 33: "f", 34: "g",
    35: "h", 36: "j", 37: "k", 38: "l",
    44: "z", 45: "x", 46: "c", 47: "v", 48: "b",
    49: "n", 50: "m",
    # keypad digits
    71: "7", 72: "8", 73: "9",
    75: "4", 76: "5", 77: "6",
    79: "1", 80: "2", 81: "3", 82: "0",
    # enter (main + keypad)
    28: ENTER, 96: ENTER,
}


class KeystrokeFramer:
    """Assembles keystrokes into card reads.

    Pure logic, no evdev -- so the framing rules are unit-testable anywhere.
    Feed characters with monotonic timestamps; completed UIDs come back out.
    """

    def __init__(
        self,
        gap_seconds: float = DEFAULT_GAP_SECONDS,
        max_length: int = MAX_UID_LENGTH,
    ):
        self.gap_seconds = gap_seconds
        self.max_length = max_length
        self._buf: list[str] = []
        self._last_ts: Optional[float] = None

    def feed(self, char: str, ts: float) -> list[str]:
        """Feed one decoded character. Returns any UIDs completed by it."""
        out: list[str] = []

        # A long gap means the buffered characters belonged to an earlier read.
        if self._buf and self._last_ts is not None and (ts - self._last_ts) > self.gap_seconds:
            done = self._finish()
            if done:
                out.append(done)

        if char == ENTER:
            done = self._finish()
            if done:
                out.append(done)
            return out

        self._buf.append(char)
        self._last_ts = ts

        if len(self._buf) >= self.max_length:
            done = self._finish()
            if done:
                out.append(done)
        return out

    def flush_if_idle(self, now: float) -> list[str]:
        """Finalize a buffered read once the reader has gone quiet."""
        if self._buf and self._last_ts is not None and (now - self._last_ts) > self.gap_seconds:
            done = self._finish()
            if done:
                return [done]
        return []

    def reset(self) -> None:
        self._buf.clear()
        self._last_ts = None

    def _finish(self) -> Optional[str]:
        value = "".join(self._buf)
        self._buf.clear()
        self._last_ts = None
        return value or None


class Reader:
    def poll(self) -> Optional[str]:
        raise NotImplementedError

    def drain(self) -> int:
        """Discard buffered reads and reset dedup. Returns how many were dropped."""
        return 0

    def close(self) -> None:
        pass


class _SimReader(Reader):
    """File-driven reader for development and tests (no hardware)."""

    def __init__(self):
        SIM_INPUT_FILE.parent.mkdir(parents=True, exist_ok=True)
        if not SIM_INPUT_FILE.exists():
            SIM_INPUT_FILE.write_text("")

    def poll(self) -> Optional[str]:
        try:
            content = SIM_INPUT_FILE.read_text()
        except FileNotFoundError:
            return None
        lines = [ln.strip() for ln in content.splitlines() if ln.strip()]
        if not lines:
            return None
        uid = lines[0]
        SIM_INPUT_FILE.write_text("\n".join(lines[1:]) + ("\n" if lines[1:] else ""))
        logger.info("SIM reader -> UID %s", uid)
        return uid

    def drain(self) -> int:
        try:
            content = SIM_INPUT_FILE.read_text()
        except FileNotFoundError:
            return 0
        count = len([ln for ln in content.splitlines() if ln.strip()])
        if count:
            SIM_INPUT_FILE.write_text("")
            logger.info("SIM reader drained %d queued read(s)", count)
        return count


class _USBKeyboardReader(Reader):
    """OMNIKEY (or any keyboard-wedge reader) via evdev.

    Grabs the input device exclusively, so keystrokes go only to this process
    and never reach a console/login prompt.
    """

    def __init__(
        self,
        device_name: str = "OMNIKEY",
        gap_seconds: float = DEFAULT_GAP_SECONDS,
        dedup_window_s: float = DEDUP_WINDOW_S,
    ):
        import evdev  # lazy: Linux only

        self._evdev = evdev
        self._device_name = device_name
        self._dedup_window_s = dedup_window_s

        self._device = self._find_device(device_name)
        self._device.grab()
        logger.info("Grabbed reader %r at %s", self._device.name, self._device.path)

        self._framer = KeystrokeFramer(gap_seconds=gap_seconds)
        self._queue: Queue[str] = Queue()
        self._lock = threading.Lock()
        self._last_uid: Optional[str] = None
        self._last_uid_at: float = 0.0

        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._read_loop, daemon=True)
        self._thread.start()

    def _find_device(self, name_substr: str):
        evdev = self._evdev
        matches = []
        for path in evdev.list_devices():
            dev = evdev.InputDevice(path)
            if name_substr.lower() in dev.name.lower():
                matches.append(dev)
            else:
                dev.close()
        if not matches:
            available = []
            for path in evdev.list_devices():
                dev = evdev.InputDevice(path)
                available.append(f"{dev.path}: {dev.name}")
                dev.close()
            raise RuntimeError(
                f"No input device matching {name_substr!r}. Available:\n  "
                + "\n  ".join(available or ["(none)"])
            )
        # Keep the first match, release the rest.
        for extra in matches[1:]:
            extra.close()
        return matches[0]

    def _read_loop(self) -> None:
        import select

        evdev = self._evdev
        fd = self._device.fd
        while not self._stop.is_set():
            try:
                readable, _, _ = select.select([fd], [], [], 0.05)
                now = time.monotonic()
                if readable:
                    for event in self._device.read():
                        if event.type != evdev.ecodes.EV_KEY:
                            continue
                        if event.value != 1:  # keydown only
                            continue
                        char = KEYCODE_TO_CHAR.get(event.code)
                        if char is None:
                            continue
                        with self._lock:
                            for uid in self._framer.feed(char, now):
                                self._emit(uid, now)
                else:
                    with self._lock:
                        for uid in self._framer.flush_if_idle(now):
                            self._emit(uid, now)
            except OSError as e:
                logger.warning("Reader device error: %s", e)
                time.sleep(0.5)
            except Exception:
                logger.exception("Reader loop error")
                time.sleep(0.5)

    def _emit(self, uid: str, now: float) -> None:
        """Queue a completed read unless it's a repeat inside the dedup window.

        Caller must hold self._lock.
        """
        if uid == self._last_uid and (now - self._last_uid_at) < self._dedup_window_s:
            logger.debug("Deduped repeat read %s", uid)
            return
        self._last_uid = uid
        self._last_uid_at = now
        self._queue.put(uid)
        logger.info("Reader -> UID %s", uid)

    def poll(self) -> Optional[str]:
        try:
            return self._queue.get_nowait()
        except Empty:
            return None

    def drain(self) -> int:
        count = 0
        with self._lock:
            self._framer.reset()
            self._last_uid = None
            self._last_uid_at = 0.0
            while True:
                try:
                    self._queue.get_nowait()
                    count += 1
                except Empty:
                    break
        if count:
            logger.info("Reader drained %d queued read(s)", count)
        return count

    def close(self) -> None:
        self._stop.set()
        self._thread.join(timeout=1.0)
        try:
            self._device.ungrab()
        except Exception:
            pass
        try:
            self._device.close()
        except Exception:
            pass


def make(reader_type: str, device_name: str = "OMNIKEY", **kwargs) -> Reader:
    if reader_type == "simulate":
        return _SimReader()
    if reader_type == "usb_keyboard":
        return _USBKeyboardReader(device_name=device_name, **kwargs)
    raise ValueError(f"unknown reader type: {reader_type!r}")


def _selftest() -> None:
    """python -m hardware.reader  -- poll for 30s and print any card taps."""
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    import sys

    reader_type = sys.argv[1] if len(sys.argv) > 1 else "usb_keyboard"
    device_name = sys.argv[2] if len(sys.argv) > 2 else "OMNIKEY"

    r = make(reader_type, device_name=device_name)
    print(f"Polling ({reader_type}) for 30s. Tap a card...")
    end = time.time() + 30
    try:
        while time.time() < end:
            uid = r.poll()
            if uid:
                print(f"  UID: {uid}")
            time.sleep(0.05)
    except KeyboardInterrupt:
        pass
    finally:
        r.close()


if __name__ == "__main__":
    _selftest()
