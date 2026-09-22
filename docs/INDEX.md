# 📚 OmniContext 文件總覽（Documentation Index）

> 最後整理：2026-09-16。本頁是整個專案文件的入口地圖；新增文件時請同步更新此頁。
>
> **每份文件只有一個職責**——同一件事不在第二個地方再寫一次。職責分工見
> [NEXT_SESSION.md → 工程慣例 → 文件同步](NEXT_SESSION.md#工程慣例照舊)。

## 我該從哪裡開始？

| 你想做的事 | 請看 |
| :--- | :--- |
| **接手上一個開發 session、繼續往下做** | [NEXT_SESSION.md](NEXT_SESSION.md) —— 現況、等待中的收據、下一步候選、環境備忘 |
| **看還有什麼待辦、已知問題** | [TODO.md](TODO.md) —— 待辦清單（含完成判準）、技術債、功能候選 |
| **查實機收據做到哪了** | 儀表板「06 系統設定 → 驗收中心」或 `python main.py verify` —— TODO A 段每一項的本機收據現況（[ADR-016](ADR-016-acceptance-center.md)） |
| 快速了解專案是什麼、能做什麼 | [README.md](../README.md)（繁中）/ [README_en.md](../README_en.md)（English） |
| 安裝、Extension 配對、日常操作、備份與故障排查 | [USAGE.md](USAGE.md) —— **使用手冊** |
| 了解目前開發到哪 | [ROADMAP.md](../ROADMAP.md) §11.2 成果紀錄（依日期一條）+ [STATUS.yaml](../STATUS.yaml) |
| 了解下一階段方向與取捨 | [ROADMAP.md](../ROADMAP.md) §13「架構整頓與推廣方向」（§12 的功能候選已暫停） |
| **想知道這個專案值不值得推廣、架構哪裡該刪該併** | [REVIEW-2026-09-16-project-assessment.md](REVIEW-2026-09-16-project-assessment.md) —— 現況數字、用處／學術／教學價值評估、架構體檢（附檔案：行號） |
| 了解產品定位與「不宣稱什麼」的證據邊界 | [PRODUCT_POSITIONING.md](PRODUCT_POSITIONING.md) |
| 修改架構前先看相關決策 | 下方 ADR 一覽 |
| 發佈前檢查 | [RELEASE_CHECKLIST.md](RELEASE_CHECKLIST.md) |

## 入門與使用

| 文件 | 說明 |
| :--- | :--- |
| [../README.md](../README.md) | 繁體中文主說明：特色、快速開始、CLI 指令、設定檔、隱私邊界 |
| [../README_en.md](../README_en.md) | English documentation（與繁中版對應） |
| [USAGE.md](USAGE.md) | **使用手冊**（14 節，開頭有目錄）：安裝與初始化、啟動與外觀、Extension 配對、使用時間與里程碑、小秘書桌面／提案／記憶、行事曆與會議、知識庫與檢索、通知與手機、摘要與快照、驗收中心、備份與 migration、平台能力、FAQ |
| [../config.example.yaml](../config.example.yaml) | 設定檔範本（`main.py init` 會據此建立本機 `config.yaml`） |

## 規劃與現況

| 文件 | 說明 |
| :--- | :--- |
| [../ROADMAP.md](../ROADMAP.md) | P0–P8 開發規劃與**成果紀錄**（已完成的事寫在 §11.2，依日期排序的單一清單）；§13 為 2026-09-16 起的架構整頓三階段與推廣路線 |
| [TODO.md](TODO.md) | **待辦清單**：等待中的使用者側收據（A）、已知問題與技術債（B）、功能候選（C，暫停）、架構整頓（D）；每項都有完成判準 |
| [../STATUS.yaml](../STATUS.yaml) | 機器可讀的現況快照：feature 清單、evidence receipts、quality gates、**真正還擋著的** known_blockers 與 capability_boundaries（已完成的歷史在 ROADMAP §11.2，不在這裡重複） |
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
| [ADR-020](ADR-020-weekly-review-said-vs-done.md) | 每週回顧（說的 vs 做的） | 已結束的 ISO 週、活躍天數只用可回溯計數、宣告優先對照實際活動的 done／drift／quiet、回顧觀察同一週一則、priority_drift 即時算且頂掉重複的被冷落、不推測原因 |
| [ADR-021](ADR-021-docs-behind-code.md) | 文件落後程式（那句常打的指令變成一張卡） | 文件檔最後異動 vs 之後的 commit 數、沒有文件紀錄一律不提、接既有兩段式 L2（起草→批准→改檔不 commit）、事實由 server 準備、順手修 401 訊息 |
| [ADR-022](ADR-022-meeting-secretary.md) | 會議秘書（第一層已實作：會後逐字稿，不碰即時音訊） | 「在開會」只用行事曆＋前景視窗兩個確定性訊號、逐字稿只從一個資料夾走既有索引路徑（需 WebVTT parser）、摘要預設本機 LLM 且雲端要明示、候選待辦不自動成為未結事項、即時音訊另案並寫下五道門 |
| [ADR-023](ADR-023-one-activity-memory.md) | 一份活動記憶（活動進 `semantic_index`，文件留給 DeskRAG） | 依「這東西是什麼」分工而不是依技術棧、活動記憶不得綁在選用依賴上、知識庫對話的活動段改查同一份核心索引、舊切片一次性清除並寫進收據、代價（活動段沒有 BM25）如實記下 |
| [ADR-024](ADR-024-secretary-layers.md) | 秘書叢集四層化 ＋ 兩個定型契約 | 方向只准往下且由掃 import 的測試把關、`Signal` 在聚合層入口一次驗完必填、`Proposal.to_dict()` 就是 API 形狀、記憶層不自己往上拿資料（組合點在呈現層）、四層不等於四個檔案 |
| [ADR-025](ADR-025-transcript-parsers-and-drift.md) | 每平台一個 transcript parser ＋ 漂移警示 | 服務不認識任何格式、共同介面只有 `discover`／`parse` 兩個函式、`parse` 是不碰 DB 的產生器、漂移＝檔案在動 ∧ 事件是零（兩半缺一不可，所以不誤報「沒在用」）、靜默零事件要讓採集器變 degraded |
| [ADR-028](ADR-028-declarative-acceptance.md) | 驗收中心改成「一個 reading ＋ 一張階梯表」 | 「去查什麼」與「查到什麼就算什麼」分開、順序就是語意（OTHERWISE 只能在最後，由測試守著）、evidence 是對外契約所以另開 facts 給判準用、功能關掉就不做昂貴收集、拆檔不等於變短（總行數幾乎沒少，如實記在 ADR 裡）|
| [ADR-029](ADR-029-frontend-dom-lock.md) | 前端行為鎖：固定輸入、完整輸出、逐字元比對 | 六分頁 × 兩語言的 DOM 快照；時鐘／時區／亂數／輪詢全部釘死、一次開機只拍一頁、輸出不 normalize（normalize 會把差異藏起來）、種子資料刻意帶引號與角括號所以跳脫壞掉會紅、pytest 守語料不守比對（比對要開瀏覽器，預設不跑）|
| [ADR-030](ADR-030-frontend-state-stores.md) | 前端共享狀態分成具名 store ＋ 工廠 | 49 個攤平欄位分成 11 個 store、`createAppState()` 造得出第二份、「哪個模組碰哪個 store」是一張要維護的宣告表（新的跨模組存取會紅）、打錯欄位名從靜默 undefined 變成測試紅字；**共用的那一份還在**——買到的是可另造一份與有歸屬，不是沒有全域，理由（listener 陷阱）寫在 ADR 裡 |
| [ADR-027](ADR-027-injected-runtime-state.md) | 程序內可變狀態收成可注入的 store | 狀態有名字、有把手，重設＝換一份新的而不是呼叫鉤子；鎖跟著它保護的資料走；只存在記憶體、一次性碼只留雜湊（安全性質一個都沒放寬）；CORS 允許清單每個請求看當下設定，改設定不必重啟；行程預設仍是單例（買到的是「可注入」不是「無全域」）|
| [ADR-026](ADR-026-frontend-modules.md) | 前端拆成 ES module：一個分頁一個檔 | 沒有打包步驟（本機工具，少一層是一層）、字典是資料且兩份 key 集合由測試把關、`fetch` 只准出現在 `core/api.js`、點擊動作走 `data-action` 委派而不是字串裡的 handler、`state.js` 是過渡形狀（改成注入是 D11） |

## 功能規格與驗證

| 文件 | 說明 |
| :--- | :--- |
| [FEATURE-001](FEATURE-001-daily-interface-usage-milestone-coach.md) | 每日主要介面使用時間與里程碑教練規格 |
| [TEST_STRATEGY.md](TEST_STRATEGY.md) | P2.5 測試策略與 contract test 設計 |
| [RELEASE_CHECKLIST.md](RELEASE_CHECKLIST.md) | 發佈前檢查清單 |
| [RELEASE_NOTES-v1.3.0a5.md](RELEASE_NOTES-v1.3.0a5.md) | v1.3.0a5 release notes（每個版本一份 `RELEASE_NOTES-v*.md`，release workflow 會自動取用） |
| [VERIFICATION-2026-08-25-next-stage.md](VERIFICATION-2026-08-25-next-stage.md) | 2026-08-25 下一階段驗證紀錄 |
| [REVIEW-2026-09-16-project-assessment.md](REVIEW-2026-09-16-project-assessment.md) | 2026-09-16 專案檢視快照：現況、推廣／學術／教學價值、架構體檢與該刪該併的判斷 |

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
| `../tests/` | **75 個 contract test 模組（831 項，829 passed + 2 conditional skip；不裝 `[rag]` extra 時 816 passed + 14 skipped）**；執行 `python -m pytest tests/`。另有前端行為鎖 `OMNI_DOM_LOCK=1 python -m pytest tests/test_dashboard_dom_lock.py`（要 Chromium，約一分鐘）|
| `../scripts/` | 驗證、清理、autostart 與 E2E 腳本 |
