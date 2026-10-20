"""維修 Agent 的程式強制規則（不靠提示詞；08_系統設計.md §5.7）。"""
from __future__ import annotations

import re

from ..dispatch.workorder import PRE_LOTO, WoStatus

# ── 1／2. LOTO 先行、作業許可 ───────────────────────────────────
SAFE_PHASES = {"summary", "prepare", "isolate"}           # LOTO 完成前只能看這些段落
RESTRICTED_PHASES = {"inspect", "repair", "verify", "restore"}
PRE_LOTO_TOOLS = {"get_workorder", "get_loto", "get_sop"}   # get_sop 只開放 summary／prepare／isolate
ALL_TOOLS = {"get_workorder", "get_loto", "get_machine_signals", "get_sop", "search_cases", "record_step",
             "request_supervisor"}


class ToolDenied(Exception):
    """工具或段落在目前工單狀態下不開放。"""


def restricted(status: str, require_permit: bool = False) -> bool:
    """True：還沒到可以給排查／維修步驟的狀態。"""
    s = WoStatus(status)
    if s in PRE_LOTO:
        return True
    return require_permit and s == WoStatus.LOTO_DONE


def allowed_tools(status: str, require_permit: bool = False) -> set[str]:
    return set(PRE_LOTO_TOOLS) if restricted(status, require_permit) else set(ALL_TOOLS)


def check_tool(name: str, args: dict, status: str, require_permit: bool = False) -> None:
    if name not in allowed_tools(status, require_permit):
        raise ToolDenied(f"工具 {name} 在 {status} 狀態不開放（LOTO 先行）")
    if name == "get_sop" and restricted(status, require_permit) and args.get("phase") not in SAFE_PHASES:
        raise ToolDenied(f"LOTO 完成前不提供 SOP 的 {args.get('phase')} 段落")


# ── 4. 不能叫人復電 ─────────────────────────────────────────────
RESTORE_REPLACEMENT = "請由原上鎖人依 LOTO 解除程序執行"
_NEG = r"(?<!禁止)(?<!不得)(?<!不可)(?<!不可以)(?<!不要)(?<!勿)(?<!不能)"
RESTORE_PATTERNS = [
    re.compile(_NEG + r"(?:復電|送電|恢復供電|恢復電源|重新通電|恢復通電|重新送電)"),
    re.compile(_NEG + r"(?:解除|取下|拆除|移除|拿掉|打開)(?:上鎖|鎖具|掛牌|安全鎖|個人鎖|掛鎖|LOTO\s*鎖)"),
    re.compile(_NEG + r"(?:合上|推上|打開|開啟)(?:主)?(?:電源開關|斷路器|隔離開關|電源)"),
    re.compile(r"(?i)(?<!not )(?:restore|re-?energi[sz]e|turn the power back on|remove (?:the )?lock)"),
]
_SENT_SPLIT = re.compile(r"(?<=[。！？!?；;\n])")
_PREFIX = re.compile(r"^(\s*(?:\d+[.、)）]|[-•*]|步驟\s*\d+[:：])?\s*)")


def filter_restore(text: str) -> tuple[str, list[str]]:
    """把「可以復電」「解除上鎖」這類指示換成「請由原上鎖人依 LOTO 解除程序執行」。回傳 (新文字, 被換掉的句子)。"""
    removed: list[str] = []
    out: list[str] = []
    for sent in _SENT_SPLIT.split(text):
        if sent and any(p.search(sent) for p in RESTORE_PATTERNS):
            removed.append(sent.strip())
            prefix = _PREFIX.match(sent).group(1)  # type: ignore[union-attr]
            tail = "\n" if sent.endswith("\n") else ("。" if sent.rstrip().endswith("。") else "")
            out.append(f"{prefix}{RESTORE_REPLACEMENT}{'。' if not tail else tail}")
        else:
            out.append(sent)
    return "".join(out), removed


# ── 3. 安全把關：取出步驟 ───────────────────────────────────────
_STEP = re.compile(r"^\s*(?:\d+[.、)）]|[-•*]|步驟\s*\d+[:：])\s*(.+?)\s*$")


def extract_steps(text: str) -> list[str]:
    steps = [m.group(1) for line in text.splitlines() if (m := _STEP.match(line))]
    if not steps and text.strip():
        steps = [text.strip()[:300]]
    return steps


# Jev 不可用時的保守退路：關鍵字命中就標紅
KEYWORDS = {
    "needs_loto": ["拆", "更換", "調整", "清潔", "清除", "取出", "鬆開", "上油", "潤滑", "檢查滾輪", "量測直徑"],
    "enters_danger_zone": ["伸手", "伸入", "進入", "手取", "身體", "取出卡料", "模具內", "運轉範圍"],
    "high_voltage": ["電控箱", "配電", "高壓", "接線", "端子", "驅動器", "電源線"],
}


def keyword_gate(step: str) -> dict[str, float]:
    return {k: (1.0 if any(w in step for w in words) else 0.0) for k, words in KEYWORDS.items()}
