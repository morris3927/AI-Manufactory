"""案例檢索用的正規化文字與關鍵字檢索（KB-2）。

事件查詢與案例都轉成同一種「結構化事實」寫法，向量與 BM25 才比得起來：
  機型 servo_roll_feeder｜事件 feed_jam_suspected｜來源 fused｜訊號 feed_length_error_mm 逐步上升；alarm_code=MISFEED｜近期變更 換料捲
數字只留型態（逐步上升、一次跳降…），不留數值：不同案例的數值幾乎不會相同，放進去只是雜訊。
"""
from __future__ import annotations

import math
import re
from collections import Counter
from datetime import datetime
from typing import Any

# 案例最終根因是「誤報／目錄外新模式」→ 預設降權（除非事件本身看起來像誤報）
NON_ROOT_CAUSES = {"none", "new", None, ""}
STATE_KEYS = {"state", "robot_state"}
RECOVERED = {"running", "ok", "pass", "normal", "正常", "運轉"}


def _num(v: Any) -> float | None:
    return float(v) if isinstance(v, (int, float)) and not isinstance(v, bool) else None


def signal_facts(log_signals: list[dict], own_machine: str | None) -> dict[str, Any]:
    """程式先把 Log 算成結論（Jev 不擅長算數）：每個訊號的起訖值、最大最小、變化型態。"""
    groups: dict[str, list[dict]] = {}
    for x in sorted(log_signals, key=lambda x: str(x.get("t"))):
        key = x["signal"] if not x.get("machine_id") or x.get("machine_id") == own_machine \
            else f"{x['signal']}@{x['machine_id']}"
        groups.setdefault(key, []).append(x)
    out: dict[str, Any] = {}
    for key, xs in groups.items():
        vals = [x.get("value") for x in xs]
        nums = [_num(v) for v in vals]
        if all(n is not None for n in nums) and nums:
            nn = [n for n in nums if n is not None]
            if len(nn) == 1:
                out[key] = {"value": nn[0]}
                continue
            diffs = [b - a for a, b in zip(nn, nn[1:])]
            if all(d > 0 for d in diffs) and len(nn) >= 3:
                pattern = "逐步上升"
            elif all(d < 0 for d in diffs) and len(nn) >= 3:
                pattern = "逐步下降"
            elif all(abs(d) < 1e-9 for d in diffs):
                pattern = "持平"
            else:
                big = max(range(len(diffs)), key=lambda i: abs(diffs[i]))
                pattern = "一次跳升" if diffs[big] > 0 else "一次跳降"
            span_min = None
            try:
                t0, t1 = datetime.fromisoformat(xs[0]["t"]), datetime.fromisoformat(xs[-1]["t"])
                span_min = round((t1 - t0).total_seconds() / 60, 1)
            except Exception:
                pass
            out[key] = {"first": nn[0], "last": nn[-1], "min": min(nn), "max": max(nn), "n": len(nn),
                        "pattern": pattern, "span_min": span_min}
        else:
            uniq = list(dict.fromkeys(str(v) for v in vals))
            out[key] = uniq[0] if len(uniq) == 1 else uniq
    return out


def _fact_phrase(key: str, v: Any) -> str | None:
    if v is None or key in {"source", "jev_available", "detect_latency_s", "jam_box", "trace_id"}:
        return None
    if isinstance(v, dict):
        return f"{key} {v['pattern']}" if v.get("pattern") else key
    if isinstance(v, bool):
        m = re.fullmatch(r"(.+)_ramp_(up|down)", key)
        if m and v:                                # 規則 facts 的 *_ramp_up＝案例 Log 的「逐步上升」
            return f"{m.group(1)} {'逐步上升' if m.group(2) == 'up' else '逐步下降'}"
        return key if v else f"{key}=否"
    if isinstance(v, (int, float)):
        return key
    if isinstance(v, (list, tuple)):
        if v and all(isinstance(x, (int, float)) and not isinstance(x, bool) for x in v):
            return key
        return f"{key}={'→'.join(map(str, v))}"
    s = str(v)
    return f"{key}={s}" if len(s) <= 40 else key


def facts_text(facts: dict[str, Any]) -> str:
    return "；".join(p for k, v in facts.items() if k != "recent_changes" and (p := _fact_phrase(k, v)))


def structured_text(*, machine_type: str | None, event_type: str | None, source: str | None,
                    facts: dict[str, Any], recent_changes: Any = None) -> str:
    rc = recent_changes if recent_changes is not None else facts.get("recent_changes")
    rc = [re.sub(r"\s*\d{1,2}:\d{2}.*$", "", str(x)) for x in (rc or [])] if isinstance(rc, list) else rc
    parts = [f"機型 {machine_type or '-'}", f"事件 {event_type or '-'}", f"來源 {source or '-'}",
             f"訊號 {facts_text(facts) or '-'}"]
    if rc:
        parts.append(f"近期變更 {'、'.join(map(str, rc))}")
    return "｜".join(parts)


def incident_query_text(incident: Any, machine_type: str | None = None) -> str:
    facts = dict(getattr(incident, "facts", None) or {})
    return structured_text(machine_type=machine_type, event_type=incident.type, source=facts.get("source"),
                           facts=facts)


def case_structured_text(doc: dict) -> str:
    trig = doc.get("trigger") or {}
    mt = doc.get("machine_type")
    return structured_text(machine_type=mt if isinstance(mt, str) else "|".join(mt or []),
                           event_type=trig.get("event_type"), source=trig.get("source"),
                           facts=signal_facts(doc.get("log_signals") or [], doc.get("machine_id")),
                           recent_changes=(doc.get("context") or {}).get("recent_changes"))


def signal_keys(facts: dict[str, Any]) -> set[str]:
    """訊號名稱集合（去掉 @鄰近機台、_max_60s 之類的視窗後綴，事件 facts 與案例 Log 才對得上）。"""
    out = set()
    for k in facts:
        if k in {"source", "recent_changes", "jev_available", "detect_latency_s", "jam_box"}:
            continue
        k = k.split("@")[0]
        k = re.sub(r"_(max|min|mean|last|first)_\d+s$|_(before|now|last2|ramp_up|ramp_down)$", "", k)
        out.add(k)
    return out


def looks_false_alarm(facts: dict[str, Any]) -> bool:
    """事件本身看起來像誤報：狀態訊號已回到運轉／OK（例如 state=alarm→running、screw_result=NOK→OK），
    或機台狀態仍是運轉。只用來「不對 none 案例減分」，不會把 none 案例往前推。"""
    for k, v in facts.items():
        if k.split("@")[0] in STATE_KEYS and isinstance(v, str) and v.lower() in RECOVERED:
            return True                            # 有警報碼但機台狀態仍是運轉
        if isinstance(v, (list, tuple)) and len(v) >= 2 and not isinstance(v[-1], (int, float)):
            if str(v[-1]).lower() in RECOVERED and str(v[0]).lower() not in RECOVERED:
                return True
    return False


# ── BM25（案例只有數百筆，直接在記憶體算）──────────────────────────
_CJK = re.compile(r"[㐀-鿿]+")
_WORD = re.compile(r"[a-z0-9]+(?:_[a-z0-9]+)*")


def tokenize(text: str) -> list[str]:
    t = text.lower()
    toks: list[str] = []
    for w in _WORD.findall(t):
        if re.fullmatch(r"[0-9_]+", w):
            continue                               # 純數字（時間、數值）不當關鍵字
        toks.append(w)
        if "_" in w:
            toks.extend(p for p in w.split("_") if not p.isdigit())
    for seg in _CJK.findall(t):
        toks.extend(seg[i:i + 2] for i in range(len(seg) - 1)) if len(seg) > 1 else toks.append(seg)
    return toks


class BM25:
    def __init__(self, docs: list[list[str]], k1: float = 1.2, b: float = 0.75):
        self.k1, self.b = k1, b
        self.tf = [Counter(d) for d in docs]
        self.len = [len(d) for d in docs]
        self.avg = (sum(self.len) / len(docs)) if docs else 0.0
        df: Counter = Counter()
        for d in self.tf:
            df.update(d.keys())
        n = len(docs)
        self.idf = {w: math.log(1 + (n - c + 0.5) / (c + 0.5)) for w, c in df.items()}

    def scores(self, query: list[str], idx: list[int] | None = None) -> list[float]:
        q = Counter(query)
        out = []
        for i in (range(len(self.tf)) if idx is None else idx):
            tf, dl, s = self.tf[i], self.len[i], 0.0
            for w, qn in q.items():
                f = tf.get(w)
                if f:
                    s += self.idf.get(w, 0.0) * f * (self.k1 + 1) / (f + self.k1 * (1 - self.b + self.b * dl / (self.avg or 1)))
            out.append(s)
        return out
