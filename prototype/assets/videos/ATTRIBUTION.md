# Demo 監視畫面：來源與授權

這些影片**不是實拍產線影像**。團隊還沒有自己拍的影片，所以 `scripts/build_demo_footage.py` 用公開授權的照片
做成固定機位的「模擬監視畫面」（裁成 16:9、緩慢推拉或微幅漂移、感光雜訊、JPEG 壓縮、時間戳記）。
每一幀下方都印著「模擬監視畫面（公開授權照片合成）」；有程式產生動態或異常效果的攝影機，另外印「合成：…」。

逐檔來源、作者、授權與改作內容見 [SOURCES.csv](SOURCES.csv)（`synthetic_edit`：`anomaly` ＝ 異常是影像編輯、
`motion` ＝ 動態是程式產生、`no` ＝ 只有裁切、推拉與畫質處理）。

| 攝影機 | 來源 | 授權 | 合成的部分 |
|---|---|---|---|
| CAM-ASM-01 組裝作業區 | Pexels 照片 6 張（SH17 資料集收錄，依其標註與本專案 YOLO 實測挑選） | Pexels License | 無像素編輯；`ppe_violation_01` 在 4.5–5.3 秒交叉淡化到另一張有人沒戴安全帽的照片（模擬時間經過，不是連續拍攝） |
| CAM-ASM-02 鎖附站 | Pexels 照片 1 張 | Pexels License | 無 |
| CAM-STP-01 沖床送料口 | [File:Air Feeder.jpg](https://commons.wikimedia.org/wiki/File:Air_Feeder.jpg)，Jstokes452 | CC BY-SA 3.0 | 衝程震動、銅帶送入（程式位移）；`feed_jam_01` 的卡料是**合成**的：取照片裡的銅帶像素，透視變形成翹起與錯位疊料的板材 |
| CAM-WLD-01 銲接機器人 | [File:Welding Robot.jpg](https://commons.wikimedia.org/wiki/File:Welding_Robot.jpg)，Ptmetindoerasakti | CC BY-SA 4.0 | 火花閃爍（從原照片分離後抖動）；`weld_smoke_01`：機器人停止＝把火花修補掉，工位外的煙霧是**合成**的（程式產生） |
| CAM-WLD-02 點銲站 | [File:FANUC welding robot reaching.jpg](https://commons.wikimedia.org/wiki/File:FANUC_welding_robot_reaching.jpg)，Phasmatisnox | CC BY 3.0 | 無 |

授權說明：

- **Pexels License**：可以免費使用、修改，不必標示出處（我們仍逐張列出原頁）。不可以把未修改的照片原樣販售，
  也不可以暗示照片中的人或品牌為本專案背書。照片裡看得到的公司名稱（例如工作服上的字樣）跟本專案無關。
- **SH17**（CC BY-NC-SA 4.0）只用來挑圖：看標註挑出有頭、有安全帽的照片。影片的像素直接取自 Pexels 原圖，沒有使用 SH17 的檔案。
- **CC BY-SA 3.0／4.0**：改作的影片要沿用相同授權（姓名標示－相同方式分享）；**CC BY 3.0**：要標示作者。
  `build_demo_footage.py` 建置時會向 Commons API 重新確認每個檔案的授權，不在允許清單裡就停止。

重建：在 `prototype/` 執行 `backend/.venv/bin/python scripts/build_demo_footage.py`。來源照片下載到
`~/.cache/sentinel-footage`（不進 repo），mp4 已列在 .gitignore。若要補上 Pexels 攝影師姓名，加 `--refresh-meta`
（從 SH17 的 meta-data 抓，Kaggle 會限流，可以晚點再跑）。
