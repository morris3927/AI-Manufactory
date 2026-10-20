# V-JEPA 技術調查與導入規劃

> 調查日期：2026-10-20
> 目的：評估把 Meta 的 V-JEPA 系列模型加入 Sentinel AI 感測層（L1），處理「需要看動態」的瞬間判斷。
> 標 🔬 的數字或行為需要我們自己實測確認。

---

## 0. 結論先講

1. **V-JEPA 不取代 YOLO，兩者互補。**
   - YOLO 回答「畫面裡**有什麼、在哪裡**」（空間）：安全帽、人、物料。
   - V-JEPA 回答「畫面裡的東西**怎麼在動、動得正不正常**」（時間）：沖床有沒有在打、送料節奏有沒有亂、人是不是跌倒。
   - 簡報敘事：**「空間交給 YOLO，時間交給 V-JEPA」**。
2. **最大價值：只用正常影片就能抓異常，不需要異常標註。**
   卡料、機構卡死這類異常沒有公開標註資料集，也很難自己拍到。V-JEPA 的 predictor 本來就在「預測接下來的畫面表徵」，預測錯得離譜＝異常。只要拍正常運轉的影片就能上線。這直接解掉我們最大的資料集難題。
3. **選型**：主力用 **V-JEPA 2 ViT-L（300M，HuggingFace 官方支援、MIT 授權）**。V-JEPA 2.1 ViT-B（80M）當「邊緣部署版」備案。VL-JEPA 只寫進文獻與未來規劃，不實作。
4. **速度定位要講清楚**：V-JEPA 吃的是一段影片（例如 2 秒、16 幀），不是單張圖，所以它是**秒級**判斷，不是 YOLO 那種毫秒級逐幀判斷。對機台運轉狀態和節奏異常，秒級已經夠，這個定位跟我們的快路（規則、秒級）一致。

---

## 1. JEPA 是什麼

**JEPA = Joint Embedding Predictive Architecture**（聯合嵌入預測架構），由 Yann LeCun 團隊提出。

| 做法 | 學什麼 | 問題 |
|---|---|---|
| 像素生成（VideoMAE、擴散模型） | 把被遮住的像素畫回來 | 花大量算力在預測不重要的細節（光影、雜訊、紋理） |
| 對比學習（CLIP） | 拉近配對樣本 | 需要大量配對資料，對動態理解弱 |
| **JEPA** | **在抽象表徵空間**預測被遮住的部分 | 丟掉無法預測的雜訊，只保留結構和運動規律 |

V-JEPA 的訓練方式：

```
影片 → 切成 tubelet（2 幀 × 16×16 像素一塊）
     → 遮掉一部分 tubelet
     → Encoder（ViT）看可見的部分 → 表徵
     → Predictor 預測「被遮掉那些塊的表徵」
     → 跟 Target Encoder（EMA 更新）算出的真實表徵比 L1 loss
```

訓練完後：
- **Encoder**：通用的影片特徵抽取器，接一個小分類頭（attentive probe）就能做各種任務，encoder 本身凍結不動。
- **Predictor**：會「預測影片接下來／被遮住的部分應該長怎樣」，可以拿來做**異常偵測**（預測誤差＝驚訝程度）。

---

## 2. 版本比較

| 版本 | 發布 | 大小 | 重點 | 取得方式 | 授權 | 本案用途 |
|---|---|---|---|---|---|---|
| V-JEPA | 2024-02 | ViT-L/H | 第一代 | GitHub `facebookresearch/jepa` | CC BY-NC | 不用（已被取代） |
| **V-JEPA 2** | 2025-06 | ViT-L 300M／H 600M／g 1B | 100 萬小時影片預訓練；SSv2 動作辨識 SOTA | **HF transformers 官方支援**、torch.hub | **MIT**（HF model card） | **主力** |
| V-JEPA 2-AC | 2025-06 | ViT-g | 加上動作條件，可做機器人規劃 | torch.hub | MIT | 不用（機器人控制，非本案） |
| **V-JEPA 2.1** | 2026-03 | ViT-B 80M／L 300M（蒸餾）、g 1B／G 2B | Dense Predictive Loss，局部（dense）特徵大幅變好，影片時序一致性更好 | torch.hub（`vjepa2_1_vit_base_384` 等）；HF 只有非官方鏡像 | 🔬 **未確認**：repo 寫 MIT，論文頁有寫 CC BY-NC-ND | **備案**：邊緣版、要定位異常區域時 |
| VL-JEPA | 2025-12 | 1.6B | 視覺語言版，預測文字 embedding 而非逐 token 生成；串流影片可「選擇性解碼」，解碼次數少 2.85 倍 | 🔬 未找到官方權重釋出 | 論文 CC BY 4.0 | 不實作，寫在未來規劃 |

### 2.1 V-JEPA 2.1 改了什麼（為什麼值得當備案）

1. **Dense Predictive Loss**：不只被遮住的 token，**可見的 token 也要算 loss**（離遮罩越近權重越大，λᵢ = λ/√d_min）。結果：每個 patch 的特徵保有空間位置資訊，不會全部變成一個全域摘要。
   → 對我們的意義：可以知道「**畫面哪一塊**在異常」，例如只有送料口那塊預測誤差大，熱力圖可以直接畫在儀表板上。
2. **Deep Self-Supervision**：多個中間層都算 loss。
3. **Multi-Modal Tokenizer**：圖片用 2D、影片用 3D 卷積切塊，共用 encoder。
4. 成績：SSv2 77.7%、EPIC-KITCHENS-100 動作預測 R@5 40.8、Ego4D 物件互動預測 7.71 mAP（比前作高 35%）。
5. **蒸餾出 ViT-B 80M**：小到可以講「邊緣端部署」。

---

## 3. 在 Sentinel AI 裡放哪裡

### 3.1 架構定位

```
L1 感測層
├── YOLO（逐幀，毫秒級）      → 物件事件：PPE、人員在崗、物料堆疊位置
├── V-JEPA（滑動視窗，秒級）  → 動態事件：機台運轉/閒置、節奏異常、人員跌倒
└── Log 門檻判定              → 設備事件
         ↓ 全部送 vision.events / mes.logs
L3 規則引擎做跨模態判斷（V-JEPA 的結果跟 Log 比對）
```

V-JEPA 仍屬於**確定性的感測層**（輸出分數，由規則判斷門檻），不是 LLM，不違反「決策權分層」的主張。

### 3.2 三個用法，依適合度排序

| # | 用法 | 對應事件 | 要不要標註 | 適合度 | 優先 |
|---|---|---|---|---|---|
| **A** | **零標註異常偵測**（predictor 預測誤差） | 沖床節奏異常、送料卡料、機構卡死、異物入侵 | **不用**，只要正常影片 | ★★★ 本案最大亮點 | P0 |
| **B** | **機台狀態分類**（凍結 encoder + attentive probe） | 運轉／閒置／異常停頓 → 拿去跟 MES Log 比對 | 少量（每類幾十段 2 秒短片） | ★★★ 直接支撐「影像 Idle ↔ Log 運轉」核心規則 | P0 |
| C | 人員動作辨識 | 跌倒、攀爬、工序順序合規 | 要（或用現成動作資料集） | ★★ | P1 |
| ✗ | PPE、物件偵測 | 安全帽、護具 | — | ✗ 靜態小物件，YOLO 明顯更好 | 不用 |

---

## 4. 實作細節

### 4.1 環境

```bash
# 建議 Python 3.12
pip install "transformers>=4.53" torch torchvision torchcodec accelerate
# 若要用 torch.hub 載 V-JEPA 2.1：
git clone https://github.com/facebookresearch/vjepa2 && cd vjepa2 && pip install .
```

- **macOS 注意**：官方 repo 依賴 `decord`，macOS 不支援，可改用 `eva-decord` 或 `decord2`。**用 HF transformers 路線的話影片解碼改用 `torchcodec` 或 OpenCV，就不會遇到這個問題。**
- **算力**：
  - 開發／特徵抽取：有 NVIDIA GPU 最好（實驗室機器或 Colab）。
  - Mac（Apple Silicon）可用 `device="mps"` 跑推論 🔬 速度待測。
  - Demo Day 現場：一台有 GPU 的筆電，或 Mac + 預先算好的結果當備案。

### 4.2 模型選擇

| 用途 | Checkpoint | 輸入 |
|---|---|---|
| 特徵抽取 + 異常偵測（主力） | `facebook/vjepa2-vitl-fpc64-256` | 256×256，訓練用 64 幀，推論可用 16 幀 |
| 現成動作分類（參考／用法 C 起點） | `facebook/vjepa2-vitl-fpc16-256-ssv2` | 256×256，16 幀，SSv2 174 類動作 |
| 邊緣版／dense 熱力圖 | torch.hub `vjepa2_1_vit_base_384` | 384×384 |

ViT-L 的設定：hidden 1024、24 層、16 heads；predictor hidden 384、12 層；patch 16、tubelet 2。

### 4.3 串流取樣（三個用法共用）

```
攝影機 30fps
  → 降採樣到 8 fps
  → 環形緩衝區保留最近 16 幀（= 2 秒）
  → 每 0.5 秒（stride = 4 幀）送一次 V-JEPA
  → 輸出 {camera_id, t_start, t_end, state, anomaly_score}
```

16 幀、256px、tubelet 2 → token 數 = (16/2) × (256/16)² = **8 × 16 × 16 = 2048**。
token 排列順序為 **時間 → 高 → 寬**：`idx = t·256 + h·16 + w`（t∈0..7）。🔬 需用單元測試確認 HF 實作順序。

```python
# sentinel/vision/vjepa_stream.py
import collections, cv2, numpy as np, torch

class ClipBuffer:
    def __init__(self, n_frames=16, src_fps=30, target_fps=8):
        self.buf = collections.deque(maxlen=n_frames)
        self.skip = max(1, round(src_fps / target_fps))
        self.i = 0

    def push(self, frame_bgr):
        self.i += 1
        if self.i % self.skip:
            return False
        self.buf.append(cv2.cvtColor(frame_bgr, cv2.COLOR_BGR2RGB))
        return len(self.buf) == self.buf.maxlen

    def clip(self):
        return np.stack(self.buf)          # (T, H, W, 3) uint8
```

### 4.4 用法 A：零標註異常偵測（P0 主打）

**原理**：把 2 秒視窗的「最後 0.5 秒」（最後 2 個時間 tubelet）遮掉，讓 predictor 根據前 1.5 秒預測它的表徵，再跟 encoder 實際看到的表徵比較。正常運轉時節奏規律，預測得準；卡料、卡死、異物出現時預測誤差會飆高。

**完全不用訓練**，只要用正常影片校準門檻。

```python
# sentinel/vision/vjepa_anomaly.py
import torch, torch.nn.functional as F
from transformers import AutoModel, AutoVideoProcessor

REPO = "facebook/vjepa2-vitl-fpc64-256"
T_TOK, HW = 8, 16 * 16            # 16 幀 / tubelet 2；256/16 = 16
N = T_TOK * HW                    # 2048
N_FUTURE = 2                      # 遮最後 2 個時間 tubelet（≈ 0.5 秒）

class VJEPAAnomaly:
    def __init__(self, device="cuda"):
        self.dev = device
        self.proc = AutoVideoProcessor.from_pretrained(REPO)
        self.model = AutoModel.from_pretrained(
            REPO, torch_dtype=torch.float16, attn_implementation="sdpa"
        ).to(device).eval()
        ctx = torch.arange(0, (T_TOK - N_FUTURE) * HW)
        tgt = torch.arange((T_TOK - N_FUTURE) * HW, N)
        self.ctx, self.tgt = ctx[None].to(device), tgt[None].to(device)

    @torch.no_grad()
    def score(self, clip_thwc):
        x = self.proc(list(clip_thwc), return_tensors="pt").to(self.dev)
        x = {k: v.half() if v.is_floating_point() else v for k, v in x.items()}
        out = self.model(**x, context_mask=[self.ctx], target_mask=[self.tgt])
        pred = out.predictor_output.last_hidden_state          # (1, N_tgt, D)
        # 🔬 target：優先用 predictor_output.target_hidden_state；
        #    若該欄位不存在，改用 encoder 全片輸出取 tgt 位置
        tgt = getattr(out.predictor_output, "target_hidden_state", None)
        if tgt is None:
            tgt = out.last_hidden_state[:, self.tgt[0]]
        tgt = F.layer_norm(tgt.float(), tgt.shape[-1:])        # 與訓練時一致
        err = (pred.float() - tgt).abs().mean(-1)              # (1, N_tgt) 每個 token 的誤差
        heat = err.view(N_FUTURE, 16, 16).mean(0)              # 16×16 空間熱力圖
        return err.mean().item(), heat.cpu().numpy()
```

**校準門檻**（每支攝影機、每台機器各自校準）：

```python
# 用 10–20 分鐘正常運轉影片
scores = [det.score(c)[0] for c in normal_clips]
mu, sd = np.mean(scores), np.std(scores)
TH_WARN  = np.percentile(scores, 99)      # 或 mu + 3*sd
TH_ALERT = np.percentile(scores, 99.9)
```

**降低誤報**（工廠很重要）：
- **ROI**：只把機台區域裁切後送進模型（例如沖床送料口），人走過背景不影響。
- **連續確認**：分數超過門檻且連續 ≥ 3 個視窗（約 1.5 秒）才發事件。
- **熱力圖過濾**：誤差最大的位置要落在 ROI 內才算數。

**備案方法**（如果 predictor 路線效果不好）：凍結 encoder 取每段視窗的平均池化 embedding，對正常 embedding 建 kNN（k=5）或 Mahalanobis 距離，距離＝異常分數。同樣不用異常標註，實作更簡單，但沒有熱力圖。

### 4.5 用法 B：機台狀態分類（P0）

**做法**：凍結 encoder，先把所有訓練片段的特徵算好存檔，再訓練一個小的 attentive probe（只有幾 MB，CPU 幾分鐘就能訓練完）。

類別：`running`、`idle`、`stalled`（有電但卡住不動或抖動）

```python
# sentinel/vision/vjepa_probe.py
import torch, torch.nn as nn

class AttentiveProbe(nn.Module):
    """與官方評估相同概念：可學習 query 對 token 做 cross-attention。"""
    def __init__(self, dim=1024, n_cls=3, heads=16):
        super().__init__()
        self.q = nn.Parameter(torch.zeros(1, 1, dim))
        self.attn = nn.MultiheadAttention(dim, heads, batch_first=True)
        self.norm = nn.LayerNorm(dim)
        self.head = nn.Linear(dim, n_cls)

    def forward(self, tokens):                     # (B, 2048, 1024)
        q = self.q.expand(tokens.size(0), -1, -1)
        z, _ = self.attn(q, tokens, tokens)
        return self.head(self.norm(z[:, 0]))

# 1) 抽特徵（只做一次）
@torch.no_grad()
def extract(model, proc, clip):
    x = proc(list(clip), return_tensors="pt").to(model.device)
    return model(**x, skip_predictor=True).last_hidden_state.squeeze(0).half().cpu()

# 2) 訓練 probe：AdamW lr=1e-3, wd=0.01, 20 epochs, batch 16, CrossEntropy
```

**資料量**：每類 50–100 段 2 秒短片，從自己拍的影片切出來即可。
**輸出**：`vision.events {type: "machine_state", state: "idle", conf: 0.93}`，交給規則引擎跟 MES Log 比對 → 「影像 Idle ↔ Log 運轉 → 不一致告警」這條核心規則就有了真正的影像依據，不再只靠 YOLO + 幀差。

> 為什麼比「YOLO + 幀間差」好：幀差會被光線變化、人走過、攝影機震動干擾；V-JEPA 學過大量影片的運動規律，能分辨「機構在週期運動」和「畫面有雜訊在動」。🔬 Demo 前做一個對照實驗（同一批影片比較兩者準確率），寫進系統測試報告。

### 4.6 用法 C：人員動作辨識（P1）

- 起點：`facebook/vjepa2-vitl-fpc16-256-ssv2` 已經能分 174 類手部／物件動作。
- 跌倒：用公開跌倒資料集訓練 probe（同 4.5 的流程，換類別）。
- 工序合規：組裝段「先鎖螺絲再接線」這類順序檢查，需要自己拍示範影片，當作 P1 加分項。

### 4.7 事件格式（送進 Kafka／queue）

```json
{
  "topic": "vision.events",
  "source": "vjepa",
  "camera_id": "press-01-feed",
  "line": "stamping",
  "machine_id": "PRESS-01",
  "t_start": "2026-11-20T10:31:02.000+08:00",
  "t_end":   "2026-11-20T10:31:04.000+08:00",
  "type": "motion_anomaly",
  "anomaly_score": 0.412,
  "threshold": 0.287,
  "consecutive": 3,
  "heatmap_peak": [12, 5],
  "state": "stalled",
  "state_conf": 0.88
}
```

### 4.8 規則引擎新增規則（L3 模組 B）

| 規則 | 嚴重度 | 類別 |
|---|---|---|
| V-JEPA `state=idle` 持續 N 秒 ＋ Log `running` | S2 | 設備（Log 失準） |
| V-JEPA `motion_anomaly` 連續 3 窗 ＋ Log 送料速率下降 | S2 | 設備（疑似卡料）→ AI 診斷 |
| V-JEPA `state=stalled` ＋ Log 馬達電流上升 | S3 | 設備（卡死、過載風險） |
| V-JEPA `motion_anomaly` ＋ YOLO 偵測到人在 ROI 內 | S3 | 工安（人員進入運轉區） |

### 4.9 效能與部署

| 項目 | 估計 | 備註 |
|---|---|---|
| ViT-L、16 幀、fp16、單一 GPU | 🔬 預估數十毫秒到百毫秒等級 | 待實測 |
| 多攝影機 | 多支攝影機的視窗湊成一個 batch 一起跑 | 4 支 → batch 4 |
| 每 0.5 秒推論一次 | 端到端延遲 ≈ 2 秒視窗 + 推論時間 | 「秒級」 |
| ViT-B（2.1）| 80M，約 ViT-L 的 1/4 | 邊緣端敘事 |

最佳化手段：fp16、`attn_implementation="sdpa"`、只送 ROI、降低推論頻率（機台狀態每 1 秒一次就夠）。

---

## 5. 資料

| 用途 | 來源 | 授權 | 備註 |
|---|---|---|---|
| 用法 A 校準、B 訓練 | **自己拍**：桌上型模擬產線（輸送帶玩具、3D 印表機、小型沖壓／打孔機構） | 自有 | **Demo Day 可以帶到現場**：當場製造卡料，觀眾親眼看到異常分數飆高 |
| 用法 A 驗證（量化結果） | **IPAD**（Industrial Process Anomaly Detection，2024）：16 種工業設備、6 小時以上合成＋真實影片，標註了工業流程的週期性 | 🔬 IEEE DataPort，需確認授權與下載方式 | 拿來跑 AUROC 寫進測試報告：「在公開工業異常資料集上達到 X」 |
| 用法 C 跌倒 | 公開跌倒資料集（另列於 `03_資料集.md`） | 依各資料集 | P1 |

> IPAD 論文自己的方法平均 AUC 68.6%，這個基準不高。如果 V-JEPA 零訓練就能接近或超過，就是很好的量化亮點。🔬

---

## 6. 風險與對策

| 風險 | 對策 |
|---|---|
| predictor 遮罩 API 用法與預期不同 | 4.4 的實作先用單元測試確認輸出形狀與 token 順序；不行就改用 4.4 的 kNN 備案（一定做得出來） |
| 背景人員走動造成誤報 | ROI 裁切、連續窗確認、熱力圖位置過濾、搭配 YOLO 人員偵測做排除規則 |
| 光線、攝影機震動 | 校準影片要涵蓋不同光線；固定攝影機 |
| Demo 現場沒有 GPU | 預錄影片 + 預先算好的分數曲線當備案；或用 2.1 ViT-B |
| V-JEPA 2.1 授權不明 | 主力用 V-JEPA 2（MIT）；2.1 只當備案，使用前確認 LICENSE |
| 評審質疑「為何不用 VLM 看影片」 | VLM 每次呼叫慢且貴、會幻覺；V-JEPA 是確定性的特徵＋分數、可設門檻、可稽核，符合快路定位 |

---

## 7. 對評分的幫助

- **創新性（30%）**：2026 年的世界模型（world model）用在工廠產線，而且用的是它「預測未來」的本事做異常偵測，不是拿來當普通分類器。同組別裡很少見。
- **可行性（30%）**：零標註，只要正常影片；HF 官方支援，幾十行程式就能跑起來。
- **價值性（30%）**：新產線、新機台不用收集異常資料就能上線（只要拍 20 分鐘正常運轉）→ 呼應勤誠海外新廠快速複製。
- 呼應初選 2.2「產線專屬輕量模型」：每台機器一組自己的門檻和 probe，encoder 共用。

---

## 8. 待辦（實作順序）

1. [ ] 環境建置，跑通 HF 範例（feature extraction）
2. [ ] 單元測試：token 數 2048、順序、predictor 遮罩輸出形狀
3. [ ] 做出模擬產線並拍攝：正常 20 分鐘，以及卡料、停頓、異物各 20 次
4. [ ] 用法 A：分數曲線、門檻校準、熱力圖 → 確認卡料時分數會飆高
5. [ ] 用法 B：切片、抽特徵、訓練 probe，並與 YOLO + 幀差做對照實驗
6. [ ] 接上事件格式 → 規則引擎 4 條新規則
7. [ ] 申請並下載 IPAD 資料集，跑 AUROC
8. [ ] 實測延遲（GPU／Mac 各一組），寫進測試報告
9. [ ] 架構圖 L1 加上 V-JEPA 方塊

---

## 參考來源

- V-JEPA 2 GitHub（程式碼、checkpoint、torch.hub 名稱、授權）：https://github.com/facebookresearch/vjepa2
- V-JEPA 2 論文：https://arxiv.org/abs/2506.09985
- V-JEPA 2.1 論文：https://arxiv.org/abs/2603.14482
- V-JEPA 2.1 介紹（TechTalks）：https://bdtechtalks.com/2026/03/23/v-jepa-2-1/
- Meta V-JEPA 2 介紹：https://ai.meta.com/blog/v-jepa-2-world-model-benchmarks/
- HF transformers V-JEPA 2 文件：https://huggingface.co/docs/transformers/model_doc/vjepa2
- HF model card（MIT）：https://huggingface.co/facebook/vjepa2-vitl-fpc64-256
- VL-JEPA 論文：https://arxiv.org/abs/2512.10942
- PULS（V-JEPA 2 用於影片異常預測，UCF-Crime AUROC 0.8994）：https://arxiv.org/abs/2607.03558
- IPAD 工業流程異常資料集：https://arxiv.org/abs/2404.15033 ／ https://ljf1113.github.io/IPAD_VAD/
