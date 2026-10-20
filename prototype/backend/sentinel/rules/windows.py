"""滑動視窗：每台機器（或攝影機）、每個訊號保留最近 10 分鐘的資料。"""
from __future__ import annotations

from collections import defaultdict, deque
from datetime import datetime
from typing import Any, Callable

from ..clock import Clock
from ..config import Thresholds
from ..schemas.events import Event, Topic


class Obs(dict):
    """可以用屬性存取的觀測值：jam.p_jam。"""

    def __getattr__(self, key: str) -> Any:
        try:
            return self[key]
        except KeyError as e:
            raise AttributeError(key) from e


class _Window:
    def __init__(self, clock: Clock, retention_s: float):
        self.clock = clock
        self.retention_s = retention_s
        self.items: deque[tuple[datetime, Any]] = deque()

    def add(self, ts: datetime, value: Any) -> None:
        self.items.append((ts, value))
        self._trim()

    def _trim(self) -> None:
        now = self.clock.now()
        while self.items and (now - self.items[0][0]).total_seconds() > self.retention_s:
            self.items.popleft()

    def since(self, last_s: float | None) -> list[tuple[datetime, Any]]:
        if last_s is None:
            return list(self.items)
        now = self.clock.now()
        return [(ts, v) for ts, v in self.items if (now - ts).total_seconds() <= last_s + 1e-6]

    def __len__(self) -> int:
        return len(self.items)


class SignalWindow(_Window):
    """數值或狀態訊號（mes.logs）。"""

    def latest(self) -> Any:
        return self.items[-1][1] if self.items else None

    def values(self, last_s: float | None = None) -> list[Any]:
        return [v for _, v in self.since(last_s)]

    def max(self, last_s: float | None = None) -> float | None:
        vals = [v for v in self.values(last_s) if isinstance(v, (int, float))]
        return max(vals) if vals else None

    def _differs(self, a: Any, b: Any, min_delta: float | None) -> bool:
        if isinstance(a, (int, float)) and isinstance(b, (int, float)):
            tol = min_delta if min_delta is not None else max(abs(a), abs(b)) * 0.05 + 1e-9
            return abs(a - b) > tol
        return a != b

    def changed_within(self, last_s: float, min_delta: float | None = None) -> bool:
        """最近 last_s 秒內是否有明顯變化（與最新值相差超過 min_delta；預設 5%，避免雜訊誤判）。"""
        if not self.items:
            return False
        cur = self.latest()
        return any(self._differs(v, cur, min_delta) for v in self.values(last_s))

    def seconds_since_change(self, min_delta: float | None = None) -> float | None:
        if not self.items:
            return None
        cur = self.latest()
        now = self.clock.now()
        for ts, v in reversed(self.items):
            if self._differs(v, cur, min_delta):
                return round((now - ts).total_seconds(), 2)
        return None

    def trend(self, last_s: float) -> float | None:
        """最小平方法斜率（單位／秒）。"""
        pts = [(ts, v) for ts, v in self.since(last_s) if isinstance(v, (int, float))]
        if len(pts) < 2:
            return None
        t0 = pts[0][0]
        xs = [(ts - t0).total_seconds() for ts, _ in pts]
        ys = [v for _, v in pts]
        n = len(xs)
        mx, my = sum(xs) / n, sum(ys) / n
        den = sum((x - mx) ** 2 for x in xs)
        return sum((x - mx) * (y - my) for x, y in zip(xs, ys)) / den if den else 0.0

    def duration_true(self, pred: Callable[[Any], bool]) -> float:
        """從最新一筆往回，條件連續成立的秒數。"""
        if not self.items or not pred(self.latest()):
            return 0.0
        now = self.clock.now()
        start = self.items[-1][0]
        for ts, v in reversed(self.items):
            if not pred(v):
                break
            start = ts
        return (now - start).total_seconds()


class EventWindow(_Window):
    """影像事件（vision.events）：值為 Obs。"""

    def latest(self, last_s: float | None = None) -> Obs | None:
        items = self.since(last_s)
        return items[-1][1] if items else None

    def count_above(self, field: str, threshold: float, last_s: float) -> int:
        return sum(1 for _, v in self.since(last_s) if (v.get(field) or 0) > threshold)

    def count(self, last_s: float) -> int:
        return len(self.since(last_s))

    def last(self, n: int, field: str | None = None) -> list[Any]:
        vals = [v for _, v in list(self.items)[-n:]]
        return [v.get(field) for v in vals] if field else vals

    def latest_ts(self) -> datetime | None:
        return self.items[-1][0] if self.items else None


EVENT_LABEL = {"coil_change": "換料捲", "die_change": "換模", "param_change": "參數變更"}


def _brief(payload: dict) -> str:
    return "，".join(f"{k}={v}" for k, v in list(payload.items())[:3])


class Windows:
    def __init__(self, clock: Clock, thresholds: Thresholds | None = None, retention_s: float = 600):
        self.clock = clock
        self.th = thresholds or Thresholds()
        self.retention_s = retention_s
        self._mes: dict[tuple[str, str], SignalWindow] = defaultdict(lambda: SignalWindow(clock, retention_s))
        self._vis: dict[tuple[str, str], EventWindow] = defaultdict(lambda: EventWindow(clock, retention_s))
        self.timeline: dict[str, deque[str]] = defaultdict(lambda: deque(maxlen=50))

    def mes(self, entity: str, signal: str) -> SignalWindow:
        return self._mes[(entity, signal)]

    def vision(self, entity: str, type_: str) -> EventWindow:
        return self._vis[(entity, type_)]

    def events(self, entity: str, type_: str) -> EventWindow:
        """非每秒的機台事件（coil_change 等）與影像事件共用同一種視窗。"""
        return self._vis[(entity, type_)]

    def ingest(self, evt: Event) -> str | None:
        """把事件放進視窗；回傳主體（machine_id 或 camera_id）。"""
        entity = evt.entity
        if not entity:
            return None
        hhmmss = evt.ts.strftime("%H:%M:%S")
        if evt.topic == Topic.MES and evt.type == "mes_log":
            prev_state = self.mes(entity, "state").latest()
            for k, v in evt.payload.items():
                self.mes(entity, k).add(evt.ts, v)
            if evt.payload.get("state") != prev_state:
                self.timeline[entity].append(f"{hhmmss} 狀態 {evt.payload.get('state')}")
        elif evt.topic == Topic.MES and evt.type == "machine_alarm":
            self.timeline[entity].append(f"{hhmmss} 異常碼 {evt.payload.get('alarm_code')}")
        elif evt.topic == Topic.MES:                     # coil_change／die_change／param_change 等機台事件
            self.events(entity, evt.type).add(evt.ts, Obs(evt.payload))
            self.timeline[entity].append(f"{hhmmss} {EVENT_LABEL.get(evt.type, evt.type)} {_brief(evt.payload)}")
        elif evt.topic == Topic.VISION:
            self.vision(entity, evt.type).add(evt.ts, Obs(evt.payload))
            if evt.type == "jev_vision":
                self.timeline[entity].append(f"{hhmmss} 影像判定卡料機率 {evt.payload.get('p_jam')}")
            elif evt.type == "machine_state":
                self.timeline[entity].append(f"{hhmmss} 影像動態 {evt.payload.get('state')}")
            elif evt.type == "ppe_violation":
                self.timeline[entity].append(f"{hhmmss} 偵測未戴安全帽")
            elif evt.type == "person_in_zone":
                self.timeline[entity].append(f"{hhmmss} 人員進入危險區 {evt.payload.get('value', True)}")
        return entity

    def summary(self, entity: str, signal: str, last_min: float = 10) -> dict:
        w = self.mes(entity, signal)
        vals = w.values(last_min * 60)
        nums = [v for v in vals if isinstance(v, (int, float))]
        return {"signal": signal, "latest": w.latest(), "n": len(vals),
                "min": min(nums) if nums else None, "max": max(nums) if nums else None,
                "trend_per_min": round((w.trend(last_min * 60) or 0) * 60, 4) if nums else None,
                "seconds_since_change": w.seconds_since_change()}

    def series(self, entity: str, last_min: float = 10) -> dict[str, list[tuple[str, Any]]]:
        out: dict[str, list[tuple[str, Any]]] = {}
        for (ent, sig), w in self._mes.items():
            if ent == entity:
                out[sig] = [(ts.isoformat(), v) for ts, v in w.since(last_min * 60)]
        return out

    def clear(self) -> None:
        self._mes.clear()
        self._vis.clear()
        self.timeline.clear()
