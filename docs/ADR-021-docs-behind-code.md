# ADR-021：文件落後程式——把「檢視同步狀態，然後更新文件」變成秘書會提的一張卡

- 狀態：Accepted（2026-09-06 實作）
- 關聯：[ADR-008](ADR-008-gated-agent-executor.md) 分級執行器（D1–D6 ＋ Addendum A 兩段式 L2 寫入）、[ADR-011](ADR-011-safe-local-repository-sync.md) 同步報告、[ADR-012](ADR-012-secretary-memory.md) 工作誌、[ADR-016](ADR-016-acceptance-center.md) 狀態字彙、[ADR-019](ADR-019-secretary-desk-home.md) 桌面焦點

## Context

使用者指出自己最常手打的一句指令：

> 「請幫我檢視目前專案的同步狀態，然後更新專案說明文件、使用文件與規劃文件。」

他同時以為秘書做不到，因為「秘書沒辦法把指令傳給桌面版的 AI」。**這個前提只有一半對。** 秘書確實碰不到 GUI 應用（Antigravity IDE、Claude Desktop），但 ADR-008 的 L2 執行器（`core/agent_dispatch.py`）早就會調度**本機 agent CLI**（`claude -p …`、`codex exec …`），而且帶著完整的安全契約：argv 白名單、禁 shell、cwd 限已探索 repo、環境變數 allowlist（API key 不轉發）、逾時、可取消、收據、批准＋一次性確認碼。

把那句指令拆開來看，缺的東西很小：

| 指令的一半 | 現況 |
| :-- | :-- |
| 檢視同步狀態 | **早就有，而且不需要 AI**：L0 唯讀 `repo_sync_report`，早晨包每天跑 |
| 更新說明／使用／規劃文件 | 能力有（兩段式 L2 會改檔），但**沒有觸發**——`_DRAFT_PLAN_TYPES` 只認 `stalled_open_loop`／`unfinished_recent`，沒有任何訊號告訴秘書「現在該提更新文件」 |

所以這一輪要做的不是新能力，是**那個訊號**，加上一個文件專用的 prompt。

## Decision

### D1 訊號只比兩個時間，不判斷文件內容

`core/docs_freshness.py` 用兩張既有事件表：

- **文件基準**：`file_activity_events` 裡該專案最近一次被改到的**文件檔**時間。文件檔認檔名前綴（`README`／`USAGE`／`ROADMAP`／`STATUS`／`CHANGELOG`／`INDEX`／`NEXT_SESSION`／`ADR-`／`TODO`／`CONTRIBUTING`／`RELEASE_CHECKLIST`）或 `docs/` 底下的 `.md`／`.rst`。
- **之後的程式活動**：`git_activity_events` 裡該 repo 在那個時間**之後**的 commit 數與訊息第一行。

`commit 數 ≥ min_commits`（預設 8）**且** 文件已 `≥ min_days`（預設 2）天沒動 → 一張 `docs_behind_code`。最多三張、依分數排序（0.55–0.8，刻意低於 `priority_drift`；若該專案是你宣告的優先，ADR-018 的加分照樣落在它身上）。

**它不看文件寫了什麼。** 標題就是那句可回溯的事實：「uavMonitor 的文件落後了：文件最後一次更新後又有 12 個 commit」。

### D2 沒有文件異動紀錄的專案一律不提

這是本 ADR 最重要的邊界。一個 repo 完全沒有文件異動事件，可能是「它真的沒有文件」，也可能是「文件目錄不在採集範圍」——**機器分不出來**，所以寧可不說，只在 `inputs.docs_freshness.skipped_no_doc_baseline` 如實列出被跳過的 repo。與 [ADR-016](ADR-016-acceptance-center.md) A5 拒絕用旁證推論同一個判斷。

### D3 動作接既有的兩段式 L2，不開第二條寫入路徑

`_DRAFT_PLAN_TYPES` 加入 `docs_behind_code` 就好，因此它自動繼承 ADR-008 Addendum A1 的**兩段式批准**：

1. **`agent_draft_plan`（L2 唯讀輸出）**：agent 讀 repo，產出一份「文件更新計畫」——README／USAGE／ROADMAP／STATUS 各該補什麼、改什麼、指到檔案與段落。prompt 由 server 端組，事實區塊是 `docs_facts`（見 D4），並明寫「**不要編造沒有依據的進度**、只輸出計畫、不要改檔」。
2. **你讀過那份計畫再批准 `agent_apply_plan`（L2 寫入）**：沿用既有約束——只能改這個 repo 內的檔案、**不准 `git commit`／`push`**、執行前後都驗 worktree 乾淨（要看得出 agent 改了什麼）、收據記 `files_changed`。

三道門（`executor.enabled` → `l2.enabled` → `l2.allow_write`）與一次性確認碼一個字都沒改。**版本控制的決定權留在使用者手上**：秘書把草稿改好，commit 由你按。

### D4 事實由 server 準備，不叫 agent 自己去猜

`docs_facts` 是 server 端組出的事實區塊（上限 2400 字，逐段截斷）：專案名、文件最後異動時間與檔名、那之後的 commit 數與最新時間、最近 12 筆 commit 訊息第一行、以及該專案最近一則工作誌／回顧觀察（ADR-012 已壓縮過的計數，**不含 prompt 原文**）。

commit 訊息是使用者自己寫的字，本來就存在 `git_activity_events`，因此 ADR-012「不存 prompt／response 原文」的邊界不變。提案由 `_find_live_proposal` 在 server 端重建，所以 `docs_facts` 永遠是 server 產生的——**呼叫端無法注入 prompt 內容**（D1 的老規矩）。

### D5 秘書自己不呼叫 LLM

訊號層完全確定性：不讀 prompt 內容、不呼叫 LLM、不寫任何資料（契約測試禁止模組出現 `llm_gateway`／HTTP 客戶端／`subprocess`／`session.add`）。唯一會用到 AI 的是你批准的那兩次 CLI 調度，而它們消耗的是你自己的 CLI 額度，收據裡如實記下 binary 與 exit code。

### D6 順手修掉一個真實的 UX 缺陷

同一輪修掉使用者實際踩到的坑：他用 `init --show-token` 拿到 token，貼進去卻被說「代碼錯誤」。根因是**設定檔是 process 啟動時載入一次的 singleton**——`init` 是另一個 process，正在跑的 server 不知道新 token，於是 `execution_authorized` fail-closed 拒絕**任何** token。原本的 401 訊息（"execution token is missing or invalid"）說不出這件事。

改為區分兩種情況：server 端完全沒有載入 token 時，訊息直接說「設定在啟動時讀取一次，請重啟服務或到設定頁按一次儲存；若設過 `OMNICONTEXT_EXECUTION_TOKEN` 環境變數，服務會以它為準」；token 不符才維持原訊息。前端改為顯示 server 的說明而不是自己那句「無效，請重試」。**安全性不變**（仍 fail-closed、仍 constant-time 比較），只是把診斷資訊還給使用者。

## Alternatives considered

- **為文件更新開一個單段式 L2 template**（直接改檔、不先起草）：拒絕。ADR-008 Addendum A1 要求所有 L2 寫入兩段式，理由正是「使用者批准的不該是抽象的『去做事』，而是一份可先讀過的計畫」。文件更新更需要這一條——LLM 最容易在文件裡寫出沒發生的進度。
- **讓 agent 自己去跑 `git log` 找出改了什麼**：拒絕。事實由秘書準備才可回溯、可截斷、可測；讓 agent 自由探索等於放棄 prompt 的 server 端唯一事實原則。
- **用檔案系統 mtime 判斷文件新舊**：拒絕。那會繞過採集層、跨平台行為不一致，而且無法回溯到某一筆事件。用 `file_activity_events` 雖然受採集範圍限制，但那個限制是**可以誠實講出來的**（D2）。
- **把「檢視同步狀態」也丟給 agent**：拒絕。`repo_sync_report` 是既有的 L0 唯讀動作，已經每天在跑；把它交給消耗額度的 CLI 是退步。
- **秘書自動 commit 文件變更**：拒絕。ADR-008 Addendum 的既有約束就禁止 agent 碰版本控制，本 ADR 不放寬。

## Consequences

- 沒有新 migration、沒有新危險能力、沒有新的隱私面；新增一種提案類型與一個訊號模組，寫入路徑仍是既有那一條。
- 使用者第一次看到的效果：某個 repo 連續 commit 但文件沒跟上時，01 桌面出現「X 的文件落後了：…又有 N 個 commit」，按下去先拿到一份可讀的文件更新計畫，讀過再批准改檔，最後自己 `git diff` 檢視並 commit。**那句他每天手打的指令，變成一張卡兩次批准。**
- `docs/TODO.md` A20 定義實機收據；`core/acceptance.py` `_check_a20` 同時回報「落後幾個 commit」與 L2 三道門的狀態（門沒開就是 `partial`，不假裝可用）。
- 契約由 `tests/test_docs_freshness.py` 守門（25 項）：文件檔判定 12 種、基準取最新且程式碼不算、commit 計數與訊息第一行、觸發與事實區塊、**沒有文件紀錄不提**、兩個門檻各自不足不提、門檻可設與可關、上限三張依分數排序、事實截斷、模組不讀 prompt 不呼叫 LLM 不寫資料、引擎卡片與 `inputs`、mute 與失敗隔離、接上既有兩段式 L2 且 prompt 分流、prompt 內事實截斷、A20 四種狀態、A20 不寫資料。
