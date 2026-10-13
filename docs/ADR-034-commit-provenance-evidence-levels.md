# ADR-034：commit 的來源歸因——三種證據分開記帳

- 狀態：**Accepted**（2026-10-09）
- 對應：TODO **F1**、[ROADMAP §15](../ROADMAP.md) repo 管理強化
- 相關：[ADR-006](ADR-006-derived-context-sessions-and-related-history.md)（時間鄰近與相似度的既有 claim boundary）、[ADR-005](ADR-005-local-semantic-index-and-ask.md)（semantic index）

---

## Context

`myGitQuickView` 讓 LLM 讀 commit message 去**猜**它出自哪個開發工具。本專案手上有更好的原料：
commit、AI turn、檔案事件都在同一個 SQLite、同一套專案身分底下。但查證確認目前**沒有任何東西**
把兩邊接起來——`git_activity_events` 與 `ai_prompt_events` 之間零欄位、零關聯表、零查詢。

動工前量過的四件事：

1. **原料已經在了。** `watchers/git_watcher.py` 存的是 `commit.message.strip()`（全文，含 trailer），
   不是 summary。HTTP ingest（`POST /api/v1/events/git`）也是原樣存。
2. **本 repo 自己就是現成的語料。** `origin/main` 223 個 commit、162 個非 merge commit 裡
   **116 個**帶 `Co-Authored-By:` 或 `Claude-Session:` trailer；`Co-Authored-By` 的 email 全部是
   `noreply@anthropic.com`。另外 46 個沒有任何 trailer——它們可能也是 AI 協作寫的，**但 commit 本身沒說**。
3. **git 自己的 trailer 解析會誤判。** `git log --format='%(trailers:only)'` 在本 repo 把
   `docs:` 認成 trailer 8 次、`refactor:` 5 次——conventional commit 的前綴長得跟 `Key: value` 一模一樣。
   所以「最後一段長得像 trailer」不能當證據，**只能認白名單上的 key**。
4. **`Claude-Session` 的 id 與本機逐字稿的 id 不在同一個空間。** trailer 的值是
   `https://claude.ai/code/session_…`（雲端 session），而 `ai_prompt_events.conversation_id`
   對 Claude Code 存的是本機逐字稿的 `sessionId`（UUID）。雲端 session 的逐字稿根本不在使用者機器上。
   所以大多數 `Claude-Session` trailer **在本機會是 `unresolved`**——這是事實，不是 bug。

## Decision

### D1. 三種證據，三個欄位，永不合併

輸出裡每一種證據各佔一個區塊，各自帶自己的 claim boundary。**不算總分、不排名、不挑「最可能的那個」。**

| 等級 | 依據 | 能說什麼 | 不能說什麼 |
| :--- | :--- | :--- | :--- |
| `trailer` | commit message 最後一段裡、白名單上的 trailer key | 「這個 commit **自己宣告**有 AI 共同作者／出自某個 session」 | 哪幾行是 AI 寫的；宣告是否屬實 |
| `temporal_only` | 同一專案、commit 之前一段時間內的 AI turn（沿用 ADR-006 的專案歸戶） | 「這段時間內同專案有這些 AI turn」 | 因果；這個 commit 用到了那些 turn |
| `similarity_only` | 拿 commit message 去 semantic index 撈相似的 AI turn | 「這些 turn 的文字跟 commit message 相近」 | 真實性、同一件工作、內容被採用 |

`temporal_only` 帶 ADR-006 session 的 claim boundary、`similarity_only` 帶 related history 的
claim boundary——**沿用既有字串**（抽成 `core/context_memory.py` 的模組常數，原處引用同一份），不另寫一套說法。

### D2. 只有 trailer 能讓 `ai_declared` 成立

頂層只有一個布林：`ai_declared`。它**只**看 `trailer` 區塊裡有沒有被白名單認成 AI 的 trailer。
時間鄰近一百筆、相似度 0.99，`ai_declared` 仍是 `false`。欄位名刻意叫 *declared* 不叫 *generated*：
它記的是 commit 說了什麼，不是我們判斷了什麼。

### D3. trailer 白名單（擴充要有實例）

只認 commit message **至少兩段**時的**最後一段**（只有一段就是 subject，`docs: x` 不是 trailer），
且該段每一行都是 `Key: value`（或續行）。key 比對不分大小寫。

| key | 條件 | 判定 |
| :--- | :--- | :--- |
| `Claude-Session` | 任何值 | AI；`tool=claude_code`；取 URL 最後一段當 session id |
| `Co-Authored-By` | email 是 `noreply@anthropic.com` | AI；`tool=claude` |
| `Co-Authored-By` | 其他 email | 記下來，但**不算 AI**（`kind=co_author`，人或未知工具） |
| 其他任何 key | — | 不記錄 |

**其他工具（Codex、Copilot…）目前不在表上**：本 repo 與測試語料裡沒有它們的 trailer 實例，
照著印象加一條就是在猜。要加的那一輪，附一個真實 commit 的 trailer 當收據。

### D4. session 只認精確相等

`Claude-Session` 取出的 id 只跟 `ai_prompt_events.conversation_id` 做**字串完全相等**比對。
對得到就列出那些列的 `source_ref`（`resolution=resolved`），對不到就 `resolution=unresolved` 並說原因。
**不剝前綴、不模糊比對、不拿時間去補**——補了就是把 `temporal_only` 偷渡進 `trailer`。

### D5. 衍生視圖，不存檔

與 ADR-006 相同：每次查詢重算，**不加欄位、不加表、不跑 migration**。理由：歸因規則（尤其 D3 白名單）
一定會再改，存成資料就得回填；而回填等於替歷史資料補上當時沒有的判斷。git 側的「指標」就是
`source_ref=git_activity_events:<id>` 本身——它已經可定址了。

### D6. 相似度預設不跑

`similarity_only` 需要本機 Ollama embedding；預設 `status=not_requested`，呼叫端明示
`include_similarity=true` 才查。查不到服務時 `status=unavailable` 並寫原因，**不 fallback 到雲端**（ADR-006）。
只回 `source_type=ai_turn` 的結果——commit 跟自己或別的 commit 相似不是歸因。

### D7. 入口

一條唯讀端點 `GET /api/v1/git/commits/{event_id}/provenance`（`core/api/activity.py`），
邏輯在 `core/commit_provenance.py`。前端與 MCP tool 不在這一輪，等這份輸出被用過再決定形狀。

## Consequences

- 本 repo 這種「每個 commit 都蓋章」的使用方式下，`trailer` 會是最常見的等級；但**雲端 session 寫的 commit
  在使用者機器上幾乎都會是 `unresolved`**——宣告存在、指不回一列。這是誠實的空白，不要拿 `temporal_only` 去填。
- 沒蓋章的 commit（本 repo 有 46 個）永遠不會被標成 AI。漏報是刻意的代價：寧可少說，不要多說。
- 白名單是一張要維護的表；新工具出現時會先「看不到」，直到有人帶著實例加一列。

## Acceptance（契約測試 `tests/test_commit_provenance.py`）

- 沒有白名單 trailer 的 commit，即使同時有時間鄰近與高相似度的 AI turn，`ai_declared` 仍為 `false`。
- 三個區塊的 `level` 值與 claim boundary 各自固定，互不出現在別的區塊。
- `temporal_only`／`similarity_only` 的 claim boundary 與 `core/context_memory.py` 的既有常數是同一個物件。
- 單段 subject（`docs: x`）、conventional prefix 不被認成 trailer；非 anthropic 的 `Co-Authored-By` 不算 AI。
- `Claude-Session` 只在 `conversation_id` 完全相等時 resolved。
