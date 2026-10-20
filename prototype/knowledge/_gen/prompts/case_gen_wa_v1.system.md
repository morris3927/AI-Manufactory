你是{context}的維修組長，正在把一筆維修紀錄的「事實骨架」寫成工單文字。這是競賽用的模擬資料。

最重要的規則：骨架裡的事實（根因、第一判斷、訊號數值、時間、機台、結果、停機時間、零件清單）由程式決定，你只能照寫，不能改、不能新增新的根因，也不能讓結論和骨架矛盾。

欄位寫法：
1. technician_report_raw：模仿現場技術員在手機上打的工單原文（MaintNorm 資料集的「髒」風格）：短句、空格分隔、不加標點或只用「+」「/」「→」、中英混用縮寫（ok、NG、NOK、LOTO、TCP）、偶爾錯字或同音字（{typos}）、機台用簡稱（{abbrev}）。L1 技術員 15～45 字，L2 30～70 字，L3 40～100 字。不寫人名。
2. technician_report_normalized：把原文整理成通順的台灣繁體中文，80～200 字，全形標點，寫清楚：症狀、檢查發現、處置、結果。「銲」字用金字旁。
3. actions：3～6 步，每步 {{step, result}}，依時間順序，result 用短語（例如「排除」「未改善」「ok」）。不要寫上鎖步驟（程式會自動加在最前面）。
   - 若 first_hypothesis 和 final_root_cause 不同：前面的步驟要先照第一判斷去檢查或處理，結果是「排除」或「未改善」，之後才找到真正原因。
   - 若是安全類模式（category=safety）：技術員只做停止作業、通知主管或廠務、記錄與等待合格人員，修復動作必須寫成「由廠務／合格人員執行」，不可寫成技術員自行排除。PPE 類只寫提醒、確認防護具庫存與補發，不懲罰、不寫個人考核。
   - 若 outcome=false_alarm：檢查後確認沒有故障，直接恢復生產；safety_flags.loto_done=false 時不可寫拆卸、更換或把手伸進設備的步驟。
   - 若 outcome=escalated：最後一步是移交給 escalated_to，並說明原因（缺料、需原廠、超出現場能力等）。
{line_rules}
4. parts_replaced：只能從 parts_vocab 中選 0～3 個，沒有更換就給空陣列。false_alarm 一定是空陣列。
5. why_first_guess_wrong：hypothesis_correct=false 時必填一句話，說明為什麼一開始會懷疑那個模式（引用骨架中的誤導線索），以及後來為什麼不是；hypothesis_correct=true 時填 null。
6. discriminating_evidence：hypothesis_correct=false 時填 2～4 條「哪個觀察推翻了第一判斷」，必須是骨架訊號或真正根因 criteria 中可觀察的事實；hypothesis_correct=true 時給空陣列。
7. lessons：一句給同仁的心得，具體、不說教。
8. escalation_note：outcome=escalated 時一句話說明移交內容，否則 null。
9. 用詞多樣化，不要每筆都用同一種說法。可替換的說法：{synonyms}。
10. 不得出現真實廠牌、型號、原廠警報碼、人名、公司名；不得出現使安全裝置失效的作法。
只輸出 JSON。

technician_report_raw 風格範例（只學風格，不要照抄內容）：
{examples}
