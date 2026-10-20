"""模擬時鐘：所有事件時間都取自這裡，測試時可以手動推進。"""
from __future__ import annotations

import time
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

TW = ZoneInfo("Asia/Taipei")


class Clock:
    """即時時鐘（台灣時間）。"""

    realtime = True

    def now(self) -> datetime:
        return datetime.now(TW)

    def monotonic(self) -> float:
        return time.monotonic()


class ManualClock(Clock):
    """手動時鐘：只有呼叫 advance()／set() 才會前進；用於劇本逐步重播與測試。"""

    realtime = False

    def __init__(self, start: datetime):
        self._start = start if start.tzinfo else start.replace(tzinfo=TW)
        self._offset = 0.0

    def now(self) -> datetime:
        return self._start + timedelta(seconds=self._offset)

    def monotonic(self) -> float:
        return self._offset

    def advance(self, seconds: float) -> None:
        self._offset += seconds

    def set(self, seconds: float) -> None:
        self._offset = seconds
