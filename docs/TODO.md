# 待辦事項與已知問題（Backlog）

> 最後更新：2026-09-23（**E1 `omni demo` 核心已實作**——見下表；剩 `memory_pick`／`reports/handoffs`／前端橫幅一小塊）。
> 2026-09-22：E 段依 [REVIEW-2026-09-22](REVIEW-2026-09-22-competitive-landscape-and-P9.md) 重排為 E1→E6；新增 **F 段** repo 管理強化（[ROADMAP §15](../ROADMAP.md)）與 B5／B6 兩個查證過的缺陷）。這頁是**唯一的待辦清單入口**；現況數據以
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
> 它只讀不做：不會替你執行任何驗收動作，也不跑 git、不連網。判準以本表為準——**本表改了，`core/acceptance/items.py` 的 `ITEMS` 要跟著改**。

| # | 項目 | 怎麼做 | 完成判準（收據） | 優先 |
| :-- | :--- | :--- | :--- | :--- |
| A1 | **全天 coverage ledger** | 讓 Windows 實機跨午夜連續運行一整天 | **隔日**查前一天：`GET /api/v1/usage/coverage?date=YYYY-MM-DD` 回 `meets_full_coverage: true`。⚠️ **當天的比例不算數**——該端點對今天的分母是「今天到目前為止經過的時間」，所以早上跑三小時就可能顯示 97%；那是「今天到現在覆蓋良好」（儀表板的 `OBSERVED` 就是這個意思，沒有錯），不是「全天」。驗收中心只採計**已結束的日子**。取得後更新 STATUS 的 `continuous_coverage_ledger` gate 與 `known_blockers` | 🔴 P0 |
| A2 | **RAG 雲端 provider 複測** | pull 最新版後，在小秘書分頁選 Gemini（或 OpenAI／Claude）問一題 | 能得到真實回答；若失敗，錯誤訊息會明確指出是金鑰、網路或逾時——把訊息回報即可續查 | 🔴 P0 |
| A3 | **Telegram 設定 + inline 批准** | 「設定 → Telegram 通知」走完設定流程 → 開「inline 批准」→ 按「🔓 解鎖遠端批准」→ 等晨報或傳 `/proposals` → 實批一次 L1 動作 | `GET /api/v1/secretary/executions` 出現一筆 `approved_via=telegram_inline` 的 receipt | 🟡 P1 |
| A4 | **L2 執行器實機試用** | 開三個執行器開關 + `python main.py init --show-token`，實跑 draft →（可選）confirm → apply | 拿到 `agent_draft_plan` 的 succeeded receipt；若試 apply，確認改動留在 worktree 且未被 commit | 🟡 P1 |
| A5 | **P4.3 對帳實操** | 「04 · Git 同步中心 → 🔍 掃描對帳」，各實跑一種動作（init／attach／clone） | 三類分類符合預期；確認「目的地已存在」「已有 remote」等拒絕條件如實擋下 | 🟡 P1 |
| A6 | **檢索 worker 大索引實測** | pull 最新版後啟動服務，等知識庫區塊「檢索 worker」卡片變「就緒」，再問一題 | `GET /api/v1/rag/retrieval/status` 回 `state: ready`、`warmup.bm25_chunks`／`vector_chunks` 與實際索引一致；第一次提問不再卡數十秒；主服務程序 RSS 維持百 MB 級（可與 STATUS `main_process_memory_mb_after_lazy_rag_start` 比對）。若預熱失敗，`last_error` 會說明原因——回報即可。**注意**：`index_present` 只看檔案／目錄在不在，不看 chunk 數——索引目錄存在但內容是 0 chunk 時，worker 會預熱完成卻仍顯示 0，該做的是先建索引而不是等預熱（2026-09-07 實機遇到）。**重建索引之後一定要重新預熱**：worker 記憶體裡的收據不會自己更新，明示按下預熱現在會強制重載（ADR-009 Addendum B）；載入計數小於 SQLite 的索引來源切片總數時，這一項回 `partial` 並直說「載入的是舊索引」，**不再只憑「有載入東西」判綠** | 🟡 P1 |
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
| A17 | **宣告式個人檔案實機收據** | 在對話框（或 Telegram）打「偏好：優先：<你本期在乎的專案>」與「偏好：語氣：簡潔」（或 直接）→ 重新載入 01 → 看提案清單、問候卡與記憶區面板頂端的個人檔案列 | 該專案的提案排到同類提案前面並多一句「你把這個專案標為本期優先」（`GET /api/v1/secretary/proposals` 的 `inputs.profile.priority_boosted` ≥ 1）；問候卡少掉鼓勵語（簡潔）或只剩一句下一步（直接），**標題與數字與改語氣前完全相同**；晨報／`/today` 開頭同樣生效；記憶區面板頂端列出你宣告的專案與語氣。刪掉那則偏好筆記後全部恢復預設。宣告的專案名若與 04 顯示的專案鍵不同（大小寫以外）而沒生效，回報實際名稱 | 🟡 P1 |
| A18 | **秘書桌面（01 首頁）實機收據** | 用 01 幾天：只看桌面（問候、上次做到哪、焦點一張、記得一則、詳情 chip），需要細節才展開「全部提案」「記憶區」或按 chip 跳分頁 | 焦點是你會挑的那件事（不是 Extension heartbeat 這種工具自己的提醒）、「記得」那則與你正在做的事有關；展開狀態重新載入後有記住；桌面底下「今天離開首頁 N 次」幾天後比第一天少（只算這個瀏覽器）。焦點挑錯就回報它挑了什麼、你想看的是什麼 | 🟡 P1 |
| A19 | **每週回顧（說的 vs 做的）實機收據** | 先宣告本期優先（「偏好：優先：<專案>」）→ 正常用一週 → 下週一開 01：看記憶區的「W## 回顧」與桌面的焦點／記得；也可對 `weekly_review` 排程按立即執行 | 回顧列出的各專案活躍天數與你的印象相符、每日工作誌天數對得上；宣告的優先真的沒做時桌面出現「你說 X 優先，上週它只有 N 天在動、Y 有 M 天」，改宣告（或下週真的排時間）後下一週消失；有在做時回顧寫「一致」、沒有偏移卡；宣告名字對不到活動時正文如實寫「沒有任何活動歸到這個名字」而不是硬算 | 🟡 P1 |
| A20 | **文件落後偵測與文件更新 L2 實機收據** | 06 → 秘書與自動化開三個執行器開關（執行器、L2、L2 寫入）→ 01 看有沒有「X 的文件落後了：…又有 N 個 commit」→ 按「起草文件更新計畫」（批准＋確認碼）→ **讀過那份計畫** → 再批准「依計畫改檔」→ 自己 `git diff` 檢視後 commit | 落後的 commit 數與你的印象相符；起草的計畫沒有編造沒發生的進度；改檔後 `git diff` 只動文件、**沒有被 commit**；`inputs.docs_freshness.skipped_no_doc_baseline` 列出的 repo 確實是「文件不在採集範圍」而不是誤判。改完文件後這張卡消失 | 🟡 P1 |
| A21 | **Chroma 空間回收** | 「02 知識庫」看儲存卡片的「Chroma 目錄／可回收」，按「🧹 回收 Chroma 空間」（大目錄要一兩分鐘），完成後按一次預熱 | worker 收據顯示回收前後位元組與刪掉的孤兒片段數；活著的索引仍可檢索（重新預熱後計數不變）。工作還在跑時這一項是 `pending`「回收正在進行中」，不是失敗——大目錄光 VACUUM 就要一兩分鐘。**背景**：Chroma 的 delete_collection 只做邏輯刪除，重建索引不會讓磁碟變小（實測 4,000 切片刪掉重建後目錄一個位元組都沒少），使用者實機是 4,839 切片對 4.24 GB 目錄 | 🟢 P2 |
| A22 | **會議秘書（會後逐字稿）實機收據** | 設 `meetings.transcript_dir` → 開了轉錄的會議結束後把逐字稿放進去 → 排程或立即執行 `meeting_notes` → 看 01 的「會議紀錄」卡，點一條「加入未結事項」 | 摘要沒有編造你沒說過的事；配對到的會議標題正確（或如實寫未配對）；**點過的才出現在未結事項**、沒點的不見於任何計數；會議中桌面那行與實際狀況相符（人眼確認） | 🟢 P2 |

> **A1 是唯一還在擋 `release_ready` 的能力型缺口。** A2 是修復後的回歸確認。
> **A6 與 A21 已於 2026-09-08 取得實機收據**（bm25=4,839／vector=4,839 與 `source_chunks` 一致；回收 1,633,386,496 bytes，見 [ROADMAP](../ROADMAP.md) §11.2）——
> 但這兩項是**可重跑**的檢查，重建索引或再次回收之後仍應複查，所以留在本表而不刪除。
>
> A22 是目前最容易取得的一項：設好 `meetings.transcript_dir`、丟一份逐字稿、跑 `meeting_notes`，再 `python main.py verify --item A22`。

---

## B. 已知問題與技術債

| # | 項目 | 現況與影響 | 建議處理 | 優先 |
| :-- | :--- | :--- | :--- | :--- |
| B1 | **337 筆 legacy AI rows 無 `response_status`** | 早期資料缺 provenance 欄位，只保留為歷史，不進入 canonical synthesis/handoff 結論 | 維持現狀（不回填假資料）；如需清理只能標記不可用，不得推測 | ⚪ P2 |
| B2 | **Extension 覆蓋邊界** | 2026-08-31 的 live PASS 只涵蓋 ChatGPT ＋ Claude.ai；**Gemini 未在該輪驗證**，且單輪 PASS 不等於連續／全天 capture coverage | 需要時對 Gemini 補一輪 `scripts/extension_live_acceptance.py` | ⚪ P2 |
| B3 | **PyPI 發佈不在範圍** | 目前只發 GitHub pre-release（wheel/sdist + SHA-256 receipt） | 待 stable release 條件齊備後再評估 | ⚪ P2 |
| B4 | **Repo onboarding 動作不留收據** | `init_folder`／`attach_remote`／`clone_repo`／`create_remote` 執行後只回傳結果，不寫任何本機紀錄，因此 A5 只能靠人眼確認（驗收中心對這項永遠回 `needs_human`） | 若要讓 A5 可機器驗，需為 onboarding 動作補一張收據（migration ＋ ADR-011 邊界討論）；在那之前維持誠實空白，不用旁證推測 | ⚪ P2 |
| B5 | **`git_activity_events` 的去重鍵同時會漏資料與造假重複** | 同一個根因（去重鍵沒正規化長度、也不帶 `repo_name`）造成兩個方向相反的缺陷：**(a) 靜默丟資料**——`watchers/git_watcher.py:160` 把雜湊截成 8 碼，`core/models.py:66` 的 `UNIQUE` 只鎖 `commit_hash` 單欄，`git_watcher.py:163-193` 的去重是 `filter_by(commit_hash=...)` 先查再寫且 `if not existing:` **沒有 else、沒有 log**，所以兩個不同 repo 的 commit 若 8 碼前綴相同，第二筆會完全無聲地消失；**(b) 假重複**——`core/api/events.py:131` 的 HTTP ingest 原樣存客戶端送來的值，`core/schemas.py:72-80` 的 `commit_hash: str` 沒有長度或格式驗證，所以同一個 commit 被 watcher 寫成 8 碼、被 API 寫成 40 碼時字串比不中，會變成兩筆。因為是應用層先查再寫，**`UNIQUE` 約束永遠不會觸發 `IntegrityError`**，兩種情形都不留任何痕跡 | 去重鍵改成 `(repo_name, 正規化後的 commit_hash)`，並在 ingest 邊界統一雜湊長度。**動到唯一鍵就是 migration**（append-only ＋ 進 `core/migrations.py` registry），而且要先決定既有資料怎麼辦——本專案不回填假資料，所以舊列只能標記不可用或原樣保留。**目前沒有實機證據**：本機 `git_activity_events` 0 筆，本項結論全部來自 schema 與程式碼路徑 | 🟡 P1 |
| B6 | **`GitHubRepoState` 的身分是裸 repo 名且全域唯一，schema 裡沒有 owner** | `core/models.py:346` `repo_name = Column(String(100), unique=True)` 存的是**不帶 owner 的裸名**，而 `integrations/github_client.py:322` 是用 `full_name` 去 upsert。結果：`alice/foo` 與 `bob/foo` 在 schema 層就無法共存（撞 unique），這不是「比對時忘了帶 owner」而是身分定義本身缺一半。另有數處大小寫敏感的裸名等值比對（`core/project_engine.py:393-395` 與 `core/handoff_engine.py:79-81` 是同一段程式複製兩份、`handoff_engine.py:147-148`、`core/agent_executor.py:211-213`），SQLite 預設 BINARY collation，`ActivityTracker` 與 `activityTracker` 配不上。**失效模式是漏接不是誤配**：配不上就回 `None`／降級成 L0 handoff，`agent_executor` 那處遇到同名多個還會 fail-closed——所以是「該顯示的沒顯示」，不是「配到別人的 repo」 | 正確的正規化**已經存在**：`core/repo_onboarding.py:66-83` 的 `canonical_github_slug`（owner/repo 小寫）且有契約測試守著。要修就是讓雲端側身分改用 slug 並把那幾處裸名比對收斂過去——同樣是 migration ＋ 要處理既有資料。**只有一個使用者、repo 名不重複時不會踩到**，所以優先度不高，但寫進來避免日後誤判成「已經有正規化」 | ⚪ P2 |

---

## C. 功能候選（依需求啟動）

> 已實作的功能不留在這裡：會議秘書第一層（原 C7）已於 2026-09-08 落地（[ADR-022](ADR-022-meeting-secretary.md)），
> 紀錄見 [ROADMAP](../ROADMAP.md) §11.2，實機收據追在 A22。**第二層（即時字幕／翻譯）刻意沒做**，要做得先過 ADR-022 D6 的五道門並另寫 ADR。

| # | 項目 | 內容 | 前置 | 優先 |
| :-- | :--- | :--- | :--- | :--- |
| C1 | **更多 L2 template** | 依 [ADR-008](ADR-008-gated-agent-executor.md) Addendum 模式**一次一個**審查新增；寫入型一律套用兩段式批准與 worktree 前置 | 依需求 | ⚪ P2 |
| C2 | **更多可排程 template** | 依 P5-R5 模式新增 **L0 唯讀**排程動作；L1/L2 永遠不可排程（模組載入即強制） | 依需求 | ⚪ P2 |
| C3 | **P4 其餘採集來源** | 瀏覽器閱讀、terminal history、未 commit 狀態（行事曆已於 2026-09-04 以 ADR-015 納入） | 每項先過「能否改變決策」檢驗才納入 | ⚪ P2 |
| C4 | **更多配色主題** | 外觀已拆成 `data-theme` × `data-accent` 兩軸，新增一套只需加一組 CSS 變數區塊，不動任何元件樣式 | 依喜好 | ⚪ P2 |
| C5 | **遠端網頁存取（私有網路）** | 讓手機用瀏覽器看完整儀表板：把 `security.allow_remote_clients` 換成 CIDR allowlist（預設只放行 Tailscale／WireGuard 網段）＋登入憑證＋PWA。**不做公開反向代理。** 需要先寫 ADR（認證形狀、失敗即拒、收據） | ADR-013 已先以 Telegram 覆蓋「觀察＋對話」 | ⚪ P2 |
| C6 | **LINE 雙向（webhook）** | 讓 LINE 也能提問與批准：需公開 HTTPS 入口（Cloudflare Tunnel／中繼）＋`x-line-signature` 驗證＋postback 按鈕，並修改 ADR-001 的 loopback 邊界。先寫 ADR 再動工 | ADR-014 已先用推播覆蓋 LINE；雙向仍建議走 Telegram | ⚪ P2 |

---

## D. 架構整頓（ROADMAP §13；R0～R2 已全部完成）

> 來源：[REVIEW-2026-09-16-project-assessment.md](REVIEW-2026-09-16-project-assessment.md) §4–5。
> 每一項結束時 `pytest` 必須全綠、`python main.py verify` 結果不得變化（整頓不改行為）。R2 的項目要先有 ADR。
> **R0 已於 2026-09-16 完成**（B5–B9 ＋ D1）、**R1 全部於同日完成**（D2 一個 LLM client、D3 一份活動來源定義、D4 `server.py` 切成 9 個 router、D5 桌面通知併入 `ChannelAdapter`、D6 旗標六層收三層），收據見 ROADMAP §11.2；**R2 也全部完成**（D7 一份活動記憶 ADR-023、D8 秘書四層化 ADR-024、D9 transcript parser 分拆與漂移警示 ADR-025、D10 前端模組化 ADR-026、D11 程序內狀態改注入 ADR-027、D12 驗收中心宣告式 ADR-028）。**這張減法清單到此結束**；下一輪要做什麼要先決定，不要自動往下找事做。
> **2026-09-19 決定**：把 D11 欠的前端那一半補完，分兩步走。**兩步都已於 2026-09-20 完成**——第一步安全網（[ADR-029](ADR-029-frontend-dom-lock.md)，後續補上五個互動場景）、第二步 `state.js` 分成具名 store ＋ 工廠（[ADR-030](ADR-030-frontend-state-stores.md)），收據見 ROADMAP §11.2。
> **還沒做、也沒有排**：完全的參數注入（共用的那一份還在）。要做的前提是先把行為鎖的互動覆蓋擴到那幾百個 `addEventListener` 路徑；理由與陷阱寫在 ADR-030 的 Consequences。在那之前不要動它。

| # | 項目 | 內容 | 完成判準（收據） | 階段 |
| :-- | :--- | :--- | :--- | :--- |

---

## E. 推廣路線（ROADMAP §14；依 E1 → E6 順序）

> 來源：[ROADMAP.md §14](../ROADMAP.md)（為什麼是這六件事、為什麼是這個順序）。
> **2026-09-22 依外部檢視重排**（[REVIEW-2026-09-22](REVIEW-2026-09-22-competitive-landscape-and-P9.md)）：
> 唯讀 MCP server 插進 `agent-transcripts` 套件**之前**，理由見 ROADMAP §14.2。
> 該文件 §7 的 E1～E9 **沒有採用**（ADR 編號衝突、含兩項非開發工作），逐項校訂見該文件開頭的「校訂註記」。
> ⚠️ **接手提醒**：若你手上的指令或記憶還寫著舊的三項順序（demo → `agent-transcripts` → `omni init`），**以本表為準**——
> 中間插了 E2～E4 三項 MCP 工作。另外 **MCP 的 ADR 一律是 ADR-032**，ADR-031 已經是 `omni demo` 的邊界（Accepted），不要覆蓋它。
> 共同鐵律：每一項結束 `pytest` 全綠、`python main.py verify` 的判定不因重構而改變；動到邊界的先寫 ADR。

| # | 項目 | 內容 | 完成判準（收據） | 階段 |
| :-- | :--- | :--- | :--- | :--- |
| E1 | `omni demo` 示範資料集**實作**（**核心已完成，2026-09-23；剩一小塊**） | 一份匿名化、可公開的假 Git ＋ AI 對話 ＋ 檔案事件，一個指令灌進**另開的** `OMNICONTEXT_HOME`，讓任何新環境（含每一個雲端開發 session）五分鐘看到首頁與提案。邊界定稿見 [ADR-031](ADR-031-omni-demo-dataset.md)。**已完成**：`main.py demo`（`core/demo_dataset.py`）——fail-closed（實機家目錄／原始碼 checkout／目前 `OMNICONTEXT_HOME` 一律拒絕）、旗標檔判定 demo_mode（不用猜）、冪等清空重灌、`demo_mode` 傳播到 `/api/v1/health`、驗收中心（`passed`/`attested` 強制降級 `needs_human`）、問候卡；兩個固定示範專案（一近一週持續在動、一前一週活躍近一週歸零）讓模式感知提案有材料可挑。容器 E2E 實測：`focus`／`resume`／`demo_mode` 皆正確，`verify` 22 項無一停在 passed/attested。**還沒做的一小塊**：`memory_pick`（秘書記憶區「記得」面板）與 `reports/handoffs/` 目前是空的——`omni demo` 只灌三張原始事件表，沒有觸發 `daily_digest`／`handoff_active_projects` 這兩個 L0 reduce；下一輪要決定「灌完資料後要不要順便呼叫這兩個 L0 template」（走 `core.scheduled_tasks`／`core.agent_dispatch`，需設計不依賴 UI 點擊的呼叫路徑）。前端示範模式橫幅（讀 `health`／`greeting` 的 `demo_mode`）也還沒做，屬於同一小塊的收尾。 | `omni demo` 在乾淨容器跑完後 `GET /api/v1/secretary/home` 有焦點**與記憶**、`reports/handoffs/` 有檔（**焦點已有收據，記憶與 handoffs 還沒**）；對**真實**家目錄執行時 fail-closed 並說明原因（✅ 已有收據）；新增契約測試涵蓋「示範資料不得寫進非示範家目錄」與「示範旗標一路標到 API」（✅ `tests/test_omni_demo.py` 14 項）；`pytest` 全綠（✅ 845 項，843 passed＋2 skipped） | 推廣 |
| E2 | **ADR-032 唯讀 MCP Context Server（先寫 ADR，Accepted 才動工）** | 以獨立、**預設關閉**、**唯讀**的 stdio MCP server 暴露 canonical context，讓 Claude Code／Codex 這類 agent 直接讀得到，不必開瀏覽器或貼剪貼簿。安全契約沿用 [REVIEW-2026-09-22](REVIEW-2026-09-22-competitive-landscape-and-P9.md) §A.4 的 D1–D6：唯讀／預設關閉／不轉發金鑰／輸出無 secret 與絕對路徑／與執行器零耦合／每次 tool call 留 receipt 但不記 query 原文。 | ADR 定稿，且**必須回答該文件沒回答的兩件事**：①模組路徑——**不得**用 top-level `mcp/`（會與 PyPI 官方 MCP SDK 的 `mcp` package 撞名，且 `pyproject.toml` 的 `packages.find.include` 是白名單）；②stdio 傳輸要不要引入官方 SDK 當相依——若要，必須比照 `[rag]` 做成 optional extra，不得進預設安裝（核心安裝 721 MB → 176 MB 是 R0 買來的）。另含六個 tool 的 schema、D1–D6、隱私邊界三處（ADR／config 註解／USAGE）、不做清單（不做任何 write tool、不做 HTTP／SSE transport、不暴露 RAG 文件切片） | 推廣 |
| E3 | MCP 第一版（切片一）：骨架 ＋ 兩個 tool | `omni mcp` 子指令 ＋ `omni_project_state`、`omni_handoff` 兩個 tool（直接讀 SQLite，**不要求主服務在跑**）。先做這兩個，是因為它們只接既有的 `core/project_engine`／`core/handoff_engine`，不碰檢索層。 | `omni mcp --selftest` 兩個 tool 各回一次、每筆帶 `source_ref` 且回查得到 SQLite row；**唯讀證明**：selftest 前後所有資料表列數不變（真 tmp DB）；契約測試守門「MCP 模組不得 import `core.agent_executor`／`core.agent_dispatch`／`core.secretary.scheduled_tasks`」與「原始碼零寫入關鍵字」；`mcp.enabled: false` 時拒絕啟動並說出原因；輸出不含 token／secret／絕對路徑（正則掃描）；`pytest` 全綠且不裝 `[rag]` 亦全綠 | 推廣 |
| E4 | MCP 第一版（切片二）：其餘四個 tool ＋ 驗收 | `omni_search_history`（**retrieval-only，不做 LLM 合成**——合成交給呼叫端 agent，server 只負責「找得到、指得回」）、`omni_open_loops`、`omni_work_sessions`、`omni_recent_digest`；並把驗收中心加到 A23–A26。 | 四個 tool 的回傳 schema 逐鍵比對（比照 [ADR-024](ADR-024-secretary-layers.md) `to_dict()` 的做法）；Ollama 不可用時 `omni_search_history` 回明確 `unavailable`，**不** fallback 到雲端、也不用空陣列冒充「沒發生」；`omni_recent_digest` 對沒有觀察的日期回「無觀察」而非即時產生；tool call receipt 存在且不含 query 原文；A23／A25／A26 機器可查、A24 標 `needs_human`，四項同步進 `core/acceptance/items.py` 的 `ITEMS` 與本頁 A 段；`python -m build` ＋ `verify_release_artifacts.py` `status: passed` 且 wheel 含 MCP 模組 | 推廣 |
| E5 | `agent-transcripts` 獨立套件 | 把 `watchers/transcripts/` 的四個平台 parser ＋ 統一 turn 模型 ＋ provenance ＋ drift 偵測抽成可單獨 `pip install` 的套件，本 repo 改成它的第一個使用端。前置條件在 D9（[ADR-025](ADR-025-transcript-parsers-and-drift.md)）已完成：共同介面只有 `discover`／`parse`，parser 不碰資料庫。**先寫 ADR**：套件邊界、版本相依與本 repo 的消費方式。 | ADR 定稿；套件可在**沒有本專案**的乾淨 venv 裡安裝並解析四種格式（附一份離線樣本的解析輸出當收據）；套件自帶的 fixture 不得夾帶任何真實 prompt／路徑／機器名（契約測試守門）；本 repo 改成使用端後 `tests/test_transcript_parsers_and_drift.py` 與 `tests/test_transcript_contracts.py` **一字不改**全綠；`python -m build` ＋ `verify_release_artifacts.py` 通過；`verify` 輸出與基底相同 | 推廣 |
| E6 | `omni init` 自動偵測 | `omni init` 偵測 `~/.claude`／`~/.codex`／Antigravity 的既有路徑並**詢問**要不要納入採集——不自動匯入、不自動開啟任何採集器、不碰危險能力開關。 | 偵測結果與實際存在的路徑一致（不存在的不列、列出的點得開）；一律需要明確回答才寫進設定，預設是「不納入」；非互動模式（`--yes` 之類）要能被關掉且預設不存在；新增契約測試涵蓋「偵測不等於啟用」；`pytest` 全綠 | 推廣 |

> **刻意不進本段的兩件事**（[REVIEW-2026-09-22](REVIEW-2026-09-22-competitive-landscape-and-P9.md) §5 的 P9-C 與 P9-D-3）：
> 「關掉 `release_ready`」與「找外部使用者安裝」都**不是開發輪次做得完的工作**——
> 前者卡在 A1（只能由 Windows 實機跨午夜連續運行一整天產生）、A2，以及 ROADMAP §12.3 的 G2／G3／G4，**已經寫在上面的 A 段**；後者是使用者側的事。
> 兩者都不在這裡再寫第二次（見下方「維護這頁的規則」）。

---

## F. repo 管理強化（ROADMAP §15；**E 段全部完成後才啟動**）

> 來源：使用者自己的另一個專案 `myGitQuickView`（GitHub 專案總覽網頁）的功能清單，2026-09-22 逐項與本專案比對後的結果。
> 為什麼是這四項、為什麼其餘的不搬，見 [ROADMAP §15](../ROADMAP.md)。
> **順序**：本段排在 E 段之後，routine 由上往下取項目時仍以 E 段為先。
> **證據等級**：下列每一條「我們目前沒有」都經過對抗式查證（每項三個不同視角的懷疑者去找反例），
> 逐項證據見 ROADMAP §15.2；被推翻的宣稱沒有寫進來。

| # | 項目 | 內容 | 完成判準（收據） | 優先 |
| :-- | :--- | :--- | :--- | :--- |
| F1 | **commit 的來源歸因（用證據，不用猜）** | `myGitQuickView` 用 LLM 讀 commit message 去**猜**它出自哪個開發工具；本專案手上有 transcript、檔案事件與 commit 在同一個資料庫、同一個專案身分底下，應該用**指得回一筆 row** 的方式回答。查證確認目前**完全沒有**這條連結：`git_activity_events` 與 `ai_prompt_events` 之間沒有任何欄位、關聯表或查詢（整個 ORM 零 `ForeignKey`、零 `relationship`、全庫零 SQLAlchemy `join`）。**原料已經在了**：`watchers/git_watcher.py:186` 已經把 commit message 全文存進 DB，而本專案自己的 commit 就帶著 `Co-Authored-By:`／`Claude-Session:` trailer——只是沒有任何東西去讀它。AI 側也已經有可定址的身分（`ai_prompt_events.turn_key` 有 unique index），**缺的只有 git 側的指標**。**先寫 ADR**（動到 provenance 語彙就是邊界）；ADR 編號動工時依 `docs/` 現況取下一個未使用號，**ADR-032 已由 E2 預留**。 | ADR 定稿，且必須把三種證據**分開記帳、不得混為一談**：①`trailer` 明證（commit message 帶 session／co-author trailer，可直接指回）②`temporal_only`（[ADR-006](ADR-006-derived-context-sessions-and-related-history.md) 的時間鄰近——該 ADR 第 22 行自己就否認因果，不得升格）③`similarity_only`（拿 commit message 去 `core/semantic_index.py` 撈相似 turn——similarity 明文不作真實性證明）。契約測試必須涵蓋：**沒有 trailer 的 commit 一律不得被標成「AI 產生」**、三個等級的欄位值不可互相污染、`temporal_only` 與 `similarity_only` 的輸出都要帶上既有的 claim boundary 字串。`pytest` 全綠；`verify` 判定不因此改變 | 🟡 P1 |
| F2 | **跨 repo GitHub 總覽接上畫面（後端已經寫好了）** | `core/api/repos.py:214-236` 與 `:239-267` 兩個跨 repo 端點（已同步 repo 全部 ＋ 跨 repo PR 最多 40 筆）**零前端呼叫者**，路由表快照是目前唯一守著它們的東西；`core/secretary/signals.py:249-256` 算好的 `repo_issue_backlog` 同樣零消費者。這正是 `myGitQuickView` 主畫面的形狀，而我們已經付過後端的錢沒領貨。**精確範圍**：缺的是「一屏看完所有 repo」，**不是**「雲端資料看不到」——查證確認 GitHub 資料已經透過別的路徑進 UI（`/api/v1/projects/active` 的 per-project 內嵌 PR 徽章、`/api/v1/repos/onboarding-report` 的 `github_not_cloned`、assistant 分頁的跨 repo PR／issue 待辦）。寫判準時不要把這件事說成「從零開始做 GitHub 畫面」。 | 兩個既有端點至少各有一個前端呼叫者且畫面顯示其欄位；`repo_issue_backlog` 出現在畫面上；**不新增後端端點**（有就先問為什麼既有的不夠）；`scripts/dashboard_dom_lock.py check` 重錄並在 PR 說明改了哪幾張、為什麼；`pytest` 全綠 | 🟡 P1 |
| F3 | **採集 repo 主要語言（幾乎免費的一個欄位）** | 查證確認全專案沒有任何地方採集或儲存 repo 語言——現有的 `file_type`／`CODE_TYPES`／`code_cnt` 都只是「是不是程式碼」的二元分桶，不是語言名。而 `integrations/github_client.py:149-175` 的 `fetch_all_repositories` **已經把整包 repo dict 抓進記憶體**，GitHub 回應本來就含 `language`，`sync_all` 只是逐欄位挑選時沒挑它。 | `GitHubRepoState` 多一個 `language` 欄位（append-only migration ＋ 進 `core/migrations.py` registry，不得靠 `create_all` 繞過）；`sync_all` 寫入該欄位；沒有語言的 repo 誠實留空**不猜**；`pytest` 全綠。**刻意不做圓餅圖**——理由見 ROADMAP §15.3 | 🟢 P2 |
| F4 | **作品集／履歷用的專案摘要**（候選，**先不排程**） | `myGitQuickView` 有「專案精華：AI 生成的摘要，用於履歷或作品集」。查證確認本專案沒有這個輸出目標——產出的 handoff／工作誌／週回顧／rollup／STATUS 草稿全部是自用（`main.py resume` 是接續用的 Context Handoff，**不是**履歷，名字容易誤導；`promo/` 是手寫的產品行銷素材，不從活動資料生成）。 | **啟動前要先回答「能否改變決策」**（[ROADMAP](../ROADMAP.md) §3.2 對新增產出的一貫門檻）：作品集摘要是「我擁有什麼」的鏡頭，本專案的其餘產出都是「我做了什麼」。答不出來就不做，不要因為它容易做就做 | ⚪ P2 |

---

## 維護這頁的規則

1. **完成即移除**：項目做完後寫進 ROADMAP §11 的成果紀錄，並從本頁刪除（D 段亦同；階段順序見 ROADMAP §13.2）。
2. **每項都要有收據**：新增項目時一併寫下「怎樣才算完成」，避免出現無法驗收的待辦。
   A 段新增或刪除項目時，同步更新 `core/acceptance/items.py` 的 `ITEMS`（驗收中心是本表的可執行副本）。
3. **誠實標記**：環境限制、外部前置（如需要使用者提供的憑證）要標出來，不要混在「還沒做」裡。
