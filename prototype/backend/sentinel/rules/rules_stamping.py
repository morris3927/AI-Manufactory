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


@rule(id="R-STP-PERSON-ZONE", line="stamping", category="safety", default_severity="S3")
def person_in_zone(w: Windows, m: str) -> Finding | None:
    """影像偵測人員在危險區，而且機台運轉中，或同線沖床光幕停用／異常 → S3（AI 不提供任何排除方法）。"""
    th = w.th
    ev = w.vision(m, "person_in_zone").latest(last_s=th.person_zone_window_s)
    if not ev or not ev.get("value", True):
        return None
    peers = [x.id for x in w.plant.lines_machines(w.plant.line_of(m))] if w.plant else [m]
    curtains = {p: w.mes(p, "light_curtain_status").latest() for p in peers}
    curtain_bad = {p: v for p, v in curtains.items() if v in ("MUTED", "FAULT")}
    running = w.mes(m, "state").latest() == "running"
    if not (running or curtain_bad):
        return None
    return Finding(type="person_in_zone", facts={
        "source": "fused", "involves_person": True, "machine_state": w.mes(m, "state").latest(),
        "light_curtain": curtain_bad or "OK", "zone": ev.get("zone", "danger_zone"), "camera_id": ev.get("camera_id"),
    })
