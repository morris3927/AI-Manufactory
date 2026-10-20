"""沖壓線規則。"""
from __future__ import annotations

from .engine import Finding, rule
from .windows import Windows


@rule(id="R-STP-FEEDJAM", line="stamping", category="equipment", default_severity="S2",
      machine_types=("servo_roll_feeder",))
def feed_jam(w: Windows, m: str) -> Finding | None:
    """RU-2④：Jev 看到卡料（連續 2 張 > 0.7）＋ Log 送料速率歸零 → 確認卡料。

    退路（Jev 不可用）：Log 送料速率歸零 ＋ 影像動態歸零（idle）。
    """
    th = w.th
    feed = w.mes(m, "feed_rate")
    if feed.latest() is None or feed.latest() != 0 or not feed.changed_within(th.feed_drop_window_s):
        return None
    err = w.mes(m, "feed_length_error_mm")
    base = {
        "feed_rate_before": feed.max(last_s=60),
        "feed_rate_now": 0,
        "seconds_since_drop": feed.seconds_since_change(),
        "alarm_code": w.mes(m, "alarm_code").latest(),
        "feed_length_error_mm_max_60s": err.max(last_s=60),
        "feed_length_error_ramp_up": (err.trend(60) or 0) > 0.001,
    }
    jv = w.vision(m, "jev_vision")
    jam = jv.latest(last_s=th.p_jam_window_s)
    if jam and jam.p_jam > th.p_jam and jv.count_above("p_jam", th.p_jam, th.p_jam_window_s) >= th.p_jam_count:
        return Finding(type="feed_jam", facts={**base, "source": "fused", "p_jam_last2": jv.last(2, "p_jam"),
                                               "jam_box": jam.get("box"), "jev_available": True})
    if jv.latest(last_s=th.jev_stale_s) is None:
        ms = w.vision(m, "machine_state").latest()
        if ms and ms.state == "idle":
            return Finding(type="feed_jam", facts={**base, "source": "mes+motion", "jev_available": False,
                                                   "motion_state": "idle"})
    return None
