# Sentinel AI — AI Manufacturing 2026 主題一

智慧產線全知守護與極速響應系統（中山資管 × 高大資工）

## 截止日

| 日期 | 項目 | 放哪 |
|---|---|---|
| 10/22（四）23:59 | Demo Day 團隊需求調查表（展示方式、設備、用電） | 表單，見 `00_比賽資訊/timeline.md` |
| 10/30（五）23:59 | 第二階段成果說明書 + 指導老師 Demo Day 出席調查 | `03_成果說明書/` |
| 11/15（日）23:59 | 決賽簡報（10–15 頁） | `04_決賽/` |
| 11 月 Demo Day | 現場原型展示 + A1 直式海報（300dpi PDF） | `prototype/`、`04_決賽/` |

## 資料夾

```
00_比賽資訊/     簡章、主辦公告（timeline.md）— 唯讀參考
01_初選/         已繳交的初選構想說明書（PDF 為最終版）
  ├ drafts/      v1–v3 docx 草稿（v3 含黃底標記，PDF 由 v3 再手改而來）
  ├ scripts/     產 docx 用的 python-docx 腳本（路徑寫死舊位置，僅存檔）
  ├ 主題發想/    story-1~3 構想演進
  └ 組員回饋/
02_規格定義/     現在的工作區：系統架構、問題處理、功能/資料集/Demo 定義
03_成果說明書/   10/30 繳交
04_決賽/         簡報、海報
prototype/       原型程式碼
```

## 模型與金鑰

金鑰放在根目錄 `.env`（已列入 `.gitignore`，不要提交、不要貼到聊天或文件）：

| 變數 | 用途 | 模型 |
|---|---|---|
| `OPENROUTER_API_KEY` | Jev 判斷（OpenRouter Decisions API，`POST https://openrouter.ai/api/alpha/decisions`） | `typesafe/jev-1.14` |
| `OLLAMA_API_KEY` | LLM：診斷說明、維修 Agent（Ollama Cloud，`https://ollama.com/api/chat`） | `deepseek-v4.1-flash` |

## 開發環境

- Python **3.13**（不要用系統預設的 3.9），專案根目錄建虛擬環境：
  ```bash
  python3.13 -m venv .venv && source .venv/bin/activate
  ```

## 知識庫 embedding（離線）

- 相似案例檢索用 `BAAI/bge-m3`（`prototype/backend/config.yaml` 的 `kb.embedding_model`）＋混合檢索（向量＋BM25＋metadata，見 `prototype/eval/results/summary.md` R1／U1）。資源不夠時可改 `intfloat/multilingual-e5-small`（未調參驗證集上差距很小）。
- 第一次：`cd prototype && make model`（下載約 2.3 GB 到 `~/.cache/huggingface`），再 `make kb` 重建向量庫。`make kb` 建完會檢查 cases collection，空的或 embedder 不符就以非 0 結束。
- 模型已在快取時，`make dev`／`make dev-demo`／`make kb` 會自動加 `HF_HUB_OFFLINE=1 TRANSFORMERS_OFFLINE=1`，**Demo 當天不需要連 Hugging Face Hub**（啟動時會印出「已快取，離線啟動」）。
- 換 embedding 模型後一定要重跑 `make kb`；`GET /api/health` 的 `warnings`／`kb.warnings` 會提示 cases 為空或建庫 embedder 與現在不同。

## 訓練主機

- 實驗室內網主機：48 核心、251 GB RAM、**RTX 2080 Ti 11 GB × 2**、CUDA 12.4
- 多人共用：訓練固定用 **GPU 1**（`CUDA_VISIBLE_DEVICES=1`）
- 系統 Python 3.10；用 `uv venv --python 3.13` 建立與本機相同的環境
- 連線方式與帳號寫在本機的 `LOCAL.md`（不進 git）
