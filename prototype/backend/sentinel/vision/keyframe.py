"""關鍵幀排程：每 N 秒一張（模擬時間）＋ Log 異常時隨選加送。"""
from __future__ import annotations


class KeyframeScheduler:
    def __init__(self, interval_s: float):
        self.interval_s = interval_s
        self._last: dict[str, float] = {}
        self._on_demand: dict[str, str] = {}

    def due(self, camera_id: str, t: float) -> str | None:
        """回傳觸發原因（'on_demand:…'／'interval'）或 None；呼叫即視為已排入。"""
        if camera_id in self._on_demand:
            reason = self._on_demand.pop(camera_id)
            self._last[camera_id] = t
            return f"on_demand:{reason}"
        last = self._last.get(camera_id)
        if last is None or t - last >= self.interval_s - 1e-6:
            self._last[camera_id] = t
            return "interval"
        return None

    def request(self, camera_id: str, reason: str) -> None:
        self._on_demand[camera_id] = reason

    def reset(self) -> None:
        self._last.clear()
        self._on_demand.clear()
