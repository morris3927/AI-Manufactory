# Sentinel AI 原型（08_系統設計.md §13）
# make dev：前後端一起啟動；make test：單元測試＋劇本重播；make kb：重建知識庫；make reset：重置 Demo 資料
BACKEND := backend
PY      := $(BACKEND)/.venv/bin/python
PORT    ?= 8000
# 知識庫 embedding 模型（config.yaml kb.embedding_model）已在 HF 快取 → 啟動時離線，Demo 不依賴 Hugging Face Hub
EMBED_MODEL = $(shell $(PY) -c "import yaml;print(yaml.safe_load(open('$(BACKEND)/config.yaml'))['kb']['embedding_model'])" 2>/dev/null)
HF_OFFLINE  = $(shell $(PY) -c "from huggingface_hub import try_to_load_from_cache as t; m='$(EMBED_MODEL)'; \
                ok = lambda f: isinstance(t(m, f), str); \
                raise SystemExit(0 if ok('config.json') and ok('modules.json') and (ok('model.safetensors') or ok('pytorch_model.bin')) else 1)" \
                >/dev/null 2>&1 && echo HF_HUB_OFFLINE=1 TRANSFORMERS_OFFLINE=1)

.PHONY: setup model dev dev-demo backend frontend test test-live kb kb-demo reset eval eval-report

setup:                      ## 建立 Python 3.13 venv 並安裝後端（含測試套件）
	test -d $(BACKEND)/.venv || python3.13 -m venv $(BACKEND)/.venv
	$(PY) -m pip install -q -e "$(BACKEND)[test,yolo,embed]"   # YOLO（PPE）與多語 embedding
	cd frontend && npm install
	$(MAKE) model

model:                      ## 下載 embedding 模型到 HF 快取（bge-m3 約 2.3 GB；只需一次，之後 make dev 自動離線）
	$(PY) -c "from huggingface_hub import snapshot_download as s; print(s('$(EMBED_MODEL)', allow_patterns=['*.json','*.safetensors','pytorch_model.bin','sentencepiece.bpe.model','1_Pooling/*']))"

dev:                        ## 前後端一起啟動（Ctrl-C 一起結束）；模型已快取時自動 HF_HUB_OFFLINE=1
	@echo "embedding：$(EMBED_MODEL) $(if $(HF_OFFLINE),（已快取，離線啟動）,（未快取：會連 Hugging Face 下載，建議先 make model）)"
	$(HF_OFFLINE) $(MAKE) -j2 backend frontend

dev-demo:                   ## 同上，但放行 review_status=draft 的 SOP（只限 Demo）
	@echo "embedding：$(EMBED_MODEL) $(if $(HF_OFFLINE),（已快取，離線啟動）,（未快取：會連 Hugging Face 下載，建議先 make model）)"
	$(HF_OFFLINE) SENTINEL_KB_ALLOW_DRAFT=1 $(MAKE) -j2 backend frontend

backend:
	cd $(BACKEND) && .venv/bin/uvicorn sentinel.main:app --reload --port $(PORT)

frontend:
	cd frontend && npm run dev

test:                       ## 單元測試＋劇本重播（不打真實 API）
	cd $(BACKEND) && .venv/bin/pytest -q

test-live:                  ## 真實 Jev／LLM 測試（需要根目錄 .env 的金鑰）
	cd $(BACKEND) && .venv/bin/pytest -q -m live

kb:                         ## 重建知識庫（只收 review_status=approved；heldout 永不入庫）；cases 為空會以非 0 結束
	cd $(BACKEND) && $(HF_OFFLINE) .venv/bin/python -m sentinel.kb.ingest --rebuild

kb-demo:                    ## 重建知識庫並放行 draft（只限 Demo）
	cd $(BACKEND) && $(HF_OFFLINE) .venv/bin/python -m sentinel.kb.ingest --rebuild --allow-draft

reset:                      ## 重置 Demo 資料（刪除 SQLite 與關鍵幀；服務啟動中請改用 POST /api/demo/reset）
	rm -f $(BACKEND)/sentinel.db $(BACKEND)/sentinel.db-wal $(BACKEND)/sentinel.db-shm
	rm -rf $(BACKEND)/keyframes

eval:                       ## 評估實驗 R1、E1–E7、U1（真實 API；已有的 raw 結果會沿用、不重打）→ eval/results/
	cd $(BACKEND) && $(HF_OFFLINE) .venv/bin/python -m sentinel.eval.retrieval_eval      # R1 檢索基準（不打 API）
	cd $(BACKEND) && .venv/bin/python -m sentinel.eval.judge_eval run
	cd $(BACKEND) && .venv/bin/python -m sentinel.eval.untuned_eval run                    # U1 未調參驗證集
	cd $(BACKEND) && .venv/bin/python -m sentinel.eval.ppe_eval run
	cd $(BACKEND) && .venv/bin/python -m sentinel.eval.feed_eval run
	cd $(BACKEND) && .venv/bin/python -m sentinel.eval.e2e_eval --runs 5 --port 8010
	$(MAKE) eval-report

eval-report:                ## 只用 eval/results/raw 重算指標與圖表（不打 API）
	cd $(BACKEND) && .venv/bin/python -m sentinel.eval.judge_eval analyze
	cd $(BACKEND) && .venv/bin/python -m sentinel.eval.untuned_eval analyze
	cd $(BACKEND) && .venv/bin/python -m sentinel.eval.ppe_eval analyze
	cd $(BACKEND) && .venv/bin/python -m sentinel.eval.feed_eval analyze
	cd $(BACKEND) && .venv/bin/python -m sentinel.eval.figures
