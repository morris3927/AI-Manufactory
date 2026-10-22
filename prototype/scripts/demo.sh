#!/usr/bin/env bash
# Demo Day 一鍵啟動：檢查 → 重置 Demo 資料 → 後端＋前端 production build → 等 /api/health。
# 用法：
#   scripts/demo.sh            # 本機（http://localhost:3000）
#   scripts/demo.sh --lan      # 手機也要連（前端改指向筆電區網 IP）
#   scripts/demo.sh --rebuild  # 強制重新 build 前端
#   scripts/demo.sh --keep     # 不清資料（接續上一場）
# 環境變數：API_PORT（8000）、WEB_PORT（3000）、DEMO_ALLOW_DRAFT（1＝放行草稿 SOP，等同 make dev-demo）
# Ctrl-C 會一起關掉前後端。
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"          # prototype/
REPO="$(cd "$ROOT/.." && pwd)"
BACKEND="$ROOT/backend"
FRONTEND="$ROOT/frontend"
API_PORT="${API_PORT:-8000}"
WEB_PORT="${WEB_PORT:-3000}"
LOG_DIR="$ROOT/.demo-logs"
LAN=0; REBUILD=0; KEEP=0
for a in "$@"; do
  case "$a" in
    --lan) LAN=1 ;;
    --rebuild) REBUILD=1 ;;
    --keep) KEEP=1 ;;
    -h|--help) sed -n '2,10p' "$0"; exit 0 ;;
    *) echo "不認得的參數：$a"; exit 2 ;;
  esac
done

ok()   { printf '  \033[32m✓\033[0m %s\n' "$1"; }
warn() { printf '  \033[33m!\033[0m %s\n' "$1"; }
die()  { printf '  \033[31m✗\033[0m %s\n' "$1"; exit 1; }

echo "== 檢查 =="
# 1. 金鑰（只檢查有沒有值，不印出來）
ENV_FILE="$REPO/.env"
[ -f "$ENV_FILE" ] || die "找不到 ${ENV_FILE}（向組長拿 .env，放在 repo 根目錄）"
for k in OPENROUTER_API_KEY OLLAMA_API_KEY; do
  if grep -Eq "^[[:space:]]*${k}[[:space:]]*=[[:space:]]*[^[:space:]#]+" "$ENV_FILE"; then ok "$k 已設定"
  else warn "$k 沒有值 → 對應的 AI 會關閉，系統改走規則退路（Demo 仍可跑，但少了 Jev／LLM）"; fi
done
# 2. YOLO 權重與影片（都不進 git）
if [ -f "$ROOT/models/ppe.pt" ]; then ok "models/ppe.pt"
else warn "缺 models/ppe.pt → PPE 改用劇本偵測。取得方式：從組內雲端硬碟下載 ppe.pt 放到 prototype/models/，或用 prototype/training 重新訓練"; fi
n_vid=$(find "$ROOT/assets/videos" -name '*.mp4' 2>/dev/null | wc -l | tr -d ' ')
if [ "$n_vid" -gt 0 ]; then ok "assets/videos：$n_vid 段影片"
else warn "assets/videos 沒有 mp4 → 攝影機改用合成畫面。產生方式：backend/.venv/bin/python scripts/build_demo_footage.py（素材來源見 assets/videos/SOURCES.csv）"; fi
# 3. Python venv、node_modules
[ -x "$BACKEND/.venv/bin/uvicorn" ] || die "後端 venv 不存在，先跑 make setup"
[ -d "$FRONTEND/node_modules" ] || die "前端 node_modules 不存在，先跑 make setup（或 cd frontend && npm install）"
# 4. 向量模型已快取 → 離線模式（會場網路不穩時不會卡在 HuggingFace）
HF_CACHE="${HF_HOME:-$HOME/.cache/huggingface}/hub/models--BAAI--bge-m3"
if [ -d "$HF_CACHE" ]; then export HF_HUB_OFFLINE=1 TRANSFORMERS_OFFLINE=1; ok "bge-m3 已快取 → HF_HUB_OFFLINE=1"
else warn "bge-m3 沒有快取，第一次啟動會從網路下載（約 2 GB）"; fi
# 5. 埠沒被占用
for p in "$API_PORT" "$WEB_PORT"; do
  if lsof -nP -iTCP:"$p" -sTCP:LISTEN >/dev/null 2>&1; then die "埠 $p 已被占用（lsof -iTCP:$p 看是誰；上一場沒關乾淨就 kill 掉）"; fi
done

HOST=localhost
if [ "$LAN" = 1 ]; then
  HOST="$(ipconfig getifaddr en0 2>/dev/null || ipconfig getifaddr en1 2>/dev/null || hostname -I 2>/dev/null | awk '{print $1}')"
  [ -n "$HOST" ] || die "抓不到區網 IP，請確認 Wi-Fi／熱點已連線"
  ok "區網模式：$HOST"
fi
API_BASE="http://$HOST:$API_PORT"

echo "== 重置 Demo 資料 =="
if [ "$KEEP" = 1 ]; then warn "--keep：保留上一場的工單與事件"
else
  rm -f "$BACKEND/sentinel.db" "$BACKEND/sentinel.db-wal" "$BACKEND/sentinel.db-shm"
  rm -rf "$BACKEND/keyframes"
  ok "已刪除 sentinel.db 與 keyframes"
fi

echo "== 前端 build（NEXT_PUBLIC_API_BASE=${API_BASE}）=="
DIST=".next-demo"
STAMP="$FRONTEND/$DIST/.api_base"
if [ "$REBUILD" = 1 ] || [ ! -f "$STAMP" ] || [ "$(cat "$STAMP")" != "$API_BASE" ] \
   || [ -n "$(find "$FRONTEND/app" "$FRONTEND/components" "$FRONTEND/lib" -newer "$STAMP" -type f 2>/dev/null | head -1)" ]; then
  (cd "$FRONTEND" && NEXT_DIST_DIR="$DIST" NEXT_PUBLIC_API_BASE="$API_BASE" npx next build >/dev/null) || die "前端 build 失敗（cd frontend && npm run build 看錯誤）"
  echo "$API_BASE" > "$STAMP"
  ok "build 完成"
else ok "沿用上次的 build"; fi

mkdir -p "$LOG_DIR"
PIDS=()
cleanup() {
  echo; echo "== 關閉 =="
  for p in "${PIDS[@]:-}"; do [ -n "$p" ] && kill "$p" 2>/dev/null || true; done
  wait 2>/dev/null || true
  echo "  已關閉前後端"
}
trap cleanup EXIT INT TERM

echo "== 啟動 =="
export SENTINEL_KB_ALLOW_DRAFT="${DEMO_ALLOW_DRAFT:-1}"
(cd "$BACKEND" && exec .venv/bin/uvicorn sentinel.main:app --host 0.0.0.0 --port "$API_PORT" >"$LOG_DIR/backend.log" 2>&1) &
PIDS+=($!)
(cd "$FRONTEND" && NEXT_DIST_DIR="$DIST" exec npx next start -H 0.0.0.0 -p "$WEB_PORT" >"$LOG_DIR/frontend.log" 2>&1) &
PIDS+=($!)

printf '  等待後端 /api/health '
for i in $(seq 1 120); do
  if curl -sf "http://localhost:$API_PORT/api/health" -o "$LOG_DIR/health.json"; then break; fi
  kill -0 "${PIDS[0]}" 2>/dev/null || { echo; die "後端啟動失敗，看 $LOG_DIR/backend.log"; }
  printf '.'; sleep 1
done
echo
[ -s "$LOG_DIR/health.json" ] || die "等了 120 秒後端還沒好，看 $LOG_DIR/backend.log"

"$BACKEND/.venv/bin/python" - "$LOG_DIR/health.json" <<'PY'
import json, sys
h = json.load(open(sys.argv[1]))
on = lambda b: "開" if b else "關（規則退路）"
kb = h.get("kb") or {}
print(f"  偵測器：{h.get('detector_mode')}　向量模型：{h.get('embedding_model')}　知識庫：文件 {kb.get('docs')}、案例 {kb.get('cases')}{'（含草稿）' if kb.get('allow_draft') else ''}")
print(f"  Jev：{on((h.get('jev') or {}).get('enabled'))}　LLM：{on((h.get('llm') or {}).get('enabled'))}　通知：{h.get('notifier')}　作業許可：{'需要' if (h.get('workflow') or {}).get('require_permit') else '不需要'}")
cams = h.get("cameras") or {}
syn = [c for c, v in cams.items() if str(v.get("source", "")).startswith("synthetic")]
print(f"  攝影機：{len(cams)} 支" + (f"，{len(syn)} 支用合成畫面（{', '.join(syn)}）" if syn else "，全部播影片"))
PY

for i in $(seq 1 30); do curl -sf -o /dev/null "http://localhost:$WEB_PORT/" && break; sleep 1; done
echo
echo "== 網址 =="
echo "  戰情儀表板  http://$HOST:$WEB_PORT/"
echo "  控制台      http://$HOST:$WEB_PORT/control"
echo "  任務看板    http://$HOST:$WEB_PORT/kanban"
echo "  手機版      http://$HOST:$WEB_PORT/m/<工單號>（例如 WO-0001）"
echo "  後端        $API_BASE/api/health"
echo "  Log         $LOG_DIR/"
echo
echo "Ctrl-C 結束。換評審前到控制台按「重置」即可，不用重開。"
wait
