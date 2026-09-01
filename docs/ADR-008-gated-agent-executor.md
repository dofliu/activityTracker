# ADR-008：Gated Agent Executor 安全契約

- 狀態：Proposed（設計定稿；實作分階段，任何 mutate 級動作上線前需使用者逐項核准）
- 日期：2026-08-31
- 範圍：P5-2（安全閘門）、P5-3（執行器）；取代 `wip/p5-2-agent-executor`（commit `871ee29`）被 revert 的實作
- 前導：[ADR-007](ADR-007-proposal-only-secretary.md) 及其 2026-08-27 Addendum

## Context

ADR-007 Addendum 記錄了第一次 executor 嘗試被 revert 的三個漏洞：

1. SafetyGate 對 L0/L1/L2 一視同仁，human-in-the-loop 未被強制。
2. command 由 request body 自由字串傳入，直接進 `create_subprocess_shell()`。
3. 執行 endpoint 不需任何 token，門檻比 Extension 寫入單筆事件還低。

本 ADR 定義重啟 executor 的完整安全契約。核心原則：**執行的不是「命令」，而是「已存在提案的白名單動作」**；使用者批准的對象永遠是 server 端已產生、可回查的 proposal，不是呼叫端提供的字串。

## Decision

### D1. Proposal 與 execution 分離

- Execute endpoint 只接受 `proposal_id`（加上 L2 所需的確認參數）；**不接受 command、path、argv 或任何自由字串**。
- Proposal 由 `proactive_secretary` 在 server 端產生，內含 `action_template_id` 與已驗證的參數；`execution_available` 只有在該 template 已註冊且前置檢查可行時才為 `true`。
- 找不到 proposal、proposal 過期（預設 24h）、或 template 未註冊 → 一律 404/409 拒絕，不得 fallback。

### D2. Action Template 白名單（server 端唯一事實）

每個 template 以程式碼註冊，欄位固定：

| 欄位 | 說明 |
| :--- | :--- |
| `template_id` | 穩定識別字，如 `repo_pull_ff` |
| `risk_level` | `L0_READ_ONLY` / `L1_ASSIST` / `L2_MUTATE` |
| `builder` | 純函式：驗證並正規化參數 → 回傳「內部函式呼叫」或 argv list；驗證失敗即拒絕 |
| `preconditions` | 執行前檢查（如 clean worktree、路徑在設定 roots 內、目標存在） |
| `timeout_seconds` | 硬性逾時；逾時即終止並記錄 |
| `receipt_fields` | 允許寫入 audit receipt 的非敏感輸出欄位 |

- 參數驗證含：路徑必須解析後落在 `project_resolution.search_roots` 或 repo sync roots 內；拒絕 `..`、symlink 逃逸與 UNC 特殊路徑；ID 類參數必須存在於 DB。
- **首批 template 全部走內部函式呼叫**（不開 subprocess）：
  - `L0` `generate_handoff(project_key)` — 重用 `core/handoff_engine.py`
  - `L0` `generate_checkpoint(hours)` — 重用 checkpoint pipeline
  - `L1` `repo_fetch(repo_id)`、`repo_pull_ff(repo_id)` — 重用 ADR-011 `core/repo_sync.py` 既有安全動作與其全部前置檢查
  - `L1` `open_loop_transition(loop_id, status, note)` — 重用既有 lifecycle API
- 需要 subprocess 的 template（調度 Claude Code／Codex CLI 等）屬 P5-R3：一律 `asyncio.create_subprocess_exec(argv_list)`，**禁止 shell**；cwd 限制在白名單 roots；環境變數以 allowlist 重建（不繼承使用者 secrets）；stdout/stderr 截斷保存摘要。

### D3. 三級授權閘門（每級都是實際不同的檢查，非標籤）

| 等級 | 觸發條件 | 要求 |
| :--- | :--- | :--- |
| `L0_READ_ONLY` | 唯讀、無外部副作用 | 可由排程自動執行；仍寫 audit receipt |
| `L1_ASSIST` | 可逆的輔助操作（ff-pull、狀態轉換） | Web/Telegram **單鍵批准** + execution token |
| `L2_MUTATE` | 修改檔案、git push、外部付費 API | 批准 + execution token + **二次確認**（回填 server 產生的一次性 6 碼 confirm code，5 分鐘失效）+ 每 template 冷卻時間 |

- `verify_authorization(level, request)` 必須對三級走不同分支並有對應 contract test；把 L2 當 L1 處理視為測試失敗。
- 批准動作本身也寫 receipt（誰、何時、透過哪個介面）。

### D4. Execution token（獨立憑證）

- 新環境變數 `OMNICONTEXT_EXECUTION_TOKEN`（`security.execution_token_env` 可改名），由 `init` 產生；**與 extension ingest token 分開**。
- 所有 execute／approve endpoints 需 `x-omnicontext-execution-token` header；缺失或錯誤 → 401。loopback-only 與 Origin allowlist 檢查照舊疊加。
- Token 永不進入 receipt、log、proposal 或 API 回應。

### D5. Audit receipt（migration 014）

新表 `agent_execution_jobs`：`id`、`proposal_id`、`template_id`、`risk_level`、`argv_or_call`（正規化後）、`status`（queued/running/succeeded/failed/timeout/cancelled/rejected）、`requested_at/started_at/finished_at`、`approved_via`、`exit_code`、`output_digest`（sha256 + 截斷摘要）、`error_code`。

- 一個 proposal 同時間只允許一個 active job（unique partial index）；重複批准回傳既有 job。
- 支援 `POST /jobs/{id}/cancel`；cancel 與 timeout 都是一級狀態，不得靜默。
- Receipt 不含 prompt/response 全文、token、金鑰或使用者路徑以外的本機路徑。

### D6. 失敗封閉與可觀察性

- 任何驗證步驟失敗 → 拒絕並記 `rejected` receipt（含 `error_code`，不含敏感內容）。
- Dashboard 秘書卡片顯示 template、風險等級、將執行的動作摘要與最近 receipt；`07 系統健康` 面板列出最近 executor 活動。
- 全域開關 `secretary.executor.enabled`（預設 `false`）；關閉時 API 回 409，UI 回到 ADR-007 的 `PROPOSAL ONLY` 樣態。

## 實作階段（每階段獨立驗收）

1. ✅ **P5-R1（2026-08-31 已實作）**：`core/secretary_advisor.py` annotate-only advisory 層——LLM 只能為既有 `proposal_id` 附加 `llm_note`／`llm_priority_hint` 與 envelope `summary`，不得增刪改任何 deterministic 欄位；預設關閉（`proactive_secretary.llm_advisor.enabled: false`）、本機 Ollama 優先、prompt 僅含白名單欄位、cloud 使用時 `cloud_llm_used` 如實轉 true；任何失敗（含 LLMClient 備援 markdown 夾帶 payload 的情境）回退 deterministic 且不寫 cache。11 項 contract tests + localhost fallback E2E 通過。
2. ✅ **P5-R2（2026-08-31 已實作）**：D1–D6 落地——`core/agent_executor.py`（白名單 templates：`generate_handoff` L0、`repo_fetch` L1、`open_loop_mark_stale` L1；`repo_pull_ff` 已註冊未映射）、migration 014 `agent_execution_receipts`（新表名避開 wip 遺留的 `agent_execution_jobs`）、`security.execution_authorized` 獨立 token、`POST /proposals/{id}/execute`／`GET /executions`／`POST /executions/{id}/cancel`、Web 批准按鈕（token 只存 sessionStorage）。16 項 contract tests 通過（含：無 token 401、惡意 body 無效、L2 必拒、timeout receipt、active dedup、receipt 摘要白名單、`create_subprocess_shell` 全庫掃描為零）；localhost 完整閉環 E2E：停滯 loop → 提案 → L1 批准 → loop 轉 stale → evidence 改變 → 同一 proposal 自動過期 404。L2 與 subprocess dispatcher 維持不可用（P5-R3）。
3. ✅ **P5-R3（2026-08-31 已實作）**：`core/agent_dispatch.py` subprocess dispatcher——全部子行程走 `create_subprocess_exec(argv)`（禁 shell、stdin 關閉）、環境變數以 allowlist 重建（PATH／HOME 等位置類；**任何 API key / token 不轉發**，contract test 以子行程實測）、cwd 僅接受已探索的唯一本機 repo root、硬性 timeout 逾時即 kill、執行中行程登記 registry 使 cancel 成為一級狀態（cancelled 不被收尾覆寫）。L2 閘門完整落地：獨立開關 `executor.l2.enabled`（預設關閉）、一次性 6 碼 confirm code（sha256 保存、預設 5 分鐘、單次有效、錯一次即作廢）、每 template 冷卻（`cooldown_seconds`，429）。首個 L2 template `agent_draft_plan`：對停滯事項調度本機 agent CLI（`agent_cli.binary/args`，預設 `claude -p`）起草行動計畫，輸出存 `agent_outputs/execution_<receipt>.md`、receipt 只留白名單統計與 digest。execute body 僅新增 `template_id`（選擇已註冊動作）與 `confirm_code` 兩個欄位，D1 不變；首呼叫回 428 + 確認碼。9 項 contract tests（env allowlist 子行程實測、timeout kill、burn-on-wrong-code、expiry、cooldown、cli_exit／cli_not_found honest receipts、跨執行緒 cancel）通過。
4. ✅ **P5-R4（R4a 2026-08-31／R4b 2026-09-01 已實作）**：R4a 秘書晨報——桌面通知與每日入口檔帶 top 建議（唯讀）。R4b `notifiers/telegram_approvals.py` Telegram inline 批准＋晚間交接：回呼走 **getUpdates 長輪詢**（只有 outbound HTTPS，不開 inbound port，loopback-only 邊界不變）；**同一 execution token 邊界**＝批准通道必須先由儀表板上帶 execution token 的請求解鎖（arm），armed 狀態只存記憶體、有 TTL（預設 24h）、服務重啟即失效；雙開關（`executor.enabled`＋`executor.telegram_approvals.enabled`，皆預設關閉）；只處理綁定 chat id 的 update（其他 chat 靜默忽略）；**只批 L0/L1**——按到 L2 立即作廢剛簽發的 confirm code 並導回儀表板；callback 只帶 `ap:<proposal_id>:<template_id>`（D1 不變，proposal 過期即 404）；每次批准寫 `approved_via=telegram_inline` 的 audit receipt。晨報／晚間交接（唯讀盤點：今日推進專案＋未結事項）推播附「✅ 批准」按鈕（未解鎖時只推唯讀清單並如實註明）；`/proposals` 指令可隨時取回建議，其他訊息一律不回應。重複 callback 去重；replay 邊界如實記載於模組 docstring。14 項 contract tests（fake transport／fake executor，不需真實 bot）。
5. ✅ **P5-R5（2026-08-31 已實作）**：`core/scheduled_tasks.py` 使用者自訂排程任務——只能排程 server 註冊的 **L0_READ_ONLY** schedulable template（模組載入即強制，L1/L2 永不可排程，對應 D3「排程自動執行僅限 L0」）；開關疊加（`executor.enabled` ＋ `executor.scheduled_tasks.enabled`，皆預設關閉，關閉時 mutation API 409）；排程設定存 migration 016 `secretary_scheduled_tasks`（daily／weekly＋weekday／monthly＋day 1–28），每次執行寫入同一張 `agent_execution_receipts`（`approved_via=schedule`、`proposal_id=scheduled_task:<id>`，active 唯一索引防重疊）；錯過的排程恢復後只補跑一次（due 以「最近應執行時刻」對「上次執行／建立時間」比較）。首批 schedulable templates：`generate_handoff`（參數需通過白名單驗證、專案必須存在）、`weekly_report_rollup`／`monthly_report_rollup`（`synthesizer/rollup.py`：只彙整已存在的每日摘要，缺日如實列出不推測，LLM 失敗回退 deterministic 並標記 `llm_used=false`）、`status_snapshot_draft`（`core/status_draft.py`：點名 STATUS.yaml `last_updated` 落後觀測活動 ≥7 天的 repo，**草稿只寫 reports/status_drafts，絕不修改使用者 repo**）。管理 API（新增／修改／刪除／立即執行）需 execution token；17 項 contract tests。同輪修正 `cancel_execution` 競態：先確認 dispatcher 已登記行程、提交 `cancelled` 後才 kill，收尾執行緒必然讀到已提交的一級狀態。

## 2026-08-31 Addendum：L2 寫入型 template（agent 實際代辦）

P5-R3 的 `agent_draft_plan` 是唯讀輸出；本 Addendum 定義第一個**會修改使用者
repo 檔案**的 template `agent_apply_plan`，安全契約在 D1–D6 之上再加四條：

- **A1. 兩段式批准**：apply 的前置條件是同專案 24 小時內存在 `succeeded`
  的 `agent_draft_plan` receipt 且其輸出檔仍在。使用者批准的不是抽象的
  「去做事」，而是一份**可先讀過的具體計畫文件**；apply 的 prompt 即該計畫
  全文（截斷上限）＋邊界指令。沒有可引用的計畫 → 不提供此動作。
- **A2. 第三開關**：`executor.l2.allow_write`（預設 `false`）。未開啟時
  寫入型 template 完全不註冊；L2 讀取型（draft）不受影響。三道門
  （token＋單鍵批准＋一次性 confirm code）與冷卻照常疊加。
- **A3. worktree 前置與絕不 commit**：dispatch 前 `git status --porcelain`
  必須乾淨（髒 worktree 一律拒絕，保護使用者未提交的工作；發放 confirm
  code 前先檢查一次，runner 內再檢查一次）。template **永不執行
  commit / push**——agent 改完的檔案以未提交變更留在 worktree，使用者用
  自己的 git 工具檢視、提交或 `git checkout .` 整批還原。
- **A4. 變更可觀察**：執行後再次 porcelain 統計，receipt 白名單欄位含
  `files_changed` 與輸出檔路徑；改動的檔名清單只出現在當次 API 回應
  （使用者當下看），不落 receipt。CLI 寫入權限由其自身旗標約束
  （Claude Code 預設 `--permission-mode acceptEdits`：允許檔案編輯、
  不授予 shell）。

## Acceptance criteria（重啟 executor 的最低門檻，對應 ADR-007 Addendum）

1. Execute endpoint 僅接受 `proposal_id`；任何含自由字串 command 的請求被 422 拒絕並有測試。
2. 全部子行程使用 `create_subprocess_exec` + argv list；程式碼庫內 `create_subprocess_shell` 出現即測試失敗。
3. 執行與批准 endpoints 需獨立 execution token；L2 另需一次性 confirm code。
4. `verify_authorization` 對 L0/L1/L2 有不同行為的 contract tests。
5. ADR-007 的 proposal-only tests 更新為「executor 關閉時行為不變」，不得留在 failing 狀態。
6. 所有 job 產生可回查 receipt；timeout、cancel、rejected 均可觀察。

## Consequences

- 秘書從「只建議」升級為「可在明確授權下代辦白名單動作」，且每一步可回查、可關閉、可回退（`secretary.executor.enabled=false` 即回到 ADR-007 狀態）。
- 白名單模板讓能力擴張變成「一次一個 template 的審查」，而不是開放式 shell。
- 成本：新增 migration 014、execution token 管理與較多 contract tests；此成本即是安全邊界本身。
