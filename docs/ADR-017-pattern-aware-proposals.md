# ADR-017：模式感知提案（秘書開始用它記得的東西）

- 狀態：Accepted（2026-09-05 實作）
- 關聯：[ADR-007](ADR-007-proposal-only-secretary.md) proposal-only 契約、[ADR-008](ADR-008-gated-agent-executor.md) 分級執行器與收據、[ADR-012](ADR-012-secretary-memory.md) 記憶區（含 Addendum A 每日工作誌）、[ADR-016](ADR-016-acceptance-center.md) D2「只算已結束的日子」

## Context

每日工作誌（ADR-012 Addendum A）讓大腦裡有了「你每天做了什麼」。但查程式會發現提案引擎（`core/proactive_secretary.py`）讀記憶區只做兩件事：`mute` 壓掉提案、附一行專案筆記。**它不看任何模式。**

結果是兩種很「秘書該注意到」的事沒有人講：

| 情境 | 現況 |
| :-- | :-- |
| 你近一週有五天在工作，但秘書沒有任何每日排程——工作誌不會寫、早晨包不會跑，記憶區不會累積 | 沒有提案；使用者得自己想到要去建排程 |
| 兩週前你天天在論文上動、這週一個檔案都沒碰，而且沒有未結事項在提醒 | 沒有提案；`stalled_open_loop` 只看 Open Loop，沒有 Open Loop 的專案安靜地流失脈絡 |
| 你天天在 uavMonitor 上 commit，它和一個一個月沒碰的 repo 同樣「落後遠端 2 個 commit」 | 兩者分數一樣，排在一起 |

這是「日誌工具」與「秘書」之間最關鍵的一步：**注意到你的習慣，並在既有的安全閘門內提議。**

## Decision

### D1 模式只來自可回溯的（專案 × 日）計數

`core/activity_patterns.py` 用三張事件表（`git_activity_events.repo_name`／`ai_prompt_events.project_tag`／`file_activity_events.project_name`）依（專案 × 日）分組，得到「哪個專案哪幾天有活動」的矩陣。

- **不讀 prompt 內容、不做任何「他大概想做什麼」的推論**（契約測試禁止模組出現 `prompt_text`／`response_text`／`llm_gateway`）。
- **沒歸戶的活動不猜專案**：只計入「任何活動」，不會被塞進某個專案。
- **今天不算。** 與 [ADR-016](ADR-016-acceptance-center.md) A1 的修正同一個教訓：今天的分母只到現在，用它算「近一週幾天在工作」會失真。近一週 = 昨天往前 N 個完整日；前一週 = 再往前 N 天。

### D2 三種確定性的產出

1. **`no_daily_routine`**：近一週活動天數 ≥ `routine_min_active_days`（預設 4），且排程任務裡沒有**啟用中**的 `morning_pack` 或 `daily_digest`。標題點名最活躍的專案（「你近一週有 5 天在工作（uavMonitor、論文），但秘書還沒有每日排程」）。修法是既有的「建立每日排程」；一旦建立這個提案就消失——**自我熄滅**。
2. **`neglected_active_project`**：某專案前一週活躍 ≥ `neglect_min_prev_days`（預設 3）天、近一週 0 天。已經有未結事項提案的專案**不重複提**（它已經在清單上）。對應的動作是既有的 L0 `generate_handoff`——看一眼再決定接續或明確放下。
3. **習慣加權**（`apply_habit_boost`）：近一週活躍 ≥ `habit_min_days`（預設 3）天的專案，其**既有**訊號（需要 pull／PR／未結事項…）分數加 `habit_boost`（預設 0.15，上限 1.0）並附一句理由「這個專案近 7 天有 5 天在動，是你目前的主線」。**這是排序，不是新提案**；設 `habit_boost: 0` 即關閉。

### D3 不新增任何可執行動作

模式提案對應的動作全部是既有 template：`neglected_active_project` 由執行器既有的「帶專案的提案給 L0 Handoff」規則自然接上；`no_daily_routine` 的 `project_key` 只是「OmniContext」佔位，明確排除在 Handoff 之外（`_NO_HANDOFF_TYPES`），它的「動作」是使用者自己按既有的建立排程按鈕（需 execution token）。

ADR-007 的 proposal-only 與 ADR-008 的批准／收據一個字都沒改。本模組唯讀、不呼叫 LLM、不寫任何資料。

### D4 沿用既有的回饋迴路

模式訊號的形狀與其他 triage signal 完全一致，因此 snooze（`proposal_snoozes`）、偏好 `mute:<type|project>`、每專案上限（diversity）全部自動適用。使用者說「不要提醒 no_daily_routine」就永遠不再出現。

### D5 證據引用工作誌，但不依賴它

模式從表計算，不解析工作誌的正文（那是脆弱的）。若記憶區裡**已有**對應日期的 `daily_digest:*` 觀察，則附為 `evidence`（kind `daily_digest`）讓卡片能說「根據你這幾天的工作誌」；沒有就不附、不編。

### D6 誠實的 inputs

`build_action_proposals` 的 `inputs.patterns` 回報：是否啟用、近一週活動天數、各專案活躍天數、既有的例行排程、產生了幾筆模式訊號、加權了幾筆。模式層任何例外都被隔離成 `{"used": false, "reason": "error:…"}`，不拖垮提案清單。

## Alternatives considered

- **用 LLM 從工作誌歸納「習慣」**：拒絕。那是推測意圖，與本專案的證據文化衝突；問候卡的事實閘已經顯示這個張力。計數就夠回答「哪些專案是主線、哪些被擱下」。
- **解析工作誌正文找模式**：拒絕。工作誌是人讀的，文案會改；模式從同一組來源表算，穩定且可測。
- **為 `no_daily_routine` 做成可一鍵執行的 template**：拒絕。建立排程本來就有既有入口且需 execution token；不為了「更順手」多開一條寫入路徑。
- **「重複手動同步」獨立成一種提案**：合併進 D2。一旦有每日排程，既有的 `repo_needs_pull` 加上習慣加權就是「接手同步」在既有安全形式下的樣子（L1、需批准）。

## Consequences

- 沒有新 migration、沒有新危險能力、沒有新的隱私面。
- 使用者第一次看到的效果：01 的提案多出「還沒有每日排程」與「X 被冷落了」兩種卡，主線專案的 pull／PR 提案排前面並多一句理由。
- `docs/TODO.md` A16 定義實機收據；`core/acceptance.py` 的 `_ITEMS` 同步。
- 契約由 `tests/test_activity_patterns.py` 守門（21 項）：矩陣分組與不猜專案、今天不算、S1 觸發與自我熄滅、S2 觸發／本週有動不提／已有未結事項不重複、證據引用工作誌、加權排序與上限與可關、引擎整合（mute／失敗隔離／可關）、執行器對應（被冷落給 Handoff、routine 不給）、模組不讀 prompt 不呼叫 LLM。
