# ADR-010：可驗證背景 Agent 任務時間

**Status:** Accepted

**Date:** 2026-08-29

**Deciders:** Project owner / OmniContext maintainer

## Context

既有主頁以 `WindowEvent` 計算前景使用時間。這可正確回答「哪個介面位於前景多久」，但無法表示 Claude Code、Claude Desktop local-agent 或 Codex 在視窗縮小後仍持續執行的任務。反過來，前景視窗保持開啟而使用者未操作時，仍可能累積前景時間。

把 AI turn timestamp 或所有 Terminal 時間直接換算成背景工時，會把等待、歷史重掃、一般 shell command 與沒有完成證據的工作混在一起。因此需要一個與前景時間完全獨立、可回查、可拒絕不完整資料的指標。

## Decision

1. 新增 `background_task_runs`，只保存 platform、session、project、start/end timestamp、來源位置與 evidence kind；不重複保存 Prompt、Response、URL 或本機內容。
2. 第一版只採 Claude Code、Claude Desktop local-agent transcript、Codex session log；一般 Terminal、PowerShell、`cmd` 與 browser AI 不納入。
3. 每個任務必須有來源內的 user prompt start timestamp，以及明確 final completion timestamp（Claude `end_turn` 或 Codex `final_answer`）才可計入。
4. 只有 start receipt 時記為 `awaiting_final`；final timestamp 缺失、end 不晚於 start、或 duration 超過可配置上限時，一律不計分鐘數。
5. 每日總數以 completed interval 的聯集計算，平行 Agent 任務不會在總背景時間中 double count；各 platform 明細仍各自保留可觀察時長。
6. Dashboard 使用獨立「BACKGROUND AGENT TASKS」卡片與 `GET /api/v1/background-tasks/today`；不加入前景目標、里程碑、AI turns 或 productivity 宣稱。

## Options Considered

### Option A：將背景任務直接併入前景使用時間

| Dimension | Assessment |
|---|---|
| Complexity | Low |
| Evidence clarity | Low |
| Double-count risk | High |

**Pros:** 只有一個數字。

**Cons:** 使用者注意力與 agent 執行牆鐘時間混在一起，背景平行任務會誇大總數。

### Option B：以所有 AI turn／Terminal interval 推估背景時間

| Dimension | Assessment |
|---|---|
| Complexity | Medium |
| Evidence clarity | Low |
| Source coverage | Broad but unreliable |

**Pros:** 可快速產生較大的時間總數。

**Cons:** 無法證明 start/end、會把一般 shell 工作與停留時間混入。

### Option C：成對 local-agent receipt 的獨立指標（採用）

| Dimension | Assessment |
|---|---|
| Complexity | Medium |
| Evidence clarity | High |
| Source coverage | Intentionally partial |

**Pros:** 可追溯、重掃 idempotent、可拒絕異常時長，且不污染前景統計。

**Cons:** 不會涵蓋未寫入本機 transcript 的工作；沒有 final receipt 的任務不會產生 duration。

## Consequences

- 使用者可同時看見「前景介面時間」與「已驗證背景 agent 執行時間」，但兩者不可相加當作總工時。
- 視窗縮小後，若任務完成並留下成對 local receipt，仍能顯示於背景任務卡片。
- Claude/Codex 的雲端聊天、generic CLI command 與未完成任務維持 `not observed` 或 `awaiting_final`，不補值。
- 未來若要支援新 Agent，必須先定義其本機 start/end receipt 格式與 final marker，再加入 platform allowlist。

## Action Items

1. [x] 新增 schema migration、idempotent task key 與 privacy-preserving receipt model。
2. [x] 將 Claude Code、Claude Desktop local-agent、Codex parser 接入 paired receipt。
3. [x] 新增 Dashboard/API、duration union 與可配置上限。
4. [x] 補 transcript、API、migration、overlap 與異常時長 contract tests。
5. [x] 取得日常使用中的 Codex live completed receipt，確認成對 timestamp 可被本機 API 結算。
6. [ ] 取得 Claude Code 與 Claude Desktop local-agent 的 live completed receipt，確認各安裝版本的 local transcript timestamp 格式。
