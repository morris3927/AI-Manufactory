# 決賽交付物

| 檔案 | 說明 |
|---|---|
| 決賽簡報 | 線上版：https://claude.ai/artifact/DdxM14sfDGb8cwnPd2uEnn（可下載 .pptx／PDF；需由擁有者分享後組員才能開啟）。原始檔在 `簡報原始檔/project/` |
| `poster/Sentinel_AI_海報_A1.pdf` | A1 直式海報（594×841 mm），由 `poster/poster.html` 用 Chrome 輸出 |
| `prototype/assets/demo/backup_demo.mp4` | Demo 備案錄影（不進 git，用 `prototype/frontend/e2e/record-demo.mjs` 重錄） |

待補：指導老師姓名、團隊每人的負責項目（簡報最後一頁與海報表頭）。

重新輸出海報：在任一裝有 playwright 的 node 環境，開 `poster/poster.html`，以 `page.pdf({width:'594mm',height:'841mm',printBackground:true})` 輸出。
