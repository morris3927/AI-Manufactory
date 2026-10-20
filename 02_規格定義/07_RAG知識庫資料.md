# 07｜RAG 知識庫資料：可用來源盤點、授權判斷與自製資料計畫

> 調查日期：2026-10-20
> 用途：決定 Sentinel AI 知識庫（向量資料庫＋維修案例庫）要放哪些公開資料、哪些必須自己產生，並定出 SOP／案例／故障模式／LOTO 檢查表的資料格式。
> 對應文件：`Jev整合規劃.md` 3.4（J3 根因選擇）、3.8（J5 結案回寫）、3.9（故障模式目錄）；`06_工廠規範調查.md` 2.2～2.6（設施規則第 57 條、LOTO）。
> 標記說明：🔬＝沒有逐字查證或授權條款沒有找到一手說明，使用前要再確認。「已開啟」＝本次實際抓取過頁面或檔案；「搜尋所見」＝只在搜尋結果看到網址，沒有打開內文。
> 競賽規則提醒：只能用公開資料集或模擬資料；外部資料要確認合法授權；不得使用企業機密。**本文件所有故障模式、SOP、案例皆為模擬資料，與勤誠興業實際製程無關。**

---

## 0. 結論先講

1. **公開資料只能當「骨架與依據」，不能直接當知識庫主體。** 網路上找得到的，大多是法規、安全指引、通用故障排除表；真正的「某台沖床、某個故障、第幾步做什麼」SOP 和維修紀錄，沒有任何公開來源可以直接用，**必須自己產生**。
2. **可以放心整段放進 RAG 的，只有政府與公部門文件**：
   - 美國聯邦政府著作（OSHA、NIOSH、NASA）：原則上沒有著作權，可自由重製。
   - 英國 HSE：Open Government Licence（OGL），註明出處即可再利用。
   - 台灣勞動部、職安署：採「政府資料開放授權條款－第 1 版」，註明出處即可重製、改作。
   - 全國法規資料庫的法條：依著作權法第 9 條，法令本身不是著作權標的。
3. **廠商手冊與產業文章（Miller、Lincoln、Atlas Copco、Thermaset、The Fabricator 等）都有著作權**，內容品質最好，但**不要整份放進向量庫、不要在 Demo repo 附原檔**。做法是讀完後用自己的話改寫成我們的 SOP，在 chunk 的 `references` 欄位附上出處網址，必要時只短句引用（台灣著作權法第 52 條合理引用、第 65 條合理使用 🔬 需個案判斷）。
4. **公開資料集用途有限但有用**：
   - MaintNorm、MaintIE（MIT 授權）：學「技術員實際怎麼寫工單」的風格，例如縮寫、錯字、短句，讓我們的模擬案例看起來像真的。
   - FMC-MWO2KG：用工單文字選 ISO 14224 故障模式，和 Jev 的 J3 是同一種問題，可以當評估方法的參考。
   - AI4I 2020（CC BY 4.0）、Azure PdM：感測數值與故障標籤的結構可以參考，用來設計我們模擬器的 log 訊號。
   - 職安署「重大職業災害公開網」（政府開放授權）：提供真實的災害類型分布，可放進安全類案例的背景。
5. **必須自製的四類資料**：
   1. 故障模式目錄（本文先擬沖壓 20 個、銲接 10 個、組裝 10 個）
   2. 每個故障模式一份 SOP
   3. 每類機台一份 LOTO 檢查表（對齊設施規則第 57 條，116-01-01 施行版）
   4. 約 320 筆模擬維修案例，其中約 20% 是「第一次判斷錯誤」的案例

   格式定義在第 4 節。
6. **Jev 和 RAG 串接的關鍵**：用 `failure_mode_id` 當共同主鍵。順序如下：
   1. 先用事件特徵檢索相似案例，餵給 Jev 的 `similar_cases`。
   2. Jev 回傳 `root_cause` 的 top-3 和機率。
   3. 用 top-3 的 `failure_mode_id` 過濾 SOP chunk，再交給 LLM 維修代理。
   4. LOTO 檢查表用 `machine_type` 直接取，不靠語意檢索，因為安全文件不能「可能找不到」。

---

## 1. 公開手冊與指引盤點（依產線）

授權欄位代號：
- **PD**：美國聯邦政府著作，不受著作權保護（17 U.S.C. §105）
- **OGL**：英國 Open Government Licence
- **TW-OGDL**：政府資料開放授權條款－第 1 版
- **©**：一般著作權
- **©-NC**：OSHA 補助計畫產出的第三方教材，OSHA 說明只限非商業、教學用途

可否入庫：
- ✅：可整段切塊放進向量庫
- ⚠️：只放「我們改寫的摘要＋出處連結」，不放原文
- ❌：不建議使用

### 1.1 跨產線：上鎖掛牌（LOTO）與安全法規

| # | 文件 | 網址 | 格式／語言 | 授權 | 可否入庫 | 品質與用途 | 查證 |
|---|---|---|---|---|---|---|---|
| L1 | 職業安全衛生設施規則（第 57、69、70、254 條等） | https://law.moj.gov.tw/LawClass/LawAll.aspx?pcode=N0060009 | HTML／中文 | 法令，著作權法第 9 條 | ✅ | LOTO 檢查表的法源；第 57 條新版 116-01-01 施行 | 06 號文件已查 |
| L2 | 職業安全衛生管理辦法 第 26 條（衝剪機械每年定期檢查：離合器及制動裝置、曲柄軸、飛輪、滑塊、連結螺栓及連桿、一行程一停止機構及緊急制動器、電磁閥、減壓閥及壓力表、配線及開關） | https://law.moj.gov.tw/LawClass/LawSingle.aspx?pcode=N0060027&flno=26 | HTML／中文 | 法令 | ✅ | 定期保養類故障模式的「依據」欄位 | 已開啟 |
| L3 | 同辦法 第 59 條（衝剪機械每日作業前檢點：離合器及制動器機能、曲柄軸等、一行程一停止機構、安全裝置、電氣及儀表） | https://law.moj.gov.tw/LawClass/LawSingle.aspx?pcode=N0060027&flno=59 | HTML／中文 | 法令 | ✅ | 每日點檢表、開機前檢查 SOP；第 80 條紀錄保存 3 年 🔬（只看過摘要） | 已開啟 |
| L4 | 機械設備器具安全標準（衝剪機械安全裝置、寸動、緊急停止） | https://6laws.net/6law/law3/機械設備器具安全標準.htm | HTML／中文 | 法令（轉載網站） | ✅（建議改抓全國法規資料庫版本） | 安全裝置類故障模式的判定依據 | 06 號文件已查 |
| L5 | 職安署南區中心《衝剪機械危害預防》單張（2020-02 建檔） | https://esh.ncut.edu.tw/var/file/7/1007/img/720104393.pdf | PDF 1 頁／中文 | TW-OGDL 🔬（原件出自職安署，但抓到的是勤益科大轉載檔） | ✅ | 整理機械設備器具安全標準第 4、6 條，內容短，適合當安全提示 chunk | 已開啟（已抽出文字） |
| L6 | 勞動部網站資料開放宣告 | https://www.mol.gov.tw/2578/2584/3276/ | HTML／中文 | 說明勞動部網站內容採 TW-OGDL；例外：專人專案撰文、特別聲明的影音圖像 | — | 授權依據 | 已開啟 |
| L7 | OSHA 29 CFR 1910.147 危害能源控制（LOTO） | https://www.osha.gov/laws-regs/regulations/standardnumber/1910/1910.147 | HTML／英文 | PD | ✅ | (d) 六步驟：準備、停機、隔離、上鎖、釋放殘能、驗證；(e) 解除程序 | 已開啟 |
| L8 | OSHA 1910.147 附錄 A「典型最低限度上鎖程序」 | https://www.osha.gov/laws-regs/regulations/standardnumber/1910/1910.147AppA | HTML／英文 | PD | ✅ | **LOTO 檢查表範本的直接來源**：通知、辨識能源、正常停機、隔離、上鎖、釋放殘能、驗證、復電五步 | 已開啟 |
| L9 | OSHA 危害能源控制主題頁（含 LOTO Fact Sheet FS-3529） | https://www.osha.gov/control-hazardous-energy | HTML／英文 | PD | ✅ | 入口頁；Fact Sheet 實際 PDF 路徑沒有開啟 🔬 | 已開啟 |
| L10 | OSHA 補助教材「LOTO Procedures Example」（Harwood 補助 SH-27664-SH5） | https://www.osha.gov/sites/default/files/2018-12/fy15_sh-27664-sh5_Toolbox_LOTO_Procedures_Example.pdf | PDF／英文 | ©-NC（受補助單位製作，非聯邦政府著作） | ⚠️ 只參考表格欄位 | 有「通知、檢視程序、停機、斷開隔離、上鎖…」逐步表格和「機台專屬 LOTO 指示」頁，適合拿來對照我們的檢查表設計 | 已開啟（已抽出文字） |
| L11 | NIOSH Workplace Solutions 2011-156《Using Lockout and Tagout Procedures to Prevent Injury and Death during Machine Maintenance》 | https://www.cdc.gov/niosh/publications/numbered/2011-156.html | HTML＋PDF／英文（另有西班牙文） | PD 🔬（CDC 一般為公有領域，個別圖片可能例外） | ✅ | 案例＋建議，適合當「為什麼要 LOTO」的說明 chunk | 已開啟 |
| L12 | HSE INDG316《Procedures for daily inspection and testing of mechanical power presses and press brakes》 | https://www.hse.gov.uk/pubns/indg316.pdf | PDF／英文 | 文件內載「© Crown copyright… may be freely reproduced, except for advertising, endorsement or commercial purposes」；HSE 網站內容另適用 OGL | ✅（註明出處；非商業用途） | **沖床與折彎機每日點檢的最佳公開範本**：分斷電檢查、通電靜態、通電動態三階段；檢查不合格時要停機隔離、掛警告牌、通知主管 | 已開啟（已抽出文字） |
| L13 | HSE HSG236《Power presses: Maintenance and thorough examination》（2003） | https://www.hse.gov.uk/pubns/books/hsg236.htm | 網頁＋免費 PDF／英文 | OGL 🔬（HSE 說明多數出版品適用 OGL） | ✅ | 附維修日誌、承包商工作紀錄、每日與定期保養檢查表範例，**可直接當案例紀錄欄位的參考** | 已開啟（介紹頁） |
| L14 | HSE L112《Safe use of power presses》ACOP 第 2 版（2014-11） | https://www.hse.gov.uk/pubns/books/l112.htm | 網頁＋免費 PDF／英文 | OGL 🔬 | ✅ | 法規層級的沖床安全使用規範 | 已開啟（介紹頁） |
| L15 | HSE 著作權說明 | https://www.hse.gov.uk/help/copyright.htm（搜尋所見） | HTML | 說明可依 OGL 免費再利用；建議標示「Contains public sector information published by the Health and Safety Executive and licensed under the Open Government Licence」；例外：非 Crown 擁有的圖片、HSE 標誌 | — | 授權依據 | 搜尋所見（另一個網址 /copyright.htm 回 404） |
| L16 | OSHA Job Hazard Analysis（OSHA 3071） | https://osha.gov/sites/default/files/publications/osha3071.pdf | PDF／英文 | PD | ✅ | 拆解作業步驟與危害，可當 SOP「危害辨識」欄位的寫法參考 | 搜尋所見 |

### 1.2 沖壓線（機械／伺服沖床、送料機、模具）

| # | 文件 | 網址 | 格式／語言 | 授權 | 可否入庫 | 品質與用途 | 查證 |
|---|---|---|---|---|---|---|---|
| S1 | OSHA Machine Guarding eTool：Mechanical Power Presses | https://www.osha.gov/etools/machine-guarding/presses/mechanical-power | HTML／英文 | PD | ✅ | 全轉式與部分轉式離合器、護圍；**沒有維修內容** | 已開啟 |
| S2 | 同 eTool：Additional References（附錄 F 拉回／約束裝置檢查表、附錄 G 雇主自主檢查表、附錄 H 傷害報告表、附錄 I 問答） | https://www.osha.gov/etools/machine-guarding/additional-references | HTML／英文 | PD | ✅ | 檢查表格式的範本 | 已開啟 |
| S3 | OSHA 1910.217 機械動力沖床 | https://www.osha.gov/laws-regs/regulations/standardnumber/1910/1910.217 | HTML／英文 | PD | ✅ | 離合器與制動器、單行程、檢查與維修紀錄要求 | 06 號文件已列 |
| S4 | Durant Tool《Progressive die misfeed troubleshooting》 | https://www.durantco.com/blog/progressive-die-misfeed-troubleshooting-feed-accuracy/ | HTML／英文 | © 2026 Durant Tool | ⚠️ | **送料故障排除順序最完整**：檢查料帶誤差型態、材料（側彎、污染）、無料一轉法測送料精度、滾輪磨損與油污、滾輪壓力、導正銷釋放時機（關閉釋放看精度是否改善）、模具對中、料環張力與整平、送料機與沖床訊號通訊。是送料類故障模式的主要依據 | 已開啟 |
| S5 | The Fabricator《Die Basics 101: Resolving slug pulling problems》等廢料上跳文章 | https://www.thefabricator.com/article/stamping/die-basics-101-resolving-slug-pulling-problems | HTML／英文 | © | ⚠️ | 原因：困氣、間隙過大、沖速過快、潤滑油黏、沖頭帶磁、頂料彈簧疲勞 | 搜尋所見 |
| S6 | The Fabricator《Ask the stamping expert: How can I avoid die crashes?》 | https://www.thefabricator.com/thefabricator/article/bending/ask-the-stamping-expert-how-can-i-avoid-die-crashes- | HTML／英文 | © | ⚠️ | 搜尋摘要提到「95% 的模具撞模是誤送料造成」🔬 | 搜尋所見 |
| S7 | MetalForming Magazine 數位版（十大模具問題：誤送料、跳屑、料帶不直、拉傷、皺褶與破裂、尺寸變異、刃口崩缺與磨耗、斷沖、廢屑、感測器誤停機） | https://www.metalformingmagazine.com/public/Images/2022/11/digital/files/basic-html/page13.html | HTML／英文 | © | ⚠️ | **故障模式清單的分類依據** 🔬（只看搜尋摘要） | 搜尋所見 |
| S8 | 伺服送料機導正銷釋放時機（MetalForming 2013-12） | https://www.metalformingmagazine.com/public/images/2013/12/digital/files/basic-html/page20.html | HTML／英文 | © | ⚠️ | 氣動釋放約 35 ms 延遲會影響導正時機 🔬 | 搜尋所見 |
| S9 | Coe Press Equipment 型錄（ServoMaster） | https://pdf.directindustry.com/pdf/coe/product-overview/111963-392433.html | PDF／英文 | © | ❌ | 只有型錄，**沒有找到公開的送料機故障排除手冊** | 搜尋所見 |

> **沖壓線缺口**：AIDA、Komatsu、金豐、協易等伺服沖床原廠維修手冊與警報碼表都沒有公開版本 🔬。驅動器警報類故障模式（過電流、位置偏差過大）只能參考通用伺服概念自行撰寫，並標成模擬資料。

### 1.3 折彎線（油壓／伺服折彎機）

| # | 文件 | 網址 | 格式／語言 | 授權 | 可否入庫 | 品質與用途 | 查證 |
|---|---|---|---|---|---|---|---|
| B1 | OSHA eTool：Powered Press Brakes | https://www.osha.gov/etools/machine-guarding/presses/powered-press-brakes | HTML／英文 | PD | ✅ | 危害：工件甩動、踏板誤觸；防護：感應式、雙手、拉回、約束裝置；**沒有維修內容** | 已開啟 |
| B2 | HSE INDG316（同 L12，涵蓋折彎機） | 同上 | — | — | ✅ | 折彎機每日點檢 | 已開啟 |
| B3 | Durmapress《Press brake bending angle inaccuracy》、《Backgauge positioning problem troubleshooting》 | https://www.durmapress.com/press-brake-bending-angle-inaccuracy-causes-fixes-accuracy-tips 、 https://www.durmapress.com/backgauge-positioning-problem-troubleshooting-guide/ | HTML／英文 | ©（廠商行銷文章） | ⚠️ | 角度不準：材料變異、模具、噸位計算、撓度補償（crowning）；後擋料：重複偏移、軸向不穩 | 搜尋所見 |
| B4 | ISO 6909:2026 折彎機安全 | https://www.iso.org/standard/83403.html | 付費標準 | © | ❌ | 只引用名稱 | 搜尋所見 |

### 1.4 銲接線（機器人 MIG、點銲）

| # | 文件 | 網址 | 格式／語言 | 授權 | 可否入庫 | 品質與用途 | 查證 |
|---|---|---|---|---|---|---|---|
| W1 | OSHA Fact Sheet FS-3647《Controlling Hazardous Fume and Gases during Welding》（2013） | https://www.osha.gov/sites/default/files/publications/OSHA_FS-3647_Welding.pdf | PDF 約 400 KB／英文 | PD | ✅ | 燻煙與排氣；對應設施規則第 292 條 | 已開啟（已抽出文字） |
| W2 | OSHA Welding, Cutting, and Brazing 主題頁 | https://www.osha.gov/welding-cutting-brazing | HTML／英文 | PD | ✅ | 入口；含 Fatal Facts 4278（局限空間火災） | 已開啟 |
| W3 | NIOSH 88-110《Criteria for a Recommended Standard: Welding, Brazing, and Thermal Cutting》 | https://www.cdc.gov/niosh/docs/88-110/default.html | HTML＋PDF／英文 | PD 🔬 | ✅ | 職業衛生背景；年代較舊 | 搜尋所見（開啟時內容空白） |
| W4 | OSHA Technical Manual Section IV Chapter 4《Industrial Robot Systems and Industrial Robot System Safety》（2021-09 更新） | https://osha.gov/otm/section-4-safety-hazards/chapter-4 | HTML／英文 | PD | ✅ | 機器人危害、教導與維修時的安全措施，對應工業用機器人危害預防標準 | 搜尋所見 |
| W5 | 美國海軍 NAVEDTRA 14250《Steelworker Volume 1》（1996-11），含 Gas Shielded-Arc Welding 章 | https://everyspec.com/USN/NAVEDTRA/NAVEDTRA_14250_VOLUME-1_NOV1996_18385/ | PDF／英文 | 美國政府訓練教材，EverySpec 標示公開發行 🔬 | ✅（確認發行聲明後） | **少數公有領域、又有 GMAW 缺陷與成因說明的教材**；內容較舊，但原理不會過時 | 已開啟（目錄頁） |
| W6 | Miller《Tips for Troubleshooting Common MIG Weld Defects》 | https://www.millerwelds.com/resources/article-library/troubleshooting-weld-defects | HTML／英文 | © Miller Electric，受網站 Terms of Use 約束 | ⚠️ | 氣孔、未熔合、燒穿、飛濺、焊道形狀，原因與對策清楚 | 已開啟 |
| W7 | Lincoln Electric《GMAW Welding Guide》C4.200（2015，96 頁） | 官方網址沒有找到；只看到轉載站，不列出 | PDF／英文 | © | ⚠️ 建議只引用書名 | 內容權威，但沒有找到官方下載點 🔬 | 搜尋所見 |
| W8 | microJoining Solutions《Resistance Welding – Trouble Shooting Guide》（D. Steinmeier） | https://microjoining.com/wp-content/uploads/2025/12/microTips-%E2%80%93-Resistance-Welding-%E2%80%93-Troubleshooting.pdf | PDF 27 KB／英文 | © | ⚠️ | 點銲問題的思考框架：電流路徑、電流密度、熱平衡；偏小件電阻銲，對車體式點銲只部分適用 | 已開啟（已抽出文字） |
| W9 | AMADA WELD TECH《Welding problems and solutions start with these 4 things》 | https://amadaweldtech.com/blog/welding-problems-and-solutions-start-with-these-4-things/ | HTML／英文 | © | ⚠️ | 點銲飛濺、電極問題 | 搜尋所見 |
| W10 | FANUC 機器人警報（SRVO 系列）第三方整理 | https://www.robots.com/articles/understanding-error-codes-in-fanuc-robots | HTML／英文 | © 第三方 | ⚠️ 只參考警報類別 | FANUC 原廠警報手冊不公開 🔬；可以只用「伺服、編碼器、過載、過熱、超行程」這些類別，**不要在 Demo 使用真實警報碼**，避免被認為用了原廠資料 | 搜尋所見 |
| W11 | Yaskawa Motoman YRC1000 文件與知識庫 | https://knowledge.motoman.com/hc/en-us/articles/4405159162263 | HTML／英文 | © | ❌（原廠手冊需透過客戶入口） | 只確認「輕微警報 4xxx～9xxx 可用 RESET 清除」這種通用知識 🔬 | 搜尋所見 |

### 1.5 表面處理線（粉體塗裝、噴漆）

| # | 文件 | 網址 | 格式／語言 | 授權 | 可否入庫 | 品質與用途 | 查證 |
|---|---|---|---|---|---|---|---|
| P1 | Thermaset《General troubleshooting – powder coatings》Part 1/2 | https://thermaset.co.uk/wp-content/uploads/2020/08/general_troubleshooting_thermaset_powder_coatings.pdf | PDF 151 KB／英文 | © 🔬（文件內沒有看到版權字樣，但屬廠商著作） | ⚠️ | **問題→原因→對策表最工整**：附著不良、深凹處上粉不足、膜厚太低、塗布前外觀不均（背電離）、法拉第籠效應、粉末受潮。直接對應「電壓、接地、吊具、輸粉量」等 log 訊號 | 已開啟（已抽出文字） |
| P2 | ChemQuest《Beneath the Surface: Understanding and Preventing Powder Coating Defects》2025 研討會簡報 | https://chemquest.com/wp-content/uploads/2025/02/Understanding-and-Preventing-Powder-Coating-Defects-2025-webinar.pdf | PDF 3.9 MB／英文 | © | ⚠️ | 雜質顆粒與纖維、火山口與魚眼、針孔、膜厚不均與橘皮、現場失效（附著、腐蝕、褪色、失光） | 已開啟（已抽出文字） |
| P3 | Products Finishing《Do You Have Good Ground?》 | https://www.pfonline.com/articles/do-you-have-good-ground | HTML／英文 | © | ⚠️ | 接地是多數塗裝缺陷的共同原因 | 搜尋所見 |
| P4 | Gema《Ask Joe Powder》2022-06 | https://www.gemapowdercoating.com/fileadmin/documents/Sonstige_PDF-Dateien/Ask_Joe_Powder/Ask-Joe-Powder-Jun-2022.pdf | PDF／英文 | © | ⚠️ | 回收粉與新粉比例 70/30 或 60/40 | 搜尋所見 |

> 表面處理線本次只列不擬故障模式。原因：題目要求的優先順序是沖壓、銲接、組裝；有機溶劑與粉塵爆炸屬另一套法規（有機溶劑中毒預防規則等，見 06 號文件 2.6）。

### 1.6 組裝線（電動起子、扭力工具、線材走線、PPE）

| # | 文件 | 網址 | 格式／語言 | 授權 | 可否入庫 | 品質與用途 | 查證 |
|---|---|---|---|---|---|---|---|
| A1 | NASA RP-1228《Fastener Design Manual》（1990-03） | https://ntrs.nasa.gov/citations/19900009424 | PDF／英文 | NTRS 標示「Work of the US Gov. Public Use Permitted.」 | ✅ | 鎖固方式、扭力公式、螺紋、墊圈；**扭力類 SOP 的公有領域理論依據** | 已開啟 |
| A2 | NASA-STD-8739.4A Change 4《Crimping, Interconnecting Cables, Harnesses, and Wiring》（2016-06，變更 2022-04） | https://standards.nasa.gov/standard/nasa/nasa-std-87394 | PDF 3.2 MB／英文 | 標示「cleared for public accessibility on the internet」；NASA 著作 | ✅ | **線材走線、彎曲半徑、束線、壓接的公開工藝標準**，可取代付費的 IPC/WHMA-A-620 | 已開啟 |
| A3 | NASA Workmanship 線上教材（Cable and Harness 一般要求） | https://workmanship.nasa.gov/lib/insp/2%20books/links/sections/401%20General%20Requirements.html | HTML／英文 | NASA 🔬 | ✅ | 有圖例的良品與不良品說明 | 搜尋所見 |
| A4 | Atlas Copco《Pocket Guide to Tightening Technique》 | https://www.atlascopco.com/zh-tw/itba/expert-hub/pocket-guide/tightening-technique （PDF：https://www.atlascopco.com/content/dam/atlas-copco/industrial-technique/general/documents/pocketguides/9833864801_L.pdf） | PDF 約 5 MB／英文（頁面有繁中） | © 2026 Atlas Copco AB | ⚠️ | 接合類型（硬接合、軟接合）、工具選擇對品質的影響；**扭力未達、浮鎖類故障模式的主要觀念來源** | 已開啟（頁面） |
| A5 | Atlas Copco S8050 電動起子故障排除（線上手冊） | https://picontent.atlascopco.com/cont/external/dir/41/15384642827_A2390001_html5_external/en-US/14503108875.html | HTML／英文 | © | ⚠️ | 症狀、原因、處置三欄：不轉、正反轉失效、斷續運轉、起子頭固定不良、起子頭晃動、**不在設定扭力停止（扭力設太高導致滑牙、起子頭與螺絲不匹配）**、過熱 | 已開啟 |
| A6 | Wikipedia「Cam out」 | https://en.wikipedia.org/wiki/Cam_out | HTML／英文 | CC BY-SA 4.0 🔬（衍生內容需同樣授權） | ⚠️ | 名詞解釋；CC BY-SA 的相同方式分享要求和我們的模擬資料混在一起會比較麻煩，建議只連結 | 搜尋所見 |
| A7 | OSHA Ergonomics（設施規則第 324 條之 1 人因危害的參考） | （本次沒有找到合適的組裝線專頁 🔬） | — | PD | — | 待補 | — |

### 1.7 真實事故敘述（安全類案例的背景，不是維修案例）

| # | 來源 | 網址 | 格式 | 授權 | 用途 | 查證 |
|---|---|---|---|---|---|---|
| R1 | 職安署「重大職業災害公開網」資料集（107 年至今，每日更新） | https://data.gov.tw/dataset/126835 ；API：https://pacs.osha.gov.tw/api/v1/getdangerocupation | CSV／JSON／API | TW-OGDL | 統計「機械夾捲」類災害比例，當作安全類模擬案例的分布依據。**欄位含事業單位名稱與地址，入庫前要去識別化，只留行業、災害類型、媒介物** | 已開啟 |
| R2 | 職安署「年度職災個案資料表」（102～106 年，已停止更新） | https://data.gov.tw/dataset/6644 | CSV／JSON／XML | TW-OGDL | 媒介物×災害類型的交叉統計 | 已開啟 |
| R3 | OSHA Accident Investigation Search（事故摘要，例如沖床連續行程夾傷手指） | https://www.osha.gov/ords/imis/accidentsearch.accident_detail?id=908103 （範例之一） | HTML／英文 | PD | 少量改寫成「安全事件」案例，例如雙踏、誤觸踏板、離合器失效 | 搜尋所見 |
| R4 | OSHA Severe Injury Reports（2015 起，含事件描述欄位） | https://osha.gov/severeinjury | CSV 下載／英文 | PD | 同上，可用關鍵字篩選 press、die、weld | 搜尋所見 |
| R5 | 紐約州衛生署 FACE 報告 16NY064（200 噸機械沖床安全塊被彈出致死，LOTO 不足） | https://health.ny.gov/environmental/investigations/face/docs/16ny064.pdf | PDF／英文 | 州政府文件 🔬 | **換模、安全塊與 LOTO 的真實教訓**，可以支撐設施規則第 69 條相關 SOP 的「為什麼」 | 搜尋所見 |

---

## 2. 公開資料集盤點（維修知識與案例庫）

| # | 資料集 | 網址 | 內容 | 授權 | 適合度與用法 | 查證 |
|---|---|---|---|---|---|---|
| D1 | **MaintNorm**（UWA NLP-TLP，WNUT 2024） | https://github.com/nlp-tlp/maintnorm ；https://huggingface.co/nlp-tlp/MaintNorm/tree/main | 12,000 筆重型移動設備維修短文（三家公司各 4,000 筆），含原文與正規化版本 | MIT | ★★★ **最適合學工單寫法**：縮寫、錯字、沒有文法的短句。用法：抽 200 筆當 few-shot 風格範例，讓 LLM 產生中文模擬工單時有同樣的「髒度」。注意：領域是礦業重機，不是沖壓 | 已開啟 |
| D2 | **MaintIE**（LREC-COLING 2024） | https://github.com/nlp-tlp/maintie | 1,076 筆專家細標註＋7,000 筆粗標註工單；5 大類（PhysicalObject、State、Process、Activity、Property）、224 個子類、6 種關係；敏感資訊已遮罩 | MIT | ★★★ **案例 schema 的分類參考**：我們的 `component`、`symptom`、`action` 欄位可以對照它的類別。也能當實體抽取的評估資料 | 已開啟 |
| D3 | **FMC-MWO2KG**（Stewart 等，MWO2KG and Echidna） | https://astro.paperswithcode.com/dataset/fmc-mwo2kg ；相關論文 https://arxiv.org/pdf/2309.08181 | 502／62／62 筆（觀察描述→ISO 14224 的 22 個故障模式代碼） | 程式碼 Apache 2.0；資料授權 🔬 | ★★★ **和 J3 同一種任務**（文字→有限故障模式）。可用來驗證「Jev 照字面讀 criteria」在英文公開資料上的準確度，當報告的外部基準 🔬（要先確認資料授權） | 搜尋所見 |
| D4 | Excavator MWO（Hodkiewicz & Ho 2016；NIST Nestor 內建 `load_excavators()`） | https://pages.nist.gov/nestor/nestor/datasets ；UWA Prognostics Data Library：prognosticsdl.systemhealthlab.com（awesome-tlp 列出） | 5 台挖土機的工單：日期、設備、原始短文、PM 類型、成本（澳幣） | 🔬（Nestor 文件沒有寫資料授權） | ★★ 風格參考；文字清理流程（Nestor tagging）可借用 | 已開啟（Nestor 頁） |
| D5 | NIST Nestor 工具 | https://www.nist.gov/el/enhancing-maintenance-strategies-manufacturing-operations/software-tools | 工單標註工具（Problem／Solution／Item） | NIST 軟體 🔬 | ★★ 結案回寫（J5）時，「問題、處置、零件」三欄式的設計可參考 | 已開啟 |
| D6 | awesome-tlp 清單（NIST TLP 社群） | https://github.com/TLP-COI/awesome-tlp | 彙整維修工單資料集：紐約市公園資產工單 167 萬筆、奧斯汀號誌工單 2.3 萬筆等 | 各自不同 | ★ 市政工單和製造無關，**只用來證明我們調查過，並在報告中引用 TLP 的方法論** | 已開啟 |
| D7 | **AI4I 2020 Predictive Maintenance**（UCI） | https://archive.ics.uci.edu/dataset/601/ai4i+2020+predictive+maintenance+dataset | 10,000 筆合成資料；5 種故障：刀具磨耗、散熱、功率、過應變、隨機 | CC BY 4.0 | ★★ **它本身就是合成資料**：可以引用它的作法（用規則加門檻產生故障標籤），說明我們的模擬方式有前例。故障規則（例如磨耗×扭力超過門檻）可以借來設計 log 訊號 | 已開啟 |
| D8 | Microsoft Azure Predictive Maintenance（Kaggle 版） | https://www.kaggle.com/arnabbiswas1/microsoft-azure-predictive-maintenance ；原始 repo：https://github.com/Azure-Samples/MachineLearningSamples-PredictiveMaintenance | 100 台機器、2015 全年：telemetry（電壓、轉速、壓力、振動）、errors、maint、failures、machines | 原 repo MIT（2019-06 已封存）；Kaggle 版授權 🔬 | ★★ **五表結構正好是我們案例庫的骨架**：errors（不停機的警告）對應我們的「前兆 log」，maint 和 failures 對應維修紀錄 | 已開啟（GitHub） |
| D9 | NASA PCoE Prognostics Data Repository（C-MAPSS、Milling 等） | https://www.nasa.gov/content/prognostics-center-of-excellence-data-set-repository ；鏡像 https://data.phmsociety.org/nasa/ | 渦輪引擎退化模擬、銑床刀具磨耗等 | PD 🔬 | ★ 和沖壓關聯低；Milling 刀具磨耗可以類比沖頭磨耗。**不建議下載** | 搜尋所見 |
| D10 | FabNER（製造領域 NER） | https://huggingface.co/datasets/DFKI-SLT/fabner | 35 萬字，取自 Web of Science 期刊摘要；12 種實體（材料、製程、設備、參數…） | 「other」，沒有寫明；文字來自期刊摘要 | ❌ **不建議**：授權不明，且內容是論文摘要，不是維修知識 | 已開啟 |
| D11 | 沖壓加速度訊號資料（1,408 個沖程、40 個異常） | https://arxiv.org/pdf/2504.21389 | 論文中的資料集 | 🔬 | ★ 若要做沖床振動異常偵測可再追；與 RAG 無直接關係 | 搜尋所見 |
| D12 | CWRU 軸承資料、IEEE DataPort bearing datasets | https://ieee-dataport.org/documents/bearing-datasets | 軸承故障振動 | 各自不同 🔬 | ★ 飛輪或曲軸軸承類故障模式的訊號設計參考 | 搜尋所見 |

**結論**：沒有任何公開資料集是「沖壓、銲接、組裝的中文維修案例」。公開資料集在本專案的角色有三個：
1. **風格**：MaintNorm、MaintIE
2. **結構**：Azure PdM 的五表、AI4I 的規則式合成
3. **評估方法**：FMC-MWO2KG

內容本身全部要自製。

---

## 3. 建議入庫清單與自製範圍

### 3.1 第一批入庫（公有領域或開放授權，可整段切塊）

| 優先 | 來源 | 預估 chunk 數 | 標 `doc_type` | 說明 |
|---|---|---|---|---|
| 1 | 設施規則第 57、58、69、70、254、284、292、300 條；管理辦法第 26、59 條；機械設備器具安全標準第 4～25 條 | 約 30 | `regulation` | 一條一個 chunk，`source=law.moj.gov.tw`，`license=TW-law` |
| 1 | OSHA 1910.147 (d)(e)＋附錄 A | 約 15 | `regulation` | LOTO 步驟 |
| 1 | HSE INDG316 | 約 20 | `guideline` | 依防護類型切塊（固定護圍、連鎖護圍、光幕…） |
| 2 | 職安署《衝剪機械危害預防》 | 約 5 | `guideline` | 中文安全提示 |
| 2 | OSHA eTool 沖床與折彎機頁、FS-3647 銲接燻煙、OTM 機器人章 | 約 40 | `guideline` | 安全背景 |
| 2 | NASA-STD-8739.4 線材章節（只取一般要求、彎曲半徑、束線） | 約 30 | `standard` | 組裝線走線 |
| 3 | NASA RP-1228 扭力章節 | 約 15 | `reference` | 組裝線扭力 |
| 3 | NAVEDTRA 14250 GMAW 章 | 約 20 | `reference` | 確認發行聲明後才放 🔬 |
| 3 | HSE HSG236 維修日誌範例 | 約 10 | `guideline` | 下載後只取附錄 🔬 |

合計約 185 個公開 chunk。英文來源建議**同時存英文原文 chunk 和中文摘要 chunk**，兩者用 `parent_id` 連結。檢索用多語 embedding（例如 bge-m3 🔬 要實測中英混合效果）。顯示給技術員時用中文摘要，並附原文連結。

### 3.2 只做「改寫摘要＋出處」，不放原文

Durant、The Fabricator、MetalForming、Miller、Lincoln、microJoining、Thermaset、ChemQuest、Atlas Copco、Durmapress。這些是**撰寫 SOP 時的參考**，在 SOP 的 `references` 欄位列出，不以 `doc_type=vendor_manual` 單獨入庫。報告裡寫明：「廠商資料僅作為撰寫模擬 SOP 的參考，未重製原文。」

### 3.3 必須自製

| 類別 | 數量 | 說明 |
|---|---|---|
| 故障模式目錄 | 沖壓 20、銲接 10、組裝 10（表面處理之後再補） | 第 5 節草稿 |
| SOP | 每個故障模式 1 份，共 40 份，約 280 個 chunk | 格式見 4.1 |
| LOTO 檢查表 | 機械沖床、伺服沖床、伺服送料機（含整料機）、油壓折彎機、銲接機器人工作站、點銲機、塗裝線（輸送鏈＋烤爐）、組裝站電動起子，共 8 份 | 格式見 4.4 |
| 維修案例 | 約 320 筆 | 第 6 節 |
| 每日點檢表 | 沖床、折彎機各 1 份 | 依管理辦法第 59 條＋INDG316 改寫 |

---

## 4. 資料格式（Schema）

### 4.1 SOP 文件（`knowledge/sop/{line}/SOP-STP-012.yaml`）

```yaml
sop_id: SOP-STP-012
version: "0.3"
title: 送料滾輪磨損／打滑之檢查與更換
line: stamping
machine_type: [servo_roll_feeder]          # 可多個
failure_mode_ids: [feed_roller_wear, feed_roll_oil_contamination]
data_label: 模擬資料                          # 固定值，UI 一律顯示
synthetic: true
author: team_sentinel                       # 不寫個人姓名
reviewed_by: [reviewer_A]                   # 代號
review_status: approved                     # draft / reviewed / approved
safety:
  loto_required: true
  loto_checklist_id: LOTO-FEED-01
  energy_sources: [electrical_servo, pneumatic_pilot_release, gravity_coil]
  ppe: [安全鞋, 防割手套, 護目鏡]
  danger_zone_entry: true                   # 會觸發 J4 安全把關
  legal_basis: [設施規則第57條, OSHA 1910.147(d)]
symptoms:                                   # 給檢索與 Jev criteria 用
  - 送料長度逐步偏短，數分鐘內累積誤差
  - 導正孔變橢圓
prerequisites:
  tools: [塞規, 游標卡尺, 扭力扳手]
  parts: [送料滾輪（上、下）]
  skill_level: 維修技術員 L2
steps:
  - no: 1
    phase: prepare                          # prepare / isolate / inspect / repair / verify / restore
    action: 通知操作員與班長停線，於 Kanban 開立維修單
    caution: null
    requires_supervisor: false
  - no: 2
    phase: isolate
    action: 依 LOTO-FEED-01 執行停機、斷電、洩除氣壓、上鎖掛牌
    caution: 送料機與沖床須同時上鎖；氣壓表歸零後才可接觸滾輪
    requires_supervisor: true
  - no: 3
    phase: inspect
    action: 目視滾輪表面是否有亮帶、油污或刮痕；以卡尺量測直徑，與基準值比較
    criteria: 直徑磨耗超過 0.1 mm 🔬（模擬基準）或表面有油污即判定異常
  # ...
verify:
  - 無料空轉一圈，量測送料長度誤差 ≤ ±0.05 mm（模擬值）
  - 試沖 20 模，導正孔正圓
restore:
  - 依 LOTO-FEED-01 解除程序，由原上鎖人取下鎖具（設施規則第 254 條精神）
escalate_if:
  - 更換滾輪後誤差仍超標 → 疑似 pilot_release_timing 或伺服驅動器，改走 SOP-STP-014
references:
  - {title: "Progressive die misfeed troubleshooting", url: "https://www.durantco.com/blog/progressive-die-misfeed-troubleshooting-feed-accuracy/", license: "©", use: paraphrase}
  - {title: "OSHA 1910.147 App A", url: "https://www.osha.gov/laws-regs/regulations/standardnumber/1910/1910.147AppA", license: "PD", use: quote}
```

### 4.2 維修案例（`knowledge/cases/{line}/CASE-STP-0001.json`）

```json
{
  "case_id": "CASE-STP-0001",
  "data_label": "模擬資料",
  "synthetic": true,
  "generator": {"model": "deepseek-v4.1-flash", "prompt_ver": "case-gen-v2", "seed": 1037},
  "line": "stamping",
  "machine_type": "servo_roll_feeder",
  "machine_id": "STP-L1-FD02",
  "opened_at": "2026-03-14T10:31:02+08:00",
  "closed_at": "2026-03-14T11:18:40+08:00",
  "shift": "day",
  "trigger": {"source": "fused", "event_type": "feed_jam_suspected", "rule_severity": "S2"},
  "context": {
    "days_since_pm": 41,
    "recent_changes": ["換料捲 09:50"],
    "material": {"grade": "SECC", "thickness_mm": 1.0, "coil_id": "C-2603-118"}
  },
  "log_signals": [
    {"t": "10:22:00", "signal": "feed_length_error_mm", "value": 0.08},
    {"t": "10:29:00", "signal": "feed_length_error_mm", "value": 0.21},
    {"t": "10:31:02", "signal": "misfeed_sensor", "value": "TRIP"}
  ],
  "technician_report_raw": "FD02 送料偏短 導正孔橢圓 下滾輪亮帶+油 清潔後仍偏 換新滾輪OK",
  "first_hypothesis": {"failure_mode_id": "material_thickness_dev", "by": "jev", "p": 0.46},
  "final_root_cause": "feed_roller_wear",
  "hypothesis_correct": false,
  "why_first_guess_wrong": "剛換料捲，Jev 傾向判材料厚度；但誤差在 9 分鐘內逐步累積，而且量測料厚在公差內",
  "discriminating_evidence": ["誤差逐步上升非階躍", "料厚實測 1.00 mm", "滾輪表面亮帶"],
  "actions": [
    {"step": "LOTO-FEED-01 上鎖", "result": "ok"},
    {"step": "清潔滾輪油污", "result": "未改善"},
    {"step": "更換上下滾輪", "result": "誤差恢復 0.02 mm"}
  ],
  "sop_used": ["SOP-STP-007", "SOP-STP-012"],
  "downtime_min": 48,
  "parts_replaced": ["feed_roll_upper", "feed_roll_lower"],
  "safety_flags": {"loto_done": true, "danger_zone_entry": true, "near_miss": false},
  "outcome": "resolved",
  "lessons": "換料後卡料不一定是料的問題，先看誤差曲線形狀",
  "references": ["SOP-STP-012"],
  "review_status": "approved"
}
```

說明：
- `technician_report_raw` 刻意寫得像現場短句，參考 MaintNorm 的風格。另外產生一份 `technician_report_normalized` 給檢索用。
- `first_hypothesis` 和 `hypothesis_correct` 是**維修代理學「排除法」的關鍵**。檢索到這類案例時，LLM 可以說「上次也先懷疑料厚，後來是滾輪」。

### 4.3 故障模式（擴充 `Jev整合規劃.md` 3.9 的 YAML）

```yaml
stamping:
  feed_roller_wear:
    label: 送料滾輪磨損／打滑
    criteria: 送料長度誤差在數分鐘內逐步累積（非瞬間歸零）；導正孔變形；常見於距上次保養超過 30 天   # ← 這一行直接給 Jev
    symptoms: [送料偏短, 導正孔橢圓, 料帶表面滑痕]
    log_signals:
      - {signal: feed_length_error_mm, pattern: ramp_up, window_min: 10}
      - {signal: days_since_pm, op: ">", value: 30}
    distinguish_from: [material_thickness_dev, pilot_release_timing]
    category: equipment
    severity_default: S2
    loto_required: true
    sop: SOP-STP-012
    sources: [S4]                 # 對應本文件 1.x 的來源編號
    data_label: 模擬資料
```

規則：
- `criteria` 只寫「判斷依據」，不寫名稱，而且要能和 `distinguish_from` 裡的模式區分開來。Jev 會照字面讀。
- 每條線最多 255 個，實際建議每線 15～30 個，太多會讓每個選項的機率被稀釋。

### 4.4 LOTO 檢查表（`knowledge/loto/LOTO-PRESS-MECH-01.yaml`）

```yaml
loto_id: LOTO-PRESS-MECH-01
machine_type: mechanical_power_press
data_label: 模擬資料
legal_basis:
  - 設施規則第57條（116-01-01 施行版）：停止運轉及送料、上鎖及設置標示、危害防止計畫、作業許可、殘壓處理
  - 設施規則第69條：換模、調模使用安全塊、安全插梢或安全開關鎖匙
  - 設施規則第70條：調模用寸動，無寸動須切斷動力電源
  - 設施規則第254條：由原掛簽人取下鎖或掛牌後才能復電
  - OSHA 1910.147 App A（作為程序範本）
permit:
  required: true                        # 第57條「作業許可程序」
  approver_role: 現場主管
  hazard_plan_ref: HPP-STP-01           # 危害防止計畫編號（模擬）
energy_sources:
  - {id: E1, type: electrical, device: 主電源隔離開關 QS1, method: 切斷＋個人鎖}
  - {id: E2, type: pneumatic, device: 離合器／平衡缸氣源閥 V1, method: 關閉＋洩壓＋鎖}
  - {id: E3, type: kinetic_flywheel, device: 飛輪, method: 等待完全停止（目視＋轉速計 0 rpm）}
  - {id: E4, type: gravity_slide, device: 滑塊, method: 下死點或插入安全塊}
  - {id: E5, type: feed_line, device: 送料機電源 QS2, method: 連動上鎖（第57條「停止送料」）}
steps:
  - {no: 1, action: 通知操作員與受影響人員, check: 簽名}
  - {no: 2, action: 確認危害防止計畫與作業許可已核准, check: 許可單號}
  - {no: 3, action: 以正常程序停機（停止按鈕），停止送料, check: 目視}
  - {no: 4, action: 依 E1～E5 逐一隔離, check: 每項勾選}
  - {no: 5, action: 每位作業人員在每個隔離點掛個人鎖與標籤, check: 鎖號登記}
  - {no: 6, action: 洩除殘壓（氣壓表歸零）、確認飛輪停止、放入安全塊, check: 壓力讀值、安全塊位置}
  - {no: 7, action: 驗證零能量：按起動鈕、寸動鈕確認無動作後歸回 OFF, check: 兩人確認}
release:
  - 清點工具、恢復護圍與光幕
  - 確認人員離開危險區
  - 各人取下自己的鎖（不得代取）
  - 通知受影響人員，試運轉
ai_constraints:                        # 給 LLM 維修代理的硬限制
  - AI 不得宣告「已完成上鎖」或「可以復電」，只能提示下一步並要求人員在 Kanban 勾選
  - 任何步驟涉及 E1～E5 → J4 needs_loto 必須為 true
```

### 4.5 切塊與 metadata 策略

| 文件類型 | 切塊單位 | 大小 | 必填 metadata |
|---|---|---|---|
| `regulation` | 一條一塊（長條文依款拆） | 100～400 字 | `source`、`license`、`article`、`effective_date` |
| `guideline`／`standard`／`reference` | 依小標題；表格一列一塊 | 300～600 字（中文）／200～400 words | `source`、`license`、`lang`、`parent_id`（中英對照） |
| `sop` | **一個 phase 一塊**（prepare／isolate／inspect／repair／verify），另有一塊 `summary` | 200～500 字 | `sop_id`、`failure_mode_ids`、`phase`、`loto_required` |
| `case` | 一案兩塊：`case_summary`（症狀＋log＋結論，用來檢索）＋`case_detail`（處置過程，用來產生回答） | 200～400 字 | `case_id`、`failure_mode_id`、`hypothesis_correct`、`machine_type` |
| `loto` | **不切塊、不進向量庫**，用 `machine_type` 直接查 | — | — |
| `failure_mode` | 一模式一塊（label＋criteria＋symptoms） | 100～200 字 | `failure_mode_id`、`line` |

所有 chunk 共用的 metadata：

```json
{"chunk_id": "...", "doc_id": "...", "doc_type": "sop|case|regulation|guideline|standard|reference|failure_mode",
 "line": "stamping|bending|welding|coating|assembly|all", "machine_type": ["..."],
 "failure_mode_ids": ["..."], "source": "url 或 internal", "license": "PD|OGL|TW-OGDL|TW-law|MIT|CC-BY-4.0|internal-synthetic",
 "data_label": "模擬資料|公開資料", "lang": "zh-TW|en", "version": "...", "review_status": "approved"}
```

**入庫守門**：`review_status != approved` 或 `license` 為空的 chunk 一律拒收。`license=©` 不能出現在向量庫，只能出現在 SOP 的 `references` 欄位。

### 4.6 Jev 與 SOP 檢索的串接

```
事件 ─▶ ① 案例檢索（向量相似度，filter: line, machine_type）→ top-5 case_summary
      ─▶ ② Jev J1–J3（state.similar_cases = ①）→ root_cause top-3 ＋ 機率
      ─▶ ③ SOP 檢索：filter failure_mode_ids ∈ top-3（不靠語意，直接用 ID）
            ├─ top-1 機率 ≥ 0.6：只取 top-1 的 SOP 全部 phase
            ├─ 0.3–0.6：取 top-3 各自的 summary＋inspect phase，讓 LLM 帶技術員「先檢查哪個能最快排除」
            └─ < 0.3 或 top-1 = new：走一般語意檢索（guideline＋case_detail），並標示「無對應 SOP，請工程師確認」
      ─▶ ④ LOTO：用 machine_type 直接取檢查表，固定附在回答最前面
      ─▶ ⑤ LLM 維修代理產生回答；每個步驟送 J4 安全把關
      ─▶ ⑥ 結案：J5 回寫 → 新 case（review_status=draft）→ 人工審核後入庫
```

重點：
- 第 ③ 步用 ID 過濾，是「可稽核」的。報告可以寫：同一個根因一定取到同一份 SOP，不會因為 embedding 漂移而取錯。
- 第 ① 步餵給 Jev 的案例要**同時包含正確與錯誤的第一判斷**，不然 Jev 只會學到「看起來像」，而不會學到「怎麼區分」。

---

## 5. 故障模式目錄草稿（全部為模擬資料）

> `log_signals` 欄位使用的訊號名稱，要和模擬器（MES log 產生器）對齊。SOP 編號是預留的，尚未撰寫。安全類模式（標 ⚠）的 SOP 一律是「停機、通知、等待主管」，AI 不提供排除方法。

### 5.1 沖壓（stamping）20 個

| # | failure_mode_id | label | criteria（給 Jev） | 症狀 | log 訊號 | 區分對象 | SOP |
|---|---|---|---|---|---|---|---|
| 1 | feed_roller_wear | 送料滾輪磨損／打滑 | 送料長度誤差在數分鐘內逐步累積，非瞬間歸零；距上次保養常超過 30 天 | 送料偏短、導正孔橢圓 | `feed_length_error_mm` 斜坡上升；`days_since_pm`>30 | 2、4 | SOP-STP-012 |
| 2 | material_thickness_dev | 材料厚度／硬度偏差 | 換料捲後短時間內發生；同一捲重複發生；換回前一捲即正常 | 卡料、沖壓力變化 | `coil_change` 事件後 <30 分鐘；`tonnage_peak` 偏移 | 1、5 | SOP-STP-007 |
| 3 | die_misalignment | 模具定位偏移 | 換模後發生；卡料伴隨異音或左右沖壓力不平衡 | 單側毛邊、異音 | `die_change` 事件；`tonnage_left/right` 差 >10% | 7、19 | SOP-STP-021 |
| 4 | pilot_release_timing | 導正銷釋放時機錯誤 | 送料長度本身準確，但導正孔被拉長；調整釋放角度後改善 | 導正孔變形、導正銷斷裂 | `pilot_release_angle` 設定變更；`feed_length_error_mm` 正常 | 1 | SOP-STP-014 |
| 5 | coil_camber | 料捲側彎／料帶偏移 | 料帶往同一側偏，越往模具後段誤差越大；與特定料捲有關 | 料帶擦側導板、後段工站卡料 | `strip_edge_sensor` 單側觸發 | 2 | SOP-STP-009 |
| 6 | slug_pulling | 廢料上跳（跳屑） | 沖孔廢料黏在沖頭被帶上，壓傷下一模或造成卡模；高速時較常見 | 製品壓痕、模面有廢料 | `spm` 提高後出現；`die_protect_sensor` 間歇觸發 | 17 | SOP-STP-031 |
| 7 | punch_edge_wear | 刃口／沖頭磨耗 | 毛邊高度隨沖次逐步增加；距上次研磨的沖次超過基準 | 毛邊、沖壓力緩升 | `stroke_count_since_regrind` 超過基準；`tonnage_peak` 緩升 | 3 | SOP-STP-033 |
| 8 | die_lubrication_insufficient | 模具潤滑不足（拉傷） | 成形件表面出現拉痕或拉傷，且潤滑器油位低或噴油次數下降 | 拉痕、黏模 | `lube_pulse_count` 下降；`lube_level` 低 | 7 | SOP-STP-035 |
| 9 | misfeed_sensor_fault | 誤送料偵測器誤報 | 偵測器觸發停機，但檢查料帶與模具皆正常；同一感測器反覆觸發 | 無實際卡料 | `misfeed_sensor` TRIP 但 `feed_length_error_mm` 正常；纜線接頭振動 | 1、6 | SOP-STP-041 |
| 10 | stripper_spring_fatigue | 脫料板彈簧疲勞 | 製品或料帶黏在上模不易脫料；與模具使用沖次相關 | 料帶被帶起、變形 | `strip_lift_sensor` 觸發；模具沖次高 | 6 | SOP-STP-036 |
| 11 | ⚠ clutch_brake_wear | 離合器／制動器磨耗（停止角度超限） | 停止時間或停止角度超過設定；超限監視器警報；屬安全裝置異常 | 滑塊停止位置偏移 | `stop_angle_deg`、`stop_time_ms` 超限 | 13 | SOP-STP-051（停機、通知、由合格人員依管理辦法第 26 條檢查） |
| 12 | overload_tonnage | 過負荷（噸位超限） | 單次沖壓力超過設定上限，過負荷保護動作；常伴隨雙料或異物 | 停機、油壓過負荷洩壓 | `tonnage_peak` > 上限；`double_blank_sensor` | 2、17 | SOP-STP-053 |
| 13 | air_pressure_low | 氣源壓力不足 | 離合器或平衡缸氣壓低於下限；廠務空壓同時段有壓降 | 離合器結合不良、行程變慢 | `air_pressure_bar` < 下限；多台同時 | 11、14 | SOP-STP-055 |
| 14 | counterbalance_misset | 平衡缸壓力設定不當 | 換模後未依上模重量調整平衡壓力；馬達電流上升 | 異音、停止角度變化 | `die_change` 事件；`counterbalance_bar` 與模具重量不符 | 11 | SOP-STP-056 |
| 15 | servo_feeder_drive_alarm | 伺服送料機驅動器警報 | 送料機驅動器報過電流或位置偏差過大，送料瞬間停止（非逐步） | 送料機停止、沖床連動停機 | `feeder_alarm` 類別碼；`feed_motor_current` 尖峰 | 1 | SOP-STP-061 |
| 16 | straightener_misset | 整平機設定不當 | 料帶有殘留捲曲（coil set），進模時翹起；換料厚後發生 | 料帶翹曲、進料口卡料 | `material_change` 事件；`loop_sensor` 異常 | 5 | SOP-STP-063 |
| 17 | scrap_chute_jam | 廢料排出堵塞 | 廢料滑道或輸送帶堵塞，廢料回堆到模具 | 模具內廢料堆積 | `scrap_conveyor_current` 異常或停止 | 6 | SOP-STP-065 |
| 18 | ⚠ light_curtain_fault | 光幕異常或被旁路 | 光幕自我診斷錯誤、頻繁誤遮斷，或視覺偵測到人員在光幕停用狀態作業 | 無法起動、或有人在危險區 | `light_curtain_status` FAULT／MUTED；視覺 `person_in_zone` | — | SOP-STP-071（停機、通知、由主管處理，AI 不提供旁路方法） |
| 19 | shut_height_misset | 閉模高度設定錯誤 | 換模後首件尺寸不良或沖壓力異常高或低；閉模高度讀值與模具資料不符 | 首件不良、過負荷 | `shut_height_mm` 與 `die_master.shut_height` 差 >0.2 mm | 3、12 | SOP-STP-023 |
| 20 | central_lube_fault | 機台集中潤滑異常 | 潤滑泵壓力不足或油位低，導軌或曲軸溫度上升 | 異音、溫升 | `lube_pressure` 低；`bearing_temp_c` 上升 | 8 | SOP-STP-057 |

### 5.2 銲接（welding）10 個

| # | failure_mode_id | label | criteria | log 訊號 | SOP |
|---|---|---|---|---|---|
| 1 | wire_feed_birdnest | 送絲不順／鳥巢 | 送絲輪處焊絲纏繞，電流不穩或斷弧；導管堵塞或送絲輪壓力過大 | `wire_feed_speed` 實際值低於設定；`arc_current` 波動 | SOP-WLD-011 |
| 2 | contact_tip_wear | 導電嘴磨耗／回燒 | 電弧起弧不良、飛濺增加，且導電嘴使用時間或起弧次數超過基準；回燒時焊絲熔黏在導電嘴 | `arc_start_fail` 增加；`tip_arc_on_min` 超過 | SOP-WLD-012 |
| 3 | shielding_gas_low | 保護氣體不足 | 焊道出現氣孔，同時氣體流量低於下限或氣瓶壓力低 | `gas_flow_lpm` < 下限 | SOP-WLD-013 |
| 4 | nozzle_spatter_buildup | 噴嘴積渣 | 氣孔逐步增加，且距上次清槍時間過長；清槍站未執行 | `torch_clean_interval_min` 超過 | SOP-WLD-014 |
| 5 | tcp_drift | 焊槍 TCP 偏移 | 焊道位置整批偏移同一方向；常發生在撞槍或更換焊槍後 | `collision_event`；`tcp_check_delta_mm` | SOP-WLD-021 |
| 6 | ground_poor | 接地不良 | 電弧不穩、電壓波動，治具接地點有銲渣或鬆脫 | `arc_voltage` 波動大，電流正常 | SOP-WLD-015 |
| 7 | fixture_fitup_gap | 治具定位不良／間隙過大 | 單一工位或單一治具燒穿或未熔合，其他工位正常；換批工件後發生 | 品質 NG 集中在某個 `fixture_id` | SOP-WLD-031 |
| 8 | spot_electrode_wear | 點銲電極頭磨耗 | 熔核變小、拉力不足或飛濺增加，且修磨計數超過設定 | `tip_dress_count`；`weld_current_kA` 正常 | SOP-WLD-041 |
| 9 | cooling_water_low | 冷卻水流量不足 | 焊槍或點銲電極過熱警報，冷卻水流量低 | `coolant_flow_lpm` 低；`torch_temp` 高 | SOP-WLD-042 |
| 10 | ⚠ fume_extraction_fault | 排煙設備異常 | 局部排氣風量低於設定或停止，作業區煙霧濃度上升（設施規則第 292 條） | `exhaust_flow` 低；視覺 `smoke` | SOP-WLD-051（停止銲接、通知） |

### 5.3 組裝（assembly）10 個

| # | failure_mode_id | label | criteria | log 訊號 | SOP |
|---|---|---|---|---|---|
| 1 | torque_not_reached | 扭力未達／浮鎖 | 鎖附結果扭力低於下限，或角度過大但扭力未到；螺絲未貼平 | `screw_result=NOK_low_torque`；`angle_deg` 過大 | SOP-ASM-011 |
| 2 | cross_thread_strip | 斜牙／滑牙 | 扭力曲線早期異常上升或到不了目標；同一孔位重複發生，可能是孔牙不良 | 扭力曲線早期尖峰；固定 `hole_id` | SOP-ASM-012 |
| 3 | bit_wear_camout | 起子頭磨耗（打滑） | 螺絲頭十字槽受損、出現打滑聲；起子頭使用次數超過基準 | `bit_cycle_count` 超過；NOK 率逐步上升 | SOP-ASM-013 |
| 4 | screw_feeder_jam | 送釘機卡釘 | 送釘逾時、吸附失敗；螺絲批次混入異物或規格不符 | `feeder_timeout` | SOP-ASM-021 |
| 5 | wrong_screw_type | 錯料／混料 | 螺絲長度或規格與 BOM 不符，鎖附深度或角度異常 | `angle_deg` 明顯偏離；換料批後發生 | SOP-ASM-022 |
| 6 | missing_screw | 漏鎖 | 工單要求的鎖附次數與實際不符；視覺檢出孔位無螺絲 | `screw_count` < 要求；視覺 `missing` | SOP-ASM-023 |
| 7 | tool_calibration_due | 扭力工具校正逾期／漂移 | 工具校正日期超過期限，或抽測扭力與設定偏差超過 ±10% 🔬（模擬門檻） | `calib_due_days`<0；抽測值 | SOP-ASM-031 |
| 8 | cable_pinch_misroute | 線材夾線／走線錯誤 | 機殼蓋合處線材被夾、彎曲半徑過小，或走線路徑與作業指導書不符（參考 NASA-STD-8739.4） | 視覺 `cable_out_of_path`；測試站絕緣不良 | SOP-ASM-041 |
| 9 | connector_not_seated | 連接器未插到位 | 測試站偵測不到裝置，目視連接器卡扣未扣上 | 測試 `device_not_found` | SOP-ASM-042 |
| 10 | ⚠ ppe_esd_missing | PPE／靜電防護未落實 | 視覺偵測未戴手套或靜電環；靜電手環測試站未通過（先確認是否「沒有提供」，設施規則第 277 條） | 視覺 `ppe_missing`；`esd_check=fail` | SOP-ASM-051（提醒，不懲罰） |

---

## 6. 模擬維修案例設計

### 6.1 數量配置（約 320 筆）

| 線 | 故障模式 | 每模式案例 | 小計 |
|---|---|---|---|
| 沖壓 | 20 | 10（高頻的 1、2、3、6、9 各 14） | 約 220 |
| 銲接 | 10 | 6 | 60 |
| 組裝 | 10 | 4 | 40 |
| **合計** | | | **約 320** |

每個故障模式的案例組成（以 10 筆為例）：

| 類型 | 比例 | 用途 |
|---|---|---|
| 典型（症狀清楚，一次判對） | 50% | 基本檢索 |
| 第一判斷錯誤（`hypothesis_correct=false`） | 20% | 教 LLM 和 Jev「怎麼區分」，例如誤判成 `distinguish_from` 列的模式 |
| 複合原因（兩個模式同時存在，例如磨耗加上換料） | 10% | 測試 top-3 的價值 |
| 誤報（`outcome=false_alarm`） | 10% | 對應 J2 是否誤報 |
| 未解決或升級（`outcome=escalated`），或最後判為 `new` | 10% | 測試 J5 的 `new` 分支 |

另外留 **60 筆不進知識庫的「保留測試集」**（每線按比例抽），專門給 `Jev整合規劃.md` 第 5 節的驗證使用，避免「用考題當教材」。

### 6.2 讓案例真實又多樣的做法

1. **參數化骨架，再讓 LLM 寫文字**：先用程式抽變數，再把這些變數交給 LLM 寫敘述。LLM 只負責「寫」，不負責「決定」根因。變數包括：
   - 故障模式
   - 機台
   - 班別（早、中、夜，夜班比例略高）
   - 距保養天數（依模式分布，例如磨耗類右偏）
   - 最近事件（換模、換料捲、換焊槍）
   - 技術員資歷（L1～L3，影響描述詳細度）
   - 季節（夏季溫度高影響潤滑或冷卻）
2. **log 訊號由模擬器產生**，和 Demo 的 MES log 產生器共用一套程式。這樣案例的訊號型態（斜坡、階躍、間歇）和 Demo 現場看到的一致。可以參考 AI4I 的「門檻規則產生故障」作法，報告中引用它作為前例。
3. **文字風格三層**：
   - `technician_report_raw`：短句、縮寫、偶爾錯字，模仿 MaintNorm 的髒度，但改成中文現場用語，例如「沖子」「模仁」「送料跑掉」
   - `technician_report_normalized`：整理後的版本
   - `lessons`：一句心得
4. **用詞多樣化**：每個症狀準備 3～5 種說法，例如「送料偏短／送不到位／pitch 跑掉／步距不準」，避免檢索只靠關鍵字命中。
5. **時間分布**：案例日期分散在 2025-10～2026-09，保養週期要合理，同一台機器的案例不能互相矛盾。
6. **去重檢查**：同一模式內兩兩計算 embedding 相似度，超過 0.92 🔬（門檻待調）就重新生成。

### 6.3 錯誤第一判斷案例的設計規則

- 錯誤的第一判斷**必須來自該模式的 `distinguish_from`**，不能亂猜。
- 必須寫 `discriminating_evidence`：哪一個觀察推翻了第一判斷。這正是 LLM 維修代理要學會講的話。
- 第一判斷的提出者 `by` 要分配給 `jev`、`technician`、`rule` 三種，避免只讓 AI 出錯，這樣報告比較中立。

---

## 7. 生成流程（LLM 合成＋人工審核）

### 7.1 流程

```
1. 準備依據：把第 3.1 節的公開 chunk 入庫，並為每個故障模式指定 2～4 個「依據 chunk」（sources 欄位）
2. 產生 SOP 草稿：DeepSeek V4.1 Flash，輸入＝故障模式 YAML＋依據 chunk＋SOP 模板＋LOTO 檢查表
   └─ 要求：每個步驟後面加 [ref:chunk_id]；找不到依據的步驟要標 [ref:none]，並在審核時特別檢查
3. 自動檢查（程式）：
   ├─ schema 驗證（pydantic）
   ├─ 涉及能源的步驟前面必須有 isolate phase
   ├─ 不得出現「旁路」「短接」「拆除光幕」「跳過連鎖」等字詞（黑名單）
   ├─ 每個 [ref:chunk_id] 必須存在，且 chunk 的 license 不是 ©
   └─ 數值門檻一律加註「模擬值」
4. J4 自我檢查：把每個步驟丟給 Jev 安全把關，needs_loto 機率和 SOP 標示不一致的步驟列出來
5. 人工審核（7.2 檢查表）→ review_status=approved
6. 產生案例：用程式抽參數骨架 → LLM 寫文字（few-shot：MaintNorm 風格 3 筆＋我們已核准的案例 2 筆）→ 自動檢查 → 抽樣審核（每模式至少審 2 筆，錯誤判斷類全審）
7. 入庫：切塊、加 metadata、建立索引；保留測試集另外存放，不入庫
```

模型選擇：生成 SOP 和案例屬於「寫」的工作，用 LLM（DeepSeek）；判斷一致性用 Jev（便宜又快）。**產生案例時不要讓 Jev 參與根因決定**，以免評估時 Jev 自己出題自己答。

### 7.2 人工審核檢查表（SOP）

- [ ] 故障模式的 criteria 和 SOP 的 inspect 步驟一致，照著做能證實或排除這個模式
- [ ] 涉及電、氣、油壓、飛輪、重力、送料的步驟之前，都有引用對應的 LOTO 檢查表
- [ ] 換模或調模步驟有安全塊（第 69 條）和寸動（第 70 條）
- [ ] 復電由原上鎖人執行（第 254 條），AI 沒有被寫成可以復電
- [ ] 安全裝置類模式（離合器、制動器、光幕、排煙）只寫「停機、通知、交由合格人員」，沒有排除方法
- [ ] 所有數值門檻都標了「模擬值」，沒有寫成像是原廠規格
- [ ] 沒有出現真實廠商的警報碼、零件號、型號規格（避免被質疑使用原廠機密）
- [ ] 每個步驟都有 `[ref]`；`[ref:none]` 的步驟經過組員確認合理
- [ ] 引用廠商資料的部分是改寫，沒有整段複製（抽查：任何連續 30 字以上與原文相同的段落就退回）
- [ ] 用語為台灣慣用語（例如「銲接」「導電嘴」「沖頭」），標點為全形
- [ ] 文件頭有 `data_label: 模擬資料`

案例審核另外加：
- [ ] log 訊號型態符合故障模式的 `log_signals`
- [ ] 錯誤判斷的案例中，`discriminating_evidence` 確實能推翻第一判斷
- [ ] 沒有人名、真實公司名、真實地址；職災公開資料引用時已去除事業單位名稱

### 7.3 「模擬資料」標示（對應競賽規則）

1. **資料層**：每份 SOP、案例、故障模式、LOTO 檢查表都有 `synthetic: true`、`data_label: 模擬資料`；公開資料 chunk 則標 `data_label: 公開資料`，並附 `license`、`source`。
2. **介面層**：維修代理的回答引用模擬 SOP 或案例時，旁邊顯示灰色標籤「模擬資料」；引用公開法規時顯示來源與授權（例如「全國法規資料庫」「OSHA（美國政府公有領域）」「Contains public sector information published by the HSE, licensed under the OGL」）。
3. **文件層**：在 repo 放一份資料來源與授權清冊（`knowledge/SOURCES.yaml`），列出每個公開來源的網址、授權、取用日期、用途（整段入庫或只作參考）。報告的「資料來源」章節直接由這份清冊產生。
4. **聲明文字**（放在報告與 Demo 首頁）：「本系統展示所用之故障模式、標準作業程序、維修案例與上鎖掛牌檢查表，均為團隊依公開法規與公開技術文件自行撰寫之模擬資料，未使用任何企業內部資料；與贊助企業實際製程無關。」
5. **不要附原檔**：Demo repo 不放任何廠商 PDF；公開政府 PDF 也只放我們切好的文字 chunk，加上原始網址，不附整份檔案。這也符合「不要把大檔案下載進專案」的要求。

---

## 8. 待辦與待查證（🔬 彙整）

| 項目 | 狀態 |
|---|---|
| HSE HSG236、L112 的 PDF 是否明確適用 OGL（INDG316 文件內寫的是「freely reproduced, except for advertising, endorsement or commercial purposes」） | 🔬 下載後看版權頁 |
| NAVEDTRA 14250 的發行聲明（Distribution Statement A？） | 🔬 |
| FMC-MWO2KG 資料本身的授權 | 🔬 查 GitHub repo |
| Kaggle 版 Azure PdM 的授權標示 | 🔬 |
| Excavator MWO 資料授權 | 🔬 |
| 職安署網站（osha.gov.tw）是否沿用勞動部的開放授權宣告 | 🔬 本次只確認了 mol.gov.tw |
| 職業安全衛生管理辦法第 80 條（紀錄保存）全文 | 🔬 只看過摘要 |
| 工業用機器人危害預防標準中維修時的條號 | 🔬（06 號文件已標） |
| 多語 embedding 在中英混合 chunk 上的檢索效果 | 🔬 實測 |
| SOP 中所有「模擬值」門檻請懂現場的老師或業師看一次是否離譜 | 待辦 |
| 表面處理線故障模式（8～10 個） | 待辦，依 P1、P2 撰寫 |
| 伺服沖床原廠警報碼與維修手冊 | 沒有找到公開版本，維持自製 |

---

## 9. 參考來源

**台灣法規與政府資料**
- 職業安全衛生設施規則（全國法規資料庫）：https://law.moj.gov.tw/LawClass/LawAll.aspx?pcode=N0060009
- 職業安全衛生管理辦法：https://law.moj.gov.tw/LawClass/LawAll.aspx?pcode=N0060027（已開啟）
  - 第 26 條：https://law.moj.gov.tw/LawClass/LawSingle.aspx?pcode=N0060027&flno=26（已開啟）
  - 第 59 條：https://law.moj.gov.tw/LawClass/LawSingle.aspx?pcode=N0060027&flno=59（已開啟）
- 機械設備器具安全標準（轉載）：https://6laws.net/6law/law3/機械設備器具安全標準.htm
- 職安署南區職業安全衛生中心《衝剪機械危害預防》（勤益科大轉載）：https://esh.ncut.edu.tw/var/file/7/1007/img/720104393.pdf（已開啟）
- 勞動部政府網站資料開放宣告：https://www.mol.gov.tw/2578/2584/3276/（已開啟）
- 重大職業災害公開網資料集：https://data.gov.tw/dataset/126835（已開啟）；API：https://pacs.osha.gov.tw/api/v1/getdangerocupation
- 年度職災個案資料表：https://data.gov.tw/dataset/6644（已開啟）
- 勞動部，製造者或輸入者應確認機械產品符合安全標準（2015-11-27）：https://www.mol.gov.tw/1607/1632/1640/10698/（已開啟）

**OSHA／NIOSH／NASA／美國政府**
- OSHA 1910.147：https://www.osha.gov/laws-regs/regulations/standardnumber/1910/1910.147（已開啟）
- OSHA 1910.147 附錄 A：https://www.osha.gov/laws-regs/regulations/standardnumber/1910/1910.147AppA（已開啟）
- OSHA Control of Hazardous Energy：https://www.osha.gov/control-hazardous-energy（已開啟）
- OSHA LOTO Procedures Example（Harwood 補助教材）：https://www.osha.gov/sites/default/files/2018-12/fy15_sh-27664-sh5_Toolbox_LOTO_Procedures_Example.pdf（已開啟）
- OSHA 1910.217：https://www.osha.gov/laws-regs/regulations/standardnumber/1910/1910.217
- OSHA Machine Guarding eTool，Mechanical Power Presses：https://www.osha.gov/etools/machine-guarding/presses/mechanical-power（已開啟）
- OSHA Machine Guarding eTool，Powered Press Brakes：https://www.osha.gov/etools/machine-guarding/presses/powered-press-brakes（已開啟）
- OSHA Machine Guarding eTool，Additional References：https://www.osha.gov/etools/machine-guarding/additional-references（已開啟）
- OSHA Welding, Cutting, and Brazing：https://www.osha.gov/welding-cutting-brazing（已開啟）
- OSHA FS-3647 Welding Fume：https://www.osha.gov/sites/default/files/publications/OSHA_FS-3647_Welding.pdf（已開啟）
- OSHA Technical Manual IV-4 Industrial Robots：https://osha.gov/otm/section-4-safety-hazards/chapter-4（搜尋所見）
- OSHA 3071 Job Hazard Analysis：https://osha.gov/sites/default/files/publications/osha3071.pdf（搜尋所見）
- OSHA Severe Injury Reports：https://osha.gov/severeinjury（搜尋所見）
- OSHA Accident Investigation 範例：https://www.osha.gov/ords/imis/accidentsearch.accident_detail?id=908103（搜尋所見）
- OSHA 著作權（標準解釋 2005-03-11）：https://www.osha.gov/laws-regs/standardinterpretations/2005-03-11（搜尋所見）
- NIOSH 2011-156 LOTO：https://www.cdc.gov/niosh/publications/numbered/2011-156.html（已開啟）
- NIOSH 88-110 Welding Criteria：https://www.cdc.gov/niosh/docs/88-110/default.html（搜尋所見）
- NY FACE 16NY064：https://health.ny.gov/environmental/investigations/face/docs/16ny064.pdf（搜尋所見）
- NASA RP-1228 Fastener Design Manual：https://ntrs.nasa.gov/citations/19900009424（已開啟）
- NASA-STD-8739.4：https://standards.nasa.gov/standard/nasa/nasa-std-87394（已開啟）
- NASA Workmanship Cable and Harness：https://workmanship.nasa.gov/lib/insp/2%20books/links/sections/401%20General%20Requirements.html（搜尋所見）
- NAVEDTRA 14250 Steelworker Vol. 1（EverySpec）：https://everyspec.com/USN/NAVEDTRA/NAVEDTRA_14250_VOLUME-1_NOV1996_18385/（已開啟）
- NASA PCoE Data Repository：https://www.nasa.gov/content/prognostics-center-of-excellence-data-set-repository（搜尋所見）；鏡像 https://data.phmsociety.org/nasa/（搜尋所見）

**英國 HSE**
- INDG316：https://www.hse.gov.uk/pubns/indg316.pdf（已開啟，已抽出文字）
- HSG236：https://www.hse.gov.uk/pubns/books/hsg236.htm（已開啟）
- L112：https://www.hse.gov.uk/pubns/books/l112.htm（已開啟）
- HSE copyright：https://www.hse.gov.uk/help/copyright.htm（搜尋所見）

**廠商與產業資料（©，只作撰寫參考）**
- Durant Tool，Progressive die misfeed troubleshooting：https://www.durantco.com/blog/progressive-die-misfeed-troubleshooting-feed-accuracy/（已開啟）
- The Fabricator，slug pulling：https://www.thefabricator.com/article/stamping/die-basics-101-resolving-slug-pulling-problems（搜尋所見）
- The Fabricator，avoid die crashes：https://www.thefabricator.com/thefabricator/article/bending/ask-the-stamping-expert-how-can-i-avoid-die-crashes-（搜尋所見）
- MetalForming 2022-11 p.13：https://www.metalformingmagazine.com/public/Images/2022/11/digital/files/basic-html/page13.html（搜尋所見）
- MetalForming 2013-12 p.20：https://www.metalformingmagazine.com/public/images/2013/12/digital/files/basic-html/page20.html（搜尋所見）
- Coe Press 型錄：https://pdf.directindustry.com/pdf/coe/product-overview/111963-392433.html（搜尋所見）
- Durmapress，bending angle inaccuracy：https://www.durmapress.com/press-brake-bending-angle-inaccuracy-causes-fixes-accuracy-tips（搜尋所見）
- Durmapress，backgauge troubleshooting：https://www.durmapress.com/backgauge-positioning-problem-troubleshooting-guide/（搜尋所見）
- ISO 6909：https://www.iso.org/standard/83403.html（搜尋所見）
- Miller，Troubleshooting MIG Weld Defects：https://www.millerwelds.com/resources/article-library/troubleshooting-weld-defects（已開啟）
- microJoining，Resistance Welding Trouble Shooting Guide：https://microjoining.com/wp-content/uploads/2025/12/microTips-%E2%80%93-Resistance-Welding-%E2%80%93-Troubleshooting.pdf（已開啟）
- AMADA WELD TECH：https://amadaweldtech.com/blog/welding-problems-and-solutions-start-with-these-4-things/（搜尋所見）
- FANUC 警報碼（robots.com）：https://www.robots.com/articles/understanding-error-codes-in-fanuc-robots（搜尋所見）
- Yaskawa Motoman 知識庫：https://knowledge.motoman.com/hc/en-us/articles/4405159162263（搜尋所見）
- Thermaset powder troubleshooting：https://thermaset.co.uk/wp-content/uploads/2020/08/general_troubleshooting_thermaset_powder_coatings.pdf（已開啟）
- ChemQuest powder defects webinar：https://chemquest.com/wp-content/uploads/2025/02/Understanding-and-Preventing-Powder-Coating-Defects-2025-webinar.pdf（已開啟）
- Products Finishing，good ground：https://www.pfonline.com/articles/do-you-have-good-ground（搜尋所見）
- Gema Ask Joe Powder：https://www.gemapowdercoating.com/fileadmin/documents/Sonstige_PDF-Dateien/Ask_Joe_Powder/Ask-Joe-Powder-Jun-2022.pdf（搜尋所見）
- Atlas Copco Pocket Guide：https://www.atlascopco.com/zh-tw/itba/expert-hub/pocket-guide/tightening-technique（已開啟）
- Atlas Copco S8050 troubleshooting：https://picontent.atlascopco.com/cont/external/dir/41/15384642827_A2390001_html5_external/en-US/14503108875.html（已開啟）
- Wikipedia Cam out：https://en.wikipedia.org/wiki/Cam_out（搜尋所見）

**資料集**
- MaintNorm：https://github.com/nlp-tlp/maintnorm（已開啟）；https://huggingface.co/nlp-tlp/MaintNorm/tree/main（搜尋所見）
- MaintIE：https://github.com/nlp-tlp/maintie（已開啟）；論文 https://aclanthology.org/2024.lrec-main.954（搜尋所見）
- FMC-MWO2KG：https://astro.paperswithcode.com/dataset/fmc-mwo2kg（搜尋所見）；LLM for Failure Mode Classification：https://arxiv.org/pdf/2309.08181（搜尋所見）
- NIST Nestor datasets：https://pages.nist.gov/nestor/nestor/datasets（已開啟）
- NIST Maintenance software tools：https://www.nist.gov/el/enhancing-maintenance-strategies-manufacturing-operations/software-tools（已開啟）
- awesome-tlp：https://github.com/TLP-COI/awesome-tlp（已開啟）
- AI4I 2020：https://archive.ics.uci.edu/dataset/601/ai4i+2020+predictive+maintenance+dataset（已開啟）
- Azure PdM（GitHub）：https://github.com/Azure-Samples/MachineLearningSamples-PredictiveMaintenance（已開啟）；Kaggle：https://www.kaggle.com/arnabbiswas1/microsoft-azure-predictive-maintenance（搜尋所見）
- FabNER：https://huggingface.co/datasets/DFKI-SLT/fabner（已開啟）
- 沖壓加速度資料論文：https://arxiv.org/pdf/2504.21389（搜尋所見）
- IEEE DataPort bearing datasets：https://ieee-dataport.org/documents/bearing-datasets（搜尋所見）
