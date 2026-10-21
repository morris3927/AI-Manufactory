"""維修 Agent 的程式強制規則（不靠提示詞；08_系統設計.md §5.7）。"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

from ..dispatch.workorder import PRE_REPAIR, WoStatus

# ── 1／2. LOTO 先行、作業許可 ───────────────────────────────────
SAFE_PHASES = {"summary", "prepare", "isolate"}           # LOTO 完成前只能看這些段落
RESTRICTED_PHASES = {"inspect", "repair", "verify", "restore"}
PRE_LOTO_TOOLS = {"get_workorder", "get_loto", "get_sop"}   # get_sop 只開放 summary／prepare／isolate
ALL_TOOLS = {"get_workorder", "get_loto", "get_machine_signals", "get_sop", "search_cases", "record_step",
             "request_supervisor"}


class ToolDenied(Exception):
    """工具或段落在目前工單狀態下不開放。"""


def restricted(status: str, require_permit: bool = True) -> bool:
    """True：還沒到可以給檢查／維修步驟的狀態（檢查與維修段落只在 IN_REPAIR 之後開放）。"""
    return WoStatus(status) in PRE_REPAIR


def allowed_tools(status: str, require_permit: bool = True) -> set[str]:
    return set(PRE_LOTO_TOOLS) if restricted(status, require_permit) else set(ALL_TOOLS)


def check_tool(name: str, args: dict, status: str, require_permit: bool = True) -> None:
    if name not in allowed_tools(status, require_permit):
        raise ToolDenied(f"工具 {name} 在 {status} 狀態不開放（LOTO 先行）")
    if name == "get_sop" and restricted(status, require_permit) and args.get("phase") not in SAFE_PHASES:
        raise ToolDenied(f"LOTO 完成前不提供 SOP 的 {args.get('phase')} 段落")


# ── 4. 不能叫人復電 ─────────────────────────────────────────────
# 復電原則：由原上鎖人親自取下自己的鎖（參考 OSHA 29 CFR 1910.147(e)(3)；電路作業依設施規則第254條
# 確認無感電之虞後才送電）；測試／定位依 OSHA 29 CFR 1910.147(f)(1)。兩種情況分開處理：
#   a) 測試／定位：SOP 允許的「暫時通電量測、寸動」→ 改寫成固定句型並要求主管在場（標 requires_supervisor）
#   b) 恢復生產：叫人復電、解除上鎖、試沖、連續生產 → 整步刪除，結尾加一行提醒（不是步驟）
# AI 永遠不宣告「可以復電」。
CLOSING_REMINDER = "提醒：完工後的復電與恢復生產，一律由原上鎖人依 LOTO 解除程序執行；AI 不會宣告可以復電。"
ENERGIZED_TEMPLATE = "此步需要通電：須主管在場，由原上鎖人依 {loto_id} 測試／定位程序暫時解除，量測完立即重新上鎖"
ENERGIZED_MARK = "此步需要通電"
_NEG = r"(?<!禁止)(?<!不得)(?<!不可)(?<!不可以)(?<!不要)(?<!勿)(?<!不能)(?<!未)(?<!不)"
RESTORE_PATTERNS = [
    re.compile(_NEG + r"(?:復電|送電|恢復供電|恢復電源|重新通電|恢復通電|重新送電)"),
    re.compile(_NEG + r"(?:解除|取下|拆除|移除|拿掉|打開)(?:自己的|個人的)?(?:上鎖|鎖具|掛牌|安全鎖|個人鎖|掛鎖|鎖|LOTO\s*鎖)"),
    re.compile(_NEG + r"(?:合上|推上|打開|開啟)(?:主)?(?:電源開關|斷路器|隔離開關|電源)"),
    re.compile(_NEG + r"(?:恢復生產|連續生產|恢復運轉|恢復量產)"),
    re.compile(r"(?i)(?<!not )(?:restore power|re-?energi[sz]e|turn the power back on|remove (?:the |your )?lock)"),
]
TEST_PATTERNS = [
    re.compile(r"(?<!不是)(?<!非)測試[／/]定位"),
    re.compile(r"暫時(?:解除|通電|送電)|通電量測|需要通電|帶電量測"),
]
PRODUCTION_PAT = re.compile(r"恢復生產|連續生產|恢復量產|可以復電|可復電")
_SENT_SPLIT = re.compile(r"(?<=[。！？!?；;\n])")
_NUMBERED = re.compile(r"^(\s*)(\d+)([.、)）]\s*)(.*)$")
_PREFIX = re.compile(r"^(\s*(?:\d+[.、)）]|[-•*]|步驟\s*\d+[:：]|[☐☑]\s*\d+[.、)）])?\s*)")


def classify(text: str) -> str | None:
    """回傳 'test'（測試／定位，改寫）、'restore'（恢復生產／復電，刪除）或 None。"""
    if not any(p.search(text) for p in RESTORE_PATTERNS + TEST_PATTERNS):
        return None
    if any(p.search(text) for p in TEST_PATTERNS) and not PRODUCTION_PAT.search(text):
        return "test"
    return "restore" if any(p.search(text) for p in RESTORE_PATTERNS) else None


@dataclass
class FilterResult:
    text: str
    removed: list[str] = field(default_factory=list)      # 刪除的恢復生產／復電指示
    rewritten: list[str] = field(default_factory=list)    # 改寫成測試／定位句型的原文


def filter_restore(text: str, loto_id: str = "LOTO") -> FilterResult:
    res = FilterResult(text="")
    template = ENERGIZED_TEMPLATE.format(loto_id=loto_id)
    out_lines: list[str] = []
    for line in text.split("\n"):
        m = _NUMBERED.match(line)
        if m:                                              # 編號步驟：整步處理
            kind = classify(m.group(4))
            if kind == "restore":
                res.removed.append(m.group(4).strip())
                continue
            if kind == "test":
                res.rewritten.append(m.group(4).strip())
                line = f"{m.group(1)}{m.group(2)}{m.group(3)}{template}"
            out_lines.append(line)
            continue
        sents = []
        for sent in _SENT_SPLIT.split(line):
            kind = classify(sent) if sent.strip() else None
            if kind == "restore":                          # 恢復生產／復電：整句刪除
                res.removed.append(sent.strip())
                continue
            if kind == "test":
                res.rewritten.append(sent.strip())
                prefix = _PREFIX.match(sent).group(1)  # type: ignore[union-attr]
                sent = f"{prefix}{template}。"
            sents.append(sent)
        joined = "".join(sents)
        if line.strip() and not joined.strip():
            continue                                       # 整行都被刪掉
        out_lines.append(joined)
    if res.removed:                                        # 刪掉步驟後重新編號，最後加提醒（不是步驟）
        n = 0
        for i, line in enumerate(out_lines):
            m = _NUMBERED.match(line)
            if m:
                n += 1
                out_lines[i] = f"{m.group(1)}{n}{m.group(3)}{m.group(4)}"
        while out_lines and not out_lines[-1].strip():
            out_lines.pop()
        out_lines.append(CLOSING_REMINDER)
    res.text = "\n".join(out_lines)
    return res


# ── 3. 安全把關：取出步驟 ───────────────────────────────────────
_STEP = re.compile(r"^\s*(?:\d+[.、)）]|[-•*]|步驟\s*\d+[:：])\s*(.+?)\s*$")


def extract_steps(text: str) -> list[str]:
    text = text.replace(CLOSING_REMINDER, "")               # 結尾提醒不是步驟
    steps = [m.group(1) for line in text.splitlines() if (m := _STEP.match(line))]
    if not steps and text.strip():
        steps = [text.strip()[:300]]
    return steps


# Jev 不可用時的保守退路：關鍵字命中就標紅
KEYWORDS = {
    "needs_loto": ["拆", "更換", "調整", "清潔", "清除", "取出", "鬆開", "上油", "潤滑", "檢查滾輪", "量測直徑"],
    "enters_danger_zone": ["伸手", "伸入", "進入", "手取", "身體", "取出卡料", "模具內", "運轉範圍"],
    "high_voltage": ["電控箱", "配電", "高壓", "接線", "端子", "驅動器", "電源線"],
    "energized": ["寸動", "試送", "通電", "試沖", "噴油測試", "送電"],
}


def keyword_gate(step: str) -> dict[str, float]:
    return {k: (1.0 if any(w in step for w in words) else 0.0) for k, words in KEYWORDS.items()}
