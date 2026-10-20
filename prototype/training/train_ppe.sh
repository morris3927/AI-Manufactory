#!/bin/bash
# 訓練主機上執行：以 SH17 預訓練的 yolo8s 為起點，微調 2 類（head、helmet）模型。
# 用 GPU 1（GPU 0 有其他人的服務）；yolo8s 在筆電上也跑得動，Demo 用。
set -e
cd ~/sentinel-train && . .venv/bin/activate
CUDA_VISIBLE_DEVICES=1 yolo train \
  model=models/yolo8s.pt data=data/ppe2/ppe2.yaml \
  epochs=60 imgsz=640 batch=32 device=0 workers=8 \
  patience=15 cos_lr=True seed=0 \
  project=runs/ppe2 name=yolo8s exist_ok=True
echo DONE > runs/ppe2/yolo8s.done
