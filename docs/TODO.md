# 待辦事項與已知問題（Backlog）

> 最後更新：2026-09-01。這頁是**唯一的待辦清單入口**；現況數據以
> [STATUS.yaml](../STATUS.yaml) 為準，接手路徑見 [NEXT_SESSION.md](NEXT_SESSION.md)。
>
> 每一項都標明**完成判準（收據）**——沒有收據就不算完成，這是本專案的一貫原則。
> 已完成的項目請移到 [ROADMAP.md](../ROADMAP.md) §11 並在此刪除，不要讓本頁變成流水帳。

## 圖例

| 標記 | 意義 |
| :--- | :--- |
| 🔴 P0 | 阻擋 `release_ready`；在這些完成前不評估正式發佈 |
| 🟡 P1 | 影響日常使用品質，應優先於新功能 |
| ⚪ P2 | 有價值但可延後；依需求決定 |
| 👤 | **需要使用者在 Windows 實機操作**，不是程式工作 |

---

## A. 等待使用者側 live 收據 👤

這些都不是「還沒寫的程式」，而是**只能在你自己機器上取得的證據**。功能已實作並有 contract tests，但本專案不把「測試通過」當成「實機可用」。

| # | 項目 | 怎麼做 | 完成判準（收據） | 優先 |
| :-- | :--- | :--- | :--- | :--- |
| A1 | **全天 coverage ledger** | 讓 Windows 實機跨午夜連續運行一整天 | 儀表板 coverage 轉 `OBSERVED`，或隔日 `GET /api/v1/usage/coverage?date=YYYY-MM-DD` 回 `meets_full_coverage: true`；取得後更新 STATUS 的 `continuous_coverage_ledger` gate 與 `known_blockers` | 🔴 P0 |
| A2 | **RAG 雲端 provider 複測** | pull 最新版後，在小秘書分頁選 Gemini（或 OpenAI／Claude）問一題 | 能得到真實回答；若失敗，錯誤訊息會明確指出是金鑰、網路或逾時——把訊息回報即可續查 | 🔴 P0 |
| A3 | **Telegram 設定 + inline 批准** | 「設定 → Telegram 通知」走完設定流程 → 開「inline 批准」→ 按「🔓 解鎖遠端批准」→ 等晨報或傳 `/proposals` → 實批一次 L1 動作 | `GET /api/v1/secretary/executions` 出現一筆 `approved_via=telegram_inline` 的 receipt | 🟡 P1 |
| A4 | **L2 執行器實機試用** | 開三個執行器開關 + `python main.py init --show-token`，實跑 draft →（可選）confirm → apply | 拿到 `agent_draft_plan` 的 succeeded receipt；若試 apply，確認改動留在 worktree 且未被 commit | 🟡 P1 |
| A5 | **P4.3 對帳實操** | 「進行中工作 → 本機 Git 同步中心 → 🔍 掃描對帳」，各實跑一種動作（init／attach／clone） | 三類分類符合預期；確認「目的地已存在」「已有 remote」等拒絕條件如實擋下 | 🟡 P1 |

> A1 是唯一還在擋 `release_ready` 的**能力型**缺口；A2 是本次修復後的回歸確認。

---

## B. 已知問題與技術債

| # | 項目 | 現況與影響 | 建議處理 | 優先 |
| :-- | :--- | :--- | :--- | :--- |
| B1 | **大型 RAG 索引首次檢索在主程序載入** | 索引達 475k chunks（4.4GB Chroma + 559MB BM25）時，首次查詢要在主服務內載入索引，可能數十秒。已加 60 秒逾時與 `status` 事件避免介面假死，但**根因未解** | 依 [ADR-009](ADR-009-deskrag-worker-index-lifecycle.md) 精神，把檢索也移到常駐 worker，或在服務啟動後背景預熱 | 🟡 P1 |
| B2 | **容器環境 1 項測試失敗** | `test_open_command_is_argv_not_shell_string` 在 Linux 容器缺 `xdg-open` 而失敗；**Windows 實機會過**。這是環境限制不是程式錯誤 | 可考慮讓該測試在缺 `xdg-open` 時 skip 並標註原因，讓 CI 訊號更乾淨 | ⚪ P2 |
| B3 | **337 筆 legacy AI rows 無 `response_status`** | 早期資料缺 provenance 欄位，只保留為歷史，不進入 canonical synthesis/handoff 結論 | 維持現狀（不回填假資料）；如需清理只能標記不可用，不得推測 | ⚪ P2 |
| B4 | **Extension 覆蓋邊界** | 2026-08-31 的 live PASS 只涵蓋 ChatGPT ＋ Claude.ai；**Gemini 未在該輪驗證**，且單輪 PASS 不等於連續／全天 capture coverage | 需要時對 Gemini 補一輪 `scripts/extension_live_acceptance.py` | ⚪ P2 |
| B5 | **PyPI 發佈不在範圍** | 目前只發 GitHub pre-release（wheel/sdist + SHA-256 receipt） | 待 stable release 條件齊備後再評估 | ⚪ P2 |

---

## C. 功能候選（依需求啟動）

| # | 項目 | 內容 | 前置 | 優先 |
| :-- | :--- | :--- | :--- | :--- |
| C1 | **更多 L2 template** | 依 [ADR-008](ADR-008-gated-agent-executor.md) Addendum 模式**一次一個**審查新增；寫入型一律套用兩段式批准與 worktree 前置 | 依需求 | ⚪ P2 |
| C2 | **更多可排程 template** | 依 P5-R5 模式新增 **L0 唯讀**排程動作；L1/L2 永遠不可排程（模組載入即強制） | 依需求 | ⚪ P2 |
| C3 | **P4 其餘採集來源** | 瀏覽器閱讀、行事曆、terminal history、未 commit 狀態 | 每項先過「能否改變決策」檢驗才納入 | ⚪ P2 |
| C4 | **更多配色主題** | 外觀已拆成 `data-theme` × `data-accent` 兩軸，新增一套只需加一組 CSS 變數區塊，不動任何元件樣式 | 依喜好 | ⚪ P2 |

---

## 維護這頁的規則

1. **完成即移除**：項目做完後寫進 ROADMAP §11 的成果紀錄，並從本頁刪除。
2. **每項都要有收據**：新增項目時一併寫下「怎樣才算完成」，避免出現無法驗收的待辦。
3. **誠實標記**：環境限制、外部前置（如需要使用者提供的憑證）要標出來，不要混在「還沒做」裡。
