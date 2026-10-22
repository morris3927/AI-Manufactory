"""相似案例檢索（kb/facts.py、KnowledgeBase.similar_cases 混合檢索）。hash embedding，不需下載模型。"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import pytest

from sentinel.kb import facts as F
from sentinel.kb.ingest import chunk_case
from sentinel.kb.retrieve import KnowledgeBase, RetrievalCfg
from sentinel.kb.store import HashEmbedder, VectorStore


@dataclass
class Inc:
    type: str
    line: str = "stamping"
    machine_id: str | None = "STP-L1-FD02"
    rule_severity: str = "S2"
    facts: dict[str, Any] = field(default_factory=dict)


def case(cid: str, rc: str, event: str, signals: list[tuple[str, Any]], *, mt: str = "servo_roll_feeder",
         outcome: str = "resolved", case_type: str = "typical", mid: str = "STP-L1-FD02") -> dict:
    return {"case_id": cid, "line": "stamping", "machine_type": mt, "machine_id": mid, "synthetic": True,
            "review_status": "approved", "case_type": case_type, "outcome": outcome, "final_root_cause": rc,
            "trigger": {"source": "mes", "event_type": event},
            "log_signals": [{"t": f"2026-10-01T10:0{i}:00+08:00", "signal": s, "value": v} for i, (s, v) in enumerate(signals)],
            "lessons": f"{rc} 的教訓"}


CASES = [
    case("C-1", "feed_roller_wear", "feed_jam_suspected",
         [("feed_length_error_mm", 0.05), ("feed_length_error_mm", 0.12), ("feed_length_error_mm", 0.2)]),
    case("C-2", "none", "feed_jam_suspected", [("feed_length_error_mm", 0.02), ("state", "alarm"), ("state", "running")],
         outcome="false_alarm", case_type="false_alarm"),
    case("C-3", "new", "feed_jam_suspected", [("feed_length_error_mm", 0.3)], case_type="new"),
    case("C-4", "air_pressure_low", "air_low_alarm", [("air_pressure_bar", 5.5), ("air_pressure_bar", 5.0),
                                                       ("air_pressure_bar", 4.6)], mt="mechanical_power_press",
         mid="STP-L2-PR03"),
    case("C-5", "overload_tonnage", "overload_stop", [("tonnage_peak", 200), ("tonnage_peak", 260)], mt="servo_press",
         mid="STP-L1-PR01"),
]


@pytest.fixture()
def kb(tmp_path):
    store = VectorStore(tmp_path / "chroma", HashEmbedder())
    store.upsert("cases", [c for d in CASES for c in chunk_case(d)])
    return KnowledgeBase(tmp_path / "none", store)


def test_tokenize_and_bm25():
    toks = F.tokenize("feed_length_error_mm 逐步上升 12.5")
    assert "feed_length_error_mm" in toks and "feed" in toks and "逐步" in toks and "12" not in toks
    bm = F.BM25([F.tokenize("air_pressure_bar 逐步下降"), F.tokenize("tonnage_peak 一次跳升")])
    s = bm.scores(F.tokenize("air_pressure_bar 下降"))
    assert s[0] > s[1] == 0


def test_structured_text_same_form_for_case_and_incident():
    c = CASES[0]
    inc = Inc("feed_jam_suspected", facts={"source": "mes", **F.signal_facts(c["log_signals"], c["machine_id"])})
    assert F.case_structured_text(c) == F.incident_query_text(inc, "servo_roll_feeder")
    assert "feed_length_error_mm 逐步上升" in F.case_structured_text(c)


def test_signal_keys_strip_window_suffix():
    assert F.signal_keys({"feed_length_error_mm_max_60s": 0.2, "feed_rate_now": 0, "source": "fused",
                          "air_pressure_bar@STP-L2-PR03": {"value": 4}}) == {"feed_length_error_mm", "feed_rate",
                                                                              "air_pressure_bar"}


def test_looks_false_alarm():
    assert F.looks_false_alarm({"state": ["alarm", "running"]})
    assert F.looks_false_alarm({"screw_result": ["NOK_LOW_TORQUE", "OK"]})
    assert not F.looks_false_alarm({"state": "alarm", "feed_rate_now": 0})


def test_hybrid_prefers_root_cause_cases_and_same_trigger(kb):
    inc = Inc("feed_jam_suspected", facts={"source": "mes", "feed_length_error_mm": {"pattern": "逐步上升"}})
    hits = kb.similar_cases(inc, k=5, machine_type="servo_roll_feeder")
    assert hits[0].failure_mode_id == "feed_roller_wear"
    # none／new 降權：排在其他同事件案例之後，但仍可能取到
    assert [h.failure_mode_id for h in hits].index("none") > 0


def test_false_alarm_like_incident_keeps_none_cases(kb):
    inc = Inc("feed_jam_suspected", facts={"source": "mes", "feed_length_error_mm": {"value": 0.02},
                                           "state": ["alarm", "running"]})
    hits = kb.similar_cases(inc, k=3, machine_type="servo_roll_feeder")
    assert "none" in [h.failure_mode_id for h in hits[:2]]


def test_machine_type_is_boost_not_filter(kb):
    # 送料機的氣壓低：同根因只出現在沖床的案例 → 不硬篩機型才找得到
    inc = Inc("air_low_alarm", facts={"source": "mes", "air_pressure_bar": {"pattern": "逐步下降"}})
    assert kb.similar_cases(inc, k=3, machine_type="servo_roll_feeder")[0].failure_mode_id == "air_pressure_low"
    kb.retrieval = RetrievalCfg(filter_machine_type=True)
    assert "air_pressure_low" not in [h.failure_mode_id for h in kb.similar_cases(inc, k=3, machine_type="servo_roll_feeder")]


def test_cache_refreshes_after_writeback_upsert(kb):
    inc = Inc("feed_jam", facts={"source": "fused", "feed_rate_now": 0, "alarm_code": "MISFEED"})
    before = kb.similar_cases(inc, k=5, machine_type="servo_roll_feeder")
    wb = case("CASE-STP-WB1", "material_thickness_dev", "feed_jam", [("feed_rate_now", 0), ("alarm_code", "MISFEED")],
              case_type="writeback")
    kb.store.upsert("cases", chunk_case(wb))
    after = kb.similar_cases(inc, k=5, machine_type="servo_roll_feeder")
    assert "CASE-STP-WB1" not in [h.case_id for h in before] and after[0].case_id == "CASE-STP-WB1"


def test_legacy_mode_still_available(kb):
    kb.retrieval = RetrievalCfg(mode="legacy", query_format="legacy")
    inc = Inc("feed_jam_suspected", facts={"feed_rate_now": 0})
    assert kb.similar_cases(inc, k=2, machine_type="servo_roll_feeder")


def test_health_warns_on_empty_cases(tmp_path, kb):
    assert kb.health()["cases"] == len(CASES) * 2 and kb.health()["warnings"] == []
    empty = KnowledgeBase(tmp_path / "none", VectorStore(tmp_path / "empty", HashEmbedder()))
    h = empty.health()
    assert h["cases"] == 0 and any("make kb" in w for w in h["warnings"])
    assert KnowledgeBase(tmp_path / "none", None).health()["warnings"]


def test_ingest_cli_fails_loudly_without_cases(tmp_path, monkeypatch):
    from sentinel.kb import ingest

    (tmp_path / "know" / "cases").mkdir(parents=True)
    monkeypatch.setenv("SENTINEL_CONFIG", str(_cfg(tmp_path)))
    assert ingest.main(["--rebuild", "--knowledge", str(tmp_path / "know")]) == 1


def _cfg(tmp_path):
    import yaml

    from sentinel.config import BACKEND_DIR
    data = yaml.safe_load((BACKEND_DIR / "config.yaml").read_text(encoding="utf-8"))
    data["paths"]["chroma"] = str(tmp_path / "chroma")
    data["kb"]["embedding"] = "hash"
    p = tmp_path / "config.yaml"
    p.write_text(yaml.safe_dump(data, allow_unicode=True), encoding="utf-8")
    return p
