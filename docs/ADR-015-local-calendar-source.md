# ADR-015：本機行事曆採集來源（.ics，唯讀）

- 狀態：Accepted（2026-09-04 實作）
- 關聯：[ADR-001](ADR-001-p2-5-trust-boundary.md) loopback／本機優先邊界、[ADR-003](ADR-003-versioned-sqlite-migrations.md) 版本化 migration、[ADR-007](ADR-007-proposal-only-secretary.md) proposal-only 秘書、[ADR-014](ADR-014-multi-channel-push-and-arm-code.md) 晨報組裝

## Context

問候卡（2026-09-04）與晨報都如實寫著「郵件與行事曆目前不在採集範圍」。使用者接著選了 TODO C3 的**行事曆**作為下一個採集來源。本專案對任何新來源的門檻是：**先過「能否改變決策」檢驗**，過不了就不納入——採集越多不等於越有用，反而增加隱私面與噪音。

### 「能否改變決策」檢驗

| 問題 | 沒有行事曆時 | 有行事曆時 |
| :-- | :-- | :-- |
| 現在該不該開始一件大任務？ | 只能憑記憶 | 「下一場 14:00 專案會議（35 分後）」→ 直接決定做小事還是大事 |
| 今天怎麼排？ | 晨報只有專案與未結事項 | 晨報多一段「📅 今日行程（3 場）」，第一眼就知道今天有多少完整時段 |
| 今天／昨天時間花去哪？ | 只看到 commit／PR／AI 對話 | 問候卡多一句「開了 3 場會」，會議負擔與產出放在同一張卡上如實對照 |

三題都會改變行為 → **納入**。反例（不納入的用法）：不做會前提醒、不做「你應該取消這場會」之類建議、不從行事曆推測產能。

### 來源選擇

| 方案 | 評估 |
| :-- | :-- |
| Google／Microsoft Graph 行事曆 API | 需 OAuth、雲端往返、憑證保管；違背本專案「本機優先、不連雲端」的預設 |
| Outlook 桌面 COM（pywin32） | 只有 Windows、Outlook 必須開著、跨平台 CI 無法驗證 |
| **本機 `.ics` 檔案／資料夾**（採用） | Outlook／Google／Apple 都能匯出或訂閱成檔；Thunderbird／iCloud／OneDrive 同步資料夾也是檔案；純讀、不需憑證、三個平台行為一致 |

## Decision

### D1 唯讀輪詢本機 `.ics`

- `watchers/calendar_watcher.py`：`CalendarWatcherService` 與其他採集器同形（`start`／`stop`／`check_health_and_heal`／`get_diagnostics`），每 `scan_interval_seconds`（預設 900）掃一次 `watchers.calendar_watcher.paths` 裡的 `.ics` 檔與資料夾（資料夾只收第一層 `.ics`）。**只讀檔案，永不寫回、永不連網。**
- 解析器 `core/ics_parser.py` 只用標準函式庫 ＋ `python-dateutil`（已是 pandas 的相依）：RFC 5545 折行、`DTSTART`／`DTEND`／`DURATION`、`VALUE=DATE` 全天、`TZID`（`zoneinfo`；查不到時區時退回本地時間並在診斷記一筆）、`RRULE`＋`EXDATE`、`RECURRENCE-ID` 覆寫、`STATUS:CANCELLED`。重複事件**只展開視野內**（今天 −7 天 ～ ＋`horizon_days`，預設 30）。
- 每個檔案各自 try/except：壞檔進 `degraded_sources`，不影響其他檔（與 git_watcher 的 repo 隔離同一模式）。

### D2 只存決策需要的欄位

`calendar_events`（migration 018）每一列是**一個展開後的實例**：`uid`、`instance_start`、`instance_end`、`all_day`、`summary`、`location`、`status`、`calendar_name`、`source_path`、`last_seen_at`；`(source_path, uid, instance_start)` 唯一。

**不存**：`DESCRIPTION`、`ATTENDEE`、`ORGANIZER`、`URL`、附件、提醒（`VALARM`）。這些是會議內容與與會者個資，對上面三題沒有幫助。`store_titles: false` 時連 `summary`／`location` 都不存（顯示為「行程」），只留時間——給共用機器或敏感行事曆用。

每次掃描以「檔案 × 視野」為單位整批替換（同一交易內刪舊插新），所以取消或移動的行程不會殘留。

### D3 只在三個地方使用，且都可回溯

- **問候卡**（`core/secretary_greeting.py`）：視窗內**已開始**的行程數進 `stats["meetings"]`（來源表 `calendar_events`），成就句多一行「開了 N 場會」；`today` 視窗另附 `schedule`（今天總數、剩餘、下一場）。claim boundary 隨採集器狀態改寫：行事曆有啟用就不再說「不在採集範圍」，改列入來源；郵件仍如實標為不在範圍。
- **晨報**（`notifiers/messages.py`）：新增 `📅 今日行程（N 場）` 分節，最多列 8 場，時間＋標題（＋地點）。讀不到就省略，晨報本體不受影響。
- **01 今日面板**（`core/secretary_packs.build_today_view`）：`calendar` 區塊——下一場、幾分鐘後、今天剩幾場。

`GET /api/v1/calendar/agenda?date=YYYY-MM-DD` 提供同一份唯讀資料給儀表板與除錯用；每個回應都帶 `claim_boundary` 與 `sources`。

### D4 預設與邊界

- `watchers.calendar_watcher.enabled` 預設 true，但**沒有設定任何路徑就是停用**（系統健康顯示「已停用」，不會出現「尚無紀錄」的假警報）。
- 不接受儀表板傳入任意路徑以外的行為：路徑只從 config 讀，與 file_watcher 相同；不提供上傳 `.ics` 的 API。
- 行事曆不進 RAG 索引、不進每日摘要的 LLM prompt（會議標題不送任何 LLM）；問候卡的 LLM 潤飾沿用數字事實閘，`schedule` 的數字與時間都在 `stats` 內才能出現。

## Alternatives considered

- **訂閱 webcal URL 直接抓**：需要連網並保存含 secret 的私人網址；使用者可改在行事曆軟體端訂閱、本機同步成檔，效果相同且不在本服務保管網址。**先不做**；若之後要做，需獨立 ADR 處理 URL 保管與失敗行為。
- **把行程當成 proposal（「會前 10 分鐘，先收尾」）**：拒絕。ADR-007 的提案要有 evidence 與可追溯動作；「提醒」屬於行事曆軟體本身的工作，重複做只會多一個通知來源。
- **存 DESCRIPTION 供 RAG 檢索**：拒絕。會議描述常含連結、密碼、與會者名單，價值低風險高。

## Consequences

- 使用者匯出或同步一份 `.ics` 到本機、在「系統設定 → 採集來源」加入路徑，晨報與問候卡就會如實反映行程；沒有設定時一切照舊。
- 新增 migration 018；`tests/test_database_migration.py` 的版本清單同步更新。
- Windows 上 `zoneinfo` 需要 `tzdata` 套件（已加入 `requirements.txt`／`pyproject.toml`，只在 win32 安裝）；找不到時區資料時退回本地時間，不會讓採集器停止。
- 契約由 `tests/test_calendar_source.py` 守門：解析（折行／全天／TZID／RRULE＋EXDATE／覆寫／取消／隱私欄位不落地）、掃描（整批替換、壞檔隔離、自我修復）、agenda（今天／下一場／剩餘）、問候卡與晨報整合、端點。
