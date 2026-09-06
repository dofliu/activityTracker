# ADR-020：每週回顧——說的 vs 做的

- 狀態：Accepted（2026-09-06 實作）
- 關聯：[ADR-012](ADR-012-secretary-memory.md) 記憶區與每日工作誌（Addendum A）、[ADR-017](ADR-017-pattern-aware-proposals.md) 活動矩陣與模式提案、[ADR-018](ADR-018-declared-profile.md) 宣告式個人檔案、[ADR-019](ADR-019-secretary-desk-home.md) 秘書桌面、[ADR-007](ADR-007-proposal-only-secretary.md)／[ADR-008](ADR-008-gated-agent-executor.md)

## Context

個性化三步之後，秘書手上有兩種互不相識的知識：

- **你說的**：ADR-018 的「優先：論文」——寫在偏好筆記裡，會讓論文的提案加分。
- **你做的**：ADR-017 的（專案 × 日）活動矩陣——知道這週 uavMonitor 動了五天、論文一天。

沒有任何地方把兩者放在一起。秘書會把論文的提案排前面，卻不會說出最該說的那句：「你說論文優先，這週卻幾乎沒碰。」既有的週報 rollup（`weekly_report_rollup`）彙整的是每日摘要文字，寫進 `reports/`，不進記憶區，也不看你的宣告。

「說的 vs 做的」是秘書最有價值、也最不需要推測的一句話：兩個都是事實，放在一起就有意義。

## Decision

### D1 期間永遠是已結束的 ISO 週

`core/weekly_review.py` 的 `review_period(now, weeks_back)` 回傳第 N 個**已結束**的週一～週日；進行中的這週永遠不算（與 ADR-016 A1、ADR-017「今天不算」同一個教訓：沒結束的期間分母不完整）。週一早上跑就是剛結束的上週。

### D2 活躍天數只用可回溯計數

沿用 ADR-017 的 `activity_matrix`：commit／AI 對話／檔案異動依（專案 × 日）分組。沒歸戶的活動只算進「整週有活動的天數」，不猜專案。**不讀 prompt 內容、不呼叫 LLM。**

### D3 說的 vs 做的：三種結果，一個誠實的 None

`compare_said_vs_done` 對每個宣告的優先給一種結果：

| 結果 | 條件 | 意思 |
| :-- | :-- | :-- |
| `done` | 活躍 > `drift_max_days`（預設 1） | 有在做 |
| `drift` | 活躍 ≤ `drift_max_days`，且同週有**未宣告**的專案 ≥ `drift_min_other_days`（預設 3） | 時間去了別處 |
| `quiet` | 活躍很少，但也沒有別的專案在忙 | 整週安靜，不算偏移 |

整體 `aligned`：沒宣告＝None、有 drift＝False、全 done＝True、其餘＝None（活動太少不好比）。專案名比對不分大小寫；對不到任何活動的名字**如實標 `matched_key: None`** 並在正文寫「沒有任何活動歸到這個名字」——可能是名字打錯，也可能真的沒做，秘書不猜。

「時間去了別處」只看未宣告的專案：兩個宣告的優先一個做了一個沒做，不算偏移——你在做你說的事之一。

### D4 一則回顧觀察，同一週只寫一次

`build_weekly_review` 把上週 reduce 成記憶區觀察「2026-W37 回顧（09-07～09-13）」：整週活動天數、依活躍天數列專案、宣告的優先各幾天與一致／不一致、每日工作誌寫了幾天（只數 `source_ref`，不解析正文）。`source_ref = weekly_review:<label>` 去重，可一鍵刪除、隨 TTL 過期；整週沒活動不寫、關閉不寫、記憶區關閉不寫。

**早晨包每天都會補**：第五步呼叫 `build_weekly_review(weeks_back=1)`，靠去重保證同一週只寫一次——不綁週一，機器週一沒開也不會漏。也可獨立排程（L0 template `weekly_review`，`weeks_back` 1–4）。

### D5 不一致就一張 `priority_drift` 提案，即時算

`collect_priority_drift_signals` 在提案引擎裡**即時**從表計算（不依賴回顧有沒有跑；已有回顧就附為證據）。標題就是那句話：「你說『論文』優先，上週它只有 1 天在動、uavMonitor 有 5 天」；`why_now`：「上一個完整週剛結束，現在調整下週最划算；再放一週，宣告就只是字」；建議動作二選一：下週把時間排給它（既有 L0 Handoff 看一眼再接續），或改宣告。

兩個刻意的互動：

- 它的專案是宣告的優先，所以 ADR-018 的優先加分會落在它身上（0.6–0.85 ＋ 0.2）——通常直接成為桌面焦點。**你說重要卻沒做的事，就該在首頁最上面。**
- 同一個專案若同時被 ADR-017 判為「被冷落」，只留 `priority_drift`：它多講了「你說過這是優先」，資訊嚴格更多。

snooze／`mute:priority_drift`／每專案上限照舊適用；回顧層任何例外隔離成 `inputs.weekly_review = {used: false, reason}`。

### D6 桌面「記得」的順序多一級

ADR-019 的挑法改為：焦點專案的決定／筆記 → **剛出爐的每週回顧（三天內）** → 最近一天的工作誌 → 釘選 → 最近記下的。週一到週三打開 01，「記得」那格就是上週的回顧；之後回到每日工作誌。

## Alternatives considered

- **用 LLM 寫「本週回顧」**：拒絕。回顧的價值在「兩個數字放在一起」，加一層文字生成只會模糊來源；而且既有的 `weekly_report_rollup` 已經是那種東西。
- **改造 `weekly_report_rollup`**：拒絕。它彙整每日摘要**文字**、輸出到 `reports/`；回顧要進記憶區、要看宣告，是不同的產物，硬接會讓兩邊都不乾淨。
- **推測偏移的原因**（「你在趕 uavMonitor 的 deadline」）：拒絕。與 ADR-017 拒絕推測意圖同一個理由；正文明寫「這只是兩個數字放在一起，為什麼由你判斷」。
- **只在週一寫回顧**：拒絕。機器週一沒開就漏一週；用去重讓「每天補上週的」等價於「一週一次」。

## Consequences

- 沒有新 migration、沒有新危險能力、沒有新的隱私面；新增一個 L0 template 與一種提案類型，對應的動作是既有的 Handoff。
- 使用者第一次看到的效果：宣告過優先的人，下週一在 01 桌面看到「你說 X 優先，上週它只有 N 天在動」；記憶區多一則「W## 回顧」，問秘書「上週我做了什麼」它答得出來。
- `docs/TODO.md` A19 定義實機收據；`core/acceptance.py` `_check_a19`。
- 契約由 `tests/test_weekly_review.py` 守門（20 項）：期間永遠已結束、活躍天數不猜專案、偏移／安靜／一致／兩個優先一做一沒做／名字對不到、門檻可設、正文三句、同一週只寫一次、關閉與記憶區關閉不寫、模組不讀 prompt 不呼叫 LLM、訊號句子與證據、沒宣告的誠實理由、引擎加分並頂掉重複的被冷落、mute 與失敗隔離、template L0 與參數邊界、早晨包補上週且失敗不拖垮、桌面剛出爐優先挑回顧、A19 三種狀態。
