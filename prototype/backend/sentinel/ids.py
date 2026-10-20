"""ID 與序列化小工具。"""
from __future__ import annotations

import json
from datetime import datetime
from typing import Any

import numpy as np
from ulid import ULID


def new_id(ts: datetime | None = None) -> str:
    """ULID：可依時間排序。帶 ts 時使用模擬時間。"""
    return str(ULID.from_datetime(ts) if ts else ULID())


def jsonable(value: Any) -> Any:
    """把 numpy、datetime 等轉成可寫入 JSON 的值；影像陣列不寫入。"""
    if isinstance(value, dict):
        return {str(k): jsonable(v) for k, v in value.items() if not isinstance(v, np.ndarray)}
    if isinstance(value, (list, tuple)):
        return [jsonable(v) for v in value]
    if isinstance(value, np.generic):
        return value.item()
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    if hasattr(value, "model_dump"):
        return jsonable(value.model_dump())
    return json.loads(json.dumps(value, default=str))
