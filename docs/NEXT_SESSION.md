# 下一個 Session 接手指南

> 最後更新:2026-09-02(session `claude/stoic-hamilton-4oicm4`:RAG 檢索移至常駐 worker、Repo 同步全覽／批次／秘書同步報告,TODO B1/B2 結案)。
> 這頁是給「下一個開發 session(人或 AI)」的最短接手路徑;現況以
> [STATUS.yaml](../STATUS.yaml) 與 [ROADMAP.md](../ROADMAP.md) §11 為準,
> **待辦清單一律以 [TODO.md](TODO.md) 為準**(含每項的完成判準)。

## 一分鐘現況

- **版本**:v1.3.0a5 已發佈為 GitHub pre-release(release workflow 自動建置,SHA-256 receipt 交叉驗證);`release_ready: false`,唯一缺口是全天 coverage ledger 實測。
- **Schema**:migration 16/16(append-only + checksum;新表勿繞過 registry)。
- **測試**:303 項 contract tests(302 passed + 1 skipped);容器/雲端環境缺 xdg-open 時 `test_open_command_is_argv_not_shell_string` 會條件 skip 並標註原因,不再是失敗。
- **秘書(P5)**:R1 LLM 註解 → R2 L1 白名單代辦 → R3 L2 subprocess dispatcher(三道門+冷卻)→ 寫入 Addendum(`agent_apply_plan` 兩段式改檔、永不 commit)→ R4a 晨報 → R4b Telegram inline 批准(getUpdates 長輪詢 outbound-only;批准通道需 execution token 解鎖、in-memory TTL 重啟即失效;只批 L0/L1)→ **R5 自訂排程任務**(僅 L0 唯讀 template 可排程:Handoff/週報/月報 rollup/STATUS 過期點名草稿;migration 016;錯過只補跑一次;每次執行寫 audit receipt),**ADR-008 R1–R5 全階段實作完成、全部預設關閉**;開關集中在儀表板「設定」分頁(小秘書執行器 + Telegram 通知兩張常用卡片,其餘設定預設收合)。契約見 [ADR-008](ADR-008-gated-agent-executor.md)。
- **摘要**:兩層增量(checkpoint 微摘要 map @本機 Ollama → 日報 reduce),`synthesizer.daily_from_micro` 預設開;日報 prompt 有逐事件截斷與總量上限;週/月報 rollup 只彙整既有每日摘要(`synthesizer/rollup.py`,缺日誠實列出、LLM 失敗回退 deterministic)。
- **UI**(2026-09-01 資訊架構重整,兩輪;2026-09-02 加第 7 分頁):導覽 7 分頁分主次——01 小秘書與知識庫(**今日行動清單**:上次做到哪＋早晨包＋提案;RAG 完整對話/引用/索引管理併入同一分頁的折疊區)/02 進行中工作(**只留專案卡**,含 git／建議 chip,展開有工作階段)/**03 Git 同步中心**(2026-09-02 從 02 的折疊卡獨立成分頁,切到分頁才掃描;含逐一動作、全覽與批次、P4.3 對帳)/04 摘要與統計(**前景使用／資料收集／背景任務三面板自 02 移入**)為主,05 情報流/06 設定/07 系統健康弱化為次要樣式。設定分頁分兩區:「秘書與自動化(常用)」預設展開(執行器內的排程任務、Telegram 連線設定為巢狀折疊;Telegram 已連線時連線設定自動收合,批准區塊成為主體)、「其他設定」預設收合。活動快照併入 04(底部折疊卡);**本機 Git 同步中心+對帳已獨立為 03 分頁**(切到分頁才做 git 掃描);「設定」分頁頂部固定「儲存並套用」列,常用卡(執行器含排程任務、Telegram 含解鎖批准)預設展開,設定一次即不動的卡(監控路徑/採集來源/摘要與 LLM/使用時間/GitHub)預設收合並記住展開狀態(localStorage)。折疊卡用原生 details/summary,各分頁桌面與 494px 皆無水平溢出(Playwright 實測)。**外觀為兩個獨立的軸**:`data-theme`(dark/light)× `data-accent`(naruto/forest/ocean),CSS 全面走 `var(--accent)`(66 處)、`--accent-hover`、`--accent-ink`;新配色只需加一組 `html[data-theme=X][data-accent=Y]` 變數區塊,不動任何元件樣式。偏好存 localStorage(`omni-theme`/`omni-palette`),extension-monitor 以 head 內小 script 讀同一個 key。
- **P4.3 Repo Onboarding**:已實作(同步中心「掃描對帳」:未 init 資料夾/無 remote repo/未 clone 的 GitHub repo;已 clone 與否只認 remote URL、同名僅提示不自動配對;init/attach/clone/create 皆單一目標確認式、不覆寫非空目錄、永不代為 push;契約在 ADR-011 Addendum)。既定 next milestone 已完成,STATUS `next_milestone` 改為「收使用者側 live 收據後重評 release_ready」。
- **RAG 對話契約**(2026-09-01 修):`resolve_secret_env` 回傳 `SecretResolution` 物件,**必須取 `.value`**——rag/ 內 4 處漏取導致 Gemini/OpenAI/Claude 走 RAG 一律失敗(物件恆為真值使「未設金鑰」判斷失效,且 repr 被帶進 URL)。金鑰現改走 header 不進 URL;SSE `event_generator` 全程 try/finally **保證送出 done**(瀏覽器只靠它解除「回覆中」),檢索移入 generator 並有 60 秒逾時,前端另有 120 秒閒置 abort。契約由 `tests/test_rag_chat_stream.py`(10 項)鎖住。
- **RAG 檢索 worker**(2026-09-02,ADR-009 Addendum):檢索預設在常駐子程序 `python -m rag.retrieval_worker` 執行(`rag/retrieval_client.py` 以 stdin/stdout JSON lines 驅動),**主服務 import `core.server` 不得載入 chromadb/fastembed/rank_bm25/jieba**(乾淨直譯器契約測試守門;`rag/retrieval/__init__.py` 已改 lazy export,`/strategies` 讀靜態 `catalog.py`——新增 retriever 要同步更新目錄)。啟動後有索引才背景預熱;逾時即 kill、下次提問自動重啟;崩潰/錯誤一律降級照常回答。`rag.retrieval.mode: in_process` 保留舊行為(測試用 `monkeypatch.setattr(router_module, "retrieval_mode", lambda: "in_process")` 切換)。狀態/預熱/釋放 API 在 `/api/v1/rag/retrieval/*`,知識庫區塊有對應卡片。契約在 `tests/test_rag_retrieval_worker.py`(20 項,用假 worker 腳本,不碰真實索引)。
- **儀表板整併與秘書每日包**(2026-09-02):**01 今天**＝「上次做到哪」(Resume 卡自 02 移入)＋早晨包一行摘要＋秘書提案(每項附 `why_now`;停滯事項提示可開 L2 起草);**02 專案**只留專案卡(git 狀態 chip 來自 `GET /api/v1/repos/sync-snapshot` 快照、💡 建議 chip 來自提案快取;展開卡內有近期工作階段;Related History 為底部折疊卡);統計三面板移到 **04 摘要與統計**。後端:`core/secretary_packs.py`(L0 `morning_pack`／`handoff_active_projects`、`ensure_default_schedules` 預設 07:30／21:30、`latest_pack_summary`、`build_today_view`)、`GET /api/v1/secretary/today`、`POST /api/v1/secretary/scheduled-tasks/presets`(需 token);晨報加早晨包一行與 top 建議的 why_now。契約在 `tests/test_secretary_packs.py`(9 項)。**沒有自動 pull／push,L1/L2 仍不可排程。**
- **Repo 同步全覽與批次**(2026-09-02,ADR-011 Addendum B):`GET /api/v1/repos/sync-status?scope=all`(全部 repo＋`last_fetch_at`＋summary)、`POST /repos/sync-fetch-all`(唯一不需列清單的批次:只動 remote-tracking refs)、`GET /repos/sync-batch-plan?action=`＋`POST /repos/sync-batch`(先列符合條件清單→確認→逐一在 lock 內重檢;批次 push 需 `repository_sync.batch.allow_push`,預設關)。小秘書:L0 排程 template `repo_sync_report`(`core/repo_sync_report.py`,唯讀不連網,寫 `reports/repo_sync/` 報告＋`latest.json` 快照)→ `build_action_proposals` 讀 ≤36h 的快照產生 `repo_needs_pull/repo_needs_push/repo_diverged`(subject_ref=`repo:<id>`)→ executor 對應 L1 `repo_pull_ff`(pull)／`repo_fetch`(push 不代辦)。**沒有每日自動 pull**(ADR-008 L1 不可排程)。契約在 `tests/test_repo_sync_batch.py`(9 項,真 tmp git repo)。
- **介紹影片**:3 分鐘 MP4 已交付使用者;場景源檔在 [`promo/`](../promo/)(單景可重渲,見其 README)。

## 待辦與下一步

**一律看 [TODO.md](TODO.md)**（每項都附完成判準）。目前的形狀是：

- **A. 等待使用者側 live 收據**(👤 需在 Windows 實機操作,不是程式工作):全天 coverage ledger(唯一還擋 `release_ready` 的能力缺口)、RAG 雲端 provider 複測、Telegram 設定＋inline 批准、L2 執行器試用、P4.3 對帳實操、檢索 worker 大索引實測、Repo 同步全覽與批次實操。
- **B. 已知問題與技術債**:legacy AI rows、Extension 覆蓋邊界、PyPI 不在範圍(原 B1 檢索在主程序載入、B2 缺 xdg-open 測試失敗已於 2026-09-02 結案;B1 的實機收據轉為 A6)。
- **C. 功能候選**:更多 L2／可排程 template、P4 其餘採集來源、更多配色。

> 這頁只保留「現況與環境」;新增待辦請寫進 TODO.md,不要在這裡另開清單。

## 工程慣例(照舊)

- **分支**:在當次 session 的指定分支開發 → push → `main` fast-forward → push(使用者要求所有成果都落在 main)。
- **誠實文化**:每個聲明附 receipt/claim boundary;測試失敗如實回報;migration 永遠 append-only;危險能力預設關閉。
- **文件同步**:功能落地時同步 USAGE / ROADMAP §11 / STATUS(quality gate + known_blockers)/ 必要時 README 與 ADR。

## 遠端容器環境備忘(在 Claude Code 雲端 session 內開發時)

- 依賴裝在 scratchpad venv(系統 pip 缺新 setuptools,jieba 會建置失敗);`pip install -e .` 後跑測試。
- Playwright 用 `executable_path=/opt/pw-browsers/chromium` + `--no-sandbox`;**Playwright 內建 ffmpeg 沒有 PNG 解碼器**,要完整 ffmpeg 用 `pip install imageio-ffmpeg`。
- `pkill -f` 的 pattern 會殺到自己的 shell(exit 144),用 `main[.]py` 這種寫法。
- 容器沒有 xdg-open(對應測試會 skip)、git 憑證只能推分支不能推 tag(發佈走 release workflow 的 workflow_dispatch)。
- 檢索 worker 在容器內可真的啟動(`POST /api/v1/rag/retrieval/warmup`);空索引預熱會觸發 fastembed 模型下載(約 4 秒,經 proxy),worker RSS 約 335 MB、主服務約 88 MB。
- E2E 對 localhost server 要用 port 8765(Origin allowlist 綁定預設埠)。
