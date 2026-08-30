# ADR-007：Proposal-only 主動秘書安全邊界

- 狀態：Accepted for Alpha implementation
- 日期：2026-08-26
- 範圍：P5-1 proposal generation only

## Context

OmniContext 已能從本機 AI turns、Git、files、Project State、Open Loops 與 Extension diagnostics 建立可追溯脈絡。下一階段需要把 evidence 轉成「值得使用者檢視的下一步」，但 P2.5 的真實 Browser capture 與完整 coverage 仍未關閉，系統也尚未具備允許自主修改的 executor safety gate。

## Decision

P5-1 Alpha 採用 deterministic、read-only derived view：

- 只讀取本機 Project State、actionable Open Loops 與非敏感 Extension status。
- 不呼叫 cloud LLM；不寫入 SQLite；不修改檔案；不執行 command；不建立 background task。
- 每個 proposal 必須提供 deterministic `proposal_id`、`proposal_type`、`project_key`、`evidence_refs`、`reason`、`suggested_action`、`risk_level` 與 `execution_available`；回應 envelope 提供 `generated_at` 與全域執行邊界旗標。
- Alpha 的 `risk_level` 固定為 `L0_READ_ONLY`，但 `execution_available` 固定為 `false`；UI 只呈現建議與 evidence，不提供批准執行按鈕。
- Extension 未有近期 verified heartbeat 時可產生 setup proposal，但不得把歷史 Browser events 說成目前在線。
- 專案停滯建議只使用 `open` Open Loops；`stale`、`resolved`、`superseded` 不得進入 actionable proposal。

## Acceptance criteria

1. 相同 evidence 與時間 bucket 產生穩定 proposal ID，且輸出順序 deterministic。
2. 所有 proposal 至少有一個可回查的 `source_ref`；缺少 evidence 時不生成建議。
3. API 為 GET/read-only，hostile Origin 仍由既有 local security middleware 拒絕。
4. 回應不包含 prompt/response 全文、token、API key、local source path 或可直接執行的 command。
5. UI 明確標示 `PROPOSAL ONLY` 與「不會自動執行」。
6. Automated tests 與 localhost live smoke 同時通過後，才可標記 `implemented_alpha`。

## Consequences

P5-1 可先驗證「建議是否有用」，不會擴大系統權限。任何 approve/execute、agent dispatch、Git/file mutation、外部 API 或付費操作都留在獨立 P5 executor gate，不能由本 ADR 推導授權。

---

## Addendum 2026-08-27：P5-2/P5-3 executor 嘗試與回退

2026-08-27 的 commit `871ee29` 實作了 `core/agent_dispatcher.py`（SafetyGate + 背景 worker）
與 `POST /api/v1/secretary/proposals/{id}/execute`，並把 `execution_available` 改為 `True`。
本 ADR 的 acceptance criteria 因此失效，`tests/test_proactive_secretary.py` 的兩項
contract test 轉為 failing。

**決議：回退至 proposal-only，executor 另案處理。** 已於 main 移除 dispatcher、
execute/jobs endpoints、`AgentExecutionJob` model 與前端 Execute 按鈕；
完整實作保留於分支 `wip/p5-2-agent-executor`（commit `871ee29`），未刪除。

回退的三個具體理由（供未來 ADR-008 直接引用）：

1. **SafetyGate 未實際分級。** `verify_authorization()` 對 `L0_READ_ONLY`、`L1_ASSIST`、
   `L2_MUTATE` 一視同仁，只檢查 `user_intent == "explicit_approval"`；而 `submit_job()`
   自行把該字串寫死傳入，human-in-the-loop 在此層未被強制。
2. **command 由呼叫端提供。** endpoint 的 `command` 取自 request body 而非 server 端
   產生的 proposal，再送入 `asyncio.create_subprocess_shell()`；proposal_id 僅為標籤，
   不構成任何約束。
3. **執行路徑無憑證要求。** local security middleware 只擋非 loopback 與帶有不允許
   Origin 的請求；不帶 Origin header 的本機請求可直接抵達，且不需 token。
   對照 browser extension 寫入單筆 AI event 需 `x-omnicontext-ingest-token`，
   執行 shell 的門檻反而更低。

**後續**：executor 重啟契約已於 2026-08-31 定稿為 [ADR-008](ADR-008-gated-agent-executor.md)；其階段 P5-R1（預設關閉的 annotate-only LLM advisory 層）已實作，`build_action_proposals` 本身維持本 ADR 的「不呼叫 LLM、不執行」契約不變。

**ADR-008 若要恢復 executor，至少需滿足：**

- endpoint 只接受 `proposal_id`，command 由 server 從白名單模板產生，不接受自由字串。
- 以 `create_subprocess_exec` 傳 argv list 取代 `create_subprocess_shell`。
- 執行路徑要求獨立 token；`L2_MUTATE` 需與 `L0`/`L1` 不同的二次確認機制。
- 本 ADR 的 acceptance criteria 與對應 contract tests 同步更新，不得留在 failing 狀態。
