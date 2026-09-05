# ADR-018：宣告式個人檔案（你自己說的，不是推測的）

- 狀態：Accepted（2026-09-05 實作）
- 關聯：[ADR-012](ADR-012-secretary-memory.md) 記憶區（偏好筆記是本 ADR 唯一的資料來源）、[ADR-017](ADR-017-pattern-aware-proposals.md) 模式感知提案（習慣加權；本 ADR 的優先加分刻意壓過它）、[ADR-007](ADR-007-proposal-only-secretary.md) proposal-only 契約、問候卡（ROADMAP §11，2026-09-04）

## Context

「讓介面更像個人秘書」的第一步（ADR-017）讓秘書開始**注意**你的習慣。但它注意到的一切都是**推出來的**：哪個專案是主線，是從活動天數算的。你沒有任何地方可以直接告訴它「這兩週我在乎的是論文，不是天天在 commit 的那個 repo」，也沒有地方說「講話簡潔一點」。

查程式會發現 ADR-012 的 `preference` 筆記名義上是「偏好」，但只有一種句型是真的：`mute:<X>`／「不要提醒 X」會改變提案；其餘每一行都只是注入對話 system prompt 的文字，秘書的排序、問候、晨報一個字都不會因此改變。使用者寫下「優先處理 uavMonitor」，什麼事都不會發生。

這是個性化最便宜、也最誠實的一步：**先把使用者已經能寫的偏好變成會改變行為的設定**，而不是再多一層推論。

## Decision

### D1 只認明確宣告，不推斷

`core/secretary_profile.py` 從 `secretary_notes.kind = preference` 的內容解析兩種指令，其餘句子原樣忽略（它們仍照 ADR-012 注入對話脈絡）：

| 指令 | 別名 | 效果 |
| :-- | :-- | :-- |
| `優先：uavMonitor、論文` | `priority:`、`本期優先：`、`優先專案：`；分隔用 `、,，;／` 皆可 | 本期優先專案（最多 8 個；同名不分大小寫去重） |
| `語氣：簡潔` | `tone: brief|direct|warm`；`直接／乾脆／terse`、`溫暖／親切`、`精簡／簡短／short` | 問候措辭 |

未知語氣（「語氣：搞笑」）不猜、不套，如實列在 `ignored`。**不從活動、prompt 或對話推斷你的優先或個性**——契約測試禁止模組出現 `prompt_text`／`AIPromptEvent`／`llm_gateway`／HTTP 客戶端。稱呼（`greeting.display_name`）與安靜時段已在設定檔，不在這裡開第二套來源。

### D2 優先只改排序，而且壓過推出來的主線

`apply_priority_boost` 對優先專案的**所有**訊號加 `proactive_secretary.profile.priority_boost`（預設 0.2，上限 0.5；分數不超過 1.0）並附理由「你把這個專案標為本期優先」、標記 `priority_declared`。

兩個刻意的取捨：

- **0.2 > 0.15**（ADR-017 `habit_boost` 預設）：同分的兩個「需要 pull」，你宣告優先的排在活動算出來的主線前面。**你說的勝過我推的。**
- **不跳過模式訊號**：習慣加權刻意不碰 `neglected_active_project`（避免主線自己加自己）；優先加分則相反——一個被冷落的優先專案正是最該浮上來的。

不新增任何提案類型、不新增任何可執行動作；snooze／mute／每專案上限照舊。

### D3 語氣只改措辭，不改任何數字

`compose_greeting(..., tone=)`：`warm` 沿用原本的情境池、`direct` 換成三句「只講下一步」的池子、`brief` 完全不講鼓勵語。問候卡、晨報開頭、Telegram `/today` 共用同一個入口 `build_greeting`，它讀個人檔案決定語氣。標題、事實句、成就清單、行程一句、claim boundary、`stats` 在三種語氣下**逐字相同**（契約測試逐欄比對），LLM 潤飾的事實閘照樣生效。

### D4 個人檔案是偏好筆記的一種讀法，不是第二套資料

- `load_profile()` 每次都從偏好筆記重新解析，依 `created_at` 升冪套用，**後寫的語氣覆蓋先寫的**；沒有任何 cache、沒有新表、沒有 migration。
- 要改就再寫一則偏好、或刪掉那則；因此 `GET /api/v1/secretary/profile` **唯讀**，刻意沒有寫入端點。
- `memory_context()` 在筆記清單前多一行「個人檔案（你宣告的）：本期優先：X、Y／語氣：簡潔」並在收據 `sections` 記 `profile`，讓秘書答題時先看到結論、再看到來源筆記；沒宣告就沒有這一行。

### D5 誠實的 inputs 與失敗隔離

`build_action_proposals` 的 `inputs.profile` 回報 `declared`／`priorities`／`tone`／`priority_boosted`；個人檔案層任何例外隔離成 `{"declared": false, "reason": "error:…"}`，提案清單照出。`build_greeting` 讀不到個人檔案就用 `warm`，卡片照出。

### D6 介面只顯示，不另開表單

01 記憶區面板頂端多一條「個人檔案」列：有宣告就列出優先專案 chips 與語氣徽章；沒有就一行提示「在對話框打『偏好：優先：…』『偏好：語氣：簡潔』」。輸入框的 placeholder 也改成帶這兩個例子。不做專用表單——宣告本來就是一則偏好筆記。

## Alternatives considered

- **用 LLM 從對話／活動歸納「使用者的個性與優先」**：拒絕。那是推測，與 ADR-017 拒絕的理由相同；而且推錯了使用者無從修正。宣告式的東西，使用者寫什麼就是什麼，刪掉就沒有。
- **新表 `secretary_profile` 與專用設定頁**：拒絕。偏好筆記已經有寫入路徑（對話框、Telegram、面板表單）、去重、刪除與注入；第二套資料會與它分歧。
- **語氣進 `config.yaml`**：拒絕。語氣是「你想怎麼被講話」，該和其他偏好放在同一個地方、用同一句話改；設定檔留給部署層面的東西。
- **讓語氣也改變 LLM 潤飾的 prompt**：延後。LLM 潤飾預設關閉；先讓規則版三種語氣穩定，再決定潤飾層要不要跟。

## Consequences

- 沒有新 migration、沒有新危險能力、沒有新的隱私面；唯一新增的讀取路徑是一個唯讀端點。
- 使用者第一次看到的效果：在對話框打「偏好：優先：論文」，01 的論文相關提案立刻排到前面並多一句理由；打「偏好：語氣：簡潔」，問候卡與 `/today` 少掉那句鼓勵。
- `docs/TODO.md` A17 定義實機收據；`core/acceptance.py` 的 `_ITEMS` 同步。
- 契約由 `tests/test_secretary_profile.py` 守門（27 項）：中英別名與去重、未知語氣忽略、只讀偏好筆記且最新語氣為準、加分含被冷落訊號與上限與可關、宣告優先壓過推出的主線、無宣告零改變、失敗隔離、對話脈絡一行與收據、三種語氣事實逐字相同、未知語氣退回溫暖、`build_greeting` 讀偏好且讀不到不壞、API 唯讀（POST 405）、模組不讀 prompt 不呼叫 LLM。
