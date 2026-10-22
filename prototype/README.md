# Sentinel AI 原型：組員上手

這份是給組員的：怎麼在自己電腦把系統跑起來、每個資料夾在做什麼、改東西前要注意什麼。

## 第一次安裝

需要：Python 3.13（不要用系統內建的 3.9）、Node.js 20 以上、Google Chrome（截圖和錄影用）。

```bash
cd prototype
make setup                     # 建 Python 環境、裝後端與前端套件（第一次大概 5–10 分鐘）
cp ../.env.example ../.env     # 填入 OPENROUTER_API_KEY、OLLAMA_API_KEY（跟組長拿，不要傳到群組以外）
bash scripts/fetch_model.sh    # 下載 PPE 偵測模型 models/ppe.pt
backend/.venv/bin/python scripts/build_demo_footage.py   # 產生 Demo 用的攝影機影片（第一次會下載公開授權照片）
make kb                        # 建知識庫（第一次會下載 bge-m3 文字向量模型，約 2 GB）
```

## 每天開發

```bash
make dev        # 後端 :8000＋前端 :3000，瀏覽器開 http://localhost:3000
make test       # 後端測試（不打真實 API）
make test-live  # 真的呼叫 Jev 與 DeepSeek（會花一點點錢）
make reset      # 清掉 Demo 資料庫，重頭開始
```

前端可以不接後端單獨跑：`cd frontend && NEXT_PUBLIC_MOCK=1 npm run dev`。

Demo Day 當天的啟動方式和備案，看 `DEMO_RUNBOOK.md`。

## 資料夾

| 資料夾 | 內容 |
|---|---|
| `backend/` | FastAPI 後端：模擬器、影像偵測、規則引擎、Jev、知識庫、DeepSeek 維修助理、工單、稽核 |
| `frontend/` | Next.js 前端：儀表板、任務看板、工單詳情、手機版、控制台、效益頁、稽核 |
| `knowledge/` | 知識庫內容：故障模式、SOP、上鎖掛牌表、模擬維修案例、法規原文；`_gen/` 是產生與檢查的腳本 |
| `data/simulated/` | 工廠設定（產線、機台、攝影機、畫面區域）、班表、門禁、劇本 |
| `assets/videos/` | Demo 用的攝影機影片（不進 git，用腳本產生；來源與授權在 `SOURCES.csv`） |
| `models/` | YOLO 權重（不進 git，用 `fetch_model.sh` 下載） |
| `training/` | PPE 模型的資料整理與訓練腳本（在實驗室訓練主機上跑） |
| `eval/` | 評估結果（E1–E7、檢索基準）與圖表；`results/summary.md` 是總整理 |
| `scripts/` | 影片產生、模型下載、Demo 啟動等工具 |

## 改東西前要知道的

- **金鑰只放根目錄 `.env`**，不要寫進程式、文件或截圖。這個 repo 是公開的。
- **實驗室主機的 IP、帳號不要寫進 repo**，放在自己電腦的 `LOCAL.md`。
- **安全相關的限制是寫在程式裡的**（作業許可、上鎖掛牌先行、復電過濾、Jev 安全把關、AI 不能改工單狀態）。改 `backend/sentinel/agent/` 或 `dispatch/` 之前先跑 `make test`，這些都有測試在顧。
- **知識庫內容是模擬資料**，改完要跑 `backend/.venv/bin/python knowledge/_gen/validate.py`，通過才能入庫。`cases/*/heldout/` 是測試用，永遠不入庫。
- 專案資料夾在 iCloud 同步的桌面上，如果看到 `xxx 2.json` 這種檔名，那是同步衝突產生的副本，刪掉即可（validate.py 會擋）。
