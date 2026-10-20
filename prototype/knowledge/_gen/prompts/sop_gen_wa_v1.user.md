請為下列故障模式撰寫 SOP 的 inspect／repair／verify 內容。

## 故障模式（line: {line}，failure_mode_id: {fm_id}）
```yaml
{fm_yaml}
```

## 適用機台
{machines}

## 上鎖掛牌檢查表（程式會在你的步驟之前插入 isolate 步驟）
{loto_summary}

## 易混淆模式（inspect 必須能區分）
{distinguish}

## escalate_if 可以指向的 SOP
{escalate_targets}

## 可引用的來源編號（ref 欄只能用這些，或 "none"）
{sources}

## 可引用的法規／公開文件 chunk 編號
{chunks}

## 要求
- title：SOP 標題，形如「{title_example}」
- symptoms：3～5 條現場會講的症狀描述，包含不同說法
- prerequisites：tools（3～6 項）、parts（可能更換的零件，通用名稱，0～4 項）、skill_level（{skill_levels}之一）
- ppe：在「{ppe_base}」之外，若有需要再補充的防護具（可空）
- inspect：3～5 步，每步有 action、criteria（判定標準，含模擬值）、caution（可為 null）、requires_energized、ref
- repair：2～4 步，每步有 action、caution、requires_energized、requires_supervisor、ref
- verify：2～3 步，每步有 action、criteria（含模擬值）、requires_energized、ref
- isolate_caution：一句本模式在上鎖時特別要注意的事
- escalate_if：2～3 條，格式「條件 → 處置（改走 SOP-xxx-xxx 或通知設備工程師／機器人工程師／品保工程師）」
- lessons：一句給技術員的提醒
