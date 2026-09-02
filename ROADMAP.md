# OmniContext 開發規劃與成果紀錄 — P0 ~ P8

> 最新更新日期：2026-08-29　｜　目前狀態：**personal alpha / P2.6 + P3 context memory + P4.2 local Git sync + P5-1 proposal-only alpha**。P3-1～P3-5、Windows Toast E2E、formal rollback、Windows/macOS/Linux CI、P5-1、collector runtime diagnostics、Extension 1.3.1 live-verification harness 與本機 Git 同步中心已完成；ChatGPT live selectors 已修復。Claude.ai 本輪 PASS receipt 與 Extension live heartbeat 尚未完成，整體不具 release-ready 或 autonomous-ready 資格。
> 本文件記錄 OmniContext 從 0 到 1 的缺陷修復歷程、已完成之架構改造與未來的維運與延伸規劃。

---

## 0.1 產品定位：不隸屬單一 AI 的工作脈絡層

ChatGPT、Gemini、Claude、Grok 等產品正在強化各自平台內的 memory、Project、conversation continuity 或資料匯入。OmniContext 的差異不在複製同樣的 provider memory，而是把**多個 AI + local application + Repository/Git/GitHub + files + foreground activity + Open Loops**歸入使用者自己的本機時間線與 canonical project state。

核心原則是：canonical context 屬於使用者與專案，不屬於任何一家 AI provider；Context Handoff 可交給不同 AI 接手，且每個結論保留 provenance、response status 與 coverage boundary。完整比較與 non-claims 見 [`docs/PRODUCT_POSITIONING.md`](docs/PRODUCT_POSITIONING.md)。

---

## 0. 系統進化歷程與實測數據對比 (最新實測校準)

經過深度代碼審查與連續運行實測，各項核心指標已全面校準至最嚴格的真實數據：

| 評估指標 | 初始狀態 (2026-08-22) | 現行實測成果 (2026-08-23 校準) | 改善效益與判定 |
| :--- | :--- | :--- | :--- |
| **AI 對話事件列** | 10 筆 (7 筆當日 + 3 筆假資料) | **2,418 筆（2026-08-24 16:35 快照）** | 其中 2,161 筆具 source provenance；337 筆 legacy rows 保留但不列入 canonical 結論 |
| **AI 回應可信狀態** | 0% (僅單向問句) | **2,053 非空／1,890 final candidates／66 partial** | Codex/Claude/Antigravity 優先使用來源的 explicit final marker；final candidate 仍不代表語意正確 |
| **檔案監控噪音比** | 3574 筆雜訊 / 1 筆論文 | **單日 ~70 筆真實寫作/代碼** | 移除 .txt、過濾自身 logs 與 CASE-* 實驗數據，設單日 5 次單檔上限 |
| **Git 倉庫覆蓋率** | 0 個 (要求根目錄為 repo) | **49+ 個 Git Repos 遞迴探索** | 90+ 筆真實 Commits 跨專案納管與 PR 即時追蹤 |
| **專案分類正確性** | 全數落入論文 (單一 .md 誤判) | **Top-Down Canonical Resolver** | 81 個碎片化子目錄收斂為清楚的論文與代碼主專案 |
| **Open Loops** | 0 筆 | **4 open／2 resolved／1 superseded** | 完成 fingerprint 回填、重複收斂與既有事項人工複核 |
| **採集器健康度狀態** | 假資料覆蓋 / 靜默未知 | **動態紅黃綠燈（逾時 3 小時標紅）** | 刪除 TestApp 測試列，Web UI 即時顯示狀態與最後寫入時間 |

---

## 1. 已完成核心里程碑 (Completed Deliverables)

### ✅ P0：數據採集管線修復與噪音過濾
1. **D1 時區統一與冪等遷移**：
   - 建立 `core/time_utils.py` 統一本地時間入口，全面取代 `utcnow()` 與 `now()` 混用問題。
   - 執行 `scripts/migrate_timezone.py` 帶冪等旗標確保時間線一致。
2. **D2 檔案噪音徹底排除 & 雙軌監控**：
   - 將 `.txt` 從預設監控副檔名移除（專注於 `.tex`, `.docx`, `.md`, `.pdf`, `.py` 等寫作與開發行為）。
   - 黑名單加入 `BladeDamage`、`outputs`、`results`、`activityTracker/logs`、`checkpoints`、`CASE-*`、`*.log`、`*.csv` 等雜訊。
   - 實作單日單檔最多 5 次事件上限，並同時監控論文庫與 `Project_CodingSimulation` 程式碼資料夾。
3. **D3 Git 49+ 倉庫遞迴探索**：
   - 實作 `discover_git_repos(root_dir, max_depth=3)`，支援 30 分鐘快取與 7 天 commit cutoff。
4. **D5 & D6 瀏覽器擴充套件去重與假開關修復**：
   - MV3 擴充套件改用 `platform + prompt_hash + hasResponse` 與 `chrome.storage.session` 去重。
   - 後端 `/api/v1/events/ai` 實作 10 分鐘視窗 Upsert，並對齊 `claude_web`、`chatgpt`、`gemini` 開關與離線佇列。

### ✅ P1：主力 AI 日誌全量接入與專案狀態層
1. **三大本機 Agent 日誌深度解析**：
   - **Codex CLI**：解析 `rollout-*.jsonl`，以新 user turn／EOF 建立 turn boundary，保留最後有效 assistant message。
   - **Antigravity**：解析 `transcript.jsonl` 的 USER_INPUT／PLANNER_RESPONSE，保存來源位置與 final-candidate 狀態。
   - **Claude Code**：優先解析 `projects/**/*.jsonl`；只有來源本身缺回答時才保留 missing 狀態。
   - **Claude Desktop Cowork／local-agent**：自動偵測 application data 內嵌 `.claude/projects/**/*.jsonl`，支援 Windows extended path、7 天首次回補與獨立 `claude_desktop` provenance；一般 cloud-chat LevelDB 保持 detected/unparsed。
   - **佔位符過濾**：Prompt 組裝排除 `[external_agent_tool_call]`、`[Codex CLI Session]` 等非人類回應；不再以舊配對率作 release 指標。
2. **專案狀態收斂引擎 (`core/project_engine.py`)**：
   - 實作 Top-Down Canonical Project Resolver，將 `response_final`、`closure_qa`、`word_pdf_v7` 等論文修訂版子目錄正確歸戶至所屬主論文（如 `1150820-opcuaPaperManus`、`09.agentSkill`）。
   - 排除 `researchProgress.md` 等單一檔名誤判為獨立專案。
3. **Open Loops 智慧萃取與清洗**：
   - 清洗標題開頭的 `**優先級 1 (`activityTracker`)**：` 雜訊標籤，讓待辦清單清爽可讀。
   - 強化多語言與符號解析，確保 `113-01 離岸風電實務` 等複雜名稱 100% 正確歸戶。

### ✅ P2：視覺化儀表板、GitHub 整合與主動推播
1. **Web UI 全功能儀表板 (`web/index.html`, `web/app.js`)**：
   - 5 大視圖切換：🎯 進行中工作、⚡ 即時活動流、📅 每日/自訂區間工作日報、📊 時間統計、⚙️ 系統設定。
   - 完整支援 **繁體中文 / English** 雙語動態即時切換。
   - 採集器面板新增「**動態健康燈號**」（逾時 3 小時標紅、30 分鐘內標綠），讓健康度一目了然。
2. **GitHub 生態深度整合 (`integrations/github_client.py`)**：
   - 支援自動讀取本機 `gh auth token` 或 `GITHUB_TOKEN` 環境變數。
   - 即時追蹤所有公開/私有倉庫的 PR 狀態、CI/CD 檢查結果與最近 Commit。
3. **安全防護與單一實例保證**：
   - 清理 Git 追蹤之 `config.yaml`、`.instance.lock` 與敏感金鑰，提供標準 `config.example.yaml`。
   - 加入單一實例檔案鎖（Single Instance Lock），杜絕多進程並發讀寫 SQLite 衝突。
4. **通知通道**：
   - Windows desktop notifier 為主要通道；Telegram notifier 保留為 opt-in，未設定 token/chat ID 時不得視為可用。

---

## 1.5 提醒通道與資料清洗（2026-08-23 新增）

Telegram 通道經評估後**不採用**（使用者未使用該工具），改為兩條零設定的本機通道。
`notifiers/telegram_notifier.py` 保留為通用 notifier 的參考實作，預設關閉。

### 桌面通知 `notifiers/desktop_notifier.py`
- 直接以 PowerShell 呼叫 Windows WinRT `ToastNotificationManager`，**不依賴 winotify / plyer**，無需安裝套件或申請帳號；WinRT 不可用時自動降級為 `MessageBox`。
- 三種情境：晨間簡報（08:30）、今日回顧（22:00）、專案停滯提醒（預設閒置 5 天）。
- 點擊通知直接開啟儀表板；`--dry-run` 可在終端機預覽。
- 自動濾除 `General / Notes` 這類未歸戶收容桶，不讓它佔用提醒版面。

### 每日入口簡報 `exporters/daily_brief.py`
- 產出 `OMNICONTEXT_TODAY.md` 與 `OMNICONTEXT_TODAY.html` 至 `exporters.daily_brief.output_dir`（預設 `D:/Project_CodingSimulation`）。
- HTML 版每 5 分鐘自動刷新，可設為瀏覽器書籤或首頁；Markdown 版可被其他工具或 AI 直接讀取。
- 設定 `inject_into` 指向既有 HTML 儀表板時，改為在 `<!-- OMNICONTEXT:START/END -->` 標記間注入。
- **注意**：`MCP/LabPagesCowork/` 底下的儀表板會被 `deploy_dashboard.py` 推送到公開 GitHub Pages，因此預設不注入該檔案，避免個人工作紀錄外流。

### 資料清洗 `scripts/purge_legacy_data.py`
- 冪等腳本，清除兩類歷史污染：seed-demo 殘留的假視窗事件（`aaai2026_draft.tex`、`TestApp`）、Agent CLI 內部訊息被誤存為使用者提問。
- 採集端 `_upsert_ai_event()` 已加上同一組過濾（`is_cli_artifact()`），並先以 `clean_prompt_text()` 脫去 `<USER_REQUEST>`、`<ADDITIONAL_METADATA>` 等包裹標籤再判斷，避免誤刪真實提問。
- 實測清除 179 筆 CLI 雜訊與 2 筆假視窗事件，真實配對率由 84.9% 提升至 **85.9%**（Codex 98%、Antigravity 94%、Claude Code 53%）。

> 舊版 pairing percentage 僅以 response 是否非空計算，已降級為歷史 heuristic，不再代表 final answer。`~/.claude/history.jsonl` 本身只存提問，canonical contract 會將其標成 `missing`。

### 視窗採集器心跳 `watchers/window_watcher.py`
- 每 5 分鐘（`heartbeat_minutes`）記錄一次實際讀到的前景視窗，讀不到時以 WARNING 標示。
- 目的是讓下次靜默失效能直接從日誌判斷是「讀不到」還是「寫不進」。

---

## 2. 維運清單 (Remaining Maintenance)

### ✅ P4.2：受控本機 Repository 同步

- 新增 Dashboard「本機 Git 同步中心」，以既有 `watchers.git_watcher.repositories` 的設定 root 為唯一範圍，顯示 branch、upstream、cached ahead/behind、staged／unstaged／untracked／conflict。
- 狀態載入不連網；使用者可對單一 repo 明確執行 `fetch --prune`、`pull --ff-only`、staged-only `commit`、`push`。
- 安全邊界：不接受 Web path、無自動排程、無 `git add`、無 force push，Pull/Push 僅於 clean 且無分歧時可用；詳見 [`ADR-011`](docs/ADR-011-safe-local-repository-sync.md)。

### ⏳ P4.3：Repo Onboarding／Reconciliation（下一階段）

- **目標**：處理三種尚未成對的狀態：一般本機資料夾、已 `git init` 但未設定 remote、以及 GitHub 已存在但尚未 clone 的 repo。
- **本機優先比對**：同時列出 configured local roots 與已同步 GitHub metadata，但不以名稱相同推論為同一專案；候選配對必須顯示 evidence（名稱、既有 remote、選定目錄）並由使用者確認。
- **確認式動作**：在使用者指定目標資料夾與 visibility 後，才可 `git init`、建立／連結 remote、建立初始 commit，或 `git clone`；clone 前須檢查目錄存在性與非空衝突。
- **不納入第一版**：批次初始化、掃描後自動發布、覆寫非空資料夾、強制重設 remote、auto-merge／force push，以及自動收集或發布任何 secret。

1. **Chrome MV3 擴充套件實機載入**：
   - Gemini 已有 3 筆 Browser events／2 筆 response；ChatGPT 已完成 2026-08-25 真實 DOM prompt/response selector probe 並修復繁中 send click。
   - Extension 1.3.2 保留 start baseline、Content Ready timestamp、event/response delta 與 JSON receipt，並移除 Manus 監控；Claude.ai 本輪 PASS 與 live heartbeat 仍待已登入 Chrome Reload 後取得。
2. **自動開機排程佈署 (`scripts/install_autostart.ps1`)**：
   - 註冊 Windows Task Scheduler 工作排程，支援背景靜默啟動（`pythonw.exe`）。
3. **視窗採集器持續觀察**：
   - 2026-08-23 重啟服務後恢復正常（當日 27 筆）。心跳日誌已就位，若再次靜默可從日誌判斷是讀取端或寫入端。
4. **Desktop／Web／Transcript coverage 分流**：
   - 2026-08-25 主頁與 `/api/v1/capture/status` 已將三種訊號分開；Claude Desktop incremental E2E 新增 148 turns／125 responses／117 final candidates。
   - 待完成：一般 Claude 雲端聊天仍不解析 cache；Claude.ai 仍需 authenticated Browser Extension receipt。

---

## 2.5 P2.5：Reliability, Security, Lifecycle & Portability Gate（進行中）

> Architecture decision：在語意記憶與自主執行之前，先讓「來源、turn、回應、待辦、權限與執行平台」都有明確契約。P5 在本節所有 release blockers 關閉前維持 blocked。

**2026-08-26 實作結果：**append-only SQLite registry 維持 7/7；Windows WinRT milestone Toast E2E 與 `1.3.0a1/schema4 → 1.3.0a2/schema5 → rollback` rehearsal 通過。Extension `1.3.1` 在 shared capture core 上新增 timestamped Content Ready receipt 與 fail-closed live verifier；ChatGPT live DOM probe 通過，Claude.ai 本輪 PASS receipt 仍待完成。P3-2～P3-5 已進入 Alpha；跨平台 workflow run `32757498004` 的六個 jobs 已通過。

### P2.5-B2 Extension live-verification harness

- `POST /api/v1/extension/verification` 建立 process-local baseline；`GET /api/v1/extension/verification/{id}` 每次以目前 heartbeat 與資料庫 counts 重新判定。
- PASS 必須同時具備開始後的新 token-authenticated heartbeat、每站新的 Content Ready timestamp、新 Browser event 與非空 assistant response；歷史 `OBSERVED`、單獨 heartbeat 或只有 prompt 都不能通過。
- Receipt 僅含平台、counts、timestamps 與 stable state，不含 token、URL、Prompt、Response 或本機 path；baseline 不寫入 SQLite，service restart 後失效。
- **Localhost receipt（2026-08-26）**：Claude.ai run 可由 UI 建立並進入 RUNNING；因本工作階段無法接管使用者已登入 Chrome，heartbeat、Content Ready、event 與 response delta 均維持 0，正確未升格為 PASS。494px 無頁面水平 overflow，console 無錯誤。
- ✅ **Real Chrome PASS receipt（2026-08-31 01:03 Asia/Taipei）**：以 `scripts/extension_live_acceptance.py` 在已登入 Chrome 實機完成——新 token-authenticated heartbeat 已驗證，ChatGPT 與 Claude.ai 各取得本輪 3 筆 event／2 筆非空 response 的 delta，全平台 `passed: true`（verification_id `857027de…`）。此 PASS 只證明該輪能力，不證明連續或全天 coverage；Gemini 未在本輪範圍。

### P2.5-S1 本機 API 安全邊界

- 禁止 wildcard CORS；只允許設定中的 local dashboard origins。
- 跨來源 browser extension ingestion 必須使用獨立 ingest token，不得取得其他 API 權限。
- `/api/v1/config` 回應遮蔽 token、API key、secret、chat ID；設定更新保留既有 secret，避免遮蔽值覆蓋真值。
- 所有本機檔案／終端機啟動改用 argument list，禁止 `shell=True`；URL 只允許 `http` / `https`。
- 驗收：惡意 Origin 讀取設定或 events 得到 403；allowed Origin 正常；config response 不含明文 secret；path payload 不進入 shell。

### P2.5-R1 採集完整性與可追溯性

- 每一筆 AI turn 增加 stable `turn_key`、`source_path`、`source_position` 與 `response_status`。
- Codex session 以 explicit `phase=final_answer` 或下一個 user turn 封閉上一輪；active EOF 保留 `partial`，後續掃描可升級或降級狀態。
- 建立持久化 ingestion checkpoint；只有解析成功後才更新 `(mtime_ns, size)`，失敗必須保留可重試狀態。
- Agent log 採 source-level fault isolation；單一 Claude Desktop 目錄 `Access Denied` 或來源解析失敗不得中止 Codex、Claude Code、Antigravity 等其他來源。
- 驗收：同一 conversation 重複相同 prompt 不互相覆蓋；重啟後未變檔案不重掃；解析失敗不前移 checkpoint；重新掃描可更新較新的 assistant response。
- **Recovery receipt（2026-08-26）**：舊 split-start process 顯示 threads running，但 window 最後事件停在 `00:01:09`，Claude Desktop 權限錯誤也會中止整輪 Agent scan。完整停止後改以 `python main.py run` 整合啟動，window events `2281 → 2289`、AI events `2722 → 2800`，最後資料分別推進到 `00:47:31`／`00:47:13`；Dashboard 同步更新且完整測試 79/79。

### P2.5-D1 資料可信度指標

- 分開呈現 `response_non_null`、`response_nonempty`、`response_final_candidate`，禁止以 non-null 代替「真實結論」。
- `status` CLI 優先讀取 live service status，無服務時才使用 local fallback。
- 專案數分列 active / idle / stale，不再把全部 ProjectState 稱為「進行中」。
- 健康度除了最後事件時間，也記錄 checkpoint/error；「沒有活動」與「collector 故障」不得混為一談。
- **Runtime diagnostics receipt（2026-08-26）**：`GET /api/v1/control/status` 新增全域 `monitoring_state`、`degraded_collectors` 與 sanitized `collector_diagnostics`。Window probe 經 30 秒持續 unavailable 才降級，成功 probe 立即恢復；Agent log 逐來源隔離錯誤。整合重啟後 window events `2323 → 2324`、AI events `2894 → 2895`，四個 Agent sources 與 Window probe 均為 healthy。degraded DOM smoke 顯示來源錯誤但不含 path／exception message，494px 即時情報流與採集卡無頁面水平 overflow、console 無錯誤；完整測試 81/81。

### P2.5-L1 Open Loop 生命週期

- 狀態至少包含 `open / stale / resolved / superseded`，並保存 `last_seen_at`、`resolution_note`、來源與 fingerprint。
- 重複摘要只更新 `last_seen_at`；不得無限制建立重複事項。
- Handoff 與提醒預設只顯示 open；stale 必須要求複核，不得直接交給 P5 執行。
- 驗收：resolve、reopen、supersede、stale 均有 API/CLI test；過時事項不再出現在 actionable handoff。

### P2.5-P1 跨平台與發佈基線

- 建立 Windows / macOS / Linux platform service abstraction；不支援的功能明確降級，不在 import 階段修改 registry 或 OS 狀態。
- `config.example.yaml` 不含個人絕對路徑；路徑支援 `~` 與環境變數展開。
- 建立 `pyproject.toml`、pytest 基線與 CI-ready test commands；測試從 P6 提前到 P2.5。
- `main.py init --watch <path>` 已可產生本機 `config.yaml`、必要目錄與 extension token；Agent/Git 自動偵測與 notification capability probe 尚待完成。
- Versioned migration 已採 1→7 append-only registry；checksum mismatch、history gap 或未知較新版本會 fail-closed，既有有資料 DB 升級前自動 online backup。

### P2.5 Release Gate

- [x] Security contract tests 與 Windows live Origin/token probe 通過。
- [x] Transcript pairing / stable turn key / malformed JSONL / checkpoint fail-closed tests 通過。
- [x] Open Loop lifecycle contract tests 通過，現有過時與重複事項完成一次人工複核。
- [x] Windows 實機 smoke 與 collector restart E2E 已通過；Windows／Ubuntu／macOS × Python 3.10/3.12 matrix run `32757498004` 六個 jobs 已取得真實 receipt。
- [x] README 隱私聲明明確區分 local storage、cloud LLM processing 與 optional integrations。
- [x] SQLite online backup 產生 integrity 與 SHA-256 evidence；isolated restore drill 通過 schema／row-count parity 並保存 JSON receipt。
- [x] Versioned migration fresh/legacy/live upgrade到 7/7；pre/post backups、restore drill 與 formal package+DB rollback 均通過。
- [x] Windows wheel/sdist contents、fresh install、1.2.0 upgrade、assets、writable-home 與 privacy exclusions 通過。
- [x] P3-2/P3-3 local-only Alpha 已完成；P5 executor 仍需獨立安全 gate。

---

## 2.6 P2.6：主要介面使用時間與每日里程碑教練（Alpha 已實作）

> 需求來源：2026-08-24 使用者臨時需求。完整規格見 [`docs/FEATURE-001-daily-interface-usage-milestone-coach.md`](docs/FEATURE-001-daily-interface-usage-milestone-coach.md)。本項為 **MoSCoW: Should Have**，不得取代 P2.5 的資料可靠性與 release blockers。

**2026-08-25 實作證據：**localhost 主頁與 `/extension-monitor` 已完成 live smoke；usage API coverage 維持 `partial`。Windows 隔離 milestone E2E 已走過真實 WinRT Toast submission、SQLite sent receipt 與 duplicate suppression；正式資料庫未寫入測試 event。

**2026-08-30 更新：continuous coverage ledger 已實作**（migration 013 `coverage_ledger_intervals`、`core/coverage_ledger.py`、scheduler `coverage_ledger_job` heartbeat、`/api/v1/usage/coverage`）。interval 結束時間永遠取最後一次 heartbeat，中斷、休眠或當機不回補；當日 ledger 覆蓋率達 `usage_tracking.coverage.full_coverage_ratio`（預設 0.95）時 usage API 的 coverage 才由 `partial` 升級為 `observed`。contract tests 已通過；**Windows 實機的全天 ledger receipt 尚未取得**，取得前仍維持 Alpha。

### 產品目的

- 依每日、每週統計 Claude Code、Codex、ChatGPT、Claude.ai、Gemini、VS Code 等主要介面的 **foreground active time**。
- 允許使用者設定每日里程碑；達標時以 dashboard 與 desktop notification 提醒、肯定或鼓勵，例如「今天 Claude + Codex 前景使用時間已達 6 小時」。
- 長時間使用時可選擇顯示休息提醒；語氣、門檻、quiet hours、通知頻率與是否啟用均由使用者設定。

### 信任與隱私邊界

- 使用時間以去除重疊後的前景視窗區間計算；AI event 只做互動次數，不可與 window duration 相加造成 double counting。
- 明確標示為「前景使用時間」，不得宣稱為實際工作時間、生產力、專注度或成果品質。
- collector 中斷或平台不支援時顯示 `partial / unavailable`，不得把資料缺口呈現為 0 小時。
- 分類規則必須 config-driven；window title 預設只做本機分類並支援遮蔽，不因本功能上傳 cloud LLM。
- 每個里程碑每日只通知一次，保存 notification receipt，並支援 opt-out、quiet hours 與 cooldown。

### 介面分工

- **Browser Extension popup**：定位為 `Extension Monitor / Ingestion Bridge`，負責本機連線、ingest token 與各網站採集狀態；可顯示一句今日摘要，但不是完整分析主頁。
- **Web Dashboard**：新增「今日使用與里程碑」區塊，呈現各介面時間、資料 coverage、目標進度與最近達成項目。
- **Desktop notification**：達標或長時間使用時提供可配置的提醒／鼓勵；點擊後回到 dashboard 詳情。

### 依賴與驗收

- 依賴：P2.5 window collector reliability、跨平台 capability probe、notification abstraction；browser-only 平台需完成 extension 實機 ingestion 才能宣稱完整 coverage。
- [x] 相同或重疊 interval 不重複計時，跨午夜正確切分至本機日期。
- [x] app/interface mapping 可由 config 增修，unknown 類別保留並顯示為 `Other`。
- [x] Dashboard 同時顯示時間、coverage 與資料更新時間。
- [x] milestone notification 具 idempotency、quiet hours、cooldown 與使用者關閉選項。
- [x] Windows Dashboard/API 與 Extension token pairing 已實機驗證。
- [x] Gemini 真實 Browser ingestion 已觀察 3 筆 event／2 筆非空 response。
- [x] 新版 Extension heartbeat 實機 receipt 與 ChatGPT／Claude.ai 本輪 live capture（2026-08-31 PASS，見 P2.5-B2）。
- [ ] 真實達標 Toast 與 macOS/Linux 實機能力仍待完成。
- [x] Contract tests 已覆蓋 interval merge、跨午夜、缺失平台、通知去重與內建 scheduler job contract。
- [x] Continuous coverage ledger：heartbeat 開啟/延長/中斷分段、時鐘倒退防護、當日 union 覆蓋率與 `observed` 升級條件均有 contract tests（2026-08-30）。
- [ ] Windows 實機全天 ledger receipt（讓正式環境的 usage coverage 實際脫離 `partial`）。
- [ ] DST 與完整 retention/privacy matrix 仍待補。

---

## 2.7 P2.7：可驗證背景 Agent／CLI 任務時間（Alpha 已實作）

> 需求來源：2026-08-29。設計決策見 [`docs/ADR-010-verified-background-agent-task-time.md`](docs/ADR-010-verified-background-agent-task-time.md)。

### 產品目的

- 補足「視窗縮小但本機 Agent 仍在執行」的可追溯時間訊號，不把它誤當成前景使用或人類工作時間。
- 第一版只處理 Claude Code、Claude Desktop local-agent transcript 與 Codex session；generic Terminal command、browser AI 與沒有 local receipt 的 provider 不補值。

### 信任與隱私邊界

- 必須同時取得來源內的 user prompt start timestamp 與 explicit final completion timestamp；缺任一端為 `awaiting_final`，不結算。
- 不重複保存 prompt、response、URL 或 source path 至 API；來源仍可在本機 ingestion provenance 中回查。
- 每日總執行秒數採 interval union；平行 Agent 不會在總數 double count，且不與 `WindowEvent` foreground time 相加。
- end ≤ start 或超過 `background_task_tracking.max_task_duration_seconds` 時標為 `untrusted_duration`，不估算。

### 依賴與驗收

- [x] SQLite migration 12、stable task key 與重掃 idempotency。
- [x] Claude/Codex transcript final marker 轉為 paired receipt，API 回應不含內容或本機 path。
- [x] Dashboard 獨立顯示 VERIFIED／WAITING、完成件數、等待 final 件數與最近完成 receipt。
- [x] Contract tests 覆蓋 start-only、explicit final、重疊 union、異常時長、migration 與 API privacy。
- [x] localhost service restart 後，取得 Codex 7 筆 completed receipt、5 筆 awaiting-final receipt；API 以 3,076.659 秒的 interval union 回傳 51.3 分鐘。
- [x] Live receipt 驗收腳本 `scripts/background_task_live_acceptance.py`（2026-08-30）：逐平台檢查當日 completed receipt、輸出非敏感 JSON receipt 與 STATUS.yaml 建議段落；已於 localhost API 完成 E2E（無 receipt 時正確 FAIL）。
- [x] Codex live 驗收 PASS（2026-08-31 執行，target date 2026-08-29：29 筆 completed／14,882.249 秒；全平台 union 28,838.971 秒、48 筆 completed）。
- [x] claude_code／claude_desktop 逐平台驗收 PASS（2026-08-31 01:18 確認 2026-08-29 資料：claude_code 7 筆／3,922.661 秒、claude_desktop 12 筆／16,091.775 秒）。**P2.7 三平台 live receipt 全數取得。**
- 邊界不變：單日 receipt 不代表全天背景工作 coverage；generic Terminal、cloud-only 與未完成任務不覆蓋。

---

# 第二階段規劃：從「日誌」到「記憶」

> 規劃日期：2026-08-24
> 依據：15 個月實際使用資料的價值評估

## 3. 現況定位與缺口分析

### 3.1 已累積的資料資產

```
2,418 筆 AI event rows · 1,890 筆 final candidates · 66 筆 partial · 約 393 萬字元
時間跨度 2025-05-19 ~ 2026-08-24（15 個月）
70 個專案狀態 · 57 個 GitHub repos · 266 筆 PR
```

### 3.2 核心問題：資料只有一種存取方式

目前 236 萬字元**只能靠時間排序捲動瀏覽**。這代表：

- 「我上次怎麼解決 SQLite database locked？」→ 答案在庫裡，但找不到。
- 「這個專案上次做到哪？」→ 只看得到最後一筆動作，看不到脈絡。
- 「我是不是問過類似的問題？」→ 無法回答，因此持續重做已解決的事。

**價值不在繼續擴大收集，而在讓既有資料可被檢索與再利用。**

> 歷史教訓：檔案事件曾從 3,575 筆噪音 → 4,327 筆 → 收斂至 789 筆。
> 收集越多不等於越有用，新增採集來源必須通過「能否改變決策」的檢驗。

### 3.3 對外定位（若日後開源）

市面同類工具（ActivityWatch、RescueTime、Timing）追蹤的是**時間**；
Rewind、Screenpipe 錄螢幕再 OCR，隱私成本與資源消耗高。

**沒有主流工具在讀本機 AI agent 的 transcript。** `~/.claude/projects/`、
`~/.codex/sessions/`、`.gemini/antigravity/brain/` 這些檔案就在硬碟上，
不需錄螢幕、不需額外權限，而裡面是真正的思考過程。這是本專案的差異化切入點。

### 3.4 讓他人可用的五個障礙

| 障礙 | 現況 | 嚴重度 |
| :--- | :--- | :--- |
| 專案根目錄設定 | `project_resolution.search_roots` 已供 Project State 與 Context Handoff 共用；未設定時退回 watcher roots | 🟢 使用者仍需在首次安裝填入自己的 roots |
| 僅支援 Windows | 視窗採集、桌面通知、開機排程綁 win32 / PowerShell | 🟡 Mac / Linux 使用者無法進入 |
| 發佈打包跨平台未驗證 | Windows isolated wheel/sdist、upgrade、assets 已通過；macOS/Linux matrix 與 public publish 尚未執行 | 🟡 Windows Alpha 可驗證，尚不能宣稱跨平台 release-ready |
| 首次啟動引導未完整 | `main.py init --watch` 已可用，但 Agent/Git 自動偵測與 capability probe 未完成 | 🟡 基本可啟動，複雜來源仍需調 config |
| 必須自備 LLM 金鑰 | 無金鑰時只剩事件流 | 🟡 Ollama 路徑已在，可作免金鑰預設 |

前兩項決定「能不能用」，後三項決定「願不願意留下」。

---

## 4. P3：記憶層（P3-1～P3-5 已完成 Alpha）

> 目標：讓 236 萬字元從「存著」變成「用得到」。**不需要任何新的採集器。**

### ✅ P3-1 專案接續 Context Handoff（已完成）

- **多維度自動提煉引擎 (`core/handoff_engine.py`)**：
  - 自動彙整專案基本資訊、閒置天數、本機絕對路徑、GitHub 倉庫與 PR 清單。
  - 提取最後活躍時間、動作摘要、未結事項（Open Loops）、最近 5 筆 Git Commits (`+insertions/-deletions`) 與關鍵檔案。
  - 智能過濾 CLI 雜訊，提取最近 3~5 輪真實問答結論與歷史決策脈絡，組裝成各主流 AI 即開即用的結構化 Prompt。
- **CLI 終端指令指南 (`python main.py resume`)**：
  - `python main.py resume`（預設最活躍專案）或 `python main.py resume <專案名稱>`。
  - 支援 `-c` / `--copy`（自動寫入 Windows 剪貼簿）、`--turns N`（指定歷史輪數）與 `--json`。
- **Web UI 一鍵無縫接續**：
  - 頂部 `RESUME HERE` 與各專案展開卡片加入 `📋 複製接續 Prompt` 按鈕，點擊彈出 Toast 提示並寫入剪貼簿。
  - 專案清單支援「60 天活躍過濾與展開更多（Show More）」、右側欄「DATA TRUST 可收摺置頂」、「Open Loops 點擊跳轉對焦專案與獨立打勾結案」。

---

### ✅ P3-2 本機語意檢索索引（2026-08-25 完成 Alpha）

- 新增 schema 6/7 `semantic_documents`，使用 loopback Ollama `bge-m3:latest` 建立 1024 維索引，涵蓋 AI turns、Git commits、file activity metadata、Open Loops 與 Project State；不額外讀取檔案正文。
- 每筆保存 `source_ref`、project、timestamp、trust status、content hash、model、float32 BLOB 與 `embedding_input_mode`。partial/legacy response 不會升格為可信結論。
- 每個成功 batch 原子提交並可依 content hash 續跑。初始全量驗收為 4,102/4,102、failure=0；Claude Desktop 修正後 incremental 更新為 4,380/4,380（`indexed=285 / unchanged=4095`）。

### ✅ P3-3 `omni ask`：問自己的歷史（2026-08-25 完成 Alpha）

- `omni ask "我上次怎麼解決 SQLite database locked?" --project activityTracker`
- retrieval-only 與本機 `llama3.1:8b` synthesis 均已實測；回傳 `[S1]` 引用、SQLite source row、時間、專案、trust 與 similarity score。
- loopback-only 預設 fail-closed；similarity 不作來源真實性、完整 coverage 或語意正確證明。

### ✅ P3-4 Related History（2026-08-25 完成 Alpha）

- 新增 `omni recall`、`POST /api/v1/context/related` 與主頁 `RELATED HISTORY`；查詢只送到 loopback Ollama embedding endpoint 且不寫入 SQLite。
- 每筆結果保留 `source_ref`、project、trust status 與 score；Ollama/index 不可用時明確降級，不 fallback 到 cloud。
- `bge-m3` 本機校準中，相關工作約 0.50–0.59、明顯無關查詢約 0.33–0.35，因此 Alpha default threshold 設為 0.50。此值不是通用真實性門檻，也不能直接判定工作重複。

### ✅ P3-5 Derived Session 敘事層（2026-08-25 完成 Alpha）

- 新增 `core/context_memory.py`、`omni sessions`、`GET /api/v1/context/sessions` 與主頁 `RECENT WORK SESSIONS`。
- Session 是 derived view：同 project 事件依 configurable inactivity gap（預設 45 分鐘）分群，以首筆 `source_ref` 建立穩定 ID；不新增 schema、不複製或改寫原始事件。
- 真實 24 小時 smoke 從 332 筆可歸戶事件產生近期 session，AI/Git/file 計數與來源可回查。Window focus 缺少 canonical project identity，因此明確排除。
- Narrative 由 deterministic template 產生，不呼叫 LLM；時間 span 不代表實際工時、任務連續性、專注或成果品質。完整決策見 `docs/ADR-006-derived-context-sessions-and-related-history.md`。

---

## 5. P4：收集層補完（僅限能改變決策的來源）

1. **瀏覽器閱讀內容**：目前只有視窗標題，不知道讀了哪篇論文。擴充套件加上
   「停留超過 60 秒的頁面記錄 URL + 標題」即可，不需抓取內文。
2. **行事曆與會議**：整合既有 Calendar MCP。會議進入 context 後，可在前一晚推播
   「上次與對方談到哪」，讓系統從「記錄過去」跨到「準備未來」。
3. **終端機指令歷史**：解析 PowerShell `ConsoleHost_history.txt`，補上 Git commit
   之前那段最容易遺忘的嘗試過程。
4. **未 commit 的工作狀態**：定期對 49 個 repo 執行 `git status`，比監控檔案異動乾淨，
   可補上「正在改但還沒提交」的盲區。

---

## 6. P5：主動秘書 AI 與自主執行架構（規劃完成；executor blocked by P2.5）

> 核心目標：從「被動記錄與定時摘要」躍升為「主動感知狀態 ➔ 預判前瞻需求 ➔ 提出行動提案 ➔ 一鍵授權背景自主作業」。

```
[ OmniContext 全景事件流 (Git / 檔案 / 跨平台 AI / 視窗) ]
                        ↓
         [ 主動情境與意圖推論引擎 (Evaluator) ]
         (工作段落停頓、專案切換、未結事項逾時、早晚時段觸發)
                        ↓
            [ 主動秘書提案 (Action Proposals) ]
          ↗                                   ↖
[ Web 儀表板 秘書建議卡片 ]               [ Telegram 即時按鈕通知 ]
          ↘                                   ↗
              [ 使用者點擊「✅ 批准執行」]
                        ↓
      [ 安全防護閘門 (3-Tier Safety Gate: L0/L1/L2) ]
                        ↓
         [ 背景任務調度器 (Agent Dispatcher) ]
         ├── 調度 Claude Code CLI / Codex / Antigravity
         ├── 執行本機 Python / Git 腳本
         └── 呼叫學術檢索 API (arXiv / Semantic Scholar)
                        ↓
               [ 任務完成回報與結案存檔 ]
```

### P5-1 主動情境與意圖推論引擎 (`core/proactive_secretary.py`)
- 監聽 SQLite WAL 事件流，在關鍵時機（工作停頓 15 分鐘、專案切換、未結事項逾時、早晨 08:30 / 晚間 22:00）觸發輕量 LLM 分析。
- 自動生成具體的 `ActionProposal` 結構體（目標專案、情境依據、建議行動、預估風險、所需工具與執行命令）。
- **2026-08-26 Alpha scope**：先落地 deterministic proposal-only derived view，只讀取 Project State、`open` Open Loops 與非敏感 Extension status；不呼叫 cloud LLM、不保存 proposal、不執行 command，也不提供批准按鈕。每項建議必須附可回查 `source_ref`，完整契約見 [`ADR-007`](docs/ADR-007-proposal-only-secretary.md)。
- Alpha acceptance：穩定 ID／排序、evidence refs、hostile Origin 403、無 token/path/prompt 全文、主頁 `PROPOSAL ONLY` 標示，以及 localhost live smoke 均通過後才標記完成。
- **Alpha receipt（2026-08-26）**：正式 localhost 從 78 個 Project States／9 個 actionable Open Loops 與 live Extension status 產生 2 張 proposal、3 個 evidence refs；`execution_available=false`、`cloud_llm_used=false`、`query_persisted=false`，hostile Origin 403。桌面與 494px UI 無頁面水平溢出、console 無錯誤；加入 collector source-isolation contract 後完整測試 79/79。
- **具體場景範例**：
  - *論文情境*：「偵測到 `AI_Papers_Auto_Claude` 新增了 3 篇文獻引用但缺少 BibTeX，是否自動檢索 DOI 並補齊文獻庫？」
  - *代碼情境*：「偵測到 `wavePowerSimuPLC` 有 1 項未結事項已停滯 48 小時，是否為您整理現有差異並產出測試診斷腳本？」
  - *協作情境*：「偵測到 GitHub 遠端 PR #30 已被合併，是否一鍵執行本地 fast-forward 同步？」

### P5-2 三級安全防護與授權閘門 (Human-in-the-Loop Safety Gate)
- **Level 0 (唯讀 / 分析)**：免確認自動執行（如文獻檢索、代碼靜態分析、產生 Context Handoff、快照存檔）。
- **Level 1 (輔助操作)**：單鍵確認執行（如 Git pull 同步、整理 Markdown 筆記、格式化檔案、更新未結事項狀態）。
- **Level 2 (高權限修改)**：需明確審閱（如修改原始碼、Git push、建立 PR、呼叫付費外部 API）。

### P5-3 背景任務調度器與 Worker 執行沙盒 (`core/agent_dispatcher.py`)
- 將 `core/handoff_engine.py` 提煉之精確 Context 作為初始 Prompt。
- 調度本機已授權的 Agent 工具（`claude code`、`codex`、`antigravity sidecar` 或本機 Python 工具）在指定沙盒目錄執行。
- 執行完成後自動抓取輸出結果、寫入活動日誌並回報完成狀態。

### P5-4 雙向互動與遠端授權介面
- **Web UI 秘書建議卡片**：於儀表板首頁動態呈現「🤖 秘書待辦提案」，提供 `[✅ 批准執行]`、`[✏️ 修改後執行]`、`[❌ 略過]` 操作。
- **Telegram Bot 雙向互動**：推播提案時附帶 Inline Keyboard 互動按鈕，在外亦可一鍵批准本機秘書開始作業。

### P5-5 智能秘書版晨間簡報與晚間交接
- **晨間前瞻（08:30）**：不只總結昨日，更主動提出「今日建議焦點」、「待決策事項」與「已預備好之 Context Handoff」。
- **晚間歸檔（22:00）**：盤點今日所有已推/未推 commits、自動歸檔未結事項、更新各專案狀態。

### P5-6 `STATUS.yaml` 自動維護與週/月報 Rollup
- 系統已知各專案最後活動與進度線索，自動起草並同步更新各 repo 之 `STATUS.yaml`。
- 將每日摘要自動 Rollup 為週報與月報，供研究進度追蹤與投稿管理。

---

## 7. P6：開源與發佈整備（portability/test 基線提前於 P2.5）

1. [x] `project_engine.py` 與 Context Handoff 已改用 `project_resolution.search_roots`；支援 `~`／環境變數與 watcher-root fallback，不再內嵌個人絕對路徑。
2. 擴充已建立的 `python main.py init`，加入本機 Agent 日誌、Git 根目錄與 notification capability 自動偵測。
3. 維護 `pyproject.toml`、contract tests、schema 7/7、verified backup、formal rollback，以及已通過的 Windows／Ubuntu／macOS × Python 3.10／3.12 CI receipts；下一步取得 live heartbeat 與 ChatGPT／Claude Extension-backed capture receipts。
4. 無 LLM 金鑰時預設走 Ollama，確保零金鑰也能完整體驗。
5. 跨平台：視窗採集與桌面通知抽象出平台介面，Windows 以外先降級為停用而非報錯。

---


---

## 8. ✅ P7：DeskRAG 本地知識庫與文件智慧問答系統深度整合 (Completed)

> 完成日期：2026-08-27 | 狀態：**✅ 已完成並通過 100/100 自動化測試驗證**

已將 deskRAG 本地知識庫系統無縫整併進 activityTracker，徹底實現單一伺服器（Single Server）運作架構，無須啟動雙伺服器：

1. **多格式文件解析中樞 (rag/parsers/)**：
   - PDF（PyMuPDF 高精度擷取與頁碼保留）、Office（Word .docx、PowerPoint .pptx、Excel .xlsx）、Markdown 與多編碼程式原始碼。
2. **階層滑動切分器 (rag/chunker.py)**：
   - 實現重疊窗口切分（Sliding Window with Overlap），保留標題、頁碼、投影片與工作表中繼資料。
3. **混合檢索引擎 (rag/retrieval/)**：
   - 整合 FastEmbed（ONNX 本地極速推論）+ ChromaDB 向量庫。
   - 整合 Jieba 繁簡中文分詞 + BM25Okapi 關鍵字索引與 Pickle 持久化。
   - 實作 Hybrid RRF（倒數排名融合）與 Weighted Fusion（線性加權融合）。
4. **多模型 LLM 網關與 SSE 串流 (rag/llm_gateway.py, rag/router.py)**：
   - 統一調度 Ollama 本機離線模型、Google Gemini、Anthropic Claude、OpenAI，支援逐字 SSE Token 串流與來源引文卡片。
5. **Web 儀表板與 Windows 檔案總管深度整合 (web/)**：
   - 新增 `03 · 知識庫與 RAG` 專屬操作介面。
   - 引文卡片點擊「在總管開啟」即可在 Windows 檔案總管精準定位並選中該檔案。
6. **資料庫遷移與完整測試**：
   - 完成 Migration 008 資料庫結構升級，全專案通過 100/100 單元與整合測試。

### ✅ P7.1：DeskRAG 索引生命週期與主服務隔離（2026-08-29）

1. **worker isolation**：掃描、解析、embedding、移除索引、清空索引、BM25 重建與一致性驗證都透過獨立本機 process 執行；`127.0.0.1:8765` 僅建立、控制與讀取 job receipt。
2. **明確資源邊界**：預設每次最多 500 檔、單檔最多 50 MB、每檔 25 ms 間隔，可由操作介面調整；資料夾完整掃描統計與本次處理上限分開呈現。
3. **安全刪除與回收**：資料夾移除與全部清空都要二次確認，明確保留來源檔案與對話；刪除 worker 批次更新 Chroma/BM25、執行 SQLite checkpoint + `VACUUM`，並保存一致性結果。
4. **可觀察性**：來源檔案、切片、最新 worker 實測向量／BM25 數量與空間以 receipt 回報；主服務不直接讀取大型 Chroma 或 BM25。若 BM25 不一致，可從既有 Chroma 重建，不需重掃來源資料夾。
5. **驗收邊界**：已通過 migration、API confirmation 與 RAG retrieval contract tests；大型正式索引重建屬 worker runtime，完成後才可宣稱 BM25 與 Chroma 一致。

### ✅ P7.2：DeskRAG 離線模型精選選單與對話歷史自動標題管理（2026-08-30）

1. **本機模型下拉選單（Ollama Model Selector）**：
   - 前端輸入框升級為直覺下拉選單，預設提供 4 款本機精選模型（`llama3.1:8b` 預設推薦、`mistral:7b`、`gemma4:e4b`、`qwen3:4b`），全離線免聯網。
   - 支援隨提供者動態切換雲端模型（Google Gemini 3.7 / Anthropic Claude 3.5 / OpenAI GPT-4o）。
2. **對話工作階段生命週期與自動標題（Chat Sessions Lifecycle & Auto-Titling）**：
   - 建立 `CreateSessionRequest` Pydantic 模型，修復 `/api/v1/rag/chat/sessions` 與 `/messages` 的 Request Body 解析問題。
   - 每次新提問自動擷取首句精華作為主題標題（如 `💬 OPC UA 時間序列 預測`），選單首項提供明確的 `➕ 建立新對話`。
   - 點選歷史對話即時還原當次完整問答歷史、引文切片卡片與模型來源。
3. **日常專案活動索引（Activity Indexer）**：
   - 修正 `ProjectState` 與 `OpenLoop` 欄位映射，將近期專案狀態與未結事項轉化為標準虛擬切片供統一語意檢索。
4. **檢索異步化（Async Retrieval）**：
   - 檢索調用改以 `asyncio.to_thread` 異步包裝，避免 475k+ 巨量切片檢索時阻塞 FastAPI 事件循環。
5. **全量測試驗收**：全專案自動化測試 135/135 PASS（含 API boundary、RAG API、Worker 與 Repo Sync）。

---

## 9. ✅ P8：系統基礎穩健化工程（生命週期維護、自我修復守護與 Web 維護面板）(Completed)

> 完成日期：2026-08-27 | 狀態：**✅ 已完成並通過 114/114 自動化測試驗證**

已完成 OmniContext 的全方位基礎穩健化加固，確保系統在 Windows 平台下長期常駐（數週至數月）具備最高等級的可靠性與可觀察性：

1. **第一步：SQLite WAL 自動 Checkpoint、歷史事件修剪與線上輪替備份 (`core/data_lifecycle.py`)**：
   - 每小時背景自動執行 `PRAGMA wal_checkpoint(TRUNCATE)`，防止高頻寫入導致 WAL 檔案無限膨脹。
   - 每日深夜 03:30 自動修剪 90 天前的高頻原始細碎事件（`FileActivityEvent`, `WindowEvent`），保留已計算的每日摘要與檢查點。
   - 滾動備份機制（保留最新 7 份 Verified Backup），杜絕磁碟空間膨脹。
   - 全域 Office 暫存鎖定檔（`~$*.docx`, `~$*.xlsx`）與下載暫存檔排除防呆。
2. **第二步：採集器局部容錯隔離與自我修復守護 (`watchers/` & `core/manager.py`)**：
   - `FileWatcherService`：Watchdog Observer 異常終止檢測與安全自動重啟重排程 (`check_health_and_heal`)。
   - `GitWatcherService`：單一損壞或鎖定 Git 倉庫局部隔離 (`_degraded_repos`)，不中斷其他 60+ 個倉庫掃描，背景線程支援自我修復。
   - `AgentLogWatcherService`：多 AI 來源（Claude Code / Codex / Antigravity / Claude Desktop）故障隔離與熔斷保護。
   - `WatcherManager`：主動巡檢所有已啟用採集器與排程器 (`supervise_and_heal`)，並保存診斷與修復收據。
   - `core/server.py`：暴露 `POST /api/v1/system/heal` 與 `GET /api/v1/system/health`。
3. **第三步：Web 儀表板系統健康燈號與一鍵維護面板 (`web/`)**：
   - 新增 `07 · 🛡️ 系統健康與維護` 專屬操作面板。
   - 視覺化 5 大採集器詳細診斷矩陣（包含失敗目錄、Git 隔離損壞倉庫警示、AI 來源狀態）。
   - 提供「一鍵自我修復」、「立即 WAL Checkpoint」、「執行資料庫完整維護」按鈕與最新維護收據展示。
   - 嵌入深色維護操作即時終端視窗（Action Console），即時輸出結構化 JSON 收據。

---

## 10. 建議執行順序

```
P2.5-S1 API 安全邊界
  → P2.5-R1 採集 provenance / final-response / checkpoint
  → P2.5-L1 Open Loop lifecycle
  → P2.5-P1 pytest / platform abstraction / generic config
  → ✅ P3-1 resume（已完成，並以新資料契約重新驗收）
  → ✅ P3-2 語意索引（4,380/4,380）
  → ✅ P3-3 omni ask（retrieval + local synthesis）
  → ✅ P3-4 Related History（local advisory）
  → ✅ P3-5 Derived Session 敘事層
  → ✅ P5-1 Proposal-only 主動建議（不執行修改；executor 於 871ee29 實作後已 revert 回 ADR-007 契約）
  → ✅ P7 DeskRAG 知識庫深度整合（含 P7.1 worker 隔離、P7.2 模型選單與對話自動標題）
  → ✅ P8 系統基礎穩健化（WAL Checkpoint／歷史修剪／自我修復／健康維護面板）
  → ✅ P4.2 受控本機 Git 同步中心
  → P4.3 Repo Onboarding／Reconciliation（下一里程碑）
  → Extension live PASS receipt 與 P2.6 continuous coverage ledger
  → P5-2+ executor 獨立安全驗收（維持 blocked by P2.5 gate）
  → P4 其餘收集層補完（能改變決策者優先）
  → P6 開源發佈（tag、release、README quickstart 乾淨環境驗證）
```

理由：
1. P3-1 已證明 Context Handoff 的產品價值，但 final-response 與 Open Loop 仍需可信度 gate。
2. P3-2 + P3-3 只有建立在可追溯 turn contract 上，語意檢索結果才可被引用與回查。
3. P5 先做 proposal-only；任何自主修改都必須具備 allowlist、dirty-worktree check、timeout、cancel、audit receipt 與分級批准。

---

## 11. 2026-08-30 專案檢視後的下一步提案（待討論）

> 背景：本日已完成 repository 整理——所有分支收斂於 `main`（`wip/p5-2-agent-executor` 內容已完整包含於 main 歷史，分支指標移除；如需回溯 executor 實作，checkout `871ee29`），並補齊文件索引（`docs/INDEX.md`）與 README 修訂。以下依「先把已實作變成已驗證，再擴張」原則排序。

### 短期（1–2 週）：清除 known_blockers 的驗證債
1. ✅ **Extension live PASS receipt**：在已登入的實機 Chrome 完成 Extension 1.3.1 heartbeat 與 ChatGPT／Claude.ai 本輪 capture 收據（STATUS `known_blockers` 首項，也是 release-ready 的最大缺口）。
   ▶ 2026-08-30：驗收腳本 `scripts/extension_live_acceptance.py` 已完成並通過 localhost E2E。
   ▶ ✅ 2026-08-31 01:03：實機 PASS 取得——heartbeat 驗證通過，ChatGPT／Claude.ai 各 3 event／2 response delta（見 P2.5-B2 與 STATUS.yaml）。
2. ✅ **P2.7 Claude Code／Claude Desktop local-agent live receipt**：目前僅 Codex 有 live completed receipt，補齊其餘兩個來源。
   ▶ 2026-08-30：驗收腳本 `scripts/background_task_live_acceptance.py` 已完成並通過 localhost E2E。
   ▶ ✅ 2026-08-31：**三平台全數 PASS**（2026-08-29 資料：codex 29 筆／14,882 秒、claude_code 7 筆／3,923 秒、claude_desktop 12 筆／16,092 秒；union 28,839 秒）。
3. **P2.6 continuous coverage ledger**：讓每日使用時間的 coverage 脫離永久 `partial` 標示。
   ▶ 2026-08-30：已實作（migration 013 + `core/coverage_ledger.py` + scheduler heartbeat + `/api/v1/usage/coverage`），contract tests 通過；剩 Windows 實機全天 receipt。

### ✅ 中期（2–6 週）：P4.3 Repo Onboarding／Reconciliation（既定 next milestone）
- 依 ADR-011 與 FEATURE-009 trust boundary 實作三種情境的單一 repo 確認式流程：本機資料夾尚未 `git init`、本機 repo 無 remote、GitHub repo 尚未 clone。
- 禁止事項維持：不同名自動配對、不自動初始化／發布、不覆寫非空目錄、不批次 create/clone、不 force reset/push。
  ▶ ✅ 2026-09-01：**已實作**——`core/repo_onboarding.py`＋同步中心「掃描對帳」區塊：對帳報告（已 clone 與否只以 remote URL 正規化比對，同名僅 `name_match_hint` 提示、永不自動配對）＋四個確認式動作（`init_folder` 只建空 .git、`attach_remote` 只接受已同步清單內的 GitHub repo、`clone_repo` 目的地存在即拒絕且 URL 不夾帶 token、`create_remote` 預設 private 且永不代為 push）。API schema `extra=forbid`＋confirmation literal、目標一律 hash id（不接受瀏覽器路徑）、單一目標 lock。契約入 ADR-011 Addendum；8 項 contract tests（真實 tmp git repo＋本機 bare clone E2E）。

### ✅ 中期（可平行）：P6 發佈整備收尾
- Wheel/sdist、formal rollback、3-OS × 2-Python CI 均已通過：走完 `docs/RELEASE_CHECKLIST.md`，打 `v1.3.0aX` tag 併發布 GitHub Release（可先不上 PyPI）。
- 在乾淨環境（或另一台機器）照 README 快速開始逐步驗證一次，修正安裝文件落差。
  ▶ 2026-08-30：已於 Linux container 完成一輪發佈預演——`python -m build`、`verify_release_artifacts`（content + privacy receipt PASS）、乾淨 venv 安裝 wheel、`init`／`assets-status`／`migration-status`（13/13）、web server HTTP smoke 與 `verify_installed_package` checks 全數通過。
  ▶ ✅ 2026-08-31：**v1.3.0a5 已發佈**為 GitHub pre-release——新增 `.github/workflows/release.yml`（推 tag 或 workflow_dispatch 即自動 build → verify → release），附 wheel/sdist 與 SHA-256 receipt，交叉驗證一致。<https://github.com/dofliu/activityTracker/releases/tag/v1.3.0a5>

### 長期（>6 週）：P5-2 executor 重啟與 P4 收集層
- P5-2 executor 曾於 `871ee29` 實作、`f8f5400` revert 回 ADR-007 proposal-only 契約；重啟條件：P2.5 gate 全綠 + allowlist、dirty-worktree check、timeout/cancel、audit receipt、L0/L1/L2 分級批准全數就位，並以獨立 ADR 驗收。
  ▶ 2026-08-31：重啟契約已定稿於 [ADR-008](docs/ADR-008-gated-agent-executor.md)（Proposed）——白名單 action template、三級實質分級、獨立 execution token、L2 一次性 confirm code、audit receipt（migration 014）、失敗封閉；實作依 P5-R1～R5 分階段。
  ▶ ✅ 2026-08-31：**P5-R1 已實作**——`core/secretary_advisor.py` annotate-only LLM 註解層（預設關閉、Ollama 優先、白名單 prompt 欄位、失敗回退 deterministic），11 項 contract tests 與 localhost fallback E2E 通過。
  ▶ ✅ 2026-08-31：**P5-R2 已實作**——ADR-008 D1–D6 落地：`core/agent_executor.py` 白名單 templates（Handoff L0／repo fetch L1／open loop 標 stale L1）、migration 014 audit receipts、獨立 execution token、Web 批准按鈕；16 項 contract tests＋完整閉環 E2E（提案→批准→生效→evidence 改變→提案自動過期）。預設關閉。
  ▶ ✅ 2026-08-31：**P5-R4a 秘書晨報已實作**——08:30 桌面晨間通知與每日入口檔（`OMNICONTEXT_TODAY`）帶入 top 建議與 LLM 總評（`briefing_proposals`，唯讀、失敗不阻斷晨報）；Telegram inline 批准與晚間交接留待 P5-R4b。
  ▶ ✅ 2026-08-31：**P5-R3 已實作**——`core/agent_dispatch.py` subprocess dispatcher（`create_subprocess_exec` argv 白名單、環境變數 allowlist 重建不轉發任何 API key、cwd 限唯一本機 repo、timeout 即 kill、執行中可取消）＋ L2 三道門（獨立開關預設關、一次性 6 碼 confirm code 5 分鐘失效單次有效、每 template 冷卻 429）；首個 L2 template `agent_draft_plan` 調度本機 Claude Code／Codex CLI 為停滯事項起草行動計畫（輸出入 `agent_outputs/`）。9 項新 contract tests。
  ▶ ✅ 2026-08-31：**兩層增量摘要已實作**——migration 015 `activity_micro_summaries`：checkpoint 時段由本機 Ollama 壓成 ≤100 字微摘要（map，失敗靜默跳過），日報 reduce 讀微摘要＋統計、缺漏時段回退原始節錄；token 用量約降一個數量級，Ollama 產日報變為可行。
  ▶ ✅ 2026-08-31：**L2 寫入型 template 已實作（ADR-008 Addendum）**——`agent_apply_plan`：兩段式批准（24h 內 succeeded 的 draft 計畫為前置、計畫全文即 prompt）、第三開關 `l2.allow_write` 預設關、dispatch 前後 `git status --porcelain`（髒 worktree 發碼前即拒）、agent 永不 commit/push（改動留 worktree 供 git diff 檢視／`git checkout .` 還原）、receipt 只記 files_changed 與輸出摘要；設定分頁第三開關。5 項 contract tests（真 git repo＋會寫檔的假 CLI）。
  ▶ ✅ 2026-08-31：**執行器設定 UI 與介紹影片**——「07 監控配置」新增小秘書執行器卡片（executor／L2／L2 寫入三開關＋agent CLI 下拉，redact/merge 熱套用，Playwright 點擊路徑實測）；3 分鐘 repo 介紹影片（18 景 1080p30）已交付，場景源檔入 `promo/` 可單景重渲。
  ▶ ✅ 2026-08-31：**P5-R5 已實作**——`core/scheduled_tasks.py` 使用者自訂排程任務：只能排程 server 註冊的 L0 唯讀 template（L1/L2 永不可排程；模組載入即強制）、開關疊加預設關閉、migration 016 排程表、每次執行寫 `agent_execution_receipts`（approved_via=schedule）、錯過只補跑一次。首批 templates：`generate_handoff`、週報／月報 rollup（`synthesizer/rollup.py`：只彙整既有每日摘要、缺日誠實列出、LLM 失敗回退 deterministic）、`status_snapshot_draft`（`core/status_draft.py`：STATUS.yaml 過期點名草稿，絕不寫使用者 repo）。管理 UI 在「07 監控配置 → 小秘書執行器」；mutation API 需 execution token。17 項 contract tests；同輪修正 `cancel_execution`「先 kill 後 commit」競態（改為先提交 cancelled 再 kill）。
  ▶ ✅ 2026-08-31：**Telegram 介面化設定流程已實作（P5-R4b 前置）**——`notifiers/telegram_setup.py`＋儀表板「06 Telegram 通知」卡片：貼 bot token → `getUpdates` 偵測 chat id → 即時連線測試（`getMe` 驗 token＋實發固定內容測試訊息）→ 全部通過才寫 config 並熱套用排程。secret 永不回流瀏覽器（redact/merge 既有機制涵蓋 bot_token/chat_id）、環境變數優先且不複製進檔案、驗證失敗 config 完全不動。15 項 contract tests（fake transport，不需真實 token）。**使用者現在可直接在介面完成 Telegram 設定**，P5-R4b（inline 批准＋晚間交接）只剩 bot 端互動實作。
  ▶ ✅ 2026-09-01：**P5-R4b 已實作**——`notifiers/telegram_approvals.py` Telegram inline 批准＋晚間交接：getUpdates 長輪詢（outbound only、不開 port）、批准通道需儀表板以 execution token 解鎖（arm，in-memory＋TTL、重啟即失效）、雙開關預設關閉、只處理綁定 chat、只批 L0/L1（L2 立即作廢 confirm code 導回儀表板）、每次批准寫 approved_via=telegram_inline receipt；晨報／晚間交接推播附「✅ 批准」按鈕、`/proposals` 指令；14 項 contract tests。**ADR-008 P5-R1～R5 全階段完成**。
  ▶ ✅ 2026-09-01：**儀表板資訊架構重整（兩輪）＋配色主題**——(1) 導覽由 8 分頁收斂為 6 並分主次（小秘書與知識庫／進行中工作／摘要與快照為主，情報流／設定／系統健康弱化）；RAG 完整區塊併入小秘書分頁（本就共用同一條對話）、活動快照併入摘要分頁、本機 Git 同步中心＋對帳移到「進行中工作」並改為展開才掃描；設定分頁分「秘書與自動化（常用，展開）」與「其他設定（收合）」兩區、頂部固定「儲存並套用」列、卡內再以巢狀折疊收納排程任務與 Telegram 連線設定（已連線自動收合），折疊狀態記於 localStorage。(2) 外觀拆成 `data-theme`（dark/light）× `data-accent`（naruto/forest/ocean）兩軸，CSS 全面走 `var(--accent)`／`--accent-hover`／`--accent-ink`，新配色只需加一組變數區塊；偏好存 localStorage 不進 config。Playwright 實測 6 分頁 × 窄版 494px 零水平溢出、6 種配色組合對比 5.37–6.99:1（AA 門檻 4.5）。同輪修掉兩個既有 bug：`/api/v1/projects/active` 的 `NameError`（未 import）與從未定義的 `--ok` CSS 變數。
  ▶ ✅ 2026-09-01：**RAG 雲端 provider 修復**——`resolve_secret_env()` 回傳 `SecretResolution` 物件，`rag/` 內 4 處直接當字串使用，導致（a）`if not api_key` 恆為偽（dataclass 恆真值），「未設定金鑰」提示永不出現；（b）物件 repr 被帶進 Gemini 請求 URL，**所有雲端 provider 的 RAG 對話一律 400 失敗**，且金鑰值落在可能進 log 的 URL 中。修法：統一 `_resolve_api_key()` 取 `.value`＋沿用設定的 `api_key_env` 名稱與 `GOOGLE_API_KEY` alias；Gemini 金鑰改走 `x-goog-api-key` header 不進 URL；SSE 產生器全程 try/finally **保證送出 `done`**（瀏覽器解除「回覆中」的唯一依據），檢索移入產生器並加 60 秒逾時與前置 `status` 事件，前端補 120 秒閒置 abort。10 項新 contract tests（含全 `rag/` 套件禁止裸用 `resolve_secret_env` 的守門測試）。
  ▶ ✅ 2026-09-02：**檢索移出主服務程序（ADR-009 Addendum；原 TODO B1 根因修法）**——新增常駐檢索 worker `rag/retrieval_worker.py`（stdin/stdout JSON lines、stdout 只承載協定、stdin 關閉即退出、不做寫入）與主服務端 `rag/retrieval_client.py`（lazy 啟動、逾時即 kill 並在下次提問自動重啟、崩潰／錯誤一律降級為不帶文件脈絡照常回答、SSE 仍保證 `done`）；服務啟動後有索引才背景預熱（無索引不啟動子程序，避免觸發模型下載），`rag.retrieval.mode: in_process` 保留舊行為。`/api/v1/rag/strategies` 改讀靜態目錄、`rag/retrieval/__init__.py` 改 lazy export，**乾淨直譯器 import `core.server` 不再載入 chromadb／fastembed／rank_bm25／jieba**（契約測試守門）。新增 `GET /retrieval/status`、`POST /retrieval/warmup`、`POST /retrieval/shutdown` 與知識庫區塊「檢索 worker」卡片（狀態／切片數／預熱耗時／worker 記憶體、預熱與釋放按鈕）。容器 E2E 收據：主服務 RSS 88 MB、worker（載入 embedding 模型後）335 MB、預熱 3.9 秒（空索引＋首次模型下載）、chat 經 worker 檢索後 `done` 正常收尾、釋放後無殘留程序；Playwright 卡片渲染與按鈕啟停無 JS 錯誤。20 項新 contract tests（假 worker 腳本測 lifecycle、真 worker 程序測協定）；同輪把缺 `xdg-open` 的測試改為條件 skip（原 TODO B2）。**大索引（475k chunks）實機收據待使用者取得（TODO A6）。**
  ▶ ✅ 2026-09-02：**Repo 同步全覽、批次與小秘書同步報告（ADR-011 Addendum B）**——使用者提出「列出所有 GitHub 專案本地／遠端是否同步、可一一或全面執行、小秘書每天確認」；對照後：GitHub 帳號驗證流程（設定 07）與逐一動作（同步中心）既有，缺的是全覽、批次與秘書參與。實作：`sync-status?scope=all` 全部 repo＋`last_fetch_at`＋summary；`sync-fetch-all` 一鍵 fetch --prune（唯一不需列清單的批次，因只動 remote-tracking refs）；批次 Pull／Push 採「先列符合條件清單→確認→逐一在 lock 內重檢」（`sync-batch-plan`／`sync-batch`，schema `extra=forbid`、單次上限 50、永不 force），批次 Push 獨立開關 `repository_sync.batch.allow_push` 預設關；小秘書新增 L0 排程 template `repo_sync_report`（唯讀不連網，寫 `reports/repo_sync/` 報告與快照）→ proposals 讀新鮮快照產生 `repo_needs_pull／repo_needs_push／repo_diverged` → `repo_needs_pull` 對應新 L1 `repo_pull_ff`（批准後執行、仍重檢），push 不代辦、分歧只提醒；「每天自動 pull」依 ADR-008 仍不存在。UI：同步中心新增「全覽與批次」表格（篩選 chip、上次 fetch 欄、逐列動作）。9 項新 contract tests（真 tmp git repo：fetch_all 不動 worktree、批次只碰清單內且重檢、push 預設 409、報告不 fetch、快照過期不提案、executor 對應 L1）；容器 Playwright E2E 走完 載入全覽→全部 Fetch→批次 Pull→批次 Push。**實機收據待使用者取得（TODO A7）。**
  下一步：待辦清單集中於 [docs/TODO.md](docs/TODO.md)——目前只剩使用者側 live 收據（全天 coverage ledger 為唯一擋 `release_ready` 的能力缺口）與可選的功能候選。交接資訊見 docs/NEXT_SESSION.md。
- P4 其餘來源（瀏覽器閱讀、行事曆、terminal history、未 commit 狀態）維持「能否改變決策」檢驗，逐項評估後才納入。
