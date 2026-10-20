// 模擬資料（前端離線 Demo 用）。人員皆為虛構姓名。
import type { ChecklistItem, Diagnosis, LotoChecklist, Person, Plant, Scenario, SopChunk, VideoAsset } from "../types";

export const PLANT: Plant = {
  lines: [
    {
      id: "stamping",
      name: "沖壓線",
      machines: [
        { id: "STP-L1-PR01", type: "servo_press", name: "250t 伺服沖床", line: "stamping" },
        { id: "STP-L1-FD02", type: "servo_roll_feeder", name: "伺服送料機", line: "stamping" },
      ],
      cameras: [
        {
          id: "CAM-STP-01",
          name: "沖壓線 送料口",
          line: "stamping",
          machine_id: "STP-L1-FD02",
          rois: { feed_inlet: [420, 310, 860, 620], danger_zone: [300, 200, 1000, 700] },
          detectors: ["motion", "jev_feed"],
          source: "assets/videos/stamping/normal_01.mp4",
        },
        {
          id: "CAM-STP-02",
          name: "沖壓線 沖床",
          line: "stamping",
          machine_id: "STP-L1-PR01",
          rois: { die_area: [360, 180, 920, 640] },
          detectors: ["motion"],
          source: "assets/videos/stamping/press_normal_01.mp4",
        },
      ],
    },
    {
      id: "assembly",
      name: "組裝線",
      machines: [
        { id: "ASM-L1-WS01", type: "workstation", name: "組裝工作站 A", line: "assembly" },
        { id: "ASM-L1-CV01", type: "conveyor", name: "輸送帶", line: "assembly" },
      ],
      cameras: [
        {
          id: "CAM-ASM-01",
          name: "組裝線 工作站",
          line: "assembly",
          machine_id: "ASM-L1-WS01",
          rois: { workstation: [0, 0, 1280, 720] },
          detectors: ["ppe"],
          source: "assets/videos/assembly/normal_01.mp4",
        },
      ],
    },
    {
      id: "welding",
      name: "銲接線",
      machines: [
        { id: "WLD-L1-RB01", type: "welding_robot", name: "銲接機器人", line: "welding" },
        { id: "WLD-L1-FX01", type: "fixture", name: "銲接治具台", line: "welding" },
      ],
      cameras: [
        {
          id: "CAM-WLD-01",
          name: "銲接線 機器人",
          line: "welding",
          machine_id: "WLD-L1-RB01",
          rois: { weld_cell: [200, 120, 1080, 680] },
          detectors: ["motion", "jev_smoke"],
          source: "assets/videos/welding/normal_01.mp4",
        },
      ],
    },
  ],
};

export const PEOPLE: Person[] = [
  { id: "P-T01", name: "林志明", role: "technician", line: "stamping" },
  { id: "P-T02", name: "張雅婷", role: "technician", line: "assembly" },
  { id: "P-T03", name: "黃信宏", role: "technician", line: "welding" },
  { id: "P-L01", name: "陳美玲", role: "leader" },
  { id: "P-S01", name: "王建國", role: "supervisor" },
  { id: "P-M01", name: "吳淑芬", role: "manager" },
  { id: "JUDGE", name: "評審", role: "judge" },
];

export const ESCALATION_CHAIN = ["technician", "leader", "supervisor"];

export function personName(id: string | null | undefined): string {
  if (!id) return "—";
  if (id === "system") return "系統";
  if (id === "jev") return "Jev";
  if (id === "llm") return "DeepSeek";
  return PEOPLE.find((p) => p.id === id)?.name ?? id;
}

export const LOTO_FEEDER: LotoChecklist = {
  loto_id: "LOTO-STP-FD",
  machine_type: "servo_roll_feeder",
  title: "伺服送料機 上鎖掛牌程序",
  steps: [
    { step_no: 1, text: "通知沖壓線作業人員即將停機上鎖", energy: "通知", checked_by: null, ts: null },
    { step_no: 2, text: "由操作面板正常停止送料機與沖床", energy: "停機", checked_by: null, ts: null },
    { step_no: 3, text: "關閉主電源隔離開關 QF1，上鎖並掛牌", energy: "電力", checked_by: null, ts: null },
    { step_no: 4, text: "關閉主氣閥並排空壓縮空氣，壓力表歸零", energy: "氣壓", checked_by: null, ts: null },
    { step_no: 5, text: "釋放送料滾輪壓力，確認機械儲能歸零", energy: "機械儲能", checked_by: null, ts: null },
    { step_no: 6, text: "按啟動鈕確認無法啟動（零能量驗證）", energy: "驗證", checked_by: null, ts: null },
  ],
};

export const CHECKLIST_FEEDER: ChecklistItem[] = [
  { item: "jam_cleared", label: "卡料已清除，模具區無殘留料片", checked_by: null, ts: null },
  { item: "feed_error_ok", label: "試沖 10 模，送料長度誤差 < 0.05 mm", checked_by: null, ts: null },
  { item: "setting_checked", label: "滾輪壓力與板厚設定已確認", checked_by: null, ts: null },
  { item: "guard_restored", label: "護罩與光柵已復位", checked_by: null, ts: null },
  { item: "loto_released", label: "已由原上鎖人依 LOTO 解除程序解鎖", checked_by: null, ts: null },
];

export const CHECKLIST_SAFETY: ChecklistItem[] = [
  { item: "ppe_corrected", label: "現場人員已配戴安全帽", checked_by: null, ts: null },
  { item: "briefed", label: "已完成現場安全宣導並記錄", checked_by: null, ts: null },
];

export const CHECKLIST_GENERIC: ChecklistItem[] = [
  { item: "cause_found", label: "異常原因已確認", checked_by: null, ts: null },
  { item: "verified", label: "復機後運轉 10 分鐘無異常", checked_by: null, ts: null },
];

export const SOP_FEEDER: SopChunk[] = [
  {
    sop_id: "SOP-STP-012",
    title: "伺服送料機卡料排除",
    phase: "prepare",
    steps: ["確認工單根因前三名與 Log 摘要", "準備塞規、分厘卡、取料夾具", "依 LOTO-STP-FD 完成上鎖"],
    source: "模擬 SOP（自編）",
  },
  {
    sop_id: "SOP-STP-012",
    title: "伺服送料機卡料排除",
    phase: "inspect",
    failure_mode_id: "FM-STP-ROLLER-WEAR",
    steps: ["目視檢查上下滾輪表面磨耗與溝痕", "以塞規量測滾輪間隙（≤ 0.05 mm）", "以分厘卡量測板材三點厚度（2.00 ± 0.05 mm）"],
    source: "模擬 SOP（自編）",
  },
  {
    sop_id: "SOP-STP-012",
    title: "伺服送料機卡料排除",
    phase: "repair",
    steps: ["使用取料夾具由送料口側退出卡料", "更換磨損滾輪或隔離不良批材料", "重新設定送料長度與滾輪壓力"],
    source: "模擬 SOP（自編）",
  },
  {
    sop_id: "SOP-STP-012",
    title: "伺服送料機卡料排除",
    phase: "verify",
    steps: ["試沖 10 模確認送料誤差", "由原上鎖人依 LOTO 解除程序解鎖", "填寫結案回報"],
    source: "模擬 SOP（自編）",
  },
];

export const SOP_PPE: SopChunk[] = [
  {
    sop_id: "SOP-SAF-003",
    title: "未配戴個人防護具處置",
    phase: "respond",
    steps: ["班長到場提醒並確認配戴", "記錄人員與時間", "當日班會宣導"],
    source: "模擬 SOP（自編）",
  },
];

export function feedJamDiagnosis(incidentId: string, learned: boolean): Diagnosis {
  return {
    incident_id: incidentId,
    root_cause_top3: learned
      ? [
          { failure_mode_id: "FM-STP-MATERIAL-THICK", name: "材料厚度偏差", p: 0.64 },
          { failure_mode_id: "FM-STP-ROLLER-WEAR", name: "送料滾輪磨損", p: 0.24 },
          { failure_mode_id: "FM-STP-DIE-MISALIGN", name: "模具定位偏移", p: 0.08 },
        ]
      : [
          { failure_mode_id: "FM-STP-ROLLER-WEAR", name: "送料滾輪磨損", p: 0.52 },
          { failure_mode_id: "FM-STP-MATERIAL-THICK", name: "材料厚度偏差", p: 0.31 },
          { failure_mode_id: "FM-STP-DIE-MISALIGN", name: "模具定位偏移", p: 0.12 },
        ],
    confidence: learned ? 0.84 : 0.78,
    mode: "jev_top3",
    p_false_alarm: 0.04,
    questions_ver: "jq-2026.10.3",
    explanation: null,
    explanation_status: "pending",
  };
}

export const FEED_JAM_EXPLANATION =
  "送料速率在 6 秒內由 42 m/min 降為 0，同時送料長度誤差由 0.02 mm 逐步升到 0.21 mm；Jev 連續兩張關鍵幀判定卡料（0.82、0.88），並框出送料口的板材。誤差是「逐步上升」而不是瞬間跳變，比較符合滾輪打滑（磨損）造成的送料不足，所以第一名是送料滾輪磨損。如果量測板厚超出 2.00 ± 0.05 mm 的公差，材料厚度偏差的可能性就會上升。建議先依 LOTO-STP-FD 完成上鎖，再依 SOP-STP-012 §3 檢查滾輪表面與間隙。";

export const FEED_JAM_EXPLANATION_LEARNED =
  "本次訊號型態與上次結案的 CASE-STP-0087（板厚 2.08 mm 超出公差）高度相似，知識庫寫回後，材料厚度偏差升為第一名。建議上鎖後先以分厘卡量測板材三點厚度，再檢查滾輪。";

export const SCENARIOS: Scenario[] = [
  { id: "ppe_helmet", title: "場景 1：PPE 未戴安全帽", description: "組裝線攝影機切到未戴安全帽影片 → YOLO＋Jev 確認 → 工安 S2 直接派班長（不叫 LLM）" },
  { id: "feed_jam", title: "場景 2：沖床送料卡料", description: "影片卡料＋Log 送料速率歸零 → 跨模態確認 → Jev 根因前三名 → 派工 → LLM 說明補上" },
  { id: "idle_mismatch", title: "場景 3：影像閒置但 Log 顯示運轉", description: "沖床畫面靜止，但 MES 回報運轉中 → Log 失準告警" },
  { id: "weld_smoke", title: "場景 4：銲接煙霧＋逐級升級", description: "Jev 看到異常煙霧 → 無人接單 → 技術員 → 班長 → 組長" },
];

export const VIDEOS: VideoAsset[] = [
  { path: "assets/videos/stamping/normal_01.mp4", label: "送料口 正常運轉", line: "stamping" },
  { path: "assets/videos/stamping/feed_jam_01.mp4", label: "送料口 卡料", line: "stamping" },
  { path: "assets/videos/stamping/press_normal_01.mp4", label: "沖床 正常運轉", line: "stamping" },
  { path: "assets/videos/stamping/press_idle_01.mp4", label: "沖床 閒置（畫面靜止）", line: "stamping" },
  { path: "assets/videos/assembly/normal_01.mp4", label: "工作站 正常作業", line: "assembly" },
  { path: "assets/videos/assembly/no_helmet_01.mp4", label: "工作站 未戴安全帽", line: "assembly" },
  { path: "assets/videos/assembly/false_alarm_01.mp4", label: "工作站 誤報情境（帽子在手上）", line: "assembly" },
  { path: "assets/videos/welding/normal_01.mp4", label: "銲接 正常", line: "welding" },
  { path: "assets/videos/welding/smoke_01.mp4", label: "銲接 異常煙霧", line: "welding" },
];
