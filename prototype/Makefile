# Sentinel AI 原型（08_系統設計.md §13）
# make dev：前後端一起啟動；make test：單元測試＋劇本重播；make kb：重建知識庫；make reset：重置 Demo 資料
BACKEND := backend
PY      := $(BACKEND)/.venv/bin/python
PORT    ?= 8000

.PHONY: setup dev dev-demo backend frontend test test-live kb kb-demo reset

setup:                      ## 建立 Python 3.13 venv 並安裝後端（含測試套件）
	test -d $(BACKEND)/.venv || python3.13 -m venv $(BACKEND)/.venv
	$(PY) -m pip install -q -e "$(BACKEND)[test]"
	cd frontend && npm install

dev:                        ## 前後端一起啟動（Ctrl-C 一起結束）
	$(MAKE) -j2 backend frontend

dev-demo:                   ## 同上，但放行 review_status=draft 的 SOP（只限 Demo）
	SENTINEL_KB_ALLOW_DRAFT=1 $(MAKE) -j2 backend frontend

backend:
	cd $(BACKEND) && .venv/bin/uvicorn sentinel.main:app --reload --port $(PORT)

frontend:
	cd frontend && npm run dev

test:                       ## 單元測試＋劇本重播（不打真實 API）
	cd $(BACKEND) && .venv/bin/pytest -q

test-live:                  ## 真實 Jev／LLM 測試（需要根目錄 .env 的金鑰）
	cd $(BACKEND) && .venv/bin/pytest -q -m live

kb:                         ## 重建知識庫（只收 review_status=approved；heldout 永不入庫）
	cd $(BACKEND) && .venv/bin/python -m sentinel.kb.ingest --rebuild

kb-demo:                    ## 重建知識庫並放行 draft（只限 Demo）
	cd $(BACKEND) && .venv/bin/python -m sentinel.kb.ingest --rebuild --allow-draft

reset:                      ## 重置 Demo 資料（刪除 SQLite 與關鍵幀；服務啟動中請改用 POST /api/demo/reset）
	rm -f $(BACKEND)/sentinel.db $(BACKEND)/sentinel.db-wal $(BACKEND)/sentinel.db-shm
	rm -rf $(BACKEND)/keyframes
