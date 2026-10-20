"""Shared loaders and constants for the knowledge-base generators and validator."""
from __future__ import annotations

import json
from pathlib import Path

import yaml

GEN = Path(__file__).resolve().parent
KB = GEN.parent
LINE = "stamping"

MACHINES = {  # proposed plant.yaml entries (PR01/FD02 already in 08_系統設計.md §3)
    "STP-L1-PR01": {"type": "servo_press", "name": "250 t 伺服沖床", "tonnage_limit": 225},
    "STP-L1-FD02": {"type": "servo_roll_feeder", "name": "伺服送料機（含料架、整平機）", "feeds": "STP-L1-PR01"},
    "STP-L2-PR03": {"type": "mechanical_power_press", "name": "200 t 機械沖床", "tonnage_limit": 180},
    "STP-L2-FD04": {"type": "servo_roll_feeder", "name": "伺服送料機（含料架、整平機）", "feeds": "STP-L2-PR03"},
}
MACHINE_TYPE_LABEL = {
    "servo_press": "伺服沖床（250 t 級，連續模）",
    "mechanical_power_press": "機械沖床（曲軸式、氣壓離合器／制動器，200 t 級）",
    "servo_roll_feeder": "伺服滾輪送料機（含料架、整平機）",
}
LOTO_BY_TYPE = {
    "servo_press": "LOTO-PRESS-SERVO-01",
    "mechanical_power_press": "LOTO-PRESS-MECH-01",
    "servo_roll_feeder": "LOTO-FEED-01",
}
SAFETY_BLACKLIST = ["旁路", "短接", "跳線", "拆除光幕", "跳過連鎖", "停用安全裝置", "停用光幕", "解除連鎖"]


def load_yaml(p: Path):
    return yaml.safe_load(p.read_text(encoding="utf-8"))


def catalog(line: str = LINE) -> dict:
    return load_yaml(KB / "failure_modes" / f"{line}.yaml")[line]


def sources() -> dict:
    return load_yaml(KB / "SOURCES.yaml")["sources"]


def signals() -> dict:
    return load_yaml(KB / "signals.yaml")


def signal_names() -> set[str]:
    s = signals()
    return set(s["mes"]) | set(s["events"]) | set(s["vision"])


def alarm_codes() -> set[str]:
    return set(signals()["alarm_codes"])


def loto_files() -> dict:
    return {p.stem: load_yaml(p) for p in sorted((KB / "loto").glob("LOTO-*.yaml"))}


def _jsonl_chunks(paths) -> dict:
    out = {}
    for p in paths:
        if p.exists():
            for line in p.read_text(encoding="utf-8").splitlines():
                if line.strip():
                    c = json.loads(line)
                    out[c["chunk_id"]] = c
    return out


def regulation_chunks() -> dict:
    # public/regulations.jsonl (stamping fetcher) + public/regulations_*.jsonl (other lines)
    pub = KB / "public"
    return _jsonl_chunks([pub / "regulations.jsonl", *sorted(pub.glob("regulations_*.jsonl"))])


def guidance_chunks() -> dict:
    # public/guidance_*.jsonl: verbatim guideline / standard chunks (OSHA FS, NASA-STD, HSE)
    return _jsonl_chunks(sorted((KB / "public").glob("guidance_*.jsonl")))


def sop_by_mode() -> dict:
    return {fm: v["sop"] for fm, v in catalog().items()}


def loto_ids_for(machine_types: list[str]) -> list[str]:
    ids = []
    for t in machine_types:
        lid = LOTO_BY_TYPE[t]
        if lid not in ids:
            ids.append(lid)
    return ids
