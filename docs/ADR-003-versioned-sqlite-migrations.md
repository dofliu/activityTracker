# ADR-003：Append-only SQLite Versioned Migration

**Status:** Accepted

**Date:** 2026-08-24

**Deciders:** Project owner / OmniContext maintainer

## Context

OmniContext 目前在 `Database.init_db()` 以 table introspection 補欄位與 index。這能支援單一開發環境，但無法回答目前 schema version、哪些步驟已執行、既有 migration 是否被改寫，也無法阻止舊版程式開啟較新的資料庫。P6 wheel/sdist 與 upgrade smoke 不能建立在這種不可回查的狀態上。

資料庫包含私人 AI 對話與檔案路徑，migration 必須 fail-closed、保留原資料，且不能把正式 DB 當測試場。

## Decision

採用 application-owned、append-only migration registry：

1. `core/migrations.py` 保存遞增且不可重排的 migration definitions。
2. `schema_migrations` 記錄 `version / name / checksum / applied_at / duration_ms`。
3. 每個 pending migration 使用獨立 transaction；只有成功完成才寫入 receipt row。SQLite DDL 失敗時不宣稱所有 schema side effects 均可 rollback。
4. 既有有資料 DB 在第一個 pending migration 前，使用 SQLite Online Backup API 建立 verified backup。
5. Fresh／unversioned DB 先依目前 SQLAlchemy models 補齊 baseline，再以 idempotent migrations 建立 history；已有 version receipt 後不得再以 `create_all` 繞過 registry。
6. Migration checksum mismatch、重複版本、未知較新版本或執行失敗時拒絕繼續啟動。
7. 舊 migration 不修改；任何 schema 變更只能新增下一個版本。
8. `migration-status` 為 read-only CLI，可回報 current/latest/pending 與 checksum 狀態。

## Options Considered

### Option A：維持 ad-hoc introspection

| Dimension | Assessment |
|---|---|
| Implementation cost | Low |
| Upgrade traceability | Low |
| Failure auditability | Low |
| Packaging readiness | Low |

**Pros:** 程式碼少，現有個人環境可繼續啟動。

**Cons:** 無版本、無 checksum、無 downgrade/newer-schema guard，無法證明升級路徑。

### Option B：導入 Alembic

| Dimension | Assessment |
|---|---|
| Implementation cost | Medium |
| Ecosystem maturity | High |
| Packaging complexity | Medium |
| Current project fit | Medium |

**Pros:** 標準工具、revision graph 與 CLI 完整。

**Cons:** 現階段只有 additive SQLite schema，導入額外 runtime/tooling 與 migration environment 的成本較高。

### Option C：Application-owned append-only registry（採用）

| Dimension | Assessment |
|---|---|
| Implementation cost | Medium |
| Upgrade traceability | High |
| Packaging complexity | Low |
| Future replacement cost | Medium |

**Pros:** 不新增 runtime dependency；可直接整合現有 online backup、SQLite 與 startup flow。

**Cons:** 需自行維護 migration discipline；若未來出現 branching/downgrade 需求，應重新評估 Alembic。

## Trade-off Analysis

目前需求是 linear、additive、single-user SQLite upgrade，不需要多人維護 revision graph。Application-owned registry 足以建立 version、checksum、backup 與 fail-closed contract，同時避免在 release baseline 前引入較重的 packaging surface。若日後需要多分支 migration、downgrade 或多資料庫 backend，Option C 不應被擴張成自製 Alembic，屆時改採成熟 migration framework。

## Consequences

- 啟動會在 schema upgrade 前產生額外 backup；這是刻意的資料安全成本。
- Fresh install 與 legacy upgrade 都會得到相同的 latest schema version。
- 舊版程式不得開啟含未知較新 migration 的 DB。
- Migration history 只證明 schema step 成功，不代表資料語意正確。
- SQLite DDL 失敗可能留下部分 schema；此時缺少 receipt 會阻止啟動，既有資料庫以 pre-migration backup 作復原依據。
- Restore drill、wheel/sdist upgrade smoke 可引用 migration version 與 receipt。

## Action Items

1. [x] 建立 migration registry、history table 與 checksum/newer-version guards。
2. [x] 將現有 AI provenance、Open Loop lifecycle、checkpoint、milestone schema 收斂為 baseline migrations。
3. [x] 啟動流程接入 pre-migration verified backup。
4. [x] 新增 read-only `migration-status` CLI。
5. [x] 通過 fresh、legacy、idempotent、checksum mismatch、newer schema、failed migration 與 create-all bypass guard tests。
6. [x] verified backup copy upgrade + restore drill 通過後，才執行 live upgrade；2026-08-25 heartbeat receipt migration 後 live DB 現為 5/5。
