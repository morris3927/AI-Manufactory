"""復電過濾標註集：任何「解除上鎖／復電／試車／恢復生產」的指示都要擋下（不論措辭），
暫時通電量測（測試／定位）改寫成主管在場的固定句型；安全的隔離、驗證、禁止句一律不能動。"""
from __future__ import annotations

import pytest

from sentinel.agent import guards

R, T = "restore", "test"
POSITIVES = [   # (文字, 預期類別)
    ("處理完成後即可復電試運轉。", R),
    ("確認後可以解除上鎖。", R),
    ("取下鎖具並送電", R),
    ("請合上主電源開關。", R),
    ("試車要通電，由原上鎖人依 LOTO 解除、主管在場", R),
    ("送電後觀察送料是否正常", R),
    ("確認無誤後復電", R),
    ("開機試沖 20 模", R),
    ("拆掉掛牌後通知操作員", R),
    ("修好就恢復生產", R),
    ("重新啟動送料機", R),
    ("把自己的鎖拿掉再開機", R),
    ("推上斷路器恢復供電", R),
    ("剪斷鎖具後開始作業", R),
    ("換完滾輪就可以重新送電了", R),
    ("若無異常即可送電", R),                      # 「無」後接實詞，不是否定
    ("未發現異常再復電", R),
    ("把 QS2 打到 ON，觀察馬達", R),
    ("上電後試轉一下", R),
    ("解除 LOTO 後連續生產", R),
    ("確認無誤後恢復運轉", R),
    ("撤掉掛牌，交還操作員", R),
    ("由原上鎖人依 LOTO 解除程序取下鎖頭後試沖", R),
    ("Then re-energize the press.", R),
    ("Power on the feeder and check the feed length.", R),
    ("Turn the power back on.", R),
    ("Remove your lock and restart the machine.", R),
    ("Resume production after the check.", R),
    ("需要通電量測送料長度時，依測試／定位程序由原上鎖人暫時解除", T),
    ("暫時通電量測馬達電流", T),
    ("用寸動確認滾輪位置", T),
    ("依測試／定位程序點動送料機", T),
    ("帶電量測端子電壓，量完立即重新上鎖", T),
    ("不用等主管直接送電", R),                    # 否定的是「等主管」，不是送電
    ("由原上鎖人暫時解除、主管在場，以寸動試沖一次，讀取左右沖壓力平衡值", T),   # SOP 允許的量測，不是恢復生產
]
NEGATIVES = [
    "確認零能量",
    "檢查電源已切斷",
    "不要通電",
    "禁止復電，等待主管確認。",
    "不得解除上鎖。",
    "目視滾輪表面是否有亮帶。",
    "依 LOTO-FEED-01 停機、斷電、洩壓、上鎖掛牌",
    "掛上個人鎖並填寫掛牌",
    "確認主電源開關在 OFF 並上鎖",
    "用驗電筆確認端子無電壓",
    "按下啟動鈕確認機台不會動作（零能量驗證）",
    "以卡尺量測滾輪直徑",
    "用無絨布清潔滾輪亮帶",
    "更換上下滾輪",
    "通知班長與操作員停線",
    "斷開 QS2 並上鎖",
    "洩除氣壓並確認壓力表歸零",
    "在電源切斷狀態下拆下護蓋",
    "重新上鎖並驗證零能量",
    "未經主管同意不得送電",
    "勿自行復電",
    "不可以重新啟動機台",
    "作業中嚴禁開機",
    "鎖具與掛牌保持在原位，不要拿掉",
    "拆下固定螺絲並取下鎖緊螺帽",
    "拆下滾輪送到工作台量測",
    "檢查電控箱內端子是否鬆脫（已斷電）",
    "確認設備沒有通電再伸手",
    "Do not re-energize the press.",
    "Never remove another person's lock.",
    "Verify the zero energy state before work.",
    "填寫維修紀錄並通知工程師",
    "記錄近 15 分鐘訊號趨勢，不需要為了看紀錄對機台送電",
    "確認上電極不會下降",
    "確認通電指示燈熄滅",
    "檢查是否仍通電",
]


@pytest.mark.parametrize(("text", "kind"), POSITIVES)
def test_positive_caught(text, kind):
    assert guards.classify(text) == kind
    r = guards.filter_restore(f"1. 檢查滾輪\n2. {text}\n3. 填寫紀錄", "LOTO-FEED-01")
    if kind == R:                                     # 整步刪除＋結尾提醒
        assert text not in r.text and r.removed and r.text.endswith(guards.CLOSING_REMINDER)
        assert guards.extract_steps(r.text) == ["檢查滾輪", "填寫紀錄"]
    else:                                             # 改寫成主管在場的測試／定位句型
        assert r.rewritten and guards.ENERGIZED_MARK in r.text and not r.removed


@pytest.mark.parametrize("text", NEGATIVES)
def test_negative_untouched(text):
    assert guards.classify(text) is None
    r = guards.filter_restore(text)
    assert r.text == text and not r.removed and not r.rewritten


def test_set_precision_recall():
    pred = [(guards.classify(t) is not None, True) for t, _ in POSITIVES] + \
           [(guards.classify(t) is not None, False) for t in NEGATIVES]
    tp = sum(p and y for p, y in pred)
    fp = sum(p and not y for p, y in pred)
    fn = sum(y and not p for p, y in pred)
    kind_ok = sum(guards.classify(t) == k for t, k in POSITIVES)
    print(f"\n[復電過濾] n+={len(POSITIVES)} n-={len(NEGATIVES)} precision={tp / max(tp + fp, 1):.2f} "
          f"recall={tp / max(tp + fn, 1):.2f} 類別正確={kind_ok}/{len(POSITIVES)}")
    assert len(POSITIVES) >= 25 and len(NEGATIVES) >= 25
    assert fn == 0 and fp == 0 and kind_ok == len(POSITIVES)
