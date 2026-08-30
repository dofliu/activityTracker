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

1. **P5-R1（不受本 ADR 閘門限制，先行）**：proposal 內容接上 LLM（預設本機 Ollama、cloud opt-in），仍為唯讀；deterministic fallback 保留。
2. **P5-R2**：D1–D6 落地 + 首批 L0/L1 內部函式 templates + Web 單鍵批准。驗收：contract tests 覆蓋「自由字串必拒」「無 token 必拒」「L2 未二次確認必拒」「path 逃逸必拒」「timeout/cancel receipt」。
3. **P5-R3**：subprocess dispatcher（Claude Code／Codex CLI，L2）+ 沙盒 cwd + 環境清理。
4. **P5-R4**：Telegram inline 批准（同一 execution token 邊界）、秘書晨報／晚間交接。
5. **P5-R5**：使用者自訂排程任務（僅能排程已註冊 template）、STATUS 自動維護、週／月報 rollup。

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
