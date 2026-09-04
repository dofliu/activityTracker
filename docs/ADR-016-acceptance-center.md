# ADR-016：驗收中心（把「完成判準」變成可重跑的查詢）

- 狀態：Accepted（2026-09-04 實作）
- 關聯：[ADR-001](ADR-001-p2-5-trust-boundary.md) loopback／本機優先邊界、[ADR-008](ADR-008-gated-agent-executor.md) audit receipt、[ADR-009](ADR-009-deskrag-worker-index-lifecycle.md) 檢索 worker 狀態邊界、[docs/TODO.md](TODO.md) A 段、[ROADMAP.md](../ROADMAP.md) §12.3

## Context

程式面已走完 P0–P8 與 ADR-008 全階段。ROADMAP §12.1 自己的結論是：**下一階段的價值來自「把已實作變成已驗證」，不是再加功能。**

但「已驗證」目前的形狀是 [TODO.md](TODO.md) A 段的 13 條文字判準（A1–A13），每條寫著「怎麼做」與「完成判準（收據）」。實際使用時有三個問題：

| 問題 | 現況 |
| :-- | :-- |
| 收據散在各處 | coverage 在一個端點、執行器收據在另一個、報告在檔案系統、記憶區在資料表——確認一項要翻好幾個地方 |
| 「做過沒」靠記性 | 隔幾天回來不記得哪一項走完了；TODO.md 是靜態文字，不會自己變 |
| 容易自我欺騙 | 最危險的失敗模式是「功能有寫、測試有過」就把一項當作驗過——這正是本專案一貫拒絕的推論 |

所以要的不是新功能，而是**把已經寫死在 TODO.md 的判準，變成可以重跑的本機查詢**。

## Decision

### D1 只讀、只查便宜的本機證據

`core/acceptance.py` 對每一項執行一個 probe。probe 能做的事只有三種：SQLite 查詢、讀設定值、看檔案在不在。

**明確不做**：不跑 `git`、不連網、不呼叫 LLM、不載入索引、不寫任何資料表或設定。理由有二——(a) 驗收中心會被反覆重整，自己不能變成負擔；(b) 一個會執行動作的驗收工具，驗的就不再是使用者的實機操作。

契約由測試守門：跑完一份報告後，所有資料表的列數必須與跑之前完全相同；模組原始碼不得出現 `subprocess`／`requests`／`httpx`／`urllib.request`。

### D2 狀態字彙分清楚「沒發生」與「查不到」

| 狀態 | 意義 |
| :-- | :-- |
| `passed` | 找到符合該項判準的本機收據 |
| `partial` | 判準有多項，只有一部分找得到（例如報告產生了但還沒有批准後的 pull 收據） |
| `pending` | 前置齊備但還沒有任何收據 |
| `needs_human` | 判準本來就要人眼比對，機器最多提供旁證 |
| `not_configured` | 功能預設關閉或未設定——**不是失敗**，只代表這條路徑沒開 |
| `runtime_only` | 只有服務執行中的那個程序看得到（CLI 另開程序查不到） |
| `attested` | 使用者親眼確認並署名（見 D4） |

`runtime_only` 是刻意存在的一格：檢索 worker 的狀態是主服務程序內的記憶體（[ADR-009](ADR-009-deskrag-worker-index-lifecycle.md)），CLI 另開一個程序永遠是 cold。把它報成 `pending` 等於宣稱「沒預熱過」，而事實只是「這裡看不到」。同理 `not_configured` 不進失敗數——危險能力預設關閉是設計，不是缺陷。

### D3 查不到就說查不到，不用旁證補值

`passed` 只能來自符合判準的收據本身：

- A2 只認 `rag_chat_messages` 內**雲端 provider 的非錯誤回答**——gateway 失敗時仍會把 `【尚未偵測到 … API Key】` 這類字串存成 assistant message，那是失敗紀錄不是回答；本機 `ollama` 也不算，因為 A2 驗的就是雲端那條路。
- A5（P4.3 對帳實操）永遠停在 `needs_human`：onboarding 的 init／attach／clone **不寫任何本機收據**（新列為 TODO B4），而「掃描對帳」要跑 git 與讀 GitHub 快取，超出 D1 的範圍。可以用「那個資料夾現在變成 repo 了」當旁證，但那無法歸因到使用者做過這個動作——**寧可空著，不要推測**。
- A11／A12 的判準本身是人眼比對（手機上收到的是不是純文字、卡上每個數字對不對得上），機器只回報設定狀態。

### D4 人工署名是另一種證據，永不覆蓋機器判定

`needs_human` 的項目若不能收斂，release gate 就永遠卡著。因此提供一個明確的人工署名：`reports/acceptance/confirmations.json` 記下 `{item_id: {confirmed_at, note, basis: "human_attested_not_machine_evidence"}}`。

規則只有一條，但很重要：**署名只能讓機器沒有判準可查的項目（`needs_human`）收斂成 `attested`；其餘項目一律機器判定優先。** 對 A1 署名不會讓它變綠——ledger 查不到就是 `pending`，署名本身仍如實留著，可以看出「有人宣稱過」與「機器沒找到」同時成立。`passed` 與 `attested` 在彙總裡也分開記帳，不混成一個數字。

寫入沒有外部效果（只是本機一個 JSON 檔），沿用 [ADR-001](ADR-001-p2-5-trust-boundary.md) 的 loopback 邊界即可，不需要 execution token。

### D5 一份報告，兩個入口

- `GET /api/v1/acceptance/checklist`（`runtime=True`，看得到程序內狀態）與 `POST /api/v1/acceptance/confirm`；儀表板「06 系統設定 → 驗收中心」。
- `python main.py verify`：服務在跑就走 live API（跟 `status` 指令同一個模式），否則以 `runtime=False` 本機唯讀查詢，並如實把該類項目標成 `runtime_only`。`--item`、`--json`、`--output`、`--confirm/--unconfirm` 供腳本與紀錄用。

只查部分項目（`--item`）時**不給 release gate**——gate 是整份清單的收斂條件，用一部分項目算出來的 gate 是誤導。

### D6 gate 對齊 ROADMAP §12.3，不自己發明標準

報告附四個 gate，文字與判準直接對應 ROADMAP §12.3：G1（🔴 P0 項目的收據）、G2（預設開啟路徑的收據）、G3（RELEASE_CHECKLIST 一輪＋該 commit 自己的 CI run receipt，屬人工）、G4（STATUS.yaml 的 quality gates 全為 `passed_*`）。

**驗收中心不會改 `release_ready`，也不會改 STATUS.yaml。** 它只回報「照 §12.3 的條件，現在缺什麼」；旗標仍由人在文件裡改。

## Alternatives considered

- **讓驗收中心自己去跑驗收動作**（例如自動發一則 LINE 訊息、自動跑一次 draft）：拒絕。那些動作有外部效果（送訊息、花 CLI 額度、動 worktree），而且自動跑出來的收據證明的是「程式能跑」，不是「使用者的實機環境能用」——正好不是 A 段要的東西。
- **把 A 段判準寫進 STATUS.yaml 的 quality gates，由驗收中心自動更新**：拒絕。STATUS.yaml 是人寫的敘事與邊界說明，自動回填會讓「機器查到的」與「人判斷的」混在同一個欄位。驗收中心只讀 STATUS.yaml 算 G4。
- **為 onboarding 動作補一張收據表（讓 A5 可機器驗）**：合理但屬另一件事（要 migration 與 ADR-011 的邊界討論），已列為 TODO B4，不夾帶在本 ADR。
- **署名可覆蓋任何項目**：拒絕。那等於給了一個「我說可以就可以」的開關，會直接摧毀本專案所有收據的意義。

## Consequences

- TODO.md A 段仍是**唯一的判準來源**；`core/acceptance.py` 的 `_ITEMS` 是它的可執行副本。**改 TODO A 段時要同步改這裡**，反之亦然。
- 新增項目時要一併回答：判準是什麼、便宜的本機查詢能不能查到、查不到的話是 `needs_human` 還是 `runtime_only`。答不出來就代表那條判準本身沒寫清楚。
- 沒有新的 migration、沒有新的危險能力、沒有預設開啟的外部行為；唯一的寫入是使用者按下確認時的一個本機 JSON 檔。
- 契約由 `tests/test_acceptance_center.py` 守門（30 項）：唯讀不留列、模組不得 shell out／連網、各項判準的 passed／partial／pending 分界、`runtime_only` 不得在 CLI 假裝查得到、署名不得覆蓋機器判定、單一 probe 例外不會讓整份報告壞掉、部分查詢不給 gate。
