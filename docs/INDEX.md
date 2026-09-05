# 📚 OmniContext 文件總覽（Documentation Index）

> 最後整理：2026-09-04。本頁是整個專案文件的入口地圖；新增文件時請同步更新此頁。

## 我該從哪裡開始？

| 你想做的事 | 請看 |
| :--- | :--- |
| **接手上一個開發 session、繼續往下做** | [NEXT_SESSION.md](NEXT_SESSION.md) —— 現況、等待中的收據、下一步候選、環境備忘 |
| **看還有什麼待辦、已知問題** | [TODO.md](TODO.md) —— 待辦清單（含完成判準）、技術債、功能候選 |
| **查實機收據做到哪了** | 儀表板「06 系統設定 → 驗收中心」或 `python main.py verify` —— TODO A 段每一項的本機收據現況（[ADR-016](ADR-016-acceptance-center.md)） |
| 快速了解專案是什麼、能做什麼 | [README.md](../README.md)（繁中）/ [README_en.md](../README_en.md)（English） |
| 安裝、Extension 配對、日常操作、備份與故障排查 | [USAGE.md](USAGE.md) —— **使用手冊** |
| 了解目前開發到哪 | [ROADMAP.md](../ROADMAP.md) §11 成果紀錄 + [STATUS.yaml](../STATUS.yaml) |
| 了解下一階段方向與取捨 | [ROADMAP.md](../ROADMAP.md) §12「下一階段規劃」 |
| 了解產品定位與「不宣稱什麼」的證據邊界 | [PRODUCT_POSITIONING.md](PRODUCT_POSITIONING.md) |
| 修改架構前先看相關決策 | 下方 ADR 一覽 |
| 發佈前檢查 | [RELEASE_CHECKLIST.md](RELEASE_CHECKLIST.md) |

## 入門與使用

| 文件 | 說明 |
| :--- | :--- |
| [../README.md](../README.md) | 繁體中文主說明：特色、快速開始、CLI 指令、設定檔、隱私邊界 |
| [../README_en.md](../README_en.md) | English documentation（與繁中版對應） |
| [USAGE.md](USAGE.md) | **使用手冊**：安裝初始化、Git 同步中心、Extension 安裝配對與 live 驗證、使用時間與里程碑、常用操作（semantic index / DeskRAG / 秘書建議 / 快照）、備份與 migration、平台能力、FAQ |
| [../config.example.yaml](../config.example.yaml) | 設定檔範本（`main.py init` 會據此建立本機 `config.yaml`） |

## 規劃與現況

| 文件 | 說明 |
| :--- | :--- |
| [../ROADMAP.md](../ROADMAP.md) | P0–P8 開發規劃與**成果紀錄**（已完成的事寫在這裡） |
| [TODO.md](TODO.md) | **待辦清單**：等待中的使用者側收據、已知問題與技術債、功能候選；每項都有完成判準 |
| [../STATUS.yaml](../STATUS.yaml) | 機器可讀的現況快照：feature 清單、evidence receipts、quality gates、known blockers |
| [PRODUCT_POSITIONING.md](PRODUCT_POSITIONING.md) | 產品定位：跨 AI、應用與 Repository 的個人工作脈絡層，以及能力／證據邊界 |

## 架構決策紀錄（ADR）

> ADR 記錄「為什麼這樣設計、邊界在哪裡」。

| 編號 | 標題 | 主題 |
| :--- | :--- | :--- |
| [ADR-001](ADR-001-p2-5-trust-boundary.md) | P2.5 可信資料與本機安全邊界 | API 安全、ingestion provenance、資料可信度 |
| [ADR-002](ADR-002-extension-monitor-and-usage-milestones.md) | Extension Monitor 與每日使用里程碑的介面邊界 | Extension 診斷頁、使用時間呈現 |
| [ADR-003](ADR-003-versioned-sqlite-migrations.md) | Append-only SQLite Versioned Migration | Schema migration、checksum、fail-closed |
| [ADR-004](ADR-004-packaged-runtime-layout.md) | Wheel/SDist Packaged Runtime Layout | 安裝後的 application home 與 assets |
| [ADR-005](ADR-005-local-semantic-index-and-ask.md) | Local Semantic Index 與 `omni ask` | 本機 embeddings、retrieval、引用 |
| [ADR-006](ADR-006-derived-context-sessions-and-related-history.md) | Derived Context Sessions 與 Related History | 衍生工作階段、相似歷史 |
| [ADR-007](ADR-007-proposal-only-secretary.md) | Proposal-only 主動秘書安全邊界 | 秘書只提案不執行的契約（P5-2 executor 曾實作後 revert 回此契約） |
| [ADR-008](ADR-008-gated-agent-executor.md) | Gated Agent Executor 安全契約（已實作至 P5-R5） | 白名單 template、三級閘門、confirm code、subprocess 沙盒、audit receipt、L0 自訂排程 |
| [ADR-009](ADR-009-deskrag-worker-index-lifecycle.md) | DeskRAG worker 索引生命週期 | RAG 索引 worker 隔離、資源與刪除邊界 |
| [ADR-010](ADR-010-verified-background-agent-task-time.md) | 可驗證背景 Agent 任務時間 | 成對 receipt 才計時的邊界 |
| [ADR-011](ADR-011-safe-local-repository-sync.md) | 受控本機 Repository 同步（含 P4.3 Onboarding Addendum） | Git 同步中心安全預設、對帳與 init/attach/clone/create 確認式動作 |
| [ADR-012](ADR-012-secretary-memory.md) | 小秘書記憶區（大腦） | 筆記表 migration 017、觀察可刪、對話注入有上限附收據、提案讀偏好、報告併入 RAG |
| [ADR-013](ADR-013-telegram-secretary-chat.md) | Telegram 小秘書對話（手機通道） | 對話與網頁同一條管線、預設關閉、內容經 Telegram 的邊界、/arm 需開關且訊息即刪、/disarm 永遠可用 |
| [ADR-014](ADR-014-multi-channel-push-and-arm-code.md) | 多通道推播（LINE）與一次性解鎖碼 | 內容與呈現分離、adapter 能力宣告、LINE 只能推播的原因、/arm 改用短效碼 |
| [ADR-015](ADR-015-local-calendar-source.md) | 本機行事曆採集來源（.ics，唯讀） | 「能否改變決策」檢驗、只讀時間／標題／地點／狀態、整批替換與壞檔隔離、不接雲端 API |
| [ADR-016](ADR-016-acceptance-center.md) | 驗收中心（完成判準機器化） | 只讀便宜查詢、狀態字彙分「沒發生／查不到」、人工署名永不覆蓋機器判定、gate 對齊 ROADMAP §12.3 |
| [ADR-017](ADR-017-pattern-aware-proposals.md) | 模式感知提案（秘書用它記得的東西） | （專案 × 日）活動矩陣、只算已結束的日子、沒有每日排程／被冷落的專案／主線加權、不新增可執行動作 |
| [ADR-018](ADR-018-declared-profile.md) | 宣告式個人檔案（你自己說的，不是推測的） | 偏好筆記裡的「優先：」「語氣：」變成會改變行為的設定、優先加分壓過推出的主線、語氣只改措辭不改數字、唯讀端點沒有第二套資料 |
| [ADR-019](ADR-019-secretary-desk-home.md) | 秘書桌面（01 分頁成為真正的首頁） | 由秘書用確定性規則挑焦點一張與記得一則、工具自身的提醒不佔焦點、完整清單降為詳情、卡片一鍵變成對話、量測「一天離開 01 幾次」 |

## 功能規格與驗證

| 文件 | 說明 |
| :--- | :--- |
| [FEATURE-001](FEATURE-001-daily-interface-usage-milestone-coach.md) | 每日主要介面使用時間與里程碑教練規格 |
| [TEST_STRATEGY.md](TEST_STRATEGY.md) | P2.5 測試策略與 contract test 設計 |
| [RELEASE_CHECKLIST.md](RELEASE_CHECKLIST.md) | 發佈前檢查清單 |
| [RELEASE_NOTES-v1.3.0a5.md](RELEASE_NOTES-v1.3.0a5.md) | v1.3.0a5 release notes（每個版本一份 `RELEASE_NOTES-v*.md`，release workflow 會自動取用） |
| [VERIFICATION-2026-08-25-next-stage.md](VERIFICATION-2026-08-25-next-stage.md) | 2026-08-25 下一階段驗證紀錄 |

## 歸檔工作文件（docs/archive/）

> 一次性的規劃書與完成報告，內容為當時快照，不再更新；現況請以 ROADMAP.md 與 STATUS.yaml 為準。

| 文件 | 說明 |
| :--- | :--- |
| [archive/2026-08-27-deskrag-integration-plan.md](archive/2026-08-27-deskrag-integration-plan.md) | DeskRAG 整合（P7）動工前規劃書 |
| [archive/2026-08-27-deskrag-integration-walkthrough.md](archive/2026-08-27-deskrag-integration-walkthrough.md) | DeskRAG 整合（P7）完成報告與當時測試結果 |

## 其他

| 位置 | 說明 |
| :--- | :--- |
| [NEXT_SESSION.md](NEXT_SESSION.md) | 下一個開發 session 的接手指南（現況、待辦、環境備忘） |
| [../promo/](../promo/) | 3 分鐘介紹影片的 18 個場景源檔、分鏡表與渲染腳本（可單景重渲） |
| [assets/](assets/) | 文件用圖片（架構與 roadmap 卡片等） |
| `../tests/` | 53 個 contract test 模組（425 項，424 passed + 1 skipped）；執行 `python -m pytest tests/ -v` |
| `../scripts/` | 驗證、清理、autostart 與 E2E 腳本 |
