# ADR-001：P2.5 可信資料與本機安全邊界

**Status:** Accepted

**Date:** 2026-08-24

**Decider:** 專案維護者

## Context

OmniContext 已能擷取本機 AI transcripts、檔案、Git、視窗與 GitHub 狀態，但目前的資料契約只描述「有事件」，尚未可靠描述事件來源、turn 邊界、回應是否為 final candidate、Open Loop 是否仍有效。Local API 也曾允許 wildcard CORS 並直接回傳完整設定。若在此狀態加入 semantic retrieval 或 autonomous worker，錯誤資料與過寬權限會被放大。

## Decision

在 P3-2 與 P5 之前加入 P2.5 gate：

1. Local API 採 deny-by-default Origin boundary；browser extension 只取得 ingestion capability。
2. AI event 使用 stable turn identity、source provenance 與 response status。
3. File checkpoint 只有在成功解析後才能前移。
4. Open Loop 採明確狀態機，stale/superseded 不得進入 actionable handoff。
5. OS-specific 行為經 platform service 執行，不在 module import 階段修改 OS 狀態。
6. 每項契約先有自動化測試，才允許上層功能依賴。

## Options Considered

### A. 直接繼續 P3/P5

- 優點：短期可快速增加可見功能。
- 缺點：retrieval 會引用不完整回應；autonomous worker 可能依過時 Open Loop 執行；安全問題仍存在。

### B. 先完成 P2.5 gate（採用）

- 優點：建立可回查、可測試、可跨平台延伸的基礎。
- 缺點：短期看得到的新功能較少，需先處理 schema 與相容性。

## Consequences

- P3-2、P3-3 可引用 stable turn 與原始來源。
- P5 proposal 只能使用 `open` 且通過 freshness 規則的事項。
- Browser extension 初次設定需要 pairing/ingest token 流程。
- Release 前仍需正式 migration、backup/restore 與 CI platform matrix。

## Acceptance

以 `docs/TEST_STRATEGY.md` 和 `ROADMAP.md` P2.5 Release Gate 為唯一完成判準；commit 訊息或欄位存在不能單獨視為完成。
