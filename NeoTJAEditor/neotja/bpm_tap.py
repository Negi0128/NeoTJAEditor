from __future__ import annotations

import time


class BpmTapper:
    """Tap-tempo BPM estimator. Framework-agnostic: feed it tap() calls from
    a button click, it tracks timestamps and reports the current BPM estimate."""

    MAX_TAPS = 8
    RESET_GAP = 2.0  # seconds since last tap before starting a fresh sequence

    def __init__(self, time_fn=None):
        self._time_fn = time_fn or time.monotonic
        self.taps: list[float] = []

    def tap(self) -> float | None:
        now = self._time_fn()
        if self.taps and (now - self.taps[-1]) > self.RESET_GAP:
            self.taps.clear()
        self.taps.append(now)
        if len(self.taps) > self.MAX_TAPS:
            self.taps.pop(0)
        return self.bpm()

    def bpm(self) -> float | None:
        if len(self.taps) < 2:
            return None
        intervals = [b - a for a, b in zip(self.taps, self.taps[1:])]
        avg = sum(intervals) / len(intervals)
        if avg <= 0:
            return None
        return 60.0 / avg

    def reset(self):
        self.taps.clear()
