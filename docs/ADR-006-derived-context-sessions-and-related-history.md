# ADR-006：Derived Context Sessions 與 Related History

- Status：Accepted for Personal Alpha
- Date：2026-08-25
- Scope：P3-4 related history、P3-5 session narrative

## Context

OmniContext 已有可追溯的 AI turn、Git commit、file activity、Open Loop 與 semantic index，但使用者仍需在大量單筆事件中自行重建「那一段時間在處理什麼」。直接新增永久 session table 會把尚未穩定的推論固化成資料事實，也會增加 migration、回填與刪除生命週期負擔。

## Decision

1. Session 採 **derived view**，不新增 schema、不複製原始事件，也不改寫歷史。
2. 只聚合已具有 canonical project 的 AI、Git、file metadata；Window focus 尚無可靠 project identity，因此明確排除。
3. 同專案事件以 configurable inactivity gap（Alpha 預設 45 分鐘）切分。`session_id` 由 project、首筆 `source_ref` 與開始時間雜湊，後續追加同一段事件時維持穩定。
4. Narrative 使用 deterministic template，列出時間範圍、來源計數與最近動作，不呼叫 LLM。
5. Related History 沿用 P3-2 local semantic index。查詢只送至 loopback Ollama embedding endpoint、不寫入 SQLite，輸出必須保留 `source_ref`、trust status 與 score。
6. Alpha threshold 預設 0.50，來自目前 `bge-m3` 本機語料校準；它是 retrieval 起點，不是跨模型通用門檻或真實性標準。

## Trust、Privacy 與 Lifecycle Boundary

- Session 只表示「時間上相近且歸於同一 project 的已觀察事件」，不代表實際工時、連續專注、因果關係、任務一致性或成果品質。
- Similarity 不代表工作真的重複、先前答案正確、資料完整，或舊結論仍可適用。
- Related query 不保存；Browser UI 不會把查詢送至 cloud provider。
- 原始 evidence 的 retention、backup 與刪除契約仍由既有 SQLite lifecycle 管理；derived session 隨查詢重算。

## Consequences

- 優點：零 migration、可立即回查來源、演算法調整不需資料修復、跨平台一致。
- 代價：大量歷史查詢需要即時計算；Window focus 暫不能參與 project session；session 邊界可能與人的主觀工作段落不同。
- 後續：只有在 grouping contract 經長期驗證後，才評估 materialized session cache；任何 P5 proposal 都只能引用 session evidence，不得把推論提升為事實。

## Acceptance

- 相同 project、gap 內事件合併；跨 project 或超過 gap 必須切開。
- Session 成長時 ID 穩定，每個 item 保留 SQLite `source_ref` 與 trust status。
- API/CLI/UI 顯示 inference/claim boundary。
- Related query 不持久化；Ollama/index 不可用時明確降級，不 fallback 到 cloud。
