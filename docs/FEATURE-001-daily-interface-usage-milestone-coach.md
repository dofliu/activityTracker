# FEATURE-001：每日主要介面使用時間與里程碑教練

- 狀態：Implemented Alpha / Browser event 與真實 Toast evidence pending
- 記錄日期：2026-08-24
- 優先級：Should Have
- 前置條件：P2.5 collector reliability 與 notification/platform abstraction

## 1. 問題與目標

OmniContext 已有 `WindowEvent`、AI event 與 desktop notification，但尚未把資料整理成「今天各主要介面使用多久、里程碑是否達成」的可操作回饋。

本功能提供：

1. Claude Code、Codex、ChatGPT、Claude.ai、Gemini、Manus、VS Code 等主要介面的每日／每週前景使用時間。
2. 使用者自訂每日目標與多階段里程碑，例如 2、4、6 小時。
3. 達標後顯示肯定、鼓勵或可選的休息提醒。
4. Dashboard 顯示資料 coverage，避免把採集失敗誤認為沒有使用。

## 2. 名詞與指標契約

| 指標 | 定義 | 不代表 |
|---|---|---|
| `foreground_active_seconds` | 去除重疊後，該介面位於前景的視窗區間總和 | 實際工作時間、生產力或專注度 |
| `ai_interaction_count` | 當日 canonical AI turns 數量 | 使用時數或成果品質 |
| `goal_progress` | `foreground_active_seconds / configured_goal` | 績效評分 |
| `coverage_status` | `complete / partial / unavailable` | 平台本身是否曾被使用 |

計算原則：

- 以本機 timezone 切分日界線；跨午夜 interval 拆分至兩日。
- 同一時間只計入一個前景介面；重疊或重送事件先 merge/deduplicate。
- AI transcript 只補充互動次數與最後活動時間，不推估 duration，也不與 window duration 相加。
- collector heartbeat 缺失、OS 不支援或分類未知時，必須反映在 coverage，不補零、不外推。

## 3. 初版 UX

### Web Dashboard

- 今日總前景使用時間與目標進度。
- 主要介面排行榜：時間、互動次數、最後活動時間。
- coverage badge 與資料更新時間。
- 今日已達成里程碑、下一個里程碑與通知設定入口。

### Browser Extension popup

Popup 定位為 `Extension Monitor / Ingestion Bridge`：

- 顯示本機 service 連線與 ingest token pairing。
- 顯示支援網站的採集狀態。
- 可選擇顯示一句今日摘要；完整時間分析仍由 Web Dashboard 負責。

### Desktop notification

訊息範例：

- 「今天 Claude + Codex 前景使用時間已達 6 小時，今日里程碑完成。」
- 「今天已完成 4 小時的 AI 協作里程碑，進度保持得很好。」
- 「已連續使用較長時間；若目前工作告一段落，可以休息一下。」

訊息模板可調整為 `neutral / encouraging / praise`，且不得使用羞辱、壓迫或未經資料支持的績效判斷。

## 4. 設定與資料生命週期

建議設定群組：

- `usage_tracking.enabled`
- `usage_tracking.interface_rules`
- `usage_tracking.daily_goal_minutes`
- `usage_tracking.milestones_minutes`
- `usage_tracking.quiet_hours`
- `usage_tracking.notifications.cooldown_minutes`
- `usage_tracking.notification_tone`
- `usage_tracking.long_session_break_reminder`

資料原則：

- 原始 `WindowEvent` 維持既有 lifecycle；每日統計為可重建 derived data。
- notification receipt 保存 `date + milestone + channel` 唯一鍵，避免服務重啟後重複通知。
- window title 僅在本機使用；分類後的統計不需要保存完整 title。
- 本功能預設不呼叫 cloud LLM；若未來加入生成式鼓勵文案，必須另行 opt-in。

## 5. 實作切片

1. [x] `UsageClassifier`：將 app name、process 與 window title 映射為 canonical interface。
2. [x] `UsageAggregator`：合併 interval、處理 timezone、coverage 與每日統計。
3. [x] `MilestoneEngine`：計算門檻、寫入 notification receipt、套用 quiet hours/cooldown。
4. [x] Dashboard API、UI card 與 localhost Extension Monitor。
5. [ ] Desktop notification 已接線；真實達標 Toast 與跨平台 capability evidence 待補。
6. [x] 34 個 tests、Windows Dashboard/API smoke 與 Extension token pairing probe 已通過。
7. [ ] 真實達標 Toast、DST、macOS/Linux CI/實機待補。

## 6. Acceptance Criteria

- [x] 重送及重疊 interval 不造成重複計時。
- [ ] 跨午夜與本機日界線測試已通過；DST 尚待具 timezone-aware schema 後驗證。
- [x] 使用者可自行定義介面、目標、門檻、通知語氣、quiet hours 與 cooldown。
- [x] 同一里程碑同一天只通知一次，服務重啟後由 SQLite receipt 保持狀態。
- [x] collector 不健康或平台不支援時顯示 `partial / unavailable`，不把 coverage 缺口包裝成完整 0。
- [x] UI 使用「前景使用時間」措辭，沒有生產力或績效推論。
- [x] 使用時間與通知功能可完全關閉，且預設不傳送資料至 cloud LLM。
