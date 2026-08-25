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
