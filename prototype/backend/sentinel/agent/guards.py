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
# 否定：否定詞（＋助動詞／虛字，或「為了…／對…」這類目的、對象片語）緊接著動詞 → 這是禁止句，不是指示
# （「禁止復電」「AI 不會宣告可以復電」「不需要為了看紀錄對機台送電」）。
# 「若無異常即可送電」「未發現異常再復電」「不用等主管直接送電」：否定的是別的東西 → 照樣過濾。
_NEG_ZH = re.compile(r"(?:禁止|嚴禁|切勿|不得|不准|不可|不要|不能|不會|不需|不必|無須|毋須|勿|未|別|沒有|沒|無|不|防止|避免|以免|是否|有無|有沒有)"
                     r"(?:要|可|可以|能|會|得|需|需要|必|必要|用|仍|還|已|有|被|曾)?"
                     r"(?:再|自行|擅自|隨意|任意|直接|先|誤|宣告|宣告可以|讓人)?"
                     r"(?:(?:為了|對|給|替|把|將|在)[^即就再才後然便則若，。；]{0,14})?$")
_NEG_EN = re.compile(r"(?i)\b(?:not|never|n't|no|don't|avoid|without)\s+(?:\w+\s+){0,2}$")
_LOCK = r"(?:自己的|個人的|所有的|全部的|個人|原本的)?(?:上鎖|鎖具|掛牌|吊牌|標籤|安全鎖|個人鎖|掛鎖|LOTO\s*鎖|鎖頭|鎖(?![緊固定附螺]))"
RESTORE_PATTERNS = [
    # 復電／送電／通電（「通電量測」「暫時通電」是測試／定位，由 TEST_PATTERNS 處理）
    re.compile(r"復電|送電|上電(?!極)|恢復(?:供電|電源|通電|氣源|動力)|重新(?:通電|送電|供電|上電)|再(?:通電|送電)"
               r"|通電(?!指示|燈|狀態|中)"),
    # 解除／取下鎖具、掛牌
    re.compile(r"(?:解除|取下|拆除|拆掉|拆下|移除|拿掉|拿下|打開|卸下|剪斷|剪開|撤掉|撤除|摘下|解開)" + _LOCK),
    re.compile(r"(?:LOTO|上鎖掛牌|上鎖)\s*(?:程序)?\s*解除|依\s*LOTO[-\w]*\s*解除|解除\s*(?:LOTO|隔離)"),
    # 合上電源、ON
    re.compile(r"(?:合上|推上|扳上|投入|打開|開啟|接通|恢復)(?:主)?(?:電源(?!箱|線)|斷路器|隔離開關|NFB|QS\d*|無熔絲開關|開關)"),
    re.compile(r"(?i)(?:打到|切到|轉到|扳到|推到|切回|設為|撥到)\s*ON\b|電源\s*ON\b"),
    # 開機、啟動、試車、恢復生產
    re.compile(r"開機(?!前)|重新(?:啟動|開機|運轉|開動)|再(?:啟動|開機)|啟動(?:機台|設備|沖床|送料機|馬達|主機|生產|運轉)"),
    re.compile(r"試車|試沖|試運轉|試轉|試產|試跑|恢復(?:生產|運轉|量產|作業)|連續生產|重新生產|復機|復線|開線"),
    re.compile(r"(?i)restore (?:the )?power|re-?energi[sz]e|power (?:it |the \w+ )?(?:back )?on|turn (?:the )?power (?:back )?on"
               r"|switch (?:it |the \w+ )?on|remove (?:the |your |all |his |her )?(?:lock|tag)|take off (?:the |your )?lock"
               r"|restart|resume (?:production|operation)|start (?:up )?the (?:press|machine|feeder|line)|test run|trial run"),
]
# 測試／定位：強標記（SOP 的測試／定位程序、暫時解除、通電量測）→ 改寫成主管在場的固定句型，
# 除非同一句明講恢復生產；弱標記（寸動、點動）只有在沒有任何試車／試沖字眼時才算測試。
TEST_STRONG = [
    re.compile(r"(?<!不是)(?<!非)測試[／/]定位"),
    re.compile(r"暫時(?:解除|通電|送電|復電|上電)|通電量測|需要通電|帶電量測|通電(?:狀態下)?(?:量測|測量)"),
]
TEST_WEAK = [re.compile(r"寸動|點動|(?i:\bjog)")]
TEST_PATTERNS = TEST_STRONG + TEST_WEAK
PRODUCTION_PAT = re.compile(r"恢復生產|連續生產|恢復量產|恢復運轉|可以復電|可復電|開機|重新啟動|復機|(?i:restart|resume)")
TRIAL_PAT = re.compile(r"試車|試沖|試運轉|試轉|試產|試跑|(?i:test run|trial run)")
_SENT_SPLIT = re.compile(r"(?<=[。！？!?；;\n])")
_NUMBERED = re.compile(r"^(\s*)(\d+)([.、)）]\s*)(.*)$")
_PREFIX = re.compile(r"^(\s*(?:\d+[.、)）]|[-•*]|步驟\s*\d+[:：]|[☐☑]\s*\d+[.、)）])?\s*)")
_CLAUSE = re.compile(r"[，,、；;。！？!?：:\n（）()]")


def _hits(patterns: list[re.Pattern], text: str) -> bool:
    """任一 pattern 在任一子句中出現、而且前面不是否定詞。"""
    for clause in _CLAUSE.split(text):
        for p in patterns:
            for m in p.finditer(clause):
                before = clause[:m.start()]
                if not (_NEG_ZH.search(before) or _NEG_EN.search(before)):
                    return True
    return False


def classify(text: str) -> str | None:
    """回傳 'test'（測試／定位，改寫）、'restore'（恢復生產／復電／解除上鎖，刪除）或 None。"""
    if text.strip() == CLOSING_REMINDER:
        return None
    production = _hits([PRODUCTION_PAT], text)
    if not production and (_hits(TEST_STRONG, text) or (_hits(TEST_WEAK, text) and not _hits([TRIAL_PAT], text))):
        return "test"
    return "restore" if _hits(RESTORE_PATTERNS, text) else None


@dataclass
class FilterResult:
    text: str
    removed: list[str] = field(default_factory=list)      # 刪除的恢復生產／復電指示
    rewritten: list[str] = field(default_factory=list)    # 改寫成測試／定位句型的原文


def _filter_line(line: str, template: str, res: FilterResult) -> str | None:
    """過濾一行；回傳 None 表示整行刪除。filter_restore 與串流用的 StreamFilter 共用。"""
    m = _NUMBERED.match(line)
    if m:                                                  # 編號步驟：整步處理
        kind = classify(m.group(4))
        if kind == "restore":
            res.removed.append(m.group(4).strip())
            return None
        if kind == "test":
            res.rewritten.append(m.group(4).strip())
            return f"{m.group(1)}{m.group(2)}{m.group(3)}{template}"
        return line
    sents = []
    for sent in _SENT_SPLIT.split(line):
        kind = classify(sent) if sent.strip() else None
        if kind == "restore":                              # 恢復生產／復電：整句刪除
            res.removed.append(sent.strip())
            continue
        if kind == "test":
            res.rewritten.append(sent.strip())
            prefix = _PREFIX.match(sent).group(1)  # type: ignore[union-attr]
            sent = f"{prefix}{template}。"
        sents.append(sent)
    joined = "".join(sents)
    if line.strip() and not joined.strip():
        return None                                        # 整行都被刪掉
    return joined


def filter_restore(text: str, loto_id: str = "LOTO") -> FilterResult:
    res = FilterResult(text="")
    template = ENERGIZED_TEMPLATE.format(loto_id=loto_id)
    out_lines = [o for line in text.split("\n") if (o := _filter_line(line, template, res)) is not None]
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


class StreamFilter:
    """LLM 串流用：只放出「完整的一行」，而且每行先過復電過濾才送到前端（未過濾的字不會出現在畫面上）。

    放出的文字與 filter_restore(全文) 一致（差別只在原文編號不連續時的重新編號）；
    最終存檔的內容仍以 filter_restore(全文) 為準。
    """

    def __init__(self, loto_id: str = "LOTO"):
        self.template = ENERGIZED_TEMPLATE.format(loto_id=loto_id)
        self.res = FilterResult(text="")
        self.buf = ""
        self.n = 0
        self.out: list[str] = []

    @property
    def text(self) -> str:
        return "\n".join(self.out)

    def _line(self, line: str) -> str | None:
        o = _filter_line(line, self.template, self.res)
        if o is None:
            return None
        m = _NUMBERED.match(o)
        if m:
            self.n += 1
            if self.res.removed:                           # 前面刪過步驟：後面的編號往前補
                o = f"{m.group(1)}{self.n}{m.group(3)}{m.group(4)}"
        self.out.append(o)
        return o

    def feed(self, chunk: str) -> list[str]:
        """餵入一段串流文字，回傳這次可以放出的（已過濾）完整行。"""
        self.buf += chunk
        *lines, self.buf = self.buf.split("\n")
        return [o for line in lines if (o := self._line(line)) is not None]

    def finish(self) -> list[str]:
        out = []
        if self.buf:
            line, self.buf = self.buf, ""
            if (o := self._line(line)) is not None:
                out.append(o)
        if self.res.removed:
            self.out.append(CLOSING_REMINDER)
            out.append(CLOSING_REMINDER)
        return out


# ── 3. 安全把關：取出步驟 ───────────────────────────────────────
_STEP = re.compile(r"^\s*(?:\d+[.、)）]|[-•*]|步驟\s*\d+[:：])\s*(.+?)\s*$")


def step_text(line: str) -> str | None:
    """一行若是步驟（編號或條列）回傳步驟文字；結尾提醒不是步驟。"""
    if line.strip() == CLOSING_REMINDER:
        return None
    m = _STEP.match(line)
    return m.group(1) if m else None


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
