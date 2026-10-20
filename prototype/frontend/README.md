# Sentinel AI 前端（Next.js）

```bash
npm install
npm run dev                          # 連後端 http://localhost:8000（NEXT_PUBLIC_API_BASE 可改）
NEXT_PUBLIC_MOCK=1 npm run dev       # 不需後端：前端內建模擬後端＋模擬 WebSocket 事件
NEXT_PUBLIC_MOCK=1 npm run build && npm start   # NEXT_PUBLIC_* 在 build 時寫入
```

頁面：`/` 戰情儀表板、`/kanban`、`/workorders/[id]`、`/m/[workorderId]`（技術員手機版）、`/control`、`/metrics`、`/audit`。
契約實作：`lib/api.ts`（REST，帶 `X-Role`／`X-Person-Id`）、`lib/ws.ts`（WebSocket）、`lib/types.ts`（型別）。模擬後端：`lib/mock/`。

## 超出 08_系統設計.md 的 API 假設（後端請對齊或告知）

- WS：連 `/ws?role=&person_id=`，連上後送 `{"action":"subscribe","channels":[...]}`（取消用 `unsubscribe`）。
  事件 type：`workorders`→`created|status_changed|updated|escalated|reset`（data 帶 `id` 或 `workorder`）；
  `ai`→`jev_vision|jev_result|llm_delta|llm_explanation`（帶 `workorder_id`／`incident_id`）；
  `agent:{id}`→`delta {message_id,text(累積全文)}`、`message {AgentMessage}`、`safety_gate {message_id,flags}`；`metrics`→`summary {MetricsSummary}`。
- 列表端點回陣列或 `{items:[...]}` 皆可。`GET /workorders` 每筆內嵌 `line, machine_id, severity, category, title, trace_id, assignee_name`。
- `GET /workorders/{id}` 回 `WorkOrderDetail`：內嵌 `incident, diagnosis, sops, loto{loto_id,steps[]}, permit, checklist[], agent_messages[], transitions[], report`。POST 操作回更新後的工單。
- Incident 額外欄位：`camera_id, title, jev_box[x1,y1,x2,y2]（1280×720）, keyframe_url, diagnosis`；diagnosis `root_cause_top3[{failure_mode_id,name,p}]`、`explanation_status`。
- `POST /permit {action:"request"|"approve"}`；`POST /loto/{step}`、`/checklist/{item}` body `{checked:true}`；`POST /report {text, root_cause_confirmed}` 回 `{check:{complete,missing[],kb_status}, workorder}`；`POST /agent {message}`。
- `POST /overrides {target:"workorder", target_id, field:"false_alarm"|"final_severity"|"safety_ack", new, reason}`。
- `GET /audit?trace_id=` 另支援 `?workorder_id=`。`GET /machines/{id}/signals` 回 `{points:[{ts, signal...}]}` 或 `{signals:{name:[{ts,v}]}}`。
- `/metrics/summary`：`reaction_s{detect,notify,arrive}, mttr_min, ai_cost_usd, ai_cost_series[{ts,cost(累計)}], handled{rules_only,jev,llm}, severity_counts, comparison[]`；`/metrics/calibration`：`bins[{p_pred,p_obs,n}], brier, ece`。
- 控制台另需（§7.1 沒列）：`GET /videos [{path,label,line}]`、`GET/POST /demo/settings {escalation_timeout_s, jev_enabled}`；`POST /cameras/{id}/source {video}`；`/plant` 的 camera 可帶 `source`、machine 可帶 `health, utilization`。
- 角色值：`technician|leader|supervisor|judge`；人員 ID `P-T01/P-L01/P-S01/JUDGE`。
