# 待辦事項與已知問題（Backlog）

> 最後更新：2026-09-04。這頁是**唯一的待辦清單入口**；現況數據以
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

> **不必自己一項項翻**：儀表板「06 系統設定 → 驗收中心」或 `python main.py verify` 會直接去本機找下表的收據，
> 告訴你每一項現在是「已取得收據／部分／尚未取得／未啟用／待你親眼確認」（[ADR-016](ADR-016-acceptance-center.md)）。
> 它只讀不做：不會替你執行任何驗收動作，也不跑 git、不連網。判準以本表為準——**本表改了，`core/acceptance.py` 的 `_ITEMS` 要跟著改**。

| # | 項目 | 怎麼做 | 完成判準（收據） | 優先 |
| :-- | :--- | :--- | :--- | :--- |
| A1 | **全天 coverage ledger** | 讓 Windows 實機跨午夜連續運行一整天 | **隔日**查前一天：`GET /api/v1/usage/coverage?date=YYYY-MM-DD` 回 `meets_full_coverage: true`。⚠️ **當天的比例不算數**——該端點對今天的分母是「今天到目前為止經過的時間」，所以早上跑三小時就可能顯示 97%；那是「今天到現在覆蓋良好」（儀表板的 `OBSERVED` 就是這個意思，沒有錯），不是「全天」。驗收中心只採計**已結束的日子**。取得後更新 STATUS 的 `continuous_coverage_ledger` gate 與 `known_blockers` | 🔴 P0 |
| A2 | **RAG 雲端 provider 複測** | pull 最新版後，在小秘書分頁選 Gemini（或 OpenAI／Claude）問一題 | 能得到真實回答；若失敗，錯誤訊息會明確指出是金鑰、網路或逾時——把訊息回報即可續查 | 🔴 P0 |
| A3 | **Telegram 設定 + inline 批准** | 「設定 → Telegram 通知」走完設定流程 → 開「inline 批准」→ 按「🔓 解鎖遠端批准」→ 等晨報或傳 `/proposals` → 實批一次 L1 動作 | `GET /api/v1/secretary/executions` 出現一筆 `approved_via=telegram_inline` 的 receipt | 🟡 P1 |
| A4 | **L2 執行器實機試用** | 開三個執行器開關 + `python main.py init --show-token`，實跑 draft →（可選）confirm → apply | 拿到 `agent_draft_plan` 的 succeeded receipt；若試 apply，確認改動留在 worktree 且未被 commit | 🟡 P1 |
| A5 | **P4.3 對帳實操** | 「04 · Git 同步中心 → 🔍 掃描對帳」，各實跑一種動作（init／attach／clone） | 三類分類符合預期；確認「目的地已存在」「已有 remote」等拒絕條件如實擋下 | 🟡 P1 |
| A6 | **檢索 worker 大索引實測** | pull 最新版後啟動服務，等知識庫區塊「檢索 worker」卡片變「就緒」，再問一題 | `GET /api/v1/rag/retrieval/status` 回 `state: ready`、`warmup.bm25_chunks`／`vector_chunks` 與實際索引一致；第一次提問不再卡數十秒；主服務程序 RSS 維持百 MB 級（可與 STATUS `main_process_memory_mb_after_lazy_rag_start` 比對）。若預熱失敗，`last_error` 會說明原因——回報即可 | 🟡 P1 |
| A7 | **Repo 同步全覽與批次實操** | 「04 · Git 同步中心 → 📋 載入全覽」→「🔄 全部 Fetch」→ 若有符合條件者按「⬇ 批次 Pull」確認清單；另在排程任務新增 `repo_sync_report` 跑一次 | 全覽表格列出全部設定 root 下的 repo（數量與 `repository_count` 一致）且「上次 fetch」欄在 Fetch 後更新；批次 Pull 收據的 success／skipped 與清單一致、被跳過者有原因；`reports/repo_sync/RepoSync_YYYYMMDD.md` 產生且小秘書提案出現「需要 pull」項目（批准後 `GET /api/v1/secretary/executions` 有 `repo_pull_ff` receipt）。若要試批次 Push，先在 config 開 `repository_sync.batch.allow_push` | 🟡 P1 |
| A8 | **小秘書每日包實機收據** | 「01 小秘書 → 今日行動清單 → 📦 建立每日排程」（需 execution token），隔天早上看 01 的早晨包摘要行與桌面／Telegram 晨報；或在「設定 → 排程任務」對 `morning_pack` 按立即執行 | `GET /api/v1/secretary/executions` 出現 `morning_pack` 與 `handoff_active_projects` 的 succeeded receipt；01 顯示「早晨包：repo 需 pull N…」；`reports/handoffs/` 有當天活躍專案的 Handoff；02 專案卡出現 git 狀態 chip。若某步失敗，receipt 的 `errors` 會列出步驟名 | 🟡 P1 |
| A9 | **小秘書記憶區實機收據** | 在 01 對話框輸入「記下來：…」與「偏好：不要提醒 repo_needs_push」→ 問一題 → 對 `morning_pack` 立即執行 → 刪一則觀察；另在 02 知識庫按「🧠 併入秘書記憶與工作紀錄」 | 回覆下方出現「🧠 參考記憶區 N 筆」且「👁 現在記得什麼」列出剛記的筆記；`GET /api/v1/secretary/proposals` 的 `inputs.memory_muted ≥ 1` 且不再出現 push 提案；記憶區出現 `observation` 並可 ✕ 刪除；RAG job `activity_sync` completed 且提問能引用 Handoff／筆記切片 | 🟡 P1 |
| A10 | **手機 Telegram 對話實機收據** | 「設定 → Telegram 通知」勾「啟用小秘書對話」→ 儲存 → 重載設定 → 在手機對 bot 送「/today」「記下來：測試」與一句提問；若要試遠端解鎖另勾「允許 /arm」，在儀表板按「🔑 產生解鎖碼」後送 `/arm <6 位數碼>` | `/today` 回今日清單、提問有答案且附「🧠 參考記憶區 N 筆」、筆記出現在儀表板 01 記憶區（source=telegram）；`/arm <碼>` 送出後 `GET /api/v1/telegram/approvals/status` 的 `armed=true`（且同一組碼再送一次會被拒），`/disarm` 立刻回 false。若對話沒反應，先看 `/status` 的「長輪詢」是否運行中 | 🟡 P1 |
| A11 | **LINE 推播實機收據** | 在 LINE Developers Console 建 Messaging API channel → 發行 long-lived token →「設定 → 03 LINE 通知」貼上 token 與 userId → 按「測試並儲存啟用」→ `python main.py notify briefing --channel telegram`（會推到所有啟用通道） | 手機 LINE 收到晨報且為**純文字**（沒有裸 `<b>` 標籤）；`GET /api/v1/notifications/channels` 的 `push_ready` 含 `line`；同時開 Telegram 時兩邊內容一致。若回 `invalid_request`，通常是收件 id 填了 LINE ID（@xxxx）而不是 userId；若回 `quota_or_rate_limited` 則是免費方案的每月推播額度用完 | 🟡 P1 |
| A12 | **小秘書問候卡實機收據** | 「系統設定 → 秘書與自動化」填問候稱呼 → 儲存 → 回 01 分頁看最上方「🤗 小秘書的話」；切 `近 2 小時` 再按 ↻；（選配）把 `proactive_secretary.greeting.llm.enabled` 設 true 後重載；隔天早上看 Telegram 晨報第一段 | 卡上每個數字都能在 03 專案卡／04 統計／同步中心對得上（滑過 chip 看來源表），沒有郵件、行事曆之類未採集的數字；今天早上與下午同一視窗的鼓勵語相同；開 LLM 後徽章變 `LLM · 供應商`，且 `GET /api/v1/secretary/greeting` 回應沒有 `llm_rejected`（若有，代表 LLM 編了數字、已自動退回規則版，屬預期行為）；晨報第一段是同一段話，07:30 收到時若今天還沒活動應寫「昨天你：」而非「今天還沒偵測到」 | 🟢 P2 |
| A13 | **本機行事曆實機收據** | 從 Outlook／Google 匯出一份 `.ics`（或設定行事曆軟體同步到本機資料夾）→「系統設定 → 採集來源 → 本機行事曆」加入路徑 → 儲存並套用 → 看「系統健康」與 01 首頁 | 系統健康「行事曆（.ics）」顯示「運作中 · N 個檔 · 視野內 M 筆」且沒有來源錯誤；01 今日面板出現「📅 今天 N 場行程，下一場 …」且與你的行事曆一致（取消的不出現、重複的週會有出現）；`GET /api/v1/calendar/agenda` 的 events 沒有任何描述／與會者欄位；隔天 Telegram 晨報有「📅 今日行程」段。若某檔顯示來源錯誤，把 `collector_diagnostics.calendar_watcher.degraded_sources` 的 error 回報即可續查 | 🟡 P1 |
| A14 | **同步中心 pull/push 修正複測** | pull 最新版 →「04 · Git 同步中心 → 📋 載入全覽 →🔄 全部 Fetch」→ 看有 `.lock`／build 產物但落後遠端的 repo（例如 uavMonitor）→ 按 Pull | 該 repo 的 Pull 按鈕可按且執行成功，本機的 `.lock`／build 檔原封不動；仍不能 pull 的 repo，該列會直接顯示**帶數字的具體理由**（未提交變更 N 筆／已分歧領先 N 落後 M／沒有 upstream 並附 `git push -u` 指令），不再是同一句通用條件。若理由與你的認知不符，把該列文字回報即可續查 | 🟡 P1 |
| A15 | **每日工作誌實機收據** | 「設定 → 排程任務」新增 `daily_digest`（或直接對它按立即執行）→ 隔天在 01 記憶區看當日工作誌 → 在對話框問「我昨天做了什麼」 | 記憶區出現「YYYY-MM-DD 工作誌」與幾則專案層觀察，數字能對得上 04 統計；回覆下方顯示「🧠 參考記憶區 N 筆」且答得出昨天的事；連跑幾天後同一天不會重複寫。若某天只有計數沒有「重點」，代表那天沒有時段微摘要（本機摘要 LLM 沒開），屬預期 | 🟡 P1 |
| A16 | **模式感知提案實機收據** | 連續使用幾天後看 01 的秘書提案（或 `GET /api/v1/secretary/proposals`） | 若還沒建每日排程，出現「你近一週有 N 天在工作（…），但秘書還沒有每日排程」且 N 對得上你的印象；建好排程後這張卡消失。有「前一週活躍、近一週歸零」的專案時出現「X 被冷落」卡，且 X 確實是你放下的東西、不是誤判；主線專案的 pull／PR 提案排在冷門 repo 前面並多一句「近 7 天有 N 天在動，是你目前的主線」。`inputs.patterns.recent_active_by_project` 的天數與 04 統計對得上。誤判就用「不要提醒 neglected_active_project」壓掉並回報 | 🟡 P1 |

> A1 是唯一還在擋 `release_ready` 的**能力型**缺口；A2、A6 是修復／重構後的回歸確認（A6 對應原 B1「首次檢索在主程序載入」，程式面已於 2026-09-02 完成，剩實機收據）。

---

## B. 已知問題與技術債

| # | 項目 | 現況與影響 | 建議處理 | 優先 |
| :-- | :--- | :--- | :--- | :--- |
| B1 | **337 筆 legacy AI rows 無 `response_status`** | 早期資料缺 provenance 欄位，只保留為歷史，不進入 canonical synthesis/handoff 結論 | 維持現狀（不回填假資料）；如需清理只能標記不可用，不得推測 | ⚪ P2 |
| B2 | **Extension 覆蓋邊界** | 2026-08-31 的 live PASS 只涵蓋 ChatGPT ＋ Claude.ai；**Gemini 未在該輪驗證**，且單輪 PASS 不等於連續／全天 capture coverage | 需要時對 Gemini 補一輪 `scripts/extension_live_acceptance.py` | ⚪ P2 |
| B3 | **PyPI 發佈不在範圍** | 目前只發 GitHub pre-release（wheel/sdist + SHA-256 receipt） | 待 stable release 條件齊備後再評估 | ⚪ P2 |
| B4 | **Repo onboarding 動作不留收據** | `init_folder`／`attach_remote`／`clone_repo`／`create_remote` 執行後只回傳結果，不寫任何本機紀錄，因此 A5 只能靠人眼確認（驗收中心對這項永遠回 `needs_human`） | 若要讓 A5 可機器驗，需為 onboarding 動作補一張收據（migration ＋ ADR-011 邊界討論）；在那之前維持誠實空白，不用旁證推測 | ⚪ P2 |

---

## C. 功能候選（依需求啟動）

| # | 項目 | 內容 | 前置 | 優先 |
| :-- | :--- | :--- | :--- | :--- |
| C1 | **更多 L2 template** | 依 [ADR-008](ADR-008-gated-agent-executor.md) Addendum 模式**一次一個**審查新增；寫入型一律套用兩段式批准與 worktree 前置 | 依需求 | ⚪ P2 |
| C2 | **更多可排程 template** | 依 P5-R5 模式新增 **L0 唯讀**排程動作；L1/L2 永遠不可排程（模組載入即強制） | 依需求 | ⚪ P2 |
| C3 | **P4 其餘採集來源** | 瀏覽器閱讀、terminal history、未 commit 狀態（行事曆已於 2026-09-04 以 ADR-015 納入） | 每項先過「能否改變決策」檢驗才納入 | ⚪ P2 |
| C4 | **更多配色主題** | 外觀已拆成 `data-theme` × `data-accent` 兩軸，新增一套只需加一組 CSS 變數區塊，不動任何元件樣式 | 依喜好 | ⚪ P2 |
| C5 | **遠端網頁存取（私有網路）** | 讓手機用瀏覽器看完整儀表板：把 `security.allow_remote_clients` 換成 CIDR allowlist（預設只放行 Tailscale／WireGuard 網段）＋登入憑證＋PWA。**不做公開反向代理。** 需要先寫 ADR（認證形狀、失敗即拒、收據） | ADR-013 已先以 Telegram 覆蓋「觀察＋對話」 | ⚪ P2 |
| C6 | **LINE 雙向（webhook）** | 讓 LINE 也能提問與批准：需公開 HTTPS 入口（Cloudflare Tunnel／中繼）＋`x-line-signature` 驗證＋postback 按鈕，並修改 ADR-001 的 loopback 邊界。先寫 ADR 再動工 | ADR-014 已先用推播覆蓋 LINE；雙向仍建議走 Telegram | ⚪ P2 |

---

## 維護這頁的規則

1. **完成即移除**：項目做完後寫進 ROADMAP §11 的成果紀錄，並從本頁刪除。
2. **每項都要有收據**：新增項目時一併寫下「怎樣才算完成」，避免出現無法驗收的待辦。
   A 段新增或刪除項目時，同步更新 `core/acceptance.py` 的 `_ITEMS`（驗收中心是本表的可執行副本）。
3. **誠實標記**：環境限制、外部前置（如需要使用者提供的憑證）要標出來，不要混在「還沒做」裡。
