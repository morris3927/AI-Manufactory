"""評估用保留測試集：knowledge/cases/**/heldout/（不入庫，只給評估用）。"""
from __future__ import annotations

from pathlib import Path

from ..kb.ingest import load_cases


def heldout_cases(knowledge_root: Path) -> list[dict]:
    return load_cases(knowledge_root, heldout=True)


def labels(cases: list[dict]) -> list[dict]:
    """評估要用的正確答案欄位（根因、類別、案例類型、第一判斷）。"""
    return [{"case_id": c["case_id"], "final_root_cause": c.get("final_root_cause"), "category": c.get("category"),
             "case_type": c.get("case_type"), "first_hypothesis": c.get("first_hypothesis"),
             "machine_type": c.get("machine_type")} for c in cases]
