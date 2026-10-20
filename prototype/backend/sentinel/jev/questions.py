"""Jev 題目定義（集中管理）。題目版本號和結果一起記錄在 jev_call.questions_ver。

修改任何題目文字、選項或標準時，要同時更新對應的版本號。
"""
from __future__ import annotations

EVENT_VER = "event-2026-10-20.3"       # J1–J3（.2：單機停機屬於 S2；.3：加入故障模式 severity_note）
FEED_VER = "feed-2026-10-20.1"         # V2
PPE_VER = "ppe-2026-10-20.1"           # V1
GATE_VER = "gate-2026-10-20.1"         # J4
WRITEBACK_VER = "writeback-2026-10-20.2"  # J5（.2：拆成三個子題，回傳缺哪一項）

CATEGORY_CRITERIA = {
    "safety": "與人員安全有關：PPE、闖入危險區、人員倒地",
    "equipment": "與機台運作有關：卡料、閒置、過熱、異常碼",
    "quality": "與產品品質有關：尺寸、外觀瑕疵",
    "false_alarm": "感測誤判或正常作業現象，不需處理",
}

SEVERITY_CRITERIA = [
    "S1：無影響，記錄即可",
    "S2：需要人員到場處理；包含單一機台卡料、停機、閒置，但沒有人員受傷或起火風險",
    "S3：有人員受傷、起火、爆炸等立即危險，或多台機台同時停止",
]


def event_questions(failure_modes: dict[str, str] | None, severity_notes: list[str] | None = None) -> dict:
    """J1 類別＋誤報、J2 嚴重度、J3 根因（故障模式少於 2 個時不問根因）。

    severity_notes：故障模式目錄的 severity_note（例如「光幕停用時有人在危險區 → S3」），補進嚴重度標準。
    """
    sev_criteria = list(SEVERITY_CRITERIA)
    if severity_notes:
        sev_criteria[2] = SEVERITY_CRITERIA[2] + "。另外下列情況也屬於 S3：" + "；".join(severity_notes[:10])
    q: dict = {
        "category": {"type": "choice", "instructions": "這個事件屬於哪一類？", "criteria": CATEGORY_CRITERIA},
        "is_false_alarm": {
            "type": "noul",
            "instructions": "依據 facts 與 timeline，這個事件是否為誤報？",
            "criteria": {"true": "不需要任何人到場處理", "false": "需要有人到場處理"},
        },
        "severity": {"type": "score", "instructions": "依據對人員安全與停線的影響評定嚴重度。",
                     "criteria": sev_criteria},
    }
    if failure_modes and len(failure_modes) >= 2:
        q["root_cause"] = {
            "type": "choice",
            "instructions": "最可能的根因是哪一個？只依據 event、timeline 與 similar_cases 判斷。",
            "criteria": dict(list(failure_modes.items())[:255]),
        }
    return q


FEED_QUESTIONS = {
    "jammed": {
        "type": "noul",
        "instructions": "roi_image 是沖床送料口。板材是否堆疊、歪斜，或卡在送料口沒有正常送入？",
        "criteria": {"true": "板材堆疊、翹起、歪斜或卡住", "false": "板材平整送入，或送料口沒有板材"},
    },
    "smoke": {"type": "noul", "instructions": "roi_image 中是否有煙霧或火光？"},
    "jam_box": {"type": "box", "instructions": "roi_image 中卡住或歪斜的板材"},
}

PPE_QUESTIONS = {
    "no_helmet": {
        "type": "noul",
        "instructions": "person_image 是作業人員裁切圖（臉部已模糊）。圖中人員頭部是否沒有配戴安全帽？",
        "criteria": {"true": "頭部沒有安全帽", "false": "有戴安全帽，或看不到頭部"},
    },
    "in_work_zone": {"type": "noul", "instructions": "圖中人員是否位於需要配戴安全帽的作業區？"},
}

SAFETY_GATE = {
    "needs_loto": {"type": "noul", "instructions": "這個操作步驟是否需要先停機並上鎖掛牌（LOTO）？"},
    "enters_danger_zone": {"type": "noul", "instructions": "這個步驟是否需要人員把身體伸入機台運轉範圍？"},
    "high_voltage": {"type": "noul", "instructions": "這個步驟是否涉及打開電控箱或接觸高壓電？"},
}


WRITEBACK_PARTS = {"has_cause": "實際故障原因", "has_action": "採取的處置", "has_result": "處置後是否恢復正常"}


def writeback_questions(failure_modes: dict[str, str] | None) -> dict:
    q: dict = {
        "complete": {"type": "noul",
                     "instructions": "回報是否同時寫出：實際故障原因、採取的處置、處置後是否恢復正常？"},
        **{k: {"type": "noul", "instructions": f"回報是否寫出{label}？"} for k, label in WRITEBACK_PARTS.items()},
    }
    fm = dict(list((failure_modes or {}).items())[:254])
    q["root_cause"] = {"type": "choice", "instructions": "實際根因屬於哪一類？",
                       "criteria": fm | {"new": "以上皆非，新的故障模式"}}
    return q
