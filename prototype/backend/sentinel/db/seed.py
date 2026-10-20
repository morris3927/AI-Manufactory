"""初始資料載入：plant.yaml → machine；roster.csv → person、shift_assignment；access.csv → access_record。"""
from __future__ import annotations

import csv
from datetime import date, datetime
from pathlib import Path

from sqlmodel import delete

from ..clock import TW
from ..schemas.plant import Plant
from .models import AccessRecord, Machine, Person, ShiftAssignment
from .session import Database


def load_plant_into_db(db: Database, plant: Plant) -> int:
    with db.session() as s:
        for m in plant.machines.values():
            s.merge(Machine(id=m.id, line=m.line, type=m.type, name=m.name))
        s.commit()
    return len(plant.machines)


def load_roster(db: Database, path: Path) -> int:
    """欄位：date, shift, line, person_id, name, role, discord_id（虛構姓名）。"""
    if not path.exists():
        return 0
    n = 0
    with db.session() as s, path.open(encoding="utf-8") as f:
        s.exec(delete(ShiftAssignment))
        for row in csv.DictReader(r for r in f if not r.startswith("#")):
            s.merge(Person(id=row["person_id"], name=row["name"], line=row["line"], role=row["role"],
                           discord_id=row.get("discord_id") or None))
            s.add(ShiftAssignment(date=date.fromisoformat(row["date"]), shift=row["shift"],
                                  line=row["line"], person_id=row["person_id"]))
            n += 1
        s.commit()
    return n


def load_access(db: Database, path: Path) -> int:
    """欄位：person_id, ts, direction。"""
    if not path.exists():
        return 0
    n = 0
    with db.session() as s, path.open(encoding="utf-8") as f:
        s.exec(delete(AccessRecord))
        for row in csv.DictReader(r for r in f if not r.startswith("#")):
            ts = datetime.fromisoformat(row["ts"])
            s.add(AccessRecord(person_id=row["person_id"], ts=ts if ts.tzinfo else ts.replace(tzinfo=TW),
                               direction=row["direction"]))
            n += 1
        s.commit()
    return n


def seed_all(db: Database, plant: Plant, roster: Path, access: Path) -> dict[str, int]:
    return {
        "machines": load_plant_into_db(db, plant),
        "shift_rows": load_roster(db, roster),
        "access_rows": load_access(db, access),
    }
