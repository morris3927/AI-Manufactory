# Sentinel AI 知識庫（沖壓線 M1 Demo＋銲接線、組裝線）

> **資料標示**：本目錄的故障模式、SOP、LOTO 檢查表與維修案例，都是團隊依公開法規與公開技術文件自己寫的**模擬資料**（`data_label: 模擬資料`、`synthetic: true`）。沒有用到任何企業內部資料，也和贊助企業的實際製程無關。數值門檻都標了「模擬值」，沒有經過現場驗證。`public/` 底下只放官方公開的法規原文（`data_label: 公開資料`）。
> 規格依據：`02_規格定義/07_RAG知識庫資料.md` §4～§7、`06_工廠規範調查.md` §2、`08_系統設計.md` §5.1、`Jev整合規劃.md` §3.9。

## 目錄與數量

| 路徑 | 內容 | 數量 | review_status |
|---|---|---|---|
| `failure_modes/stamping.yaml` | 沖壓故障模式目錄（§4.3）。`criteria` 直接交給 Jev J3／J5，`sop` 是共同主鍵 | 20 個（2 個安全類：clutch_brake_wear、light_curtain_fault） | approved＊ |
| `sop/stamping/SOP-STP-*.yaml` | 每個模式一份 SOP（§4.1），phase 依序為 prepare→isolate→inspect→repair→verify→restore | 20 份（18 份由 LLM 起草＋人工修正，2 份安全類用固定模板） | approved＊ |
| `loto/LOTO-*.yaml` | 伺服沖床、機械沖床、伺服送料機的上鎖掛牌檢查表（§4.4）。**不進向量庫**，用 `machine_type` 直接取 | 3 份（沖壓線）＋3 份（銲接、組裝：LOTO-WELD-ROBOT-01、LOTO-SPOT-WELD-01、LOTO-ASM-TOOL-01） | approved＊／＊＊ |
| `cases/stamping/CASE-STP-*.json` | 模擬維修案例（§4.2、§6），訓練／檢索用 | 100 筆 | draft |
| `cases/stamping/heldout/` | **保留測試集：只能拿來測試，不可入庫**（每個模式 1 筆） | 20 筆 | draft |

＊＊ 銲接、組裝檔案由 **AI 角色扮演審核（模擬資深銲接／組裝主管），非真人專家審核**（`reviewed_by: [ai_sim_reviewer_weld_asm]`），紀錄見 `reviews/2026-10-21_weld_asm.md`。

＊ 2026-10-21 由 **AI 角色扮演審核（模擬資深維修課長），非真人專家審核**（`reviewed_by: [ai_sim_reviewer_press_chief]`、`review_note`）。審核紀錄與「留給真人專家複核」清單見 `reviews/2026-10-21_press_chief.md`。改成 approved 之後 `build_kb`／`ingest` 不需 `--allow-draft` 就會收這些檔案；對外說明時請講清楚這不是真人專家簽核。SOP 的審核欄位由 `_gen/sop_patches.yaml` 的 `review:` 區塊在 assemble 時寫入。
| `failure_modes/welding.yaml`、`assembly.yaml` | 銲接、組裝故障模式目錄（各 10 個；安全類各 1：fume_extraction_fault、ppe_esd_missing） | 20 個 | approved＊＊ |
| `sop/welding/SOP-WLD-*.yaml`、`sop/assembly/SOP-ASM-*.yaml` | 每個模式一份 SOP（同一套 prepare／isolate／restore 骨架） | 20 份（18 份 LLM 起草＋審稿修正，2 份安全類固定模板） | approved＊＊ |
| `cases/welding/`、`cases/assembly/` | 模擬維修案例 | 各 50 筆 | approved＊＊ |
| `cases/welding/heldout/`、`cases/assembly/heldout/` | **保留測試集，不可入庫**（每個模式 1 筆） | 各 10 筆 | approved＊＊ |
| `public/regulations_weld_asm.jsonl` | 銲接、組裝相關法規條文（全國法規資料庫、eCFR 逐字抓取） | 45 個 | approved |
| `public/guidance_weld_asm.jsonl` | 公開指引原文：OSHA FS-3647（PD）、NASA-STD-8739.4A（PD）、HSE INDG316（Crown copyright，可免費重製但不得作廣告、背書或商業用途，須標示 HSE） | 15 個 | approved |
| `public/regulations.jsonl` | 法規條文 chunk（§4.5），從全國法規資料庫與 eCFR 官方頁面原文抓取 | 51 個 | approved |
| `signals.yaml` | 訊號詞彙表，`log_signals` 只能用這裡的名稱（模擬器共用） | — | — |
| `SOURCES.yaml` | 資料來源與授權清冊（§7.3），`sources` 與步驟 `ref` 引用的編號都在這裡 | 37 個來源 | — |
| `_gen/` | 產生與驗證程式、prompt、LLM 原始輸出（可重現） | — | — |

**案例組成**（120 筆）：典型 60、第一判斷錯誤 24（20%）、複合原因 12、誤報 12、升級 6、目錄外新模式（`final_root_cause: new`）6。高頻的 5 個模式（feed_roller_wear、material_thickness_dev、die_misalignment、slug_pulling、misfeed_sensor_fault）各 9 筆，其餘模式各 5 筆。錯誤的第一判斷一定從真正模式的 `distinguish_from` 裡抽；提出者 `by` 平均分給 jev、technician、rule 三種。

**法規 chunk**：
- 職業安全衛生設施規則第 57（116-01-01 施行的新版，加上目前仍有效的 113-08-01 版）、58、69、70、254、277、284、292、300 條
- 職業安全衛生管理辦法第 26、59、80 條
- 機械設備器具安全標準第 3、4、6、11-1、12、12-2、18-1、19、20、22、25、31、35、37、38、39、40、42 條
- 職業安全衛生法第 6、18、37 條
- OSHA 29 CFR 1910.147 (c)(4)(ii)、(d)(1)～(6)、(e)(1)～(3)、(f)(1)、(f)(3)

注意：設施規則第 57 條的新版要到 116-01-01 才施行。所以新版 chunk 標了 `effective_status: promulgated_not_yet_effective`，同時保留現行版本 `REG-N0060009-57-v20240801`。osha.gov 會擋程式存取（CloudFront 403），OSHA 原文改從官方 eCFR（ecfr.gov）抓取。所有條文都是逐字抓取，沒有經過 LLM。沒抓到的條文會標 🔬 並略過，不會自己補寫。

## 產生方式（可重現）

```bash
cd prototype/knowledge/_gen
python3.13 fetch_regulations.py          # 法規 → public/regulations.jsonl（用 curl；python3.13 的 SSL 不接受 law.moj.gov.tw 的憑證鏈）
python3.13 gen_sops.py draft             # LLM 起草 18 份 SOP 的 inspect/repair/verify（已快取在 raw/sop/）
python3.13 gen_sops.py assemble          # 套用 sop_patches.yaml，加上固定的 prepare/isolate/restore → sop/stamping/
python3.13 gen_cases.py draft            # LLM 依骨架撰寫案例文字（已快取在 raw/cases/）
python3.13 gen_cases.py assemble         # 合併骨架與文字、自動檢查、切出 heldout → cases/stamping/
python3.13 validate.py [-v]              # schema 與交叉參照驗證，有錯誤就以 exit 1 結束
```

- LLM：DeepSeek V4.1 Flash，經 Ollama Cloud 呼叫（`_gen/llm.py`，從 repo 根目錄的 `.env` 讀 `OLLAMA_API_KEY`），輸出用 JSON schema 限制格式。prompt 放在 `_gen/prompts/`。
- **事實由程式決定，LLM 只負責寫文字**（§6.2）。`case_skeleton.py`（seed 2026）會先決定根因、案例類型、第一判斷、機台、時間與班別、保養日曆、近期事件、料捲與模具、log 訊號型態、停機時間和安全旗標，再交給 LLM 寫原始工單、整理後工單、處置步驟、`why_first_guess_wrong`、`discriminating_evidence` 和心得。產生案例時 Jev 不參與根因決定。
- 同一台機台的案例時間不重疊，`days_since_pm` 依 60 天保養週期（模擬值）推算，彼此一致；夜班比例略高。
- 安全類模式的 SOP **不經過 LLM**，只寫停機、通知、上鎖、等待合格人員和原上鎖人解鎖，不提供任何排除方法。
- 人工審稿時發現的 LLM 錯誤記在 `_gen/sop_patches.yaml`，每次 assemble 都會重新套用。修正內容包括：安全塊用語統一、移除「卡在下死點時手動盤車」這種危險作法、門檻和目錄對齊、需要通電或氣源的步驟加上 `requires_energized`。
- 只要步驟需要通電，程式會自動在前面插入 LOTO 檢查表裡的「測試／定位程序」（OSHA 1910.147(f)(1)：原上鎖人取下自己的鎖、主管在場、只能寸動）。
- 第一個「試沖／連續生產」的 verify 步驟前，程式會插入恢復確認步驟（撤出安全塊、恢復護圍與光幕、人員離開、原上鎖人解鎖後才以正常模式試沖），因為測試／定位程序只涵蓋寸動調整與讀值。
- 只讀 MES 紀錄的步驟不標 `requires_energized`，不為了看紀錄對機台送電。

## 給 backend／simulator 的對接事項

1. **需要新增的 MES 訊號**（`signals.yaml` 裡 `status: proposed`，正常範圍都是模擬值）：
   - `tonnage_balance_pct`、`stroke_count_since_regrind`、`die_stroke_total`
   - `stop_angle_deg`、`stop_time_ms`
   - `air_pressure_bar`、`counterbalance_bar`、`shut_height_mm`
   - `lube_pressure_bar`、`bearing_temp_c`、`die_lube_pulse_per_min`
   - `scrap_conveyor_current_a`、`light_curtain_status`
   - `pilot_release_angle_deg`、`straightener_setting_mm`
   - 事件 `param_change`；影像事件 `person_in_zone`
   - `alarm_code` 的通用類別碼（MISFEED、DIE_PROTECT、OVERLOAD、OVERRUN、AIR_LOW、FD_OVERCURRENT…，共 15 個）都列在 `signals.yaml`，刻意不使用真實廠牌的警報碼
   - 假設 `feed_rate` 的單位是「次／分」，數值約等於 spm，和 feed_jam 劇本的 42 一致
2. **plant.yaml 建議新增的機台**：除了既有的 STP-L1-PR01、STP-L1-FD02，案例還用到 `STP-L2-PR03`（mechanical_power_press，200 t）和 `STP-L2-FD04`（servo_roll_feeder）。
3. 案例 `log_signals[].t` 是完整的 ISO-8601 時間（+08:00）。訊號來自另一台機台時會帶 `machine_id`，例如送料機案例裡的沖床噸位。
4. **入庫守門**：目前除了法規以外都是 `review_status: draft`，照 §4.5 的規則會被 `build_kb.py` 拒收。Demo 前要嘛完成人工審核並改成 `approved`，要嘛在 build_kb 加一個明確的 `--allow-draft`（只限 Demo）參數。**`cases/stamping/heldout/` 不管怎樣都不能入庫。**
5. 案例的 `final_root_cause` 除了目錄 ID，還可能是 `none`（誤報）或 `new`（目錄外）；`related_failure_mode_ids` 可以拿來當 chunk metadata。

## 銲接線、組裝線（2026-10-21 新增）

流程和沖壓線相同，但另外寫了一套腳本，沖壓的產生程式與輸出都沒有改動。各線的設定放在 `_gen/lines.py`。

```bash
cd prototype/knowledge/_gen
python3.13 fetch_public_weld_asm.py                      # → public/regulations_weld_asm.jsonl、guidance_weld_asm.jsonl
python3.13 gen_sops_wa.py draft|assemble --line welding  # assembly 同理；修正放在 sop_patches_weld_asm.yaml
python3.13 gen_cases_wa.py draft|assemble|approve --line welding   # 修正與簽核 hash 放在 case_patches_weld_asm.yaml
python3.13 validate.py                                   # 沖壓＋validate_lines.py（銲接、組裝）一起驗證
```

- **銲接**：機器人 MIG 工作站（`robot_mig_cell`：WLD-L1-RC01、RC02）、座式點銲機（`spot_welder`：WLD-L1-SP01、WLD-L2-SP02），保養週期 30 天（模擬值）。依 01 號文件 C1，銲接工位 ROI 內的火花與銲煙屬正常；排煙 SOP 寫明「機器人停止後仍冒煙或工位外有火光」要當疑似火災，走緊急應變。機器人 SOP 一律有：掛「禁止觸動」、攜帶安全栓、圍欄外監視人員（工業用機器人危害預防標準第 26、33 條）、教導模式降速並握住致能開關（第 27 條）、不得修改安全參數。點銲機有安全插銷（1910.255(b)(8)）與不通電加壓。需要到工作站外研磨或補銲的模式（fixture_fitup_gap、ground_poor）會加上動火許可與清除可燃物（第 173 條）。
- **組裝**：電動起子鎖附站（`screwdriving_station`：ASM-L1-ST01、ST02）、人工組裝／功能測試站（`manual_assembly_station`：ASM-L1-MA01、MA02），保養週期 90 天（模擬值）。採輕量 LOTO：拔插頭並套插頭鎖蓋或取下電池，依據 1910.147(a)(2)(iii)(A)。PPE／靜電模式不經 LLM，只寫暫停、先查防護具有沒有發放（第 277 條）、提醒不懲罰，`loto_required: false`。線材彎曲半徑引用 NASA-STD-8739.4A 表 7-1。
- **案例**：每條線 60 筆（訓練 50＋heldout 10，每個模式 1 筆 heldout）。組成：典型 31、第一判斷錯誤 11（18%）、複合 6、誤報 6、升級 3、目錄外新模式 3。seed 固定（銲接 2027、組裝 2028）。上鎖後的試銲或試鎖前，程式會自動插入測試／定位步驟；移交待修時也會插入鎖具交接步驟。
- **公開資料**：設施規則第 106、108、173、174、245、250、277-1、287、324-1 條；工業用機器人危害預防標準第 4、13、21、24～29、31～33 條（pcode N0060025，已在全國法規資料庫查證）；管理辦法第 40、47、60、66、77 條；29 CFR 1910.252(a)(1)、(a)(2)(ii)～(iv)、(c)(1)(iii)、(c)(3)；1910.254(c)(2)、(d)(2)～(4)、(d)(9)；1910.255(a)(4)、(b)(3)、(b)(5)、(b)(7)、(b)(8)、(e)；1910.147(a)(2)(iii)(A)。指引原文用 pdftotext 抽出，只去掉頁碼與項目符號。**NIOSH 2011-156 沒有入庫**：cdc.gov 對程式存取回 HTTP 403（Akamai），換了網址也一樣，所以只列在 SOURCES 當參考，沒有自己補寫內容。
- **給 simulator 的 TODO（signals.yaml 標 `line: welding|assembly`、`status: proposed`）**：
  - 銲接：`wire_feed_speed_mpm`、`wire_feed_motor_current_a`、`arc_current_a`、`arc_current_cv_pct`、`arc_voltage_v`、`arc_voltage_cv_pct`、`arc_start_retry_per_100`、`tip_arc_on_min`、`gas_flow_lpm`、`gas_supply_bar`、`torch_clean_interval_min`、`tcp_check_delta_mm`、`defect_rate_pct`、`weld_current_ka`、`electrode_force_kn`、`weld_count_since_dress`、`tip_dress_count`、`coolant_flow_lpm`、`coolant_return_temp_c`、`exhaust_flow_m3h`、`exhaust_dp_pa`、`exhaust_fan_state`、`robot_state`；事件 `weld_defect`、`collision_event`、`consumable_change`、`part_lot_change`；影像 `smoke`（none／in_cell／outside_cell）、`flame`
  - 組裝：`screw_result`、`final_torque_nm`、`final_angle_deg`、`rundown_torque_nm`、`nok_hole_id`、`nok_rate_pct`、`bit_cycle_count`、`feeder_pick_fail_count`、`screw_count_actual`、`screw_count_required`、`torque_audit_dev_pct`、`insulation_mohm`、`test_result`、`esd_check`；事件 `screw_lot_change`、`calib_due_days`、`ppe_stock_count`；影像 `cable_out_of_path`、`connector_unlatched`、`screw_missing`、`ppe_missing`
  - 警報類別碼新增 18 個（ARC_START_FAIL、GAS_LOW、COLLISION、FUME_EXH_LOW、SCREW_NOK、FEEDER_TIMEOUT、ESD_CHECK_FAIL…），一樣刻意不用真實廠牌的警報碼
- **plant.yaml 建議新增**：上面 8 台機台（`welding`、`assembly` 兩條線的 `machines` 目前是空的）。

## 人工審核清單（§7.2）與目前狀態

| 項目 | 狀態 |
|---|---|
| criteria 和 SOP inspect 一致，照著做能證實或排除 | ⚠️ LLM 依 criteria 撰寫，validate 檢查格式；內容待組員逐份確認 |
| 涉及能源的步驟前面有 LOTO | ✅ 由程式保證（validate：isolate 在 inspect／repair 之前、通電步驟前有測試／定位程序） |
| 換模或調模步驟有安全塊（第 69 條）和寸動（第 70 條） | ✅ validate 檢查 |
| 由原上鎖人取下自己的鎖具（參考 OSHA 1910.147(e)(3)）；電氣送電前確認無感電之虞（設施規則第 254 條第 2 項），AI 不能復電 | ✅ 固定模板＋validate |
| 安全類模式只寫停機、通知、交給合格人員 | ✅ 固定模板，validate 禁止 repair phase 和 LLM 產生 |
| 數值門檻都標了「模擬值」 | ✅ validate 用正則檢查 inspect／repair／verify 與 criteria；2026-10-21 AI 角色扮演審核已調整不合理的門檻（見 reviews/）；⚠️ 仍請業師看一次 |
| 沒有真實廠商的警報碼、零件號、型號 | ✅ prompt 禁止，警報碼用自訂類別；⚠️ 仍需人工抽查 |
| 每步都有 `[ref]`；`[ref:none]` 經組員確認 | ✅ 剩下 4 步 `ref: none` 已改寫並標「【現場經驗（模擬）】」（validate 對有這個標記的步驟不再警告） |
| 廠商資料是改寫，沒有整段複製 | ✅ © 來源只給 LLM 標題，沒有給原文，所以不可能照抄；references 標 `use: paraphrase` |
| 台灣用語、全形標點 | ✅ validate 檢查中文後接半形標點；2026-10-21 AI 角色扮演審核已抽查並修正（扭矩→扭力、點動→寸動等）|
| 文件頭有 `data_label: 模擬資料` | ✅ |
| 案例：log 訊號型態符合 `log_signals` | ✅ 由骨架程式依模式產生 |
| 案例：`discriminating_evidence` 確實能推翻第一判斷 | ⚠️ 24 筆第一判斷錯誤的案例要全部人工審（§7.1 第 6 點） |
| 案例：沒有人名、公司名、地址 | ✅ 技術員只用代號 TECH-xx；validate 做簡單的人名規則檢查 |

最近一次執行 `validate.py`（2026-10-21）：沖壓、銲接、組裝三條線一起驗證，結果 **PASS（0 error、0 warning）**。銲接／組裝的 LOTO `machine_type` 已經加進 validator（`MTYPES_ALL`），新的檢查放在 `_gen/validate_lines.py`。
