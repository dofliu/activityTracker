# ADR-002：Extension Monitor 與每日使用里程碑的介面邊界

**Status:** Accepted

**Date:** 2026-08-24

**Deciders:** Project owner / OmniContext maintainer

## Context

Browser Extension popup 現在同時呈現 local service health 與 ingest token，但使用者也希望從 `127.0.0.1:8765` 查看相同狀態。另一方面，新增的每日介面使用時間必須避免將 AI event 次數誤算為 duration，且不能因 collector 缺資料就顯示 0 小時。

瀏覽器一般網頁無法直接讀寫 Extension 的 `chrome.storage`；若把原始 popup 直接掛到 localhost，token 欄位會失效，也會模糊 Extension 與 dashboard 的安全邊界。

## Decision

1. Extension popup 保留 ingest token pairing，並以 authenticated status probe 驗證 token。
2. localhost 新增 `/extension-monitor` 與主頁 monitor card，只顯示 service、各平台 enabled／event count／last capture，不讀取或顯示 token。
3. 使用時間以 `WindowEvent` 的去重、去重疊前景 interval 為唯一 duration 來源；AI events 只計互動次數。
4. 第一版不建立 materialized daily summary；由 pure aggregation service 即時計算，避免 derived data 漂移。資料量增加後再評估 materialization。
5. 每日 milestone 使用獨立 receipt table，以 `(local_date, milestone_minutes, channel)` unique key 保證 idempotency。
6. 在沒有 continuous coverage ledger 前，usage API 最高只回報 `partial`；collector 不支援或停用則為 `unavailable`。

## Options Considered

### Option A：直接由 localhost 載入 popup.html

| Dimension | Assessment |
|---|---|
| Complexity | Low |
| Security clarity | Low |
| Extension API compatibility | Broken outside Extension context |

**Pros:** 重用現有畫面。

**Cons:** `chrome.storage` 無法使用，使用者會誤以為網頁能完成配對。

### Option B：Dashboard-native monitor + Extension-only pairing（採用）

| Dimension | Assessment |
|---|---|
| Complexity | Medium |
| Security clarity | High |
| Portability | High |

**Pros:** token 不離開 Extension sandbox；localhost 與 popup 各自呈現可驗證狀態。

**Cons:** 需要共用 API 與兩個小型 view。

### Option C：由 localhost 注入 token 到 Extension

| Dimension | Assessment |
|---|---|
| Complexity | High |
| Security clarity | Low |
| Browser compatibility | Low |

**Pros:** 配對步驟較少。

**Cons:** 需要 external messaging/native messaging，權限與攻擊面不符 local-first baseline。

## Consequences

- localhost 可以完整觀察 Extension ingestion，但不能替 Extension 寫入 token。
- Dashboard 顯示的使用時間是可回查的前景時間，不是生產力或真實工時。
- 在 coverage ledger 完成前，即使 collector 目前 healthy 也不顯示 `complete`。
- milestone evaluation 可安全地由 scheduler 重複執行，不會重複通知。

## Action Items

1. [x] 建立 `UsageAnalytics` pure aggregation 與 tests。
2. [x] 建立 milestone receipt model/migration 與 evaluator。
3. [x] 新增 extension status/pairing API 與 `/extension-monitor`。
4. [x] 主頁新增今日使用與 Extension Monitor card。
5. [x] scheduler 接入 milestone evaluation，release template 預設 opt-in。
6. [x] Windows Dashboard/API 與 browser-extension token pairing probe 通過；token 未出現在回應，惡意 Origin 為 403。
7. [ ] 補真實網站 browser event、真實達標 Toast、DST 與 macOS/Linux evidence。
