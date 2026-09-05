# ADR-012：小秘書記憶區（Secretary Memory）

- 狀態：Accepted（2026-09-02 實作）
- 關聯：[ADR-007](ADR-007-proposal-only-secretary.md) proposal-only 邊界、[ADR-008](ADR-008-gated-agent-executor.md) 分級執行器與 L0 排程、[ADR-009](ADR-009-deskrag-worker-index-lifecycle.md) RAG worker 隔離

## Context

小秘書已經有很多「像記憶的東西」：每日摘要與 checkpoint 微摘要、Context Handoff、Repo 同步報告、STATUS 草稿、早晨包收據、提案 snooze。但它們散在檔案與不同資料表裡，**沒有一個地方是秘書回答問題或主動思考時固定會看的**：

- 使用者在對話框問「目前哪個專案最需要注意」時，RAG 只檢索到文件切片，不知道今天的狀態與提案。
- 使用者說過的偏好（「repo push 不用一直提醒」）與決定（「alpha 等 v2 再 merge」）沒有地方放，下次還是被提醒。
- 秘書自己每天從 L0 收據看到的事（「2 個 repo 需要 pull」）沒有留下痕跡，隔天等於沒發生。

需求方的原話是：秘書要有一個「記憶區（大腦）」，不管是使用者主動問、還是秘書主動想，都有同一個參考來源。

## Decision

以三層組成記憶區，一次落地，全部本機：

### D1 筆記表 `secretary_notes`（migration 017）

| 欄位 | 說明 |
| :-- | :-- |
| `kind` | `user_note`（記下來）、`preference`（偏好）、`decision`（決定）、`observation`（秘書自己的觀察） |
| `project_key` | 選填；有值時同專案的提案卡會顯示「你之前記過」 |
| `title` / `body` | 純文字；body ≤ 4000 字 |
| `source` / `source_ref` | 來源（`chat` / `web` / `api` / `morning_pack`）；observation 以 `source_ref` 去重（同一來源同一天只寫一次） |
| `pinned` | 置頂優先進入對話脈絡 |

- 使用者可寫的 kind 只有前三種；`observation` **只能**由秘書的 L0 收據產生（目前：早晨包的 repo 同步計數、STATUS 過期數、失敗步驤），API 拒絕外部寫入 `observation`。
- 每一筆都可單獨刪除；`DELETE /api/v1/secretary/memory?kind=observation` 一鍵清掉全部觀察，使用者筆記不受影響。
- 寫筆記不是 L1 動作（沒有外部效果、不碰任何 repo），沿用 loopback 邊界即可，不需要 execution token。
- 對話框前綴「記下來：」「偏好：」「決定：」（英文 `/note` `/pref` `/decision` `remember:`）直接寫進筆記、不送 LLM；可帶 `@專案` 或 `[專案]` 標記。前後端用同一套規則（`core/secretary_memory.parse_note_command`）。

### D2 既有產物併入 RAG activity 領域

`rag/activity_indexer.py` 除了原有的專案狀態／Open Loop／AI 對話／Git commit，新增：

- `secretary_notes` 全部筆記（`source_type=secretary_note`，`trust_status` 使用者筆記為 `user_stated`、觀察為 `derived_observation`）；
- `activity_micro_summaries` 時段微摘要（已壓縮、不含原文）；
- `exporters.reports_dir` 下**秘書自己寫出的報告**：`handoffs/`、`repo_sync/`、`status_drafts/`，以及根目錄的 `OMNICONTEXT_TODAY.md` 與 `Weekly_Rollup_*` / `Monthly_Rollup_*`。每類只取最新 30 份、每份截到 6000 字；根目錄其他 markdown 一律不讀。

併入透過新的 RAG worker job `activity_sync`（`POST /api/v1/rag/memory/sync`，知識庫分頁「🧠 併入秘書記憶與工作紀錄」）在**獨立 worker 程序**執行——主服務仍不載入 chromadb／fastembed（ADR-009 契約不變）。重跑覆蓋同一批 `activity` 領域切片。

### D3 固定脈絡：對話注入與提案引擎讀筆記

- `memory_context()` 組一段有上限的文字（預設 2500 字，`secretary_memory.chat_context.max_chars`），固定順序：今日狀態（上次做到哪、早晨包一行）→ top 3 提案（含「為什麼是現在」）→ 偏好與決定 → 使用者筆記 → 未過期的觀察（`observation_ttl_days`，預設 14）。`/api/v1/rag/chat` 每次把它接在 system prompt 與檢索切片之間；SSE 多一個 `memory` 事件回傳**收據**（用了幾筆、幾個字、是否截斷、哪幾段），不回傳記憶內容本身。介面在回覆下方顯示「🧠 參考記憶區 N 筆」。
- `GET /api/v1/secretary/memory/context` 回傳與注入內容**完全相同**的文字與收據，介面按「👁 現在記得什麼」即可看到秘書當下的參考來源。
- 提案引擎 `build_action_proposals` 讀偏好筆記：每一行符合 `不要提醒 X` / `mute: X`（X 為 proposal_type 或 project_key）就壓掉對應提案，`inputs.memory_muted` 如實計數；同專案最近一則決定／筆記以 `memory_note` 附在提案上。偏好**只做這一種確定性解析**，不解析成任何會執行的動作。
- 記憶區任何一層故障（資料表鎖死、視圖壞掉、超過 8 秒）一律降級：對話照常回答並在收據標 `reason`、早晨包照常完成並記 `observations_written=0`、提案照常產生並記 `memory_error`。

## Alternatives considered

- **只靠 RAG 檢索既有報告**：拒絕作為唯一方案。檢索是「問到才有」，無法保證今日狀態與偏好每次都在；也無法讓使用者刪除秘書「記錯」的東西。
- **讓 LLM 自行摘要對話成長期記憶**：拒絕。會把 prompt／response 原文或推測寫進持久層，違反本專案「每個聲明附收據、不存原文」的文化；秘書觀察只能由確定性的 L0 收據推出。
- **偏好寫成可執行規則（例如自動 snooze、自動 pull）**：拒絕。偏好只影響提案的呈現，執行仍走 ADR-008 的批准與 token。

## Consequences

- 使用者多一個「記下來」入口（對話前綴、01 記憶區面板、API），且提案與回答會引用它；秘書觀察可一鍵刪除，記憶區永遠可審。
- 對話 system prompt 多至多 2500 字；本機 Ollama 小模型的 context 佔用需在 USAGE 提示可調小或關閉（`secretary_memory.chat_context.enabled: false`）。
- 新增一張表（migration 017，append-only），`secretary_notes` 進入備份範圍。
- 契約由 `tests/test_secretary_memory.py` 守門：CRUD 與去重、前綴解析、早晨包→觀察且故障隔離、提案 mute 與 memory_note、脈絡順序／上限／收據、API 權限、RAG 切片白名單、worker job 註冊。

## 2026-09-05 Addendum A：每日工作誌（讓大腦裡有「你做了什麼」）

### Context

原決策的 `observation` 只有一個來源：早晨包收據。那些觀察講的是**秘書自己輸出了什麼**（幾個 repo 需要 pull、STATUS 過期幾個、Handoff 寫了幾份），不是**你做了什麼**。

使用者指出這個落差：在 Antigravity 下「幫我更新同步這個專案 本地雲端更新」、在 OmniContext 介面測試、編修論文——這些 OmniContext 其實都採集到了，卻沒有一則進入秘書的大腦。結果是問它「上週我在 uavMonitor 上做了什麼」，它答不出來；主動提案也只能看當下狀態，沒有「這個人平常都在做什麼」的底。

### Decision

新增 L0 唯讀 template `daily_digest`（`core/activity_digest.py`）：把某一天的活動 **reduce** 成記憶區觀察。

1. **不新增任何資料類別。** 「你問 AI 什麼」這件事 `activity_micro_summaries`（migration 015）本來就在存——checkpoint 時段由本機 LLM 把事件（含 prompt，各裁到 120 字）壓成 ≤600 字摘要。工作誌只是把當天的**微摘要**與**可回溯計數**組合起來，因此本 ADR「不存 prompt／response 原文」的邊界原封不動（契約測試驗證：seed 的 prompt 原文不得出現在任何筆記裡）。
2. **不呼叫 LLM。** 它是 reduce 不是重新生成：微摘要是既有的，計數是查詢。收據固定 `llm_used: false`，契約測試也禁止模組原始碼出現 `llm_gateway`／HTTP 客戶端／`subprocess`。沒有微摘要（本機 LLM 沒開）就只寫計數，並在正文如實寫「以上只有計數」——不編故事。
3. **寫的是 observation，沿用既有生命週期。** 每天寫一則日層筆記（`source_ref=daily_digest:YYYY-MM-DD`）＋每個有實質活動的專案一則（`daily_digest:YYYY-MM-DD:<project>`，帶 `project_key` 讓「我在 X 做了什麼」可回溯）。source_ref 去重 ⇒ 同一天重跑不重複寫；一鍵刪除與 TTL 與其他觀察完全一致。只有 1 筆事件的專案不佔一則記憶。
4. **沒歸戶的活動不猜專案**：`project_tag`／`project_name`／`repo_name` 為空的事件只進日層計數，不會被塞進某個專案。
5. **接進既有流程**：獨立可排程，也是早晨包的第四步（早上跑時「昨天」已是完整的一天）。寫進去的觀察由 `memory_context()` 自動注入每次對話的 system prompt——這正是使用者要的「他才是真的清楚我每天做了什麼」。
6. **可關**：`proactive_secretary.daily_digest.enabled`（預設 true——它唯讀、不連網、不呼叫 LLM、產出可刪）；`secretary_memory.enabled` 關閉時一律不寫。

### Consequences

- 沒有新 migration、沒有新危險能力、沒有新的隱私面：所有輸入都是已經在本機資料庫裡的東西。
- 早晨包的 `errors` 多一個可能的步驟名 `daily_digest`。這是刻意的：工作誌是**讀資料**的步驟，資料庫真的讀不到時應該如實記錯讓使用者知道那天沒寫成——與「記憶層寫入失敗要靜默」是兩件不同的事。
- 邊界照舊如實標示：只彙整採集器看到的活動，沒被採集到的工作不代表沒做。
