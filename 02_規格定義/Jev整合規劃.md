# Jev（TypeSafe System One Model）整合規劃

> 調查日期：2026-10-20
> 來源：TypeSafe 官方部落格、docs.typesafe.ai、官方 SDK、社群整理、OpenRouter Jev 文件（2026-10 版）
> 標 🔬 的項目需要我們自己實測或確認。
> **存取方式（定案）**：OpenRouter **Decisions API**（`POST https://openrouter.ai/api/alpha/decisions`），model = `typesafe/jev-1.14`（2026-10-14 發布，OpenRouter 實測 P50 延遲 0.17 秒）。**不是** chat completions 介面，OpenAI SDK 不能用；官方 typesafe-sdk 可以把 base_url 指向 OpenRouter。

---

## 0. 結論先講

1. **Jev 是「只做判斷、不寫文章」的 AI 模型。** 你給它一份結構化的狀態（JSON），加上事先定義好的題目（是非題、選擇題、評分題），它在 **70–500ms**（通常約 100ms）內回傳**答案與每個選項的機率**。它不會產生選項以外的答案，價格幾乎免費（輸入 $0.042／百萬 token，輸出免費）。
2. **剛好補上我們架構的空缺。** 原本只有兩層：規則（毫秒級、死板）和 LLM（幾秒、會寫、很貴）。中間「需要一點語意判斷但要很快」的事情，例如這個事件算哪一類、是不是誤報、最可能是哪個根因，以前只能硬寫規則或丟給 LLM 慢慢想。
3. **新敘事：快思慢想三層決策**（命名直接來自 Kahneman 的 System 1／System 2）：

   | 層 | 誰 | 速度 | 做什麼 |
   |---|---|---|---|
   | 反射 | 規則引擎 / CEP | 毫秒 | 確定性、攸關安全、要可稽核的判斷 |
   | **直覺（System 1）** | **Jev** | **約 100ms** | **有限選項的語意判斷，附帶機率** |
   | 深思（System 2） | LLM Agent | 秒 | 產生診斷說明、現場對話、整理知識 |

   95%／5% 的比例不用改：規則仍處理約 95% 的事件，剩下 5% 交給 AI。只是 AI 內部再分成兩層：Jev 先快速判斷，**真正需要寫文字或對話時才叫 LLM**。
4. **Jev 1.14 可以看圖片**，還有 **Box** 題型（回傳目標在圖中的位置框）。這讓它能直接判斷「沒有訓練資料的視覺事件」，例如送料口有沒有卡料、有沒有煙霧，**解決最大的資料集風險**（見 2.2 的 V1、V2）。
   - 但官方說明：**帶圖片的請求會進入長上下文模式，比較慢、也比較不準**，而且圖片與 Box 功能還是早期功能，格式可能變動。
   - 所以分工是：**YOLO 在本機逐幀跑**（毫秒級、不出廠）；**Jev 看關鍵幀**（觸發時或每幾秒一次），負責確認與零樣本判斷。
5. 它是**雲端 API**，會和初選 4.2「預設地端」的主張衝突，對策見第 6 節。

---

## 1. Jev 是什麼

| 項目 | 內容 |
|---|---|
| 公司 | TypeSafe AI（潛行兩年後公開） |
| 名稱由來 | 經濟學家 William Stanley Jevons（Jevons 悖論：效率提升反而讓用量暴增） |
| 類型 | System One Model：快速、結構化、可以直接給程式使用的判斷 |
| 訓練方法 | RLCD（Reinforcement Learning for Calibrated Decisions），目標是**機率校準**：模型說 90% 的時候，大約 90% 是對的 |
| 生成方式 | 平行取樣，一次請求同時回答所有題目，不是逐字生成 |
| 輸入 | 文字、結構化狀態（JSON）、**圖片**（PNG／JPEG，每邊 256–1,568 像素，base64 data URL）。不支援聲音、影片 |
| 輸出 | 預先定義的型別：Noul（是非）、Choice（選擇）、Score（評分）、**Box（圖中位置，1.14 新增）** |
| 延遲 | 文字請求 OpenRouter P50 **0.17 秒**；帶圖片的請求較慢 🔬 需實測 |
| 價格 | 輸入 $0.042／百萬 token，輸出免費 |
| 限制 | Context 262K token；文字超過約 32,000 bytes 或帶圖片 → 長上下文模式（較慢、較不準）；Choice 最多 255 個選項；Score 2–10 級；Box 一題只找一個目標，**沒有「找不到」的答案**（要搭配 Noul 確認目標是否存在） |
| 版本 | `typesafe/jev-1.14`（鎖定）；`~typesafe/jev-latest` 會自動更新 |
| 權重 | **不開源**，只能用雲端 API；官方 SDK 為 MIT 授權 |

### 1.1 四種題型

| 題型 | 回傳 | 信心度 | 本案用途 |
|---|---|---|---|
| **Noul**（是非） | `p`：命題為真的機率（0–1） | `|2p − 1|` | 是否為誤報？是否需要停機？回報內容是否完整？ |
| **Choice**（2–255 選 1） | 最高選項＋每個選項的機率 | `(p_max − 1/n) / (1 − 1/n)` | 事件類別、根因、派工技能類別 |
| **Score**（2–10 級） | 機率加權後的級數（**從 0 起算**）＋每一級的機率＋legend | 分布的集中程度 | 嚴重度（S1/S2/S3）、緊急程度 |
| **Box**（1.14 新增） | 目標在圖中的 `top／bottom／left／right`（0–1 的比例）＋信心度 | 回傳值內附 | 在儀表板關鍵幀上框出卡料、煙霧位置 |

### 1.2 它擅長什麼、不擅長什麼

| 擅長 | 不擅長，要改用程式處理 |
|---|---|
| 從有限選項中判斷 | 產生文字（交給 LLM） |
| 附帶校準過的機率 → 可以用信心度決定走哪條路 | 算術、比大小、數數 → 在程式裡先算好，例如 `idle_seconds: 312, over_threshold: true` |
| 很多題同時問，幾乎不增加延遲 | 日期、時間的先後與間隔 → 程式先算 |
| 便宜，可以每個事件都問 | 雙重否定、多步推理 → 拆成多題 |
| | state 塞太多不相關內容 → 只放跟判斷有關的欄位 |
| | 選項順序會影響結果 → 要做選項順序對調測試 |

「零幻覺」的意思是**不會回答選項以外的東西**，但**仍然可能選錯**。所以信心度門檻和規則把關都還是需要。

---

## 2. 放進 Sentinel AI 的哪裡

```
L1 感測（YOLO / Log 判定 / 門禁）
        ↓ 結構化事件
L3 快路：規則引擎 / CEP ──── 確定性規則命中 → 直接分級、派工（約 95%）
        │
        └─ 規則判不了、或需要語意判斷
                ↓
   ★ L3.5 直覺層：Jev（文字約 0.2 秒，一次請求問多題）
        │  V1 關鍵幀確認（看圖：YOLO 觸發的事件是不是真的）
        │  V2 零樣本視覺判斷（看圖：卡料、煙霧、物料堆疊，不需要訓練資料）
        │  J1 事件分類／誤報判斷
        │  J2 嚴重度補判（只能升級，不能降級）
        │  J3 根因 top-k 機率
        ↓ 依信心度分流
   高信心 → 自動派工（帶 Jev 的根因機率）
   低信心 → 交給 LLM 深思，或請人確認
        ↓
L4 慢路：LLM（診斷說明文字、維修 Agent 對話）
        │  J4 Agent 建議的安全把關（Jev）
        ↓
L6 結案
        │  J5 回報品質檢查、知識結構化分類（Jev）
        ↓ 寫回知識庫
```

### 2.1 五個介入點

| # | 位置 | 題型 | 解決什麼 | 優先 |
|---|---|---|---|---|
| **J1** | 規則引擎之後 | Choice 事件類別 ＋ Noul 是否誤報 | 原本「工安／設備」分流是寫死的；YOLO 誤報（例如人在辦公區沒戴安全帽）以前沒辦法過濾 | **P0** |
| **J2** | 規則沒有命中的組合 | Score 嚴重度 1–3 | 長尾的事件組合不用每種都寫規則 | **P0** |
| **J3** | 派工前 | Choice 根因（從案例庫的故障模式中選，最多 255 個） | **把 story-2「80% 感測器髒污、15% 皮帶鬆脫」變成真的校準機率**，不再是 LLM 隨口給的數字 | **P0（主打）** |
| J4 | 維修 Agent 給建議時 | Noul：是否涉及需停機上鎖（LOTO）／高壓／進入運轉區 | Agent 的建議經過安全把關，涉及危險操作就強制要求主管確認 | P1 |
| J5 | 結案寫回知識庫前 | Noul 回報是否完整 ＋ Choice 根因歸類 | 避免把垃圾回報寫進知識庫；知識自動結構化 | P1 |

### 2.2 兩個視覺介入點（Jev 1.14 看圖）

| # | 什麼時候送圖 | 題目 | 解決什麼 | 優先 |
|---|---|---|---|---|
| **V1** | YOLO 偵測到違規（例如沒戴安全帽）時，送**那一幀的人員裁切圖** | Noul：圖中人員是否確實沒有配戴安全帽？Noul：人員是否位於需要配戴的作業區？ | 過濾 YOLO 誤報（帽子被擋住、角度問題），降低誤派工 | P0 |
| **V2** | ① Log 出現異常徵兆（送料速率下降、停機）時；② 每 5 秒定時對機台 ROI 取一幀 | Noul：送料口是否有板材堆疊、歪斜或卡住？Noul：機台周圍是否有煙霧或火光（銲接工位以外）？**Box：卡住的板材位置** | **沒有公開資料集的事件也能偵測**，不用自己標註與訓練 | **P0（主打）** |

設計原則：
- **YOLO 負責「快」，Jev 負責「懂」**。YOLO 在本機逐幀跑；Jev 只看關鍵幀，每支攝影機最多每 5 秒一張，觸發時另外加送。
- **送出去的圖只裁機台 ROI**；人員圖只在 V1 確認時送，而且先把臉部模糊化（見第 6 節）。
- V2 的結果仍然交給規則引擎做跨模態確認：**Jev 看到卡料 ＋ Log 送料速率歸零** → 確認卡料。單一來源不直接派工。
- 圖片請求比較慢、也比較不準（官方說明）→ V2 用「連續 2 張關鍵幀都判定 > 0.7」才成立，降低單張誤判。

### 2.3 J3 為什麼是主打

初選寫的是「LLM 檢索案例後**排序**出 top-k 根因」。LLM 給的「80%」只是它寫出來的文字，沒有校準過，評審一問就會露餡。

改成 Jev 之後：
1. **故障模式目錄**（自建，例如沖壓段 30 種、銲接段 20 種）做成 Choice 的選項，每個選項附一句判斷標準。
2. state 放入：本次事件、近 10 分鐘的事件時間線、機台資訊、RAG 檢索到的 3–5 筆相似歷史案例。
3. Jev 回傳**每個根因的機率**，約 100ms。
4. 派工通知直接列出前 3 名根因與機率，再附上 SOP（查表）。
5. LLM 只負責把結果寫成給人看的說明，而且是**非同步**執行，不會卡住派工。

→ 派工通知從「等 LLM 5–10 秒」變成「**1 秒內附帶根因機率送出**」，LLM 的說明晚幾秒再補上。
→ **系統測試報告可以畫校準曲線**（reliability diagram）：Jev 說 70% 的那些案例，實際上有多少比例是對的。這是很有說服力的量化結果。

---

## 3. 實作細節

### 3.1 安裝與設定

**呼叫方式**：OpenRouter Decisions API（不是 chat completions，OpenAI SDK 不能用）

```bash
export OPENROUTER_API_KEY=sk-or-...
pip install httpx            # 直接打 HTTP；或 pip install typesafe-sdk 再把 base_url 指到 OpenRouter
```

```bash
# 最小測試
curl https://openrouter.ai/api/alpha/decisions \
  -H "Authorization: Bearer $OPENROUTER_API_KEY" \
  -H "Content-Type: application/json" \
  -d '{
    "model": "typesafe/jev-1.14",
    "state": {"event": "沖床送料速率 10 秒內由 42 spm 降到 0，影像判定送料口有板材"},
    "questions": {
      "is_jam": {"type": "noul", "instructions": "這是否為送料卡料？"}
    }
  }'
```

**回傳格式**（取自 OpenRouter 文件的實際回應）：

```json
{
  "id": "gen-dec-...",
  "model": "typesafe/jev-1.14-20261014",
  "provider": "TypeSafe",
  "answers": {
    "is_bug": { "type": "noul", "noul": 0.96 },
    "team":   { "type": "choice", "choice": "payments", "confidence": 0.67,
                "probabilities": { "payments": 0.78, "frontend": 0.22, "account": 0 } },
    "urgency":{ "type": "score", "score": 1.99, "confidence": 0.99,
                "probabilities": { "0": 0, "1": 0, "2": 1 },
                "legend": { "0": "...", "1": "...", "2": "..." } },
    "where":  { "type": "box", "box": { "top": 0.49, "bottom": 0.89, "left": 0.58, "right": 0.96 },
                "confidence": 0.93 }
  },
  "usage": { "input_tokens": 476, "output_tokens": 70, "cost": 0.000019992 }
}
```

- Noul 的 `criteria` 可選填 `{"true": "...", "false": "..."}`，把是與否的判斷標準寫清楚。
- Choice 的 `criteria` 值可以是 `null`（只給選項名稱）。
- 每次回應都有 `usage.cost`（美元）→ 直接記錄，效益報告的 AI 成本就是實測值。

**圖片格式**：放在 `state` 裡，`{"type": "image_url", "image_url": {"url": "data:image/jpeg;base64,..."}}`
- 只接受 PNG、JPEG，每邊 256–1,568 像素、總像素 78,400–2,458,624。小於下限回 400，超過上限回 413。
- 不套用 EXIF 旋轉，不接受動畫 PNG，不抓遠端網址。
- Jev 可以讀取 `state` 任何深度的圖片，例如 `{"roi_image": {...}}`。

### 3.2 事件 state 設計原則

- **只放判斷需要的欄位**，不要整包 log 塞進去。
- **數字和時間先在程式裡算好**，給 Jev 的是結論，例如 `"idle_over_threshold": true`。
- **不放人名、影像、工廠機密**，只放代碼（資安，見第 6 節）。

```python
# sentinel/jev/state.py
def build_state(evt, timeline, machine, similar_cases):
    return {
        "event": {
            "line": evt.line,                      # "stamping"
            "machine": evt.machine_type,           # "250t 沖床"
            "source": evt.source,                  # "vision" / "mes" / "fused"
            "type": evt.type,                      # "feed_jam_suspected"
            "rule_severity": evt.rule_severity,    # 規則引擎判的等級，可能是 None
            "facts": evt.facts,                    # 程式先算好的布林值／區間
        },
        "timeline_10min": [t.summary for t in timeline],   # 例如 "10:31:02 送料速率 0"
        "machine_context": {
            "last_maintenance_days_ago": machine.days_since_pm,
            "open_issues": machine.open_issue_codes,
        },
        "similar_cases": [c.short for c in similar_cases],  # RAG 前 3–5 筆
    }
```

### 3.3 共用 client

```python
# sentinel/jev/client.py
import os, httpx

URL = "https://openrouter.ai/api/alpha/decisions"
MODEL = "typesafe/jev-1.14"          # 鎖定版本，門檻是對這個版本調的

class Jev:
    def __init__(self, timeout=3.0):
        self.http = httpx.AsyncClient(
            timeout=timeout,
            headers={"Authorization": f"Bearer {os.environ['OPENROUTER_API_KEY']}"},
        )

    async def ask(self, state, questions):
        r = await self.http.post(URL, json={"model": MODEL, "state": state, "questions": questions})
        r.raise_for_status()
        d = r.json()
        return d["answers"], d["model"], d["usage"]["cost"]
```

逾時（3 秒）或錯誤時：**規則引擎的判斷照樣派工**，只是不附 Jev 的結果。Jev 掛掉不能讓系統停擺。

### 3.4 J1＋J2＋J3：文字事件判斷（一次請求問完）

```python
# sentinel/jev/judge.py
from .catalog import load_failure_modes   # {"feed_roller_wear": "送料速率在數分鐘內逐步下降...", ...}

def event_questions(line):
    return {
        "category": {
            "type": "choice",
            "instructions": "這個事件屬於哪一類？",
            "criteria": {
                "safety": "與人員安全有關：PPE、闖入危險區、人員倒地",
                "equipment": "與機台運作有關：卡料、閒置、過熱、異常碼",
                "quality": "與產品品質有關：尺寸、外觀瑕疵",
                "false_alarm": "感測誤判或正常作業現象，不需處理",
            },
        },
        "is_false_alarm": {
            "type": "noul",
            "instructions": "依據 facts 與 timeline，這個事件是否為誤報？",
            "criteria": {"true": "不需要任何人到場處理", "false": "需要有人到場處理"},
        },
        "severity": {
            "type": "score",
            "instructions": "依據對人員安全與停線的影響評定嚴重度。",
            "criteria": [
                "S1：無影響，記錄即可",
                "S2：需要人員到場處理；包含單一機台卡料、停機、閒置，但沒有人員受傷或起火風險",
                "S3：有人員受傷、起火、爆炸等立即危險，或多台機台同時停止",
            ],
            # 2026-10-20 實測：原本 S3 寫「已經或即將造成停線」，Jev 把單純卡料判成 S3（0.97）。
            # Jev 照字面讀，卡料確實會停線 → 標準要寫清楚「單機停機屬於 S2」。
        },
        "root_cause": {
            "type": "choice",
            "instructions": "最可能的根因是哪一個？只依據 event、timeline 與 similar_cases 判斷。",
            "criteria": load_failure_modes(line),   # 最多 255 個
        },
    }

async def judge(jev, state, line):
    a, model, cost = await jev.ask(state, event_questions(line))
    rc = a["root_cause"]
    top3 = sorted(rc["probabilities"].items(), key=lambda kv: -kv[1])[:3]
    return {
        "category": a["category"]["choice"],
        "category_conf": a["category"]["confidence"],
        "p_false_alarm": a["is_false_alarm"]["noul"],
        "severity_score": a["severity"]["score"],     # 0–2，0 = S1
        "root_cause_top3": top3,
        "root_cause_conf": rc["confidence"],
        "model": model, "cost": cost,
    }
```

### 3.5 V1＋V2：關鍵幀視覺判斷

```python
# sentinel/jev/vision.py
import base64, cv2

def to_part(frame_bgr, roi=None, max_side=768):
    """裁 ROI → 縮放到 Jev 允許的範圍 → JPEG base64 image part"""
    if roi:
        x1, y1, x2, y2 = roi
        frame_bgr = frame_bgr[y1:y2, x1:x2]
    h, w = frame_bgr.shape[:2]
    s = min(max_side / max(h, w), 1.0)
    if min(h, w) * s < 256:                  # 每邊至少 256
        s = 256 / min(h, w)
    img = cv2.resize(frame_bgr, (round(w * s), round(h * s)))
    ok, buf = cv2.imencode(".jpg", img, [cv2.IMWRITE_JPEG_QUALITY, 85])
    return {"type": "image_url",
            "image_url": {"url": "data:image/jpeg;base64," + base64.b64encode(buf).decode()}}

FEED_QUESTIONS = {
    "jammed": {
        "type": "noul",
        "instructions": "roi_image 是沖床送料口。板材是否堆疊、歪斜，或卡在送料口沒有正常送入？",
        "criteria": {"true": "板材堆疊、翹起、歪斜或卡住", "false": "板材平整送入，或送料口沒有板材"},
    },
    "smoke": {
        "type": "noul",
        "instructions": "roi_image 中是否有煙霧或火光？",
    },
    "jam_box": {"type": "box", "instructions": "roi_image 中卡住或歪斜的板材"},
}

async def check_feed(jev, frame, roi, machine):
    state = {"machine": machine, "camera": "送料口", "roi_image": to_part(frame, roi)}
    a, model, cost = await jev.ask(state, FEED_QUESTIONS)
    return {
        "p_jam": a["jammed"]["noul"],
        "p_smoke": a["smoke"]["noul"],
        # Box 沒有「找不到」，只有 p_jam 夠高時才採用框
        "box": a["jam_box"]["box"] if a["jammed"]["noul"] > 0.7 else None,
        "model": model, "cost": cost,
    }
```

V1（PPE 確認）的寫法相同：state 放人員裁切圖（臉部已模糊），題目為「圖中人員頭部是否戴有安全帽」。

🔬 第一次串接時要確認：中文題目的準確度、圖片請求的實際延遲、Box 的位置準不準。如果中文表現較差，題目改用英文。

### 3.6 信心度分流（程式邏輯，Jev 不做最終決定）

```python
# sentinel/jev/gate.py
SEVERITY_RANK = {"S1": 1, "S2": 2, "S3": 3}

def decide(rule_sev, j):
    # 1) 安全底線：規則判 S3 的事件，Jev 不能降級
    jev_sev = "S" + str(round(j["severity_score"]) + 1)   # Score 從 0 起算
    final_sev = max(rule_sev or "S1", jev_sev, key=SEVERITY_RANK.get)

    # 2) 誤報過濾：只有非 S3、而且 Jev 非常確定是誤報時才壓掉，仍然要記錄
    if final_sev != "S3" and j["p_false_alarm"] > 0.9:
        return {"action": "log_only", "reason": "jev_false_alarm"}

    # 3) 根因信心度
    if j["root_cause_conf"] >= 0.6:
        diag = "jev_top3"                 # 派工通知直接附 top3，LLM 非同步補說明
    else:
        diag = "llm_deep"                 # 信心不足 → 交給 LLM 深入診斷

    return {"action": "dispatch", "severity": final_sev,
            "category": j["category"], "diagnosis_mode": diag}
```

門檻（0.9、0.6）是初始值，要用第 5 節的測試集校準。

### 3.7 J4：維修 Agent 的安全把關

```python
SAFETY_GATE = {
    "needs_loto": {"type": "noul", "instructions": "這個操作步驟是否需要先停機並上鎖掛牌（LOTO）？"},
    "enters_danger_zone": {"type": "noul", "instructions": "這個步驟是否需要人員把身體伸入機台運轉範圍？"},
    "high_voltage": {"type": "noul", "instructions": "這個步驟是否涉及打開電控箱或接觸高壓電？"},
}
# Agent 每次輸出建議步驟前呼叫；任一 p > 0.3 → 在對話中標紅，並要求主管在 Kanban 按確認
```

這裡的門檻故意設得很低（0.3）：寧可多問一次，不能漏掉。

### 3.8 J5：結案知識回寫

```python
WRITEBACK = {
    "complete": {"type": "noul", "instructions": "回報是否同時寫出：實際故障原因、採取的處置、處置後是否恢復正常？"},
    "root_cause": {"type": "choice", "instructions": "實際根因屬於哪一類？",
                   "criteria": load_failure_modes(line) | {"new": "以上皆非，新的故障模式"}},
}
# complete < 0.7 → 退回請人員補寫；root_cause = new → 通知工程師新增故障模式
```

這樣知識庫會**越用越準**：每次結案的根因標籤都成為 J3 的歷史案例，也成為第 5 節評估資料的一部分。

### 3.9 故障模式目錄（J3、J5 共用）

存成 `knowledge/failure_modes/{line}.yaml`：

```yaml
stamping:
  feed_roller_wear:
    label: 送料滾輪磨損
    criteria: 送料速率在數分鐘內逐步下降，非瞬間歸零；常見於上次保養超過 30 天
    sop: SOP-STP-012
  material_thickness_dev:
    label: 材料厚度偏差
    criteria: 換料捲後短時間內發生卡料；同批料重複發生
    sop: SOP-STP-007
  die_misalignment:
    label: 模具定位偏移
    criteria: 卡料伴隨異音或沖壓力異常；換模後發生
    sop: SOP-STP-021
  # ...
```

`criteria` 寫的是**判斷依據**，不是名稱。Jev 是照字面讀的，寫得越具體越準。

---

## 4. 延遲與成本

| 環節 | 預估／實測 |
|---|---|
| 規則引擎 | < 10 ms |
| Jev（文字，一次請求 3 題） | **2026-10-20 從台灣實測：0.48–0.69 秒（每次新建連線，6 次）**；第一次冷啟動 1.6 秒。正式程式用 keep-alive 連線、啟動時先暖機一次，預期可再降低 🔬 |
| Jev（帶圖片） | 🔬 待測 |
| 派工通知送出 | **事件發生後 < 1 秒，附根因機率** |
| LLM 說明（非同步） | 3–10 秒後補上 |

**成本試算**：每個事件的 state 約 1,500 token。假設一條線每天 2,000 個事件都問 Jev（因為便宜，連 S1 都可以問）：
2,000 × 1,500 = 300 萬 token／天 → **約 US$0.13／天**。效益報告可以寫「AI 判斷成本每條線每月不到 5 美元」。

---

## 5. 驗證方式（直接成為系統測試報告的內容）

1. **建立標註測試集**：用 Log 模擬器加上 YOLO 事件，產生 **300 筆**情境，每筆由組員標出正確答案（類別、是否誤報、嚴重度、根因）。
   - 200 筆用來調門檻，100 筆保留作最終測試。
2. **量測指標**：
   - 類別、根因：Top-1／Top-3 準確率
   - 誤報過濾：Precision／Recall（**S3 的漏報率必須為 0**）
   - **校準曲線**：把信心度分 5 組，各組實際準確率
   - 延遲：P50／P95
3. **比較組**（評審最愛看）：

   | 方法 | 準確率 | 延遲 | 成本 |
   |---|---|---|---|
   | 純規則 | | | 0 |
   | Jev | | 約 100ms | |
   | LLM（同一批題目，用 `system-one-adapter` 改接 LLM） | | 秒級 | |

   官方的 `system-one-adapter-python` 提供**完全相同的介面**，但背後改接 OpenAI／Anthropic／Gemini，正好拿來做公平比較。
4. **穩健性測試**：選項順序對調（Jev 已知會偏好前面的選項）、在 state 塞入無關內容、邊界情境。
5. **影子模式**：Demo 前先讓 Jev 跟規則平行跑，記錄兩者判斷不一致的地方。

---

## 6. 資安：雲端 API 和「預設地端」的衝突

初選 4.2 寫「含廠內資料的診斷與維修對話走地端」。Jev 只能走雲端，要這樣處理：

1. **文字事件只送去識別化的代碼**：不送人名、客戶或料號。state 內容是「沖壓線、250t 沖床、送料速率歸零、保養 34 天前」這種程度。
   **圖片只送必要的關鍵幀**：機台 ROI 裁切圖（不含人）；人員圖只在 V1 確認 PPE 時送，而且先把臉部模糊化。整段影片永遠留在本機。
2. **分級原則改寫**：
   - 去識別化的事件判斷 → 可以走雲端 Jev（低敏感、高頻、要快）
   - 含廠內文件的診斷對話 → 走地端 LLM（高敏感、低頻）
3. **離線備案**：Jev 呼叫失敗或逾時時，系統退回「規則＋YOLO」照常派工，只是沒有根因機率與視覺確認。🔬 `system-one-adapter` 可以改接地端開源模型提供相同題型，有時間再做。
4. 文件裡要寫：正式導入前需簽資料處理協議（DPA）並評估跨境傳輸。

---

## 7. 對評分的幫助

- **創新性**：「快思慢想」三層決策，而且用的是 2026 年才出現的新型判斷模型，同組別很少見。
- **可行性**：官方 SDK，幾十行程式；有可以平行比較的 LLM 版本；測試方法明確。
- **價值性**：派工從「等 LLM 好幾秒」變成「1 秒內附根因機率」；AI 成本每月幾美元。
- **永續性／治理**：機率經過校準、可以設門檻、判斷結果可稽核；安全底線仍然由規則把關。

---

## 8. 待辦

1. [x] 拿 OpenRouter key 跑通 3.1 的 curl，確認 `typesafe/jev-1.14` 回傳格式（2026-10-20 完成，格式與文件一致；每次約 US$0.00002）
2. [ ] 實測延遲：文字請求已測（約 0.5 秒）；帶 1 張圖的請求待測
3. [ ] 測中文題目與英文題目的準確度差異
4. [ ] V2 先驗證：拿 20 張「正常送料／卡料」圖片，看 `jammed` 的機率分得開嗎；Box 準不準
5. [ ] 寫故障模式目錄（沖壓、銲接、組裝，每段 20–30 種）
6. [ ] 實作 client、J1–J3、V1–V2、信心度分流
7. [ ] Log 模擬器產生 300 筆標註測試集
8. [ ] 三方比較實驗（規則／Jev／LLM），畫校準曲線
9. [ ] 架構圖加入 L3.5 直覺層，簡報加上「快思慢想」頁

## 參考來源

- 官方發表：https://typesafe.ai/blog/introducing-system-one-models-and-jev
- OpenRouter Jev 1.14 頁面：https://openrouter.ai/typesafe/jev-1.14
- OpenRouter Jev 文件：https://openrouter.ai/docs/guides/community/jev
- OpenRouter Jev 教學（含實際回應格式）：https://openrouter.ai/docs/guides/community/jev-tutorial
- OpenRouter 圖片輸入規格：https://openrouter.ai/docs/guides/community/multimodal-decisions
- 官方文件：https://docs.typesafe.ai/ （Quickstart：https://docs.typesafe.ai/introduction/quickstart ；Python SDK：https://docs.typesafe.ai/sdk/python ）
- Python SDK：https://github.com/typesafe-ai/typesafe-sdk-python
- System One Adapter（相同介面接 LLM）：https://github.com/typesafe-ai/system-one-adapter-python
- 社群技術整理（限制、失效模式、設計模式）：https://gist.github.com/pjburnhill/adf8d28efcad9df037bfdece178ef965
- 資源整理：https://github.com/AbdelStark/awesome-typesafe-jev
- 使用教學：https://flaviocopes.com/jev/
- Pydantic AI 整合：https://pydantic.dev/docs/ai/models/typesafe/
