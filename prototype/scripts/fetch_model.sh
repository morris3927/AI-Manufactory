#!/bin/bash
# 下載 PPE 偵測模型（YOLOv8s，頭部／安全帽 2 類）到 prototype/models/ppe.pt
# 模型放在 GitHub Release（models-v1），不進 git。
# 授權：以 SH17（CC BY-NC-SA 4.0）與 Hard Hat Workers（CC0）訓練，僅供非商業使用。
set -e
cd "$(dirname "$0")/.."
URL="https://github.com/morris3927/AI-Manufactory/releases/download/models-v1/ppe.pt"
mkdir -p models
if [ -f models/ppe.pt ]; then
  echo "models/ppe.pt 已存在，略過"
  exit 0
fi
echo "下載 $URL"
if curl -fL --progress-bar -o models/ppe.pt.part "$URL"; then
  mv models/ppe.pt.part models/ppe.pt
  echo "完成：models/ppe.pt"
else
  rm -f models/ppe.pt.part
  echo "下載失敗：Release 可能還沒上傳。"
  echo "替代做法：向組員要 ppe.pt，或依 training/ 的腳本在訓練主機重新訓練。"
  exit 1
fi
