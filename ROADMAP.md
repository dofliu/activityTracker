# OmniContext 開發規劃與成果紀錄 — P0 ~ P8

> 最新更新日期：2026-09-13　｜　目前狀態：**personal alpha（v1.3.0a5 pre-release）／P0–P8 全數完成＋ADR-008 P5-R1～R5 全階段落地**。
> P2.6 coverage ledger、P3 context memory、P4.2/4.3 Git 同步與對帳、P5 分級執行器與排程、P6 發佈整備、P7 DeskRAG、P8 自我修復均已實作；
> 秘書側另有記憶區（ADR-012）／Telegram 對話（ADR-013）／多通道推播（ADR-014）／本機行事曆（ADR-015）／驗收中心（ADR-016）／模式感知提案（ADR-017）／
> 宣告式個人檔案（ADR-018）／秘書桌面（ADR-019）／每週回顧（ADR-020）／文件落後偵測（ADR-021）／會議秘書第一層（ADR-022）。**危險能力一律預設關閉。**
> 66 個 contract test 模組、663 項測試（662 passed + 1 skipped；不裝 `[rag]` extra 時 650 passed + 12 skipped）；schema migration 18/18。
> **仍不具 release-ready 資格**：全天 coverage ledger 實測與各功能的使用者實機收據未齊（見 §12 與 [docs/TODO.md](docs/TODO.md)）。
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
1. **Web UI 全功能儀表板 (`web/index.html`, `web/js/`)**：
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

### 桌面通知 `notifiers/desktop_notifier.py` ＋ `notifiers/channels.DesktopChannel`
- 直接以 PowerShell 呼叫 Windows WinRT `ToastNotificationManager`，**不依賴 winotify / plyer**，無需安裝套件或申請帳號；WinRT 不可用時自動降級為 `MessageBox`。
- 三種情境：晨間簡報（08:30）、今日回顧（22:00）、專案停滯提醒（預設閒置 5 天）。
- 2026-09-16（D5）起桌面是第三個 `ChannelAdapter`：**內容與 Telegram／LINE 同一份組裝**（`notifiers/messages.py`），扇出同一處（`notifiers/secretary_push.py`），`desktop_notifier.py` 只負責送達。
- toast 放不下整份晨報，`render_toast()` 每個分節只取前兩行並留下省略記號——摘要，不是截頭。
- 點擊通知直接開啟儀表板；`--dry-run` 可在終端機預覽。
- 自動濾除 `General / Notes` 這類未歸戶收容桶，不讓它佔用提醒版面（判定只有 `core.project_engine.is_bucket_project` 一份，三個通道一致）。

### 每日入口簡報 `exporters/daily_brief.py`
- 產出 `OMNICONTEXT_TODAY.md` 與 `OMNICONTEXT_TODAY.html` 至 `exporters.daily_brief.output_dir`（預設 `D:/Project_CodingSimulation`）。
- HTML 版每 5 分鐘自動刷新，可設為瀏覽器書籤或首頁；Markdown 版可被其他工具或 AI 直接讀取。
- 設定 `inject_into` 指向既有 HTML 儀表板時，改為在 `<!-- OMNICONTEXT:START/END -->` 標記間注入。
- **注意**：`MCP/LabPagesCowork/` 底下的儀表板會被 `deploy_dashboard.py` 推送到公開 GitHub Pages，因此預設不注入該檔案，避免個人工作紀錄外流。

### 資料清洗 `scripts/purge_legacy_data.py`（一次性腳本，2026-09-16 R0 已自 repo 移除，見 git 歷史）
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
3. 維護 `pyproject.toml`、contract tests、schema migration registry（目前 18/18）、verified backup、formal rollback，以及已通過的 Windows／Ubuntu／macOS × Python 3.10／3.12 CI receipts；下一步取得 live heartbeat 與 ChatGPT／Claude Extension-backed capture receipts。
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
4. **多模型 LLM 網關與 SSE 串流 (rag/llm_gateway.py → 2026-09-16 D2 後併入 core/llm_client.py, rag/router.py)**：
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

## 11. 成果紀錄（Results Log）

> 本節是**做過什麼**的單一紀錄。還沒做的看 [docs/TODO.md](docs/TODO.md)（每項附完成判準），
> 為什麼這樣設計看對應 ADR，接下來的方向看 §12。

### 11.1 2026-08-30 專案檢視的三個時間桶（皆已完成）

> 背景：本日已完成 repository 整理——所有分支收斂於 `main`（`wip/p5-2-agent-executor` 內容已完整包含於 main 歷史，分支指標移除；如需回溯 executor 實作，checkout `871ee29`），並補齊文件索引（`docs/INDEX.md`）與 README 修訂。以下依「先把已實作變成已驗證，再擴張」原則排序。

#### 短期（1–2 週）：清除 known_blockers 的驗證債
1. ✅ **Extension live PASS receipt**：在已登入的實機 Chrome 完成 Extension 1.3.1 heartbeat 與 ChatGPT／Claude.ai 本輪 capture 收據（STATUS `known_blockers` 首項，也是 release-ready 的最大缺口）。
   ▶ 2026-08-30：驗收腳本 `scripts/extension_live_acceptance.py` 已完成並通過 localhost E2E。
   ▶ ✅ 2026-08-31 01:03：實機 PASS 取得——heartbeat 驗證通過，ChatGPT／Claude.ai 各 3 event／2 response delta（見 P2.5-B2 與 STATUS.yaml）。
2. ✅ **P2.7 Claude Code／Claude Desktop local-agent live receipt**：目前僅 Codex 有 live completed receipt，補齊其餘兩個來源。
   ▶ 2026-08-30：驗收腳本 `scripts/background_task_live_acceptance.py` 已完成並通過 localhost E2E。
   ▶ ✅ 2026-08-31：**三平台全數 PASS**（2026-08-29 資料：codex 29 筆／14,882 秒、claude_code 7 筆／3,923 秒、claude_desktop 12 筆／16,092 秒；union 28,839 秒）。
3. **P2.6 continuous coverage ledger**：讓每日使用時間的 coverage 脫離永久 `partial` 標示。
   ▶ 2026-08-30：已實作（migration 013 + `core/coverage_ledger.py` + scheduler heartbeat + `/api/v1/usage/coverage`），contract tests 通過；剩 Windows 實機全天 receipt。

#### ✅ 中期（2–6 週）：P4.3 Repo Onboarding／Reconciliation（既定 next milestone）
- 依 ADR-011 與 FEATURE-009 trust boundary 實作三種情境的單一 repo 確認式流程：本機資料夾尚未 `git init`、本機 repo 無 remote、GitHub repo 尚未 clone。
- 禁止事項維持：不同名自動配對、不自動初始化／發布、不覆寫非空目錄、不批次 create/clone、不 force reset/push。
  ▶ ✅ 2026-09-01：**已實作**——`core/repo_onboarding.py`＋同步中心「掃描對帳」區塊：對帳報告（已 clone 與否只以 remote URL 正規化比對，同名僅 `name_match_hint` 提示、永不自動配對）＋四個確認式動作（`init_folder` 只建空 .git、`attach_remote` 只接受已同步清單內的 GitHub repo、`clone_repo` 目的地存在即拒絕且 URL 不夾帶 token、`create_remote` 預設 private 且永不代為 push）。API schema `extra=forbid`＋confirmation literal、目標一律 hash id（不接受瀏覽器路徑）、單一目標 lock。契約入 ADR-011 Addendum；8 項 contract tests（真實 tmp git repo＋本機 bare clone E2E）。

#### ✅ 中期（可平行）：P6 發佈整備收尾
- Wheel/sdist、formal rollback、3-OS × 2-Python CI 均已通過：走完 `docs/RELEASE_CHECKLIST.md`，打 `v1.3.0aX` tag 併發布 GitHub Release（可先不上 PyPI）。
- 在乾淨環境（或另一台機器）照 README 快速開始逐步驗證一次，修正安裝文件落差。
  ▶ 2026-08-30：已於 Linux container 完成一輪發佈預演——`python -m build`、`verify_release_artifacts`（content + privacy receipt PASS）、乾淨 venv 安裝 wheel、`init`／`assets-status`／`migration-status`（13/13）、web server HTTP smoke 與 `verify_installed_package` checks 全數通過。
  ▶ ✅ 2026-08-31：**v1.3.0a5 已發佈**為 GitHub pre-release——新增 `.github/workflows/release.yml`（推 tag 或 workflow_dispatch 即自動 build → verify → release），附 wheel/sdist 與 SHA-256 receipt，交叉驗證一致。<https://github.com/dofliu/activityTracker/releases/tag/v1.3.0a5>

#### ✅ 長期（>6 週）：P5-2 executor 重啟與 P4 收集層

> 這個桶子後來承接了 2026-08-31 之後的所有功能；逐項紀錄見下方 **§11.2**。
- P5-2 executor 曾於 `871ee29` 實作、`f8f5400` revert 回 ADR-007 proposal-only 契約；重啟條件：P2.5 gate 全綠 + allowlist、dirty-worktree check、timeout/cancel、audit receipt、L0/L1/L2 分級批准全數就位，並以獨立 ADR 驗收。
- P4 其餘來源（瀏覽器閱讀、行事曆、terminal history、未 commit 狀態）維持「能否改變決策」檢驗，逐項評估後才納入。

### 11.2 功能成果（依日期）

- 2026-08-31：重啟契約已定稿於 [ADR-008](docs/ADR-008-gated-agent-executor.md)（Proposed）——白名單 action template、三級實質分級、獨立 execution token、L2 一次性 confirm code、audit receipt（migration 014）、失敗封閉；實作依 P5-R1～R5 分階段。
- ✅ 2026-08-31：**P5-R1 已實作**——`core/secretary_advisor.py` annotate-only LLM 註解層（預設關閉、Ollama 優先、白名單 prompt 欄位、失敗回退 deterministic），11 項 contract tests 與 localhost fallback E2E 通過。
- ✅ 2026-08-31：**P5-R2 已實作**——ADR-008 D1–D6 落地：`core/agent_executor.py` 白名單 templates（Handoff L0／repo fetch L1／open loop 標 stale L1）、migration 014 audit receipts、獨立 execution token、Web 批准按鈕；16 項 contract tests＋完整閉環 E2E（提案→批准→生效→evidence 改變→提案自動過期）。預設關閉。
- ✅ 2026-08-31：**P5-R4a 秘書晨報已實作**——08:30 桌面晨間通知與每日入口檔（`OMNICONTEXT_TODAY`）帶入 top 建議與 LLM 總評（`briefing_proposals`，唯讀、失敗不阻斷晨報）；Telegram inline 批准與晚間交接留待 P5-R4b。
- ✅ 2026-08-31：**P5-R3 已實作**——`core/agent_dispatch.py` subprocess dispatcher（`create_subprocess_exec` argv 白名單、環境變數 allowlist 重建不轉發任何 API key、cwd 限唯一本機 repo、timeout 即 kill、執行中可取消）＋ L2 三道門（獨立開關預設關、一次性 6 碼 confirm code 5 分鐘失效單次有效、每 template 冷卻 429）；首個 L2 template `agent_draft_plan` 調度本機 Claude Code／Codex CLI 為停滯事項起草行動計畫（輸出入 `agent_outputs/`）。9 項新 contract tests。
- ✅ 2026-08-31：**兩層增量摘要已實作**——migration 015 `activity_micro_summaries`：checkpoint 時段由本機 Ollama 壓成 ≤100 字微摘要（map，失敗靜默跳過），日報 reduce 讀微摘要＋統計、缺漏時段回退原始節錄；token 用量約降一個數量級，Ollama 產日報變為可行。
- ✅ 2026-08-31：**L2 寫入型 template 已實作（ADR-008 Addendum）**——`agent_apply_plan`：兩段式批准（24h 內 succeeded 的 draft 計畫為前置、計畫全文即 prompt）、第三開關 `l2.allow_write` 預設關、dispatch 前後 `git status --porcelain`（髒 worktree 發碼前即拒）、agent 永不 commit/push（改動留 worktree 供 git diff 檢視／`git checkout .` 還原）、receipt 只記 files_changed 與輸出摘要；設定分頁第三開關。5 項 contract tests（真 git repo＋會寫檔的假 CLI）。
- ✅ 2026-08-31：**執行器設定 UI 與介紹影片**——「07 監控配置」新增小秘書執行器卡片（executor／L2／L2 寫入三開關＋agent CLI 下拉，redact/merge 熱套用，Playwright 點擊路徑實測）；3 分鐘 repo 介紹影片（18 景 1080p30）已交付，場景源檔入 `promo/` 可單景重渲。
- ✅ 2026-08-31：**P5-R5 已實作**——`core/scheduled_tasks.py` 使用者自訂排程任務：只能排程 server 註冊的 L0 唯讀 template（L1/L2 永不可排程；模組載入即強制）、開關疊加預設關閉、migration 016 排程表、每次執行寫 `agent_execution_receipts`（approved_via=schedule）、錯過只補跑一次。首批 templates：`generate_handoff`、週報／月報 rollup（`synthesizer/rollup.py`：只彙整既有每日摘要、缺日誠實列出、LLM 失敗回退 deterministic）、`status_snapshot_draft`（`core/status_draft.py`：STATUS.yaml 過期點名草稿，絕不寫使用者 repo）。管理 UI 在「07 監控配置 → 小秘書執行器」；mutation API 需 execution token。17 項 contract tests；同輪修正 `cancel_execution`「先 kill 後 commit」競態（改為先提交 cancelled 再 kill）。
- ✅ 2026-08-31：**Telegram 介面化設定流程已實作（P5-R4b 前置）**——`notifiers/telegram_setup.py`＋儀表板「06 Telegram 通知」卡片：貼 bot token → `getUpdates` 偵測 chat id → 即時連線測試（`getMe` 驗 token＋實發固定內容測試訊息）→ 全部通過才寫 config 並熱套用排程。secret 永不回流瀏覽器（redact/merge 既有機制涵蓋 bot_token/chat_id）、環境變數優先且不複製進檔案、驗證失敗 config 完全不動。15 項 contract tests（fake transport，不需真實 token）。**使用者現在可直接在介面完成 Telegram 設定**，P5-R4b（inline 批准＋晚間交接）只剩 bot 端互動實作。
- ✅ 2026-09-01：**P5-R4b 已實作**——`notifiers/telegram_approvals.py` Telegram inline 批准＋晚間交接：getUpdates 長輪詢（outbound only、不開 port）、批准通道需儀表板以 execution token 解鎖（arm，in-memory＋TTL、重啟即失效）、雙開關預設關閉、只處理綁定 chat、只批 L0/L1（L2 立即作廢 confirm code 導回儀表板）、每次批准寫 approved_via=telegram_inline receipt；晨報／晚間交接推播附「✅ 批准」按鈕、`/proposals` 指令；14 項 contract tests。**ADR-008 P5-R1～R5 全階段完成**。
- ✅ 2026-09-01：**儀表板資訊架構重整（兩輪）＋配色主題**——(1) 導覽由 8 分頁收斂為 6 並分主次（小秘書與知識庫／進行中工作／摘要與快照為主，情報流／設定／系統健康弱化）；RAG 完整區塊併入小秘書分頁（本就共用同一條對話）、活動快照併入摘要分頁、本機 Git 同步中心＋對帳移到「進行中工作」並改為展開才掃描；設定分頁分「秘書與自動化（常用，展開）」與「其他設定（收合）」兩區、頂部固定「儲存並套用」列、卡內再以巢狀折疊收納排程任務與 Telegram 連線設定（已連線自動收合），折疊狀態記於 localStorage。(2) 外觀拆成 `data-theme`（dark/light）× `data-accent`（naruto/forest/ocean）兩軸，CSS 全面走 `var(--accent)`／`--accent-hover`／`--accent-ink`，新配色只需加一組變數區塊；偏好存 localStorage 不進 config。Playwright 實測 6 分頁 × 窄版 494px 零水平溢出、6 種配色組合對比 5.37–6.99:1（AA 門檻 4.5）。同輪修掉兩個既有 bug：`/api/v1/projects/active` 的 `NameError`（未 import）與從未定義的 `--ok` CSS 變數。
- ✅ 2026-09-01：**RAG 雲端 provider 修復**——`resolve_secret_env()` 回傳 `SecretResolution` 物件，`rag/` 內 4 處直接當字串使用，導致（a）`if not api_key` 恆為偽（dataclass 恆真值），「未設定金鑰」提示永不出現；（b）物件 repr 被帶進 Gemini 請求 URL，**所有雲端 provider 的 RAG 對話一律 400 失敗**，且金鑰值落在可能進 log 的 URL 中。修法：統一 `_resolve_api_key()` 取 `.value`＋沿用設定的 `api_key_env` 名稱與 `GOOGLE_API_KEY` alias；Gemini 金鑰改走 `x-goog-api-key` header 不進 URL；SSE 產生器全程 try/finally **保證送出 `done`**（瀏覽器解除「回覆中」的唯一依據），檢索移入產生器並加 60 秒逾時與前置 `status` 事件，前端補 120 秒閒置 abort。10 項新 contract tests（含全 `rag/` 套件禁止裸用 `resolve_secret_env` 的守門測試）。
- ✅ 2026-09-02：**檢索移出主服務程序（ADR-009 Addendum；原 TODO B1 根因修法）**——新增常駐檢索 worker `rag/retrieval_worker.py`（stdin/stdout JSON lines、stdout 只承載協定、stdin 關閉即退出、不做寫入）與主服務端 `rag/retrieval_client.py`（lazy 啟動、逾時即 kill 並在下次提問自動重啟、崩潰／錯誤一律降級為不帶文件脈絡照常回答、SSE 仍保證 `done`）；服務啟動後有索引才背景預熱（無索引不啟動子程序，避免觸發模型下載），`rag.retrieval.mode: in_process` 保留舊行為。`/api/v1/rag/strategies` 改讀靜態目錄、`rag/retrieval/__init__.py` 改 lazy export，**乾淨直譯器 import `core.server` 不再載入 chromadb／fastembed／rank_bm25／jieba**（契約測試守門）。新增 `GET /retrieval/status`、`POST /retrieval/warmup`、`POST /retrieval/shutdown` 與知識庫區塊「檢索 worker」卡片（狀態／切片數／預熱耗時／worker 記憶體、預熱與釋放按鈕）。容器 E2E 收據：主服務 RSS 88 MB、worker（載入 embedding 模型後）335 MB、預熱 3.9 秒（空索引＋首次模型下載）、chat 經 worker 檢索後 `done` 正常收尾、釋放後無殘留程序；Playwright 卡片渲染與按鈕啟停無 JS 錯誤。20 項新 contract tests（假 worker 腳本測 lifecycle、真 worker 程序測協定）；同輪把缺 `xdg-open` 的測試改為條件 skip（原 TODO B2）。**大索引（475k chunks）實機收據待使用者取得（TODO A6）。**
- ✅ 2026-09-02：**Repo 同步全覽、批次與小秘書同步報告（ADR-011 Addendum B）**——使用者提出「列出所有 GitHub 專案本地／遠端是否同步、可一一或全面執行、小秘書每天確認」；對照後：GitHub 帳號驗證流程（設定 07）與逐一動作（同步中心）既有，缺的是全覽、批次與秘書參與。實作：`sync-status?scope=all` 全部 repo＋`last_fetch_at`＋summary；`sync-fetch-all` 一鍵 fetch --prune（唯一不需列清單的批次，因只動 remote-tracking refs）；批次 Pull／Push 採「先列符合條件清單→確認→逐一在 lock 內重檢」（`sync-batch-plan`／`sync-batch`，schema `extra=forbid`、單次上限 50、永不 force），批次 Push 獨立開關 `repository_sync.batch.allow_push` 預設關；小秘書新增 L0 排程 template `repo_sync_report`（唯讀不連網，寫 `reports/repo_sync/` 報告與快照）→ proposals 讀新鮮快照產生 `repo_needs_pull／repo_needs_push／repo_diverged` → `repo_needs_pull` 對應新 L1 `repo_pull_ff`（批准後執行、仍重檢），push 不代辦、分歧只提醒；「每天自動 pull」依 ADR-008 仍不存在。UI：同步中心新增「全覽與批次」表格（篩選 chip、上次 fetch 欄、逐列動作），並依使用者要求把整個同步中心（逐一動作＋全覽批次＋P4.3 對帳）從「02 進行中工作」的折疊卡獨立為 **03 · Git 同步中心** 分頁（切到分頁才掃描；其餘分頁順延編號為 04–07）；「重新歸戶」按鈕實際只重讀清單與最近事件、不做伺服器端重新歸戶，已改名「重新整理」以免誤導。9 項新 contract tests（真 tmp git repo：fetch_all 不動 worktree、批次只碰清單內且重檢、push 預設 409、報告不 fetch、快照過期不提案、executor 對應 L1）；容器 Playwright E2E 走完 載入全覽→全部 Fetch→批次 Pull→批次 Push。**實機收據待使用者取得（TODO A7）。**
- ✅ 2026-09-02：**儀表板資訊整併（01 今天／02 專案／04 統計）＋小秘書更主動**——使用者指出「進行中的工作、上次做到哪、Active Workstreams、秘書建議、同步中心」高度重複。整併：**01** 改為「TODAY · 今日行動清單」——最上方是「上次做到哪」（原 02 的 Resume 卡）、早晨包一行摘要、其下是秘書提案（每項多一句**「為什麼是現在」**，停滯／未收尾事項另提示可開 L2 讓小秘書先起草計畫）；**02** 只留專案卡，卡上多 git 狀態 chip（來自 L0 同步報告快照、附 fetch 時間）與「💡 N 建議」chip，展開卡內新增「近期工作階段」（原獨立面板移入），Related History 改為底部折疊卡；前景使用／資料收集／背景任務三張統計面板移到 **04 · 摘要與統計**。秘書主動性：新 L0 template `morning_pack`（同步報告＋STATUS 草稿＋活躍專案 Handoff，三步各自容錯、收據扁平）與 `handoff_active_projects`（時窗＋上限）、`POST /api/v1/secretary/scheduled-tasks/presets` 一鍵建立 07:30 早晨包＋21:30 晚間 Handoff（idempotent、需 execution token、受 ADR-008 疊加開關）、`GET /api/v1/secretary/today`、`GET /api/v1/repos/sync-snapshot`；晨報帶入早晨包一行與 top 建議的「為什麼是現在」。全部仍是 L0 唯讀，沒有自動 pull／push。`tests/test_secretary_packs.py` 9 項；容器 Playwright 走完 01→建立排程→手動跑早晨包→今日出現早晨包摘要→02 chips／展開卡 sessions→04 統計。**實機收據待使用者取得（TODO A8）。**
- ✅ 2026-09-02：**01 三欄版面＋知識庫拆回獨立分頁＋Extension 403 可讀化**——01 小秘書改為左（今日行動清單）／中（交辦與提問）／右（全站 rail：今日統計可收合＋Focus Now，寬度 296→272px）三欄，1280px 以下自動疊成單欄；知識庫（RAG 完整對話／引用／索引管理）依使用者要求從 01 拆回「02 · 知識庫」獨立分頁（共用同一條 RAG 對話），其餘分頁順延為 03–08。Extension 寫入被拒時 server 回 403 detail `extension ingest token missing/mismatch`＋hint，並每 60 秒一則節流 WARNING（原本只有一串裸 403 難以判讀）。Playwright：1600／1280／494 px 三欄→單欄切換正確、統計欄可收合、知識庫分頁可用、無溢出無錯誤。
- ✅ 2026-09-02：**小秘書記憶區（ADR-012）已實作**——migration 017 `secretary_notes`（筆記／偏好／決定＋秘書觀察，觀察依 source_ref 每日去重、可單筆或一鍵刪除）；對話前綴「記下來／偏好／決定」直接寫筆記不送 LLM；`memory_context()` 把今日狀態＋top 提案＋筆記注入每次 RAG 對話（上限 2500 字、SSE `memory` 收據、`GET /secretary/memory/context` 可檢視）；提案引擎讀偏好 `不要提醒 X` 壓提案並附同專案決定；`activity_indexer` 新增筆記／微摘要／`reports/` 白名單報告切片，經 RAG worker job `activity_sync` 併入知識庫。01 中欄新增記憶區面板。`tests/test_secretary_memory.py` 20 項。
- ✅ 2026-09-03：**Telegram 小秘書對話（ADR-013）已實作**——手機在綁定 chat 直接打字即提問,走與儀表板交辦框同一條管線(`core/secretary_ask.ask_secretary`:記憶區脈絡＋`rag.router._retrieve_citations`＋所選 LLM),回覆附引用檔名與「參考記憶區 N 筆」;「記下來／偏好／決定」用同一套 `parse_note_command` 寫進 `secretary_notes`(source=telegram)不送 LLM;`/today` `/notes` `/status` `/help` 唯讀指令;批准維持 ADR-008 兩道門(只 L0/L1、需先 arm),新增 `/arm <token>`(獨立開關預設關、訊息即刻刪除、token 不進 log/receipt)與永遠可用的 `/disarm`;問答丟背景執行緒、同時只一題、長答案分段;poller 改為「批准或對話任一啟用」才開。三個開關全部預設關閉,隱私邊界(內容經 Telegram)寫在 UI／config／USAGE 三處。`tests/test_telegram_chat.py` 26 項。
- ✅ 2026-09-03：**多通道推播與一次性解鎖碼（ADR-014）已實作**——內容與呈現分離(`notifiers/messages.py` 的通道中立 `Message`＋`render_plain`／`render_telegram_html`,內容一律 escape)、adapter 層(`notifiers/channels.py`,`TelegramChannel`／`LineChannel` 各自宣告 receive／buttons／delete_message／rich_text)、扇出(`notifiers/secretary_push.py`,逐通道 try/except 與 receipt);**LINE 只能推播**(`notifiers/line_setup.py`:憑證環境變數優先、`/v2/bot/info` 驗 token＋實發測試訊息、驗證通過才寫 config、token 只走 Authorization header)——原因是 LINE Messaging API 沒有輪詢介面,接收訊息需要公開 webhook,會打破 ADR-001 的 loopback-only 邊界,故提問與批准仍走 Telegram。`/arm` 改收儀表板簽發的 6 位數短效碼(只存雜湊、300 秒失效、單次、猜錯即焚、disarm 與重啟即銷毀),手機從此不必持有長期 execution token。`main.py notify --dry-run` 與實際送出共用同一組組裝函式(移除兩份重複格式字串);`TelegramNotifier` 縮為相容外殼(180→73 行)。順手修掉兩處 `display` 覆蓋 `hidden` 屬性的樣式 bug(解鎖碼框、同步中心篩選列)。`tests/test_notification_channels.py` 29 項＋`tests/test_telegram_chat.py` 改寫為短效碼契約。
- ✅ 2026-09-03：**系統設定合併與左欄切換**——依使用者要求把 07 設定改名「06 · 系統設定」，並將 06 即時情報流、08 系統健康收進去；設定內部改為左側欄切換 10 個區塊（秘書與自動化、Telegram、LINE、監控路徑、採集來源、摘要與 LLM、使用時間、GitHub；維運：即時情報流、系統健康），取代原本的「常用／其他」折疊分組。導覽由 8 分頁收斂為 6。所有元件 id 與載入邏輯不變（只重新掛載），最後檢視的區塊記在 localStorage，儲存列只在設定類區塊顯示；900px 以下左欄轉為水平可捲動的分區列。
- ✅ 2026-09-04：**小秘書問候卡（01 首頁）**——依使用者要求在 01 分頁最上方加一張秘書主動說話的卡：說明「今天」或「近 2 小時」做了什麼，再接一句鼓勵。所有數字都可回溯到資料表（`core/secretary_greeting.collect_activity_stats`：commit／PR 開與合併／AI 對話輪數／檔案異動並區分論文文檔與程式／推進的專案／收掉的未結事項／前景時間；近 2 小時另帶最近一段時段摘要），沒被採集到的工作不代表沒做，**郵件與行事曆不在採集範圍**，卡上寫明。文案由規則產生（有名字就帶、開工不到 4 小時就說「才開工約 N 小時」、什麼都沒看到就誠實說），鼓勵語依深夜／長時間／週末／衝很快／穩等情境選池，同一天同一視窗固定同一句不會跳動；`proactive_secretary.greeting.llm.enabled`（預設關）可讓所選 LLM 潤飾語氣，但**多出統計裡沒有的數字就退回規則版**，失敗也退回。顯示名稱在「系統設定 → 秘書與自動化」；卡每 10 分鐘自動更新；Telegram `/today` 開頭同一段話。`GET /api/v1/secretary/greeting?window=today|2h`。`tests/test_secretary_greeting.py` 22 項。
- ✅ 2026-09-04：**問候卡進晨報＋卡片淡底色**——依使用者要求：01 的問候卡改用主色淡漸層底與左側色條強調（`color-mix`，不支援時退回一般面板色，三套配色×明暗皆可）；晨報（Telegram／LINE 共用 `build_morning_briefing`）第一段加入小秘書的話，`proactive_secretary.greeting.in_morning_briefing` 預設開。因 07:30 多半還沒有今日活動，新增有上界的 `yesterday` 視窗（今天 00:00 為界，所有查詢同時套下界與上界，且不算「開工多久」），今天沒活動就改說昨天、昨天也沒有才誠實說今天還沒偵測到；LLM 潤飾沿用卡片的設定與事實閘。問候讀不到只省略那段，晨報本體不受影響。`tests/test_notification_channels.py` +3、`tests/test_secretary_greeting.py` +1。
- ✅ 2026-09-04：**本機行事曆採集來源（ADR-015）**——TODO C3 的行事曆先過「能否改變決策」檢驗（現在該不該開始大任務／今天怎麼排／時間花去哪，三題都會改變行為）才納入。**唯讀輪詢本機 `.ics` 檔或資料夾**（Outlook／Google／Apple 匯出或同步皆可），不接任何雲端 API、不寫回。解析器 `core/ics_parser.py` 只用標準函式庫＋dateutil：折行、全天、TZID／UTC 換成本地時間、RRULE＋EXDATE、RECURRENCE-ID 覆寫、CANCELLED、跳過 VALARM；**只取時間／標題／地點／狀態**，DESCRIPTION／與會者／連結一律不落地（`store_titles: false` 連標題都不存）。`watchers/calendar_watcher.py` 與其他採集器同形（自我修復、壞檔隔離、診斷），每次掃描以「檔案 × 視野」整批替換（migration 018 `calendar_events`，`(source_path, uid, instance_start)` 唯一），消失的來源會清掉。只在三處使用且都可回溯：問候卡多「開了 N 場會」與「📅 今天 N 場行程，下一場 14:00 …」（claim boundary 改寫為只有郵件不在範圍）、晨報多「📅 今日行程」分節、01 今日面板多一行下一場。沒設路徑就是停用，系統健康不報假警報。`GET /api/v1/calendar/agenda?date=`。`tests/test_calendar_source.py` 17 項；migration 測試同步到 18。
- ✅ 2026-09-04：**驗收中心（ADR-016）**——§12.1 的結論是「下一階段的價值來自把已實作變成已驗證」，但「已驗證」的形狀原本只是 [docs/TODO.md](docs/TODO.md) A 段的 13 條文字判準：收據散在端點／資料表／檔案系統，確認一項要翻好幾個地方，隔幾天回來就不記得哪一項走完了。因此把**判準本身變成可重跑的本機查詢**：`core/acceptance.py` 對 A1–A13 各跑一個 probe，只做三件事——SQLite 查詢、讀設定值、看檔案在不在；**不跑 git、不連網、不呼叫 LLM、不載入索引、不寫任何資料**（契約測試守門：跑完一份報告後所有資料表列數不變，模組原始碼不得出現 subprocess／requests／httpx／urllib.request）。狀態字彙刻意分清楚「沒發生」與「查不到」：`not_configured`（危險能力預設關閉是設計，不進失敗數）與 `runtime_only`（檢索 worker 狀態是主服務程序內的記憶體，CLI 另開程序永遠 cold——報成 pending 等於宣稱沒預熱過）各自成一格。查不到就說查不到：A2 只認 `rag_chat_messages` 內雲端 provider 的**非錯誤**回答（gateway 失敗也會把錯誤字串存成 assistant message，本機 ollama 也不算）；A5 永遠停在 `needs_human`，因為 onboarding 動作根本不留收據（新列為 TODO B4），寧可空著也不用「那個資料夾現在變成 repo 了」這種無法歸因的旁證。人眼判準的項目提供**人工署名**（`reports/acceptance/confirmations.json`），規則只有一條但關鍵：署名只能讓機器沒有判準可查的項目收斂成 `attested`，**永不覆蓋機器判定**——對 A1 署名不會讓它變綠，署名仍如實留著，`passed` 與 `attested` 在彙總裡分開記帳。四個 release gate 的文字與判準直接對應 §12.3（G4 讀 STATUS.yaml 的 quality gates），但**驗收中心不會改 `release_ready`、也不會寫 STATUS.yaml**，只回報現在缺什麼；只查部分項目時不給 gate（用一部分項目算出來的 gate 是誤導）。入口兩個：`GET /api/v1/acceptance/checklist`＋「06 系統設定 → 驗收中心」（左欄第 11 區塊），與 `python main.py verify`（服務在跑就走 live API，否則本機唯讀並如實標 `runtime_only`；`--item`／`--json`／`--output`／`--confirm`）。沒有新 migration、沒有新危險能力，唯一寫入是使用者按下確認的那個本機 JSON 檔。`tests/test_acceptance_center.py` 30 項；容器 Playwright 實測 1440／494 px 13 列 4 gate 零水平溢出、中英文皆有字串。跨平台收據：Platform Matrix 以 workflow_dispatch 在本 commit（`5112314`）上跑了自己的一輪，Windows／Ubuntu／macOS × Python 3.10/3.12 **六個 job 全綠**（run [33900879922](https://github.com/dofliu/activityTracker/actions/runs/33900879922)）——該 workflow 只在 push 到 main 與 workflow_dispatch 時觸發，不跑 pull_request，所以 PR 上的跨平台驗證要手動派工。
- ✅ 2026-09-05：**驗收中心合併進 main ＋ 同步中心 pull/push 前置條件修正（ADR-011 Addendum C）**——驗收中心（ADR-016）以 PR #14 合併，合併後 main 的 Platform Matrix run [33936839212](https://github.com/dofliu/activityTracker/actions/runs/33936839212) 六個 job 全綠。同一輪修掉使用者實測回報的同步中心問題：repo 明明落後遠端卻沒得按 Pull、多個 repo 因為有 `.lock` 檔而不能 pull——追查後是同一個根因，且是本專案自己判斷過嚴：`clean` 把 **untracked 檔案**也算進去，於是 `uv.lock`／`package-lock.json`／`build/` 這些真實專案幾乎必然存在的東西讓 Pull 永久灰掉。以真實 repo 實測 `git pull --ff-only` 三種情境（無關的 untracked → 成功且檔案原封不動；untracked 會被覆蓋 → **Git 自己拒絕並保留本機內容**；已追蹤檔案有未提交修改 → 拒絕）後確認：擋 untracked 沒有多保護任何東西，而 push 根本不碰 worktree。改為只看 `tracked_clean`（staged／unstaged／conflicted），`attention_count` 與 summary 的 `dirty` 同步改用它（build 產物不該讓每個 repo 都被標成需要處理）。第二個問題是**拒絕理由完全不具體**：不論哪個 repo 都回同一句「僅限 clean worktree…」且只放在 tooltip，使用者看到灰按鈕無從判斷。改為逐 repo 依序回報第一個真正擋住的原因並帶實際數字（沒有 upstream 附上 `git push -u origin <branch>` 指令／請先 Fetch／已分歧領先 N 落後 M／沒有落後附上次 fetch 時間／進行中的 Git 操作／衝突數／未提交變更數），順序是先答「有沒有事要做」再答「能不能做」；UI 直接把理由顯示在該列，批次對話框也列出被排除 repo 的原因。順手修掉一個既有的脆弱設計：批次結果原本靠**比對人類可讀訊息的關鍵字**決定 skipped 還是 failed，改用 `RepositorySyncRejected(kind=...)`。9 項新 contract tests（真實 tmp git repo，含 untracked-lock 可 pull、理由帶數字、分類不靠字串三條），容器 Playwright 以三個重現 repo（uavMonitor 落後＋`.lock`／thesisDraft 已追蹤變更／localOnly 無 upstream）實測：uavMonitor 的 Pull 按鈕從灰變可按，無 upstream 那列顯示可直接照抄的指令，零水平溢出。**實機再走一輪的收據待使用者取得。**
- ✅ 2026-09-05：**每日工作誌（ADR-012 Addendum A）**——使用者指出一個真實落差：在 Antigravity 下「幫我更新同步這個專案 本地雲端更新」、在儀表板測試、編修論文，這些 OmniContext 都採集到了，但**秘書的大腦裡沒有任何一天的紀錄**（`observation` 只來自早晨包收據，講的是秘書自己輸出了什麼），所以問它「我昨天做了什麼」答不出來。新增 L0 唯讀 template `daily_digest`（`core/activity_digest.py`）：把某一天的**可回溯計數**（commit 與 repo／AI 對話輪數與平台／檔案異動並分論文文檔與程式／推進的專案／收掉的未結事項／會議／前景時間）與**當天已保存的時段微摘要**組合成記憶區觀察——日層一則、每個有實質活動的專案各一則（帶 `project_key`，讓「我在 X 做了什麼」可回溯）。三個刻意的設計：(1) **不新增任何資料類別**——「你問 AI 什麼」`activity_micro_summaries`（migration 015）本來就在存（本機 LLM 壓成 ≤600 字），工作誌只是 reduce，因此 ADR-012「不存 prompt／response 原文」的邊界原封不動（契約測試驗證 prompt 原文不得出現在任何筆記裡）；(2) **不呼叫 LLM**——收據固定 `llm_used: false`，模組原始碼禁止出現 `llm_gateway`／HTTP 客戶端／`subprocess`，沒有微摘要的那天只寫計數並如實說明「以上只有計數」，不編故事；(3) **沿用既有生命週期**——`source_ref` 去重（同一天重跑不重寫）、可一鍵刪除、隨 TTL 過期；沒歸戶的活動不猜專案，只有 1 筆事件的專案不佔一則記憶。獨立可排程，也是早晨包的第四步。寫進去的觀察由 `memory_context()` 自動注入每次對話的 system prompt——這正是使用者要的「他才是真的清楚我每天做了什麼」。沒有新 migration、沒有新危險能力、沒有新的隱私面。`tests/test_activity_digest.py` 17 項；容器 E2E 以一天的真實資料（Antigravity 4 輪、uavMonitor 2 commit、論文 3 個 .tex、2 則微摘要）驗證：寫出 3 則觀察，且三則都確實出現在 `memory_context()` 的注入文字裡。**實機連續幾天的收據待使用者取得（TODO A15）。**
- ✅ 2026-09-05：**修正驗收中心 A1 的假綠燈**——使用者貼出實機 `python main.py verify` 輸出，A1 顯示「2026-09-05 的 ledger coverage 達門檻（97.12%）✅」。追查後那是**我自己寫的判定有問題**：`get_daily_coverage` 對**當天**的分母是「今天到目前為止經過的時間」而不是 24 小時，所以早上跑三小時、觀測到 2.9 小時就會是 97%——`_check_a1` 從 offset 0（今天）起算，於是把半天當成「全天 coverage」通過了。這正是本專案最該拒絕的那種假綠燈，而且擋的是唯一的 🔴 P0 release gate。改為**只採計已結束的日子**（offset ≥ 1），今天單獨放在 `today_partial` 只當進度顯示並在文案標明「只涵蓋已過的 N 小時，不算全天」，永遠不會讓 A1 變綠；證據欄位改名 `best_completed_day`／`completed_days_with_ledger` 讓語意自明。TODO A1 的完成判準補上這個陷阱的說明（**隔日**查前一天，當天比例不算數）。同時確認**儀表板 usage 面板的 `OBSERVED` 沒有錯也不必改**：它衡量的本來就是「今天到現在」的採集覆蓋，與 A1 要的「跨午夜完整一天」是兩個不同的問題——只有越界宣稱 release 收據的那一個要修。新增回歸測試重現實機情境（今天 03:00、覆蓋 2 小時 55 分 → 97% 但 A1 必須是 pending 且仍列在 `blocking_release`）。
- ✅ 2026-09-05：**模式感知提案（ADR-017）——秘書開始用它記得的東西**。使用者問「下一步是全面測試驗證，還是讓介面更像個人秘書」；檢視後給的答案是：測試驗證不是開發方向（481 項契約測試＋三平台 CI 已經很厚，缺的是使用者一個下午的實機收據），個性化才是——但要做的不是「更像人」，而是**讓秘書開始使用它已經記得的東西**。查程式確認落差：提案引擎讀記憶區只做 `mute` 與附一行筆記，**不看任何模式**。新增 `core/activity_patterns.py`：用三張事件表依（專案 × 日）分組成活動矩陣，產生三種確定性產出——(1) `no_daily_routine`：近一週活動 ≥ 4 天但沒有啟用中的早晨包／工作誌排程，標題點名最活躍的專案，建了排程就自我熄滅；(2) `neglected_active_project`：前一週活躍 ≥ 3 天、近一週 0 天的專案，已有未結事項提案者不重複提，對應執行器既有的 L0 `generate_handoff`；(3) **習慣加權**（排序不是新提案）：近一週活躍 ≥ 3 天的專案，其既有訊號（需要 pull／PR／未結事項）加 0.15 分並附「近 7 天有 N 天在動，是你目前的主線」。三條邊界：**只用可回溯計數不推測意圖**（契約測試禁止模組出現 `prompt_text`／`llm_gateway`）、**只算已結束的日子**（與 A1 修正同一個教訓）、**不新增可執行動作**（`no_daily_routine` 明確排除在 Handoff 之外）。訊號形狀與其他 triage signal 一致，snooze／偏好 mute／每專案上限全部自動適用；若記憶區已有對應日期的工作誌就附為證據（沒有不編）；`inputs.patterns` 誠實回報活動天數、各專案天數、既有排程、加權筆數，模式層任何例外隔離成 `used: false`。`tests/test_activity_patterns.py` 21 項；容器 E2E 以兩週真實資料從 `GET /api/v1/secretary/proposals` 取得兩種提案（`no_daily_routine` 的證據引用到前一輪留下的 `daily_digest:2026-09-04`；`oldPaper` 被判為被冷落而近一週有動的「論文」正確地沒有）。**實機連續使用幾天後的收據待使用者取得（TODO A16）。**
- ✅ 2026-09-05：**宣告式個人檔案（ADR-018）——你自己說的，不是推測的**。個性化第二步。查程式確認 ADR-012 的 `preference` 筆記只有 `mute:<X>` 一種句型會改變行為，其餘全是注入對話的文字——使用者寫「優先處理 uavMonitor」什麼都不會發生。新增 `core/secretary_profile.py`，從偏好筆記解析兩種**明確宣告**：(1) `優先：X、Y`（`priority:`／`本期優先：`）——這些專案的**所有**訊號（含 ADR-017 的被冷落訊號）加 0.2 分並附「你把這個專案標為本期優先」，**刻意大於習慣加權的 0.15：你說的優先勝過活動推出來的主線**；(2) `語氣：簡潔｜直接｜溫暖`（`tone:`）——問候卡、晨報開頭、`/today` 的鼓勵語簡潔＝不講、直接＝一句話只講下一步、溫暖＝原池，**只改措辭不改任何數字**（契約測試逐欄比對三種語氣的標題、事實句、成就清單、stats 完全相同）。三條邊界：**不從活動、prompt 或對話推斷**（模組禁止出現 `prompt_text`／`AIPromptEvent`／`llm_gateway`）、**沒有第二套資料**（每次從偏好筆記重新解析、後寫的語氣覆蓋先寫的、`GET /api/v1/secretary/profile` 唯讀且刻意沒有寫入端點——要改就再寫一則或刪掉那則）、**失敗隔離**（`inputs.profile` 誠實回報，例外不拖垮提案清單；問候讀不到就用溫暖）。`memory_context()` 在筆記前多一行「個人檔案（你宣告的）：…」並記在收據 `sections`；01 記憶區面板頂端列出宣告的專案 chips 與語氣徽章，沒宣告就提示怎麼打。沒有新 migration、沒有新危險能力。`tests/test_secretary_profile.py` 27 項。**實機收據待使用者取得（TODO A17）；第三步（01 分頁成為真正的首頁）待使用者確認後再開。**
- ✅ 2026-09-05：**秘書桌面（ADR-019）——01 分頁成為真正的首頁**。個性化第三步。前兩步讓秘書開始用它記得的東西（ADR-017）並聽你說在乎什麼（ADR-018），但介面還是儀表板：六個分頁、三十幾個面板，01 的提案一次六張、記憶區一次六十則、上次做到哪藏在清單頂端。新增 `core/secretary_home.py` 與唯讀端點 `GET /api/v1/secretary/home`，用**確定性規則**從既有資料挑出三樣東西：(1) **焦點**——提案引擎排序後第一張**關於你的工作**的提案（分數已含 mute／snooze／習慣加權／宣告優先）；容器 E2E 立刻暴露一個設計問題：`verify_extension_heartbeat` 永遠 1.0 分、HIGH，會永久占住首頁焦點，因此 OmniContext 自身的設定提醒（extension 沒 heartbeat、秘書沒有每日排程）留在完整清單、只在沒有別的可看時才佔焦點；(2) **記得**——一則筆記，順序刻意：焦點專案的決定／筆記 → 最近一天的工作誌（日層、未過期）→ 釘選 → 最近記下的，卡上寫「為什麼挑這則」；(3) 上次做到哪、行事曆一句、個人檔案一行、各詳情面板的計數。01 左欄改為「問候卡 → 秘書桌面 → 預設收合的『TODAY · 全部提案』與『記憶區』」（展開狀態記在 localStorage，與其他可收合面板同一套），桌面底部一列**詳情 chip**展開面板或跳分頁——沒有任何面板被移除。**對話優先**的形狀是「每張卡都能一鍵變成一句話」：💬 問秘書把帶脈絡的問題預填到交辦框、不自動送出；拒絕「窄螢幕把對話欄排最前」（空對話框會把桌面推到第一屏之外）。**可量測的指標**：從 01 切到其他分頁計一次、依日期存 localStorage、桌面顯示「今天離開首頁 N 次 · 昨天 M 次」——只算這個瀏覽器、不進資料庫，是評估這個 ADR 的尺而不是新採集面。不用 LLM 挑卡（與 ADR-016／017 同一個理由：秘書『決定顯示什麼』必須可解釋）；每一節各自隔離失敗，`sections` 如實回報、徽章 RULES→PARTIAL。`tests/test_secretary_home.py` 14 項；容器 Playwright 以真實 API 驗證：兩個槽位、六個 chip、兩個面板預設收合且 chip 展開後狀態記住、summary 裡的按鈕不開合面板、問秘書預填、切分頁計數 1→2、chip 跳到 04、1440／494 px 零溢出、英文字串。**實機收據待使用者取得（TODO A18）；個性化三步到此完成。**
- ✅ 2026-09-06：**每週回顧——說的 vs 做的（ADR-020）**。個性化三步完成後使用者問下一步，四條路線（每週回顧／手機同一個桌面／先補實機收據／C5 遠端存取）中選了這條。落差很具體：秘書手上有兩種互不相識的知識——ADR-018 的「你說論文優先」與 ADR-017 的「這週 uavMonitor 五天、論文一天」——卻沒有任何地方把兩者放在一起講；既有的 `weekly_report_rollup` 彙整的是每日摘要文字、寫進 reports/、不看宣告。新增 `core/weekly_review.py`：(1) 期間永遠是**已結束**的 ISO 週（與 A1、ADR-017「今天不算」同一個教訓）；(2) 活躍天數沿用 ADR-017 的活動矩陣，沒歸戶的不猜專案；(3) 對每個宣告的優先給 `done`／`drift`（活躍 ≤ 1 天且同週有**未宣告**的專案 ≥ 3 天）／`quiet`（整週安靜不算偏移），整體 `aligned` 在活動太少時誠實地是 None，名字對不到活動如實標 `matched_key: None`；(4) 寫成一則記憶區觀察「W37 回顧」，`source_ref` 去重同一週一則，**早晨包每天都補上週的**（不綁週一，機器週一沒開也不漏），也可獨立排程（L0 `weekly_review`，`weeks_back` 1–4）；(5) 不一致就一張 `priority_drift` 提案**即時**從表算（不依賴回顧有沒有跑；有就附為證據），標題就是那句話「你說『論文』優先，上週它只有 1 天在動、uavMonitor 有 5 天」，建議二選一：排時間（既有 L0 Handoff）或改宣告；它的專案是宣告優先所以吃到 ADR-018 加分、多半直接成為桌面焦點——**你說重要卻沒做的事就該在首頁最上面**；同專案若同時被判「被冷落」只留 drift（資訊嚴格更多）。桌面「記得」多一級：剛出爐（三天內）的回顧優先於昨天的工作誌（ADR-019 Addendum A）。不用 LLM、不讀 prompt、不推測原因（正文明寫「這只是兩個數字放在一起，為什麼由你判斷」）；回顧層例外隔離成 `inputs.weekly_review`。`tests/test_weekly_review.py` 20 項；`SCHEDULABLE_TEMPLATES` 的 L0 allowlist 測試同步加入 `weekly_review`。**實機收據待使用者取得（TODO A19）。**
- ✅ 2026-09-06：**首頁兩塊、一屏（ADR-019 Addendum B）**——使用者拿到最新 main 後的第一輪實機回饋：右欄的 DATA TRUST／今日統計／FOCUS NOW「現在沒那麼需要，不用放首頁」，首頁四塊「可以整理，不用往下捲就看到全部」。右欄整個移出（統計與 Focus Now 到 05 最上方、DATA TRUST 到 06 系統健康，`.shell` 變單欄）；問候卡併進秘書桌面（共用 ↻、成就清單改一行 chips）、記憶區併進交辦框（預設收合）、全部提案收合在桌面底部；卡片標題與正文 line-clamp。元件 id 與 API 全部不變，純版面。容器 Playwright：1920×1080 與 1440×900 的 01 不需捲動、494px 零水平溢出、chip 展開記憶區、05／06 兩處都看得到搬過去的東西。
- ✅ 2026-09-06：**文件落後程式（ADR-021）——把使用者最常手打的那句指令變成一張卡**。使用者指出他最常下的指令是「檢視目前專案的同步狀態，然後更新說明文件、使用文件與規劃文件」，並以為秘書做不到（「沒辦法把指令傳給桌面版的 AI」）。**前提只有一半對**：秘書碰不到 GUI 應用，但 ADR-008 的 L2 早就會調度本機 agent CLI（`claude -p`／`codex exec`，argv 白名單、禁 shell、cwd 限已探索 repo、env allowlist、逾時、可取消、收據、批准＋一次性確認碼）。把那句指令拆開，前半段（同步狀態）是既有的 L0 `repo_sync_report`、每天在跑；後半段缺的**不是能力而是觸發**——`_DRAFT_PLAN_TYPES` 只認停滯事項。新增 `core/docs_freshness.py`：用 `file_activity_events` 的文件檔最後異動時間對比 `git_activity_events` 在那之後的 commit 數（≥ 8 個且文件 ≥ 2 天沒動），產生 `docs_behind_code` 提案，最多三張依分數排序。**只比時間與數量，不判斷文件內容**；**沒有文件異動紀錄的 repo 一律不提**（分不出「沒有文件」與「文件目錄不在採集範圍」，與 A5 拒絕旁證同一個判斷），被跳過的 repo 如實列在 `inputs.docs_freshness.skipped_no_doc_baseline`。動作**接既有的兩段式 L2、不開第二條寫入路徑**：`agent_draft_plan` 先產出一份可讀的「文件更新計畫」（prompt 由 server 組，事實區塊含文件基準、commit 數與最近 12 筆 commit 訊息、該專案最近一則工作誌，明寫「不要編造沒有依據的進度」），使用者讀過再批准 `agent_apply_plan` 實際改檔——只能改此 repo、**不准 git commit／push**、前後驗 worktree 乾淨，commit 權留在使用者手上。訊號層完全確定性、不呼叫 LLM、不寫任何資料。順手修掉使用者實際踩到的坑（D6）：設定檔是 process 啟動時載入一次的 singleton，先 `run` 後 `init` 產生 token 會讓**任何** token 被拒，而 401 訊息說不出這件事——改為區分「服務沒有載入 token（請重啟或按儲存；注意 env 優先）」與「token 不符」，前端顯示 server 的說明；安全性不變（仍 fail-closed、constant-time）。`tests/test_docs_freshness.py` 25 項；全套 589 項（588 passed + 1 conditional skip、60 模組）；同輪為 401 分支加一項契約測試。**實機收據待使用者取得（TODO A20）。**
- ✅ 2026-09-07：**修正驗收中心 A6 的誤導訊息**——使用者實機跑 `retrieval/warmup` 後，status 顯示 `state: ready`、`chroma_ms: 2552`、`embedding_ready: true`、`worker_rss_mb: 1480`，但 `bm25_chunks` 與 `vector_chunks` 都是 0，A6 仍回 `pending` 並說「預熱完成後這裡會顯示載入計數」。追查後是判定訊息指錯方向：`index_present()` **只檢查 BM25 檔案或 chroma 目錄存不存在，不看 chunk 數**，所以「索引目錄在但內容是空的」會落到 pending 那一支——而預熱其實早就完成了（2.5 秒是開啟 Chroma、1.5GB RSS 幾乎全是 fastembed 模型本身、6ms 的檢索也印證沒有內容可搜）。改為在 `state == ready` 且有 `warmup_at` 卻仍 0 chunk 時回 `not_configured` 並直說「worker 已預熱完成，但索引裡是 0 個 chunk——索引目錄存在不代表有內容，請先到 02 知識庫建索引」。這與 A1 假綠燈是同一類問題的反面：**狀態訊息指向錯誤的下一步也是 bug**。`tests/test_acceptance_center.py` 加一項（空索引→not_configured、真的沒預熱→pending、有內容→passed）；TODO A6 判準補上這個陷阱。
- ✅ 2026-09-07（同日第二輪）：**預熱按鈕沒有預熱，而 A6 用舊收據判綠**（ADR-009 Addendum B）。使用者建完索引（`source_chunks` 3 → 4,839、`consistency: matched`）後按下預熱，拿回的仍是重建之前的收據（`bm25_chunks: 3`、`vector_chunks: 3`，`pid` 與 `warmup_at` 都沒變），A6 還報 ✅ passed 寫著 bm25=3／vector=3。根因是 `warmup_in_background()` 只要「worker 活著且預熱過」就短路回舊狀態，完全不管索引在那之後重建過。改為 `force` 參數分開兩條路徑：**啟動時的自動預熱維持 idempotent，使用者明示按下的預熱一律重載**（同一個 worker 程序，不重啟）。同時 A6 在判綠前比對 worker 載入計數與 SQLite 的 `rag_indexed_files.chunk_count` 之和（唯讀、不 import 任何索引套件），載入計數較小就回 `partial` 並直說「worker 載入的是舊索引：記憶體裡 vector=N，但索引現在有 M 個 chunk」——A6 的判準因此從「有載入東西」升級為「載入的量不小於索引現在的內容」。兩項各有一支會因舊行為而失敗的回歸測試（強制重載、舊索引不判綠）；全套 592 項（591 passed + 1 conditional skip）。**這一輪是使用者實機回報找出來的第二個假綠燈**，與前一輪同一條原則：狀態訊息指錯下一步就是 bug。
- ✅ 2026-09-07（第三輪）：**Chroma 的「刪除」不會讓磁碟變小**（ADR-009 Addendum C）。使用者的 `chroma_bytes` 是 4.24 GB，索引裡只有 4,839 個切片。先用 chromadb 1.5.9 實測確認這是預期中的殘留：建一個 4,000 切片的 collection、`delete_collection` 再重建，**目錄 14,209,408 bytes 一個位元組都沒少**——`chroma.sqlite3` 的 1,791 頁裡 1,590 頁變成空頁（VACUUM 後 7.3 MB → 0.8 MB），舊的 HNSW 片段目錄整個留著且不再被 `segments` 表引用。既有的清空流程救不了：`compact_sqlite()` VACUUM 的是 OmniContext 自己的 SQLite，不是 `chroma.sqlite3`。新增 `chroma_report()`（唯讀空間帳：目錄大小、SQLite 空頁、活片段、孤兒片段、認不得的項目；**不 import chromadb**）與 `compact_chroma()`（既有 index worker 的新 job type，刪孤兒目錄＋VACUUM）。三條 fail-closed：**讀不到 `segments` 表就什麼都不刪**、只刪名稱是 UUID 且不在表裡的目錄、刪不掉的如實記在 `failed_dirs`；刪除前重讀一次 `segments`，其間變成活的就跳過。端點需要 `confirm=true` 並先請檢索 worker 讓開；儲存卡片寫出「Chroma 目錄／可回收」，殘留超過 200 MB 直接點名孤兒片段數與空頁大小。容器 E2E（真的用 chromadb 建索引再刪重建）：12,932,956 → 2,625,700 bytes（回收 79.7%），活的 collection 回收後仍可檢索（900 切片、查詢照常命中）；再按一次回收誠實回報 0；250 MB 殘留的警示與確認對話框都如實寫出數字。驗收中心 A21 只認 worker 收據（沒跑過就 pending，不掃描目錄猜）。`tests/test_rag_storage_compaction.py` 8 項＋A21 1 項；全套 601 項（600 passed + 1 conditional skip）。
- ✅ 2026-09-08：**「還在跑」不是「沒有完成」**。使用者在 4 GB 目錄上按下回收、工作還在 VACUUM 時跑 verify，A21 回的是「最近一次 Chroma 回收沒有完成（running）；**再跑一次**或看工作訊息」——又是狀態訊息指錯下一步：工作正常進行中，而且同時只能有一個索引工作，真的再按會被拒。改為先查有沒有進行中的 `compact_chroma` 工作，有就回 `pending`「回收正在進行中（running）。大目錄光 VACUUM 就要一兩分鐘，等它跑完再看這一項；不用再按一次」；收據查詢則排除進行中的工作，所以進行中不會蓋掉先前那份成功的收據。狀態字彙（`ACTIVE_STATUSES`）與 `rag/jobs.py` 一致由契約測試鎖住；全套 603 項（602 passed + 1 conditional skip）。
- ✅ 2026-09-08 **實機收據（A6＋A21）**：明示預熱後 worker 載入 bm25=4839／vector=4839（與 `source_chunks` 一致）→ A6 passed，同時證明強制重載有效（同一輪之前拿到的是 3）。回收 **1,633,386,496 bytes**（4,254,097,732 → 2,620,711,236）→ A21 passed，但**刪掉 0 個孤兒片段目錄**——這台機器的殘留全部在 `chroma.sqlite3` 的空頁裡，與容器實驗的組成不同（那次主要是孤兒 HNSW 目錄）。回收後仍有 2.62 GB；那是活片段目錄還是資料庫本身，要用 `/storage/chroma` 的 `sqlite_bytes` 與 `live_segment_dirs` 分辨，**在那之前不宣稱還能再回收多少**。
- ✅ 2026-09-08：**幾個月沒動的 PR／issue 不納入考量**（ADR-007 Addendum 2026-09-08）。使用者看到 01 桌面兩張 HIGH 卡：z72-scada-system #92／#93，CI 綠燈、已開啟 100～101 天沒更新，卻寫著「只差一個 review、收益立即」——「這種已經超過 60 天的就不用納入考量了」。分流訊號原本把年齡當單調的「越久越該處理」；改為年齡有兩個區間：新增 `proactive_secretary.github_stale_after_days`（預設 60，0 = 不過濾），超過的 PR／issue 不進提案，但 `inputs.github_stale_excluded` 如實寫出門檻、數量與最舊的十個對象，`open_prs`／`open_issues` 仍是全部開著的計數，提案區多一行「另有 N 件…不列入考量」。只動 GitHub 訊號，未結事項／被冷落／文件落後各有自己的年齡語意，不動。3 項契約測試；全套 606 項（605 passed + 1 conditional skip）。
- ✅ 2026-09-08：**會議秘書第一層**（[ADR-022](docs/ADR-022-meeting-secretary.md)，起草與實作同日）。使用者在線上會議問秘書「你可以看到嗎」，秘書答不能——對的；接著問「知道我在開會就可以做即時紀錄、翻譯、筆記嗎」。把它拆成兩層：**第一層是會後**——你把 Teams 匯出的逐字稿放進 `meetings.transcript_dir`（沒設就是關），L0 template `meeting_notes` 產出一則可刪的記憶區觀察（配對到的會議標題／發言與講者數／摘要／**候選**待辦），01 提案卡上每條候選旁邊是「加入未結事項」「忽略」——**點了才進 open_loops**，秘書自己永遠不寫。「在開會」只用兩個確定性訊號（行事曆進行中 ＋ 前景是 Teams／Zoom），只有其一時如實說差異。新增 WebVTT parser（`<v NAME>` 與 `NAME:` 兩種講者標記、字幕「同一句長出來」的重複要收斂）、`.vtt` 進 RAG 支援清單。摘要預設 **ollama（全本機）**；雲端 provider 在觀察正文明寫「與會者的發言曾送往該供應商」。**即時音訊（第二層）沒做**，五道門寫在 ADR-022 D6。容器實機（真實 Teams 格式）找到並修掉一個 bug：`LLMClient` 連不上供應商時是**回傳錯誤字串**而不是丟例外，那串錯誤被當成摘要送進事實閘，於是對使用者說「摘要編造了數字 11434」——真正原因是 ollama 沒開；已在事實閘之前判斷並改成「provider 未回覆摘要…（可跑 `python main.py llm-test` 診斷）」。驗收 A22（機器只查資料夾／整理過幾份／候選待辦計數，摘要品質留人眼）。20 項契約測試；全套 626 項（625 passed + 1 conditional skip）。
- ✅ 2026-09-13：**文件整理**——README／README_en 依實際程式重寫（補上 2026-09-04 之後的驗收中心、模式感知提案、宣告式個人檔案、秘書桌面、每週回顧、文件落後偵測與會議秘書共七項功能，修正 53 模組／425 項等過期數字為 62 模組／626 項、schema 7/7 為 18/18、ADR-015 為 ADR-022、設定左欄 10 區塊為 11 區塊），設定與 Extension 安裝步驟改為指向 `config.example.yaml` 與 USAGE 不再各寫一份；USAGE 的「常用操作」由 32 節流水帳重組為六節（小秘書／行事曆與會議／知識庫檢索／通知與手機／摘要快照／驗收中心）並加目錄，標題移除日期、刪掉一段描述 ADR-019 之前版面的過期重複；本節（原 §11）的成果紀錄原本掛在兩個不同的父項下導致日期跳動，現合併為單一依日期排序的清單。
- ✅ 2026-09-16：**R0 減法第一輪（TODO B5–B9）**——依同日專案檢視（[docs/REVIEW-2026-09-16-project-assessment.md](docs/REVIEW-2026-09-16-project-assessment.md) §4.2）逐項處理已核實的死碼與依賴債，**不改行為**：(B5) 刪 `core/server.py` 被遮蔽的第一個 `SystemMaintenanceRequest`；(B6) 自 `pyproject.toml`／`requirements.txt` 移除零 import 的 `pandas`／`pillow`／`sse-starlette`／`python-dotenv`，`rapidocr-onnxruntime` 改為 `[ocr]` 選用依賴（未裝時圖片解析維持檔名 stub）；(B7) 刪 `notifiers/telegram_notifier.py`（零 importer）、`synthesizer/scheduler.py` 永遠跑不到的 `_std_scheduler_loop` 備援（約 170 行，APScheduler 是硬依賴）、`scripts/inspect_logs／inspect_codex／inspect_assistants／check_real_recent／windows_milestone_e2e／migrate_timezone／purge_legacy_data／cleanup_noise.py` 與 CLI `clear-demo`；(B8) `marked` v15.0.12 改隨 wheel 本機提供（`web/vendor/`）、移除 Google Fonts 連結改全本機字型堆疊，`verify_release_artifacts` 與 `assets-status` 納入 vendor 檔，新增「index.html 不得外連」契約測試；(B9) `manager.get_status()` 的 `metrics` 成為狀態數字唯一定義（補 `project_states`／`open_loops_open_count`，非空回應排除占位字串），`main.py status` 只呈現不再自算。**收據**：`pytest` 628 passed ＋ 1 skipped（新增 3 項契約測試）；`python main.py verify` 輸出與基底 commit 逐行相同；`python -m build` ＋ `verify_release_artifacts.py` 通過且 wheel 不再含上述腳本；乾淨 venv `pip install -e ".[dev]"` 797 MB → 721 MB（pillow／python-dotenv 仍由 fastembed／chromadb 間接帶入，只是不再由本專案宣告）。R0 剩 D1（RAG 依賴改選用）。
- ✅ 2026-09-16（第二輪）：**R0 完成——知識庫依賴改為選用 extra（TODO D1）**。`chromadb`／`fastembed`／`rank-bm25`／`jieba`／`pymupdf`／`python-docx`／`python-pptx`／`openpyxl` 自核心依賴移到 `[project.optional-dependencies] rag`；`dev` 改為自我參照 `omnicontext[rag]` ＋ `omnicontext[test]`（pip 可解析，已實測）。新增 `rag/availability.py`（只用 `importlib.find_spec`，不 import 套件）作為單一定義，每條會用到這些套件的路徑都**在動手前**說清楚缺什麼：建 job → `create_job` 拒絕、API 回 503 且 `detail.error = rag_extra_not_installed` 附 `missing` 與 `install_hint`；檢索 worker → 不啟動子程序、狀態 `unavailable`（只針對預設指令，測試注入的替身不受影響）；對話 → 照常回答只是不帶文件脈絡；啟動預熱 → 略過並記 `rag_extra_not_installed`；驗收中心 A6 → `not_configured` 並給安裝指令；儀表板 02 分頁把會失敗的按鈕灰掉並顯示安裝指令。**收據**：有 extra 時 `pytest` 638 passed ＋ 1 skipped（新增 11 項）；**沒有** extra 的乾淨 venv `pip install -e ".[test]"` 只有 **176 MB**（B6 後 721 MB → 176 MB，`[rag]` 佔約 550 MB），`import core.server` 成功、`pytest` 626 passed ＋ 12 skipped（需要套件的測試以 `importorskip` 標明）；`verify` 輸出與基底相同；wheel METADATA 的 `Provides-Extra: rag／ocr／test／dev` 正確；CI 新增 `test-core-without-rag-extra` job（ubuntu／3.12，不裝 `[rag]` 跑全套）。踩坑：依賴檢查一開始放在 router 與 startup 外圍，結果沒裝套件的環境裡連注入假 worker 的測試都被擋——**檢查要放在真的會失敗的那一層（client 啟動子程序處）**，外圍只讀它回報的狀態。
- ✅ 2026-09-16（第三輪）：**R1 D2——一個 LLM client**。刪 `synthesizer/llm_client.py`（233 行，同步、Ollama 走 `/api/generate`、gemini 預設 2.5-flash）與 `rag/llm_gateway.py`（215 行，串流、Ollama 走 `/api/chat`、gemini 預設 3.7-flash），新增 `core/llm_client.py`（466 行）作為唯一實作：provider 別名（gpt／claude／google）、`DEFAULT_MODELS`、`KEY_ENVS` 各只定義一次；`LLMClient.generate()`（同步，失敗**回傳** `[本機備援模式]` markdown）與 `LLMClient.stream_chat()`（非同步，失敗 **yield** `[OpenAI API 錯誤]` 等字串）共用同一組預設與金鑰解析；Ollama 兩條路徑都走 `/api/chat`、system prompt 以 system role 傳（不再拼 `<system>` 標籤）。第三條 Ollama 呼叫（`core/semantic_index._generate_local_answer`，`omni ask`）改走 `LLMClient.ollama_chat()`（保留 loopback-only 與低溫度）；第四份金鑰解析（`rag/embeddings.py` 寫死 `OPENAI_API_KEY`）改走 `resolve_provider_api_key()`（開始尊重 `synthesizer.openai.api_key_env`）；`core/secretary_advisor.py` 私有的 `_DEFAULT_MODELS` 表刪除。知識庫對話的 provider／model 預設（`rag.active_provider`／`rag.active_model`）搬到呼叫端 `rag/router._chat_target()`，`core/secretary_ask` 共用——LLM client 不認識 rag 的設定鍵。**行為不變的部分**：所有失敗抬頭字面不動（`core/meeting_transcripts.looks_like_llm_error` 與 `core/acceptance._LLM_ERROR_MARKERS` 靠它們辨識供應商錯誤，有契約測試）；`python main.py llm-test` 輸出結構不變。**收據**：`pytest` 647 passed ＋ 1 skipped（新增 `tests/test_llm_client_single_source.py` 9 項；其中一項掃全 repo 禁止在 `core/llm_client.py` 以外寫死模型名——第一次跑就抓到 `semantic_index.py` 漏掉的一處）；不裝 `[rag]` 635 passed ＋ 12 skipped；`verify` 輸出與基底相同；wheel 只含 `core/llm_client.py`。
- ✅ 2026-09-16（第四輪）：**R1 D3——活動來源定義只有一份**。動工前先核對，發現檢視當時「四份各自實作」的說法**只對一半**：`core/weekly_review.active_days_by_project` 早就委派給 `activity_patterns.activity_matrix`，`core/activity_digest.collect_day_stats` 也早就委派給 `secretary_greeting.collect_activity_stats`。真正剩下的重複是**三件事各寫三遍**——哪三張表算活動、專案名在哪個欄位、一天從哪到哪。新增 `core/activity_sources.py`（175 行）收成單一定義：`EVENT_SOURCES`（表 × 專案欄位 × 時間欄位）、`normalize_project`（空白＝沒歸戶，不猜）、`day_bounds`／`window_bounds`（一律半開區間）、`project_activity_matrix`（專案 × 日）與 `project_event_counts`（專案 × 筆數）。四個使用端全部改吃它：`activity_patterns.activity_matrix` 變成視窗換算的薄包裝、`weekly_review` 直接用新函式、`activity_digest.per_project_counts` 改用共用計數（自己那三段 group-by 刪除）、`secretary_greeting` 的三張事件表查詢改由 `source_for()` 決定要查哪張表與哪個時間欄位。**刻意不合併**問候卡的 PR／專案狀態／未結事項／前景時間查詢——那些不是活動來源，硬併只會讓新模組變成第二個 god object。**收據**：`pytest` 659 passed ＋ 1 skipped（新增 `tests/test_activity_sources_single_source.py` 12 項，核心是一支「同一天四處數字必須對得上」的對帳測試，另一支掃原始碼禁止四個使用端再自己查三張事件表的專案欄位）；不裝 `[rag]` 647 passed ＋ 12 skipped；`verify` 輸出與基底相同；四個使用端淨減 77 行。
- ✅ 2026-09-16（第六輪）：**R1 D5——桌面通知併入 `ChannelAdapter`，扇出只剩一處**。`notifiers/desktop_notifier.py`（282 行）原本自己組晨報／今日回顧／停滯／里程碑四種內容、自己送、自己吞例外——同一件事在它與 `notifiers/messages.py` 各寫一遍，而且**兩邊已經漂了**：未歸戶收容桶（`General / Notes`）桌面會濾、Telegram／LINE 不會，同一天兩個通道講的專案數不一樣。現在 `desktop_notifier.py` 只剩 transport（165 行：WinRT toast、MessageBox 降級、送達收據、`--dry-run` 預覽），內容一律由 `notifiers.messages` 組、由新的 `notifiers.channels.DesktopChannel` 轉成 toast、由 `notifiers.secretary_push` 扇出。歸戶判定收進 `_projects_and_loops` 一處（認 `core.project_engine.is_bucket_project`），三個通道從此講同一組數字。新增 `render_toast()`：toast 放不下整份晨報，所以做的是**摘要不是截頭**——每個分節都保留（標題＋前兩行），讓「今日重點專案／未結事項／待判斷建議」每一段都露臉（P5-R4a 的秘書 top 建議因此仍在桌面通知裡），被省略的部分一律留記號。`evaluate_daily_milestones` 的 `notifier=` 換成 `channels=`，里程碑改走 `push_usage_milestone`（預設只送桌面，與 `MilestoneNotificationReceipt.channel` 記的一致）。桌面**刻意不進** `enabled_push_channels`：它有自己的排程時間，混進遠端預設清單會讓同一天跳兩次 toast，要送就明講 `desktop_channels()`。**收據**：`pytest` 672 passed ＋ 1 skipped（`test_notification_channels.py` 新增 9 項桌面 adapter 斷言：能力宣告、摘要渲染與截斷記號、收據不含內文、失敗不影響其他通道、開關與平台、dry-run 預覽、里程碑同路徑、收容桶不得被當成專案）；`verify` 輸出與基底 commit 逐行相同；`python -m build` ＋ `verify_release_artifacts.py` 通過；不裝 `[rag]` 660 passed ＋ 11 skipped。
- ✅ 2026-09-16（第七輪）：**R1 完成——D6 六層旗標收三層、引擎參數回程式常數**（[ADR-008 Addendum D](docs/ADR-008-gated-agent-executor.md)）。保留三層**真正的**分級（`executor.enabled` → `l2.enabled` → `l2.allow_write`），把兩層擋不住任何東西的旗標併掉：(1) `executor.scheduled_tasks.enabled` 併入 executor——可排程的只有 server 註冊的 **L0 唯讀** template（L1／L2 永不可排程，有測試把關），而且任務要先被親手建立，建立本身就需要 execution token；(2) `telegram_approvals.allow_remote_arm` 併入批准通道開關——`/arm` 要的是儀表板簽發的一次性 6 碼（需 execution token、只存雜湊、5 分鐘失效、用過即銷毀），沒有碼開著也解不開。`telegram_approvals.enabled` 保留為**通道**開關（要不要讓手機有批准能力），不是執行器的第四層分級。**升級不靜默放寬**：兩個被併掉的鍵明確寫成 `false` 的既有設定檔照樣關著（曾經啟用、建過任務、後來關掉的人，休眠任務不會自己醒過來），儀表板偵測到這個已淘汰的鍵會直接說出來。設定面同時處理：`config.example.yaml` 449 → **344 行**（設定行 327 → **252**），移出 22 個秘書評分權重（`*_boost`／`*_min_days`／視窗長度／各種上限）、視窗標題忽略清單、介面辨識規則（三者本來就有相同數值的程式常數，仍可覆寫同名鍵），並刪掉範例檔裡作者個人的路徑樣式（`BladeDamage`／`CASE-*`）。**收據**：`pytest` 683 passed ＋ 1 skipped（新增 `tests/test_config_surface.py` 11 項：三層仍疊加、只有 L0 可排程、executor 關著連建立任務都拒絕、`/arm` 跟著通道開關、明確 false 仍有效、搬家後數字一模一樣、範例檔不再帶那些鍵與個人路徑）；不裝 `[rag]` 671 passed ＋ 11 skipped；`verify` 輸出與基底相同；`python -m build` ＋ `verify_release_artifacts.py` 通過。**沒有做到的事**：TODO 寫的「`config.example.yaml` < 300 行」沒達成（344 行）——剩下的 85 行是註解，寫的是隱私與安全邊界（哪個開關會把內容送出本機、逐字稿裡別人的發言、LINE 為什麼只能推播）。為了湊行數刪掉它們是優化指標而不是優化產品，所以留著並如實記錄。
- ✅ 2026-09-16（第八輪）：**R2 D7——一份活動記憶**（[ADR-023](docs/ADR-023-one-activity-memory.md) Accepted）。動工前先核對兩邊到底各索引什麼，發現重複之外還有**分歧**：AI turn／commit／專案狀態／未結事項四種兩邊都有，檔案事件只有核心看得到，秘書筆記與時段微摘要只有 RAG 看得到——同一個問題問 `omni ask` 與問知識庫對話，本來就會拿到不同的證據集合。決定依「這東西是什麼」分工：**活動記憶（七種來源）＝`core/semantic_index`**（核心、Ollama ＋ SQLite BLOB、零重依賴、每筆只 embedding 一次）；**文件（使用者資料夾＋秘書寫出的報告檔）＝DeskRAG**（選用 `[rag]`）。`rag/activity_indexer.py`（448 行）縮成 `rag/report_indexer.py`（232 行，只剩報告檔，白名單不變）；`collect_source_documents` 補上 `secretary_note` 與 `micro_summary`（搬家沒弄丟任何來源）；知識庫對話的活動段改查同一份核心索引（`retrieval_type: semantic_index`，每筆帶 `source_ref` 與 trust status，system prompt 分「文件切片」與「你自己的工作紀錄」兩段）。**遷移**：報告同步 job 一次性清掉舊的 `source_domain == "activity"` 切片並把刪掉幾筆寫進收據；端點、job 型別與路由不變，`semantic_documents` schema 不變（不需要 migration）。**反向選項（全部進 Chroma）刻意不採用**：那會讓「秘書記得你做過什麼」綁死在選用依賴上。**收據**：`pytest` 694 passed ＋ 1 skipped（新增 `tests/test_one_activity_memory.py` 10 項：只有一份定義、`rag/` 不得再讀活動資料表、同一筆不重算、活動段回溯得到原始 row、Ollama 掛掉只少活動段、沒裝 `[rag]` 仍有活動記憶、舊切片被清掉）；不裝 `[rag]` 681 passed ＋ 12 skipped；`verify` 輸出與基底相同；`python -m build` ＋ `verify_release_artifacts.py` 通過。**如實記下的代價**：中文關鍵字檢索目前只有文件段享有 BM25，活動段是純向量。
- ✅ 2026-09-16（第九輪）：**R2 D8——秘書叢集四層化**（[ADR-024](docs/ADR-024-secretary-layers.md) Accepted）。`core/` 底下十一個平輩秘書模組收成 `core/secretary/` 一個套件、四層依賴（`types` → `memory` → `signals` → `aggregate` → `packs`／`greeting` → `present`），**方向只准往下**，由掃 import 的契約測試把關。兩個定型契約取代 `dict[str, Any]`：`Signal`（收集器與聚合層之間唯一的介面，`from_dict` 在聚合層入口一次驗完必填欄位，錯誤訊息說得出是哪個收集器缺什麼）與 `Proposal`（`to_dict()` **就是** API 回傳的形狀，逐鍵比對測試把關；frozen，提案是唯讀建議這件事寫進型別）。**環真的拆了**：`memory` 不再為了脈絡裡那行早晨包與三個提案反過來 import `packs`／`aggregate`——組合點移到呈現層的 `full_memory_context()`；`build_today_view` 從 packs 搬到 present（它本來就是「01 今天」的組裝）、`ensure_default_schedules`／`presets_status` 搬到 `scheduled_tasks`（它們在建立／列出排程），`packs ↔ scheduled_tasks` 的互 import 因此消失。指向 `core.*` 的函式內延遲 import 從 **116 降到 15**（第三方／選用依賴的 73 處延遲 import 刻意保留）。**行為不變**：API 路由與 JSON 形狀、job 型別、`verify` 輸出全部原封不動；測試只改 patch 目標與 import 路徑（模組層 import 後 patch 要綁使用端——這是 D4 同一個踩坑），斷言一字未改。**偏離已明說**：TODO 寫「四個檔案」，實作是**四層七檔**（另有 `types`／`greeting`／`packs`）——把問候卡與早晨包硬塞進 `present.py` 會做出一個 1,500 行的新 god object，那正是 D4 剛拆掉的東西，理由寫在 ADR-024。**收據**：`pytest` 709 passed ＋ 1 skipped（新增 `tests/test_secretary_layers.py` 15 項）；不裝 `[rag]` 696 passed ＋ 12 skipped；`verify` 輸出與基底相同；`python -m build` ＋ `verify_release_artifacts.py` 通過且 wheel 含 `core/secretary/` 全部模組。
- ✅ 2026-09-17（第十輪）：**R2 D9——每平台一個 transcript parser ＋ 漂移警示**（[ADR-025](docs/ADR-025-transcript-parsers-and-drift.md) Accepted）。`watchers/agent_log_watcher.py`（1,066 行）裡塞了兩件性質不同的事：採集服務的骨架，與四個平台、五套 parser。拆成 `watchers/transcripts/{base,claude_code,claude_desktop,codex,antigravity,drift}.py`，服務只剩 515 行且**不再認識任何一種格式**（契約測試掃 `PLANNER_RESPONSE`／`session_meta`／`stop_reason` 等關鍵字，出現在服務裡就失敗）。**共同介面只有兩個函式**：`discover(cfg, *, full_history, now)` 說檔案在哪、`parse(path, *, cfg, now)` 產出 `TranscriptTurn`；`parse` 是產生器且不碰資料庫（契約測試禁止 parser 出現 `get_db`／`session_scope`／`AIPromptEvent`），所以每個 parser 都能離線單測。`claude_desktop` 明著 import `claude_code.parse_claude_jsonl`——Desktop 寫的就是同一種 JSONL，這個 import 是事實的反映。**漂移警示**回答的是本專案一直沒答的問題：四種格式都是別家工具的私有格式，格式一變 parser 不會拋例外，它會正常跑完、產出零筆事件，`healthy` 只證明沒有拋例外、不證明有採集到東西。`drift.py` 是純函式：**檔案 mtime 在視窗內 ∧ 該平台視窗內零事件** 才警示（視窗 3 天、程式常數，不新增設定鍵）——沒用過、被關掉、目錄不存在都不會誤報；成立時經 `collector_diagnostics.agent_log_watcher.drift` 一路到系統健康頁，並把該採集器標成 `degraded`。**行為不變**：解析規則、`response_status` 判定、CLI 雜訊過濾、背景工作證據條件、Antigravity 的 `url` 用未 resolve 路徑而 `source_path` 用 resolve 過的——逐字保留（含看起來像瑕疵的地方），測試只改呼叫點與 import 路徑，斷言一字未改。**收據**：`pytest` 732 passed ＋ 1 skipped（新增 `tests/test_transcript_parsers_and_drift.py` 23 項：模組行數上限、parser 不碰 DB、服務不認識格式、共同介面、三個平台的解析行為、漂移的兩半缺一不可、從服務到健康頁的 degraded）；不裝 `[rag]` 719 passed ＋ 13 skipped；`verify` 輸出與基底逐行相同；`python -m build` ＋ `verify_release_artifacts.py` `status: passed`，wheel 含 `watchers/transcripts/` 全部模組。**如實記下的邊界**：漂移只看得到整個平台的靜默——同一平台兩種 parser（Codex 的 `.json` 與 `.jsonl`）其中一種失效而另一種還在產出時，這個警示抓不到。
- ✅ 2026-09-17（第十一輪）：**R2 D10——前端拆成 ES module**（[ADR-026](docs/ADR-026-frontend-modules.md) Accepted）。`web/app.js`（6,096 行、一個 `<script>` 整包載入）拆成 `web/js/` 的 module 樹：`main.js` 進入點、`core/`（`api`／`i18n`／`state`／`ui`／`dom`）、`tabs/` 十個分頁模組，**最大一檔 915 行**（契約測試上限 1,500）。**682 行的 `I18N` 物件變成資料**：`web/i18n/{zh-TW,en}.json`，開機時載入。**九處裸 `fetch()` 收回 `core/api.js`**——它現在是整個前端唯一出現 `fetch(` 的檔案（契約測試把關），並補了 `request()` 讓真的需要看 `res.status`（401 換 token、428 二次確認）與需要 `response.body`（RAG 串流）的呼叫端不必自己寫 fetch。**17 處寫在 HTML 字串裡的 `onclick=` 全部改成 `data-action` ＋ 一個 document 層委派**：函式不再需要掛上 `window`（這正是模組化原本卡住的地方），參數也不再經過 HTML 字串。
  **抽字典抽出三個真的 bug**（正是 ADR 說「沒人守」的那一類）：`btn_show_more_projects`／`btn_collapse_projects` 只有中文——英文介面那兩個按鈕一直顯示中文；`repo_overview_*`／`btn_repo_*` 六個鍵只有英文（畫面靠 HTML 寫死的中文撐著，所以沒人發現）；`secretary_inbox_hint` 被 index.html 引用但**兩份字典都沒有**，那一行永遠不會翻譯。三者都補齊，中文字串與現行畫面逐字相同；另刪掉兩個沒有任何地方引用的死鍵。現在兩份字典各 341 個鍵、集合相同，由 `tests/test_frontend_modules.py` 守著。
  **行為不變**：版面、字串、API 呼叫時機與輪詢節奏全部不動；`main.js` 的初始化序列與拆分前逐字相同，只多了一行「先等字典載進來」。**收據**：`pytest` 775 passed ＋ 1 skipped（新增 `tests/test_frontend_modules.py` 43 項）；不裝 `[rag]` 762 passed ＋ 13 skipped；`python main.py verify` 在同一份資料上與 D10 前**逐行相同**（用 `git archive HEAD` 取出改動前的樹、以 `OMNICONTEXT_HOME` 指向同一個資料庫比對）；`python -m build` ＋ `verify_release_artifacts.py` `status: passed`，wheel 含 `web/js/` 全部模組與兩份 `web/i18n/*.json`；新增 `scripts/dashboard_smoke.py`（真瀏覽器跑六分頁 × 1440／494 px）回 **`ok: true`**——零 console error、零未捕捉例外、零水平溢出、字典載入且中英切換正常。**如實記下的代價**：字典改成 fetch 進來，開機多一次 loopback 往返；`?v=` cache-buster 只掛在進入點，子模組靠 ETag 重新驗證；`state.js` 把散落的全域變成集中的全域——看得見了，但還是全域，改成注入是 D11。**沒做的事**：105 處 `innerHTML` 原封不動，把它和模組化混在同一輪會讓「行為不變」變成無法驗證的宣稱。
- ✅ 2026-09-17（第十二輪）：**R2 D11——程序內可變狀態收成可注入的 store**（[ADR-027](docs/ADR-027-injected-runtime-state.md) Accepted）。五個模組各自帶一個模組層可變全域（`_PENDING_L2_CONFIRMS`、`_ARMED_UNTIL`／`_PENDING_ARM_CODE`／`_PROCESSED_CALLBACK_IDS`、`_ASK_IN_FLIGHT`／`_ASKS_ANSWERED`、`_LLM_CACHE`、advisor `_cache`、`_PROJECT_CACHE`／`_LAST_PROJECT_REFRESH_TIME`），全部收進 `core/runtime_state.py` 的五個具名 store（`ConfirmStore`／`ApprovalState`／`ChatState`／`TtlCache`／`ProjectCache`），**每個自帶自己的鎖**——鎖跟著它保護的資料走，不再是一個模組層 `Lock` 保護一堆不相干的變數。碰狀態的函式加一個具名參數（`state=`／`cache=`／`confirms=`），不給就用 `runtime_state()` 的行程預設，**產品行為一字不差**。
  **清點比當初數的多兩個**：TODO 寫「三個 `_reset_*_for_tests` 鉤子」，實際有**五個**（`_reset_state_for_tests` 兩個、`_reset_llm_cache_for_tests`、`_reset_pending_confirms`、`reset_advisor_cache`）——五個全部從產品程式碼刪除（它們本來會進 wheel，而且只清得掉自己記得要清的欄位，漏一個的症狀是「單獨跑會過、一起跑會壞」）。取代它們的是 `tests/conftest.py` 的一個 autouse fixture：每個測試換一份全新的 `RuntimeState`，沒有人需要記得呼叫。刪鉤子時抓到一個真的踩坑：`test_llm_polish_cannot_add_facts` 中段那一次 `_reset_llm_cache_for_tests()` **不是清場，是測試邏輯的一部分**（強迫第二次 `polish_with_llm` 快取落空），盲刪會讓斷言變成假通過——改成注入一份新的 `TtlCache`，斷言一字未改。
  **CORS 不必再重啟**：`DynamicCorsMiddleware` 是 Starlette `CORSMiddleware` 的薄子類，每個請求比對一次當下設定的允許清單，**只有在真的變了的時候**才重算衍生標頭。改之前 `allow_origins` 凍結在 import 時刻：使用者在儀表板改了 `security.allowed_origins` 並存檔，安全邊界 middleware（每個請求重讀）立刻生效、CORS 標頭卻還是舊的，瀏覽器照樣擋下來——而且沒有任何地方寫著要重啟。邊界沒有放寬：允許清單仍然只來自設定檔，wildcard 照樣拒絕，loopback 限制與 extension token 邊界原封不動，改的只是「什麼時候讀」。
  **收據**：`pytest` 795 passed ＋ 1 skipped（新增 `tests/test_runtime_state_injection.py` 20 項：CORS 不重啟即生效、wildcard 仍然拒絕、產品程式碼零 `_reset_*_for_tests`、舊全域名稱零殘留、`runtime_state.py` 不碰磁碟／網路／資料庫且只 import 標準庫、兩份狀態互不影響、一次性碼只留雜湊、arm code 取出即銷毀、callback 去重有上限、注入真的接上了）；不裝 `[rag]` 782 passed ＋ 13 skipped；`python main.py verify` 在同一份資料上與 D11 前**逐位元組相同**；`python -m build` ＋ `verify_release_artifacts.py` `status: passed`；`scripts/dashboard_smoke.py` 回 `ok: true`。**契約測試自己也驗過**：把 `DynamicCorsMiddleware` 換回 `CORSMiddleware` 重跑，那支測試如預期在標頭那一行失敗（請求仍回 200——正是「邊界放行、CORS 沒跟上」的原形）。另外在**真的跑起來的服務**上走了一次同樣的流程（同一個 PID、`POST /api/v1/config` 後`access-control-allow-origin` 立刻變成新來源），不只在 TestClient 裡。
  **如實記下的代價**：每個碰狀態的函式多一個參數；**行程預設仍然是 process-wide 單例**——D11 買到的是「可注入」而不是「無全域」，要做到「每個請求一份」得把 store 放進 FastAPI `app.state` 並讓所有呼叫端拿 `Request`，代價大於收益。**沒做的事**：`agent_dispatch.py` 的 `_RUNNING` 與 `server.py` 的 `_EXTENSION_TOKEN_WARNING_STATE` 本質上綁在程序上、沒有測試鉤子，留在原地；ADR-026 承諾的「前端 `state.js` 改成注入」**這一輪沒有兌現**——前端 49 個共享值全部經由渲染函式的閉包讀寫，而且沒有等價於 pytest 的前端安全網可以證明「行為不變」，在能證明之前不動它，比動了再說「應該沒壞」誠實；它留在檢視報告 §4.4，不改成已完成。
- ✅ 2026-09-18（第十三輪）：**R2 D12——驗收中心改成宣告式表格**（[ADR-028](docs/ADR-028-declarative-acceptance.md) Accepted）。`core/acceptance.py` 是 1,560 行，其中 **992 行是 22 個手寫的 `_check_aN`**。那 22 個函式流程完全一樣（查資料 → 組 evidence → 一連串 `if ... return {"status", "detail", "evidence"}`），所以重複的是**協定**，不是內容。拆成兩件事：**去查什麼**（`readings.py` 的 reading 函式，回傳一個 `Reading`，不判定）與**查到什麼就算什麼**（`items.py` 表格裡與規格並排的階梯，由上往下第一條成立的說了算）；中間是十行的 `Ladder` 直譯器。套件五個模組：`rules`（狀態字彙、`Reading`、直譯器、共用查詢）／`readings`（585）／`items`（399）／`report`（249）／`__init__`（74），方向只准往下由掃 import 的測試把關。
  **`Reading` 有兩格是被現況逼出來的**：`evidence` 是會回到 API 與畫面上的對外契約，`facts` 只給階梯用——A8 的 evidence 給「最近一筆收據」但敘述要「最近一筆**成功**的收據」（最新那筆失敗時兩者不同）、A15 的 evidence 只留最近 7 天但敘述要第一天、A17 的 `tone_label` 刻意不對外。沒有 `facts` 的話，為了讓規則寫得出來就得偷偷往 evidence 加欄位——那是改了對外契約還說自己沒改。**提早回傳也保留**：功能關掉時不做昂貴的收集（A16／A19／A20／A22 的 `not_configured`、A6 的 `runtime_only`、A21 的「正在回收中」），連「只有那條分支才有的 evidence 形狀」都一樣。
  **收據**：`pytest` 810 passed ＋ 1 skipped（新增 `tests/test_acceptance_declarative.py` 15 項，守的是機構本身：22 項全是 `Ladder`、每個階梯以 `OTHERWISE` 收尾且只能在最後、狀態只能來自那七個常數、`attested` 不准寫進表格、沒有沒人用的 reading、沒有任何檔案超過 600 行、相依方向只准往下；另外四項把「順序就是語意」釘死——A15 的開關在最後、A16 的在最前、A21 的「沒跑完」先於「孤兒目錄」、「進行中」先於舊的完成收據。那四組正是**對調之後既有 36 項測試仍然全綠**的地方，實測過）；不裝 `[rag]` 793 passed ＋ 13 skipped；`python main.py verify` 與 D12 前**逐位元組相同**；`python -m build` ＋ `verify_release_artifacts.py` `status: passed`，wheel 含 `core/acceptance/` 五個模組；`scripts/dashboard_smoke.py` `ok: true`；另外在**真的跑起來的服務**上打 `/api/v1/acceptance/checklist`，22 項與 4 個 gate 都在，A6 走的是 CLI 看不到的 runtime 分支。
  **真正的證據是差分收據**：`pytest` 全綠只走得到本機資料庫剛好產生的那一條分支。把改動前的 `core/acceptance.py` 原封不動載成第二個模組，用 **85 個合成狀態**（每一項的每一條分支各至少一次，含 runtime、停用、失敗、正在進行中、舊索引、空的 day token、字串當路徑、`result_json` 不是物件…）同時餵給改動前與改動後，比對整份報告的 JSON——**鍵的順序也算**——85 個全部逐位元組相同。harness 自己也驗過會壞：蓄意把 `:.2%` 改成 `:.1%`、把 A21 兩個判斷對調、拿掉 `or not result`，三種都被抓出來。
  **如實記下**：**總行數幾乎沒有變少（1,560 → 1,505）**。省掉的約 250 行 `if/return` 樣板被表格結構吃回去；內容本身（22 份中文規格、22 個 evidence 組裝、約 80 句分支敘述）不可壓縮，要讓數字掉下來只能砍敘述或 evidence 欄位，那是改行為。「單檔 < 600 行」這個收據是**靠拆檔**達成的，不是靠變短。表格裡也仍有 lambda：73 條規則裡 8 條的判準用到（集中在 6 項），敘述那一欄約半數是 f-string 函式——不宣稱「全部宣告式」。**檢視報告那一條的建議只對了一半**：22 項並沒有、也不該收斂到「少數幾個通用探針」，因為每一項讀的東西本來就不同；真正重複的是判定協定。
- ✅ 2026-09-19（第十四輪）：**前端行為鎖**（[ADR-029](docs/ADR-029-frontend-dom-lock.md) Accepted）——D11 的續章，不是新題目。ADR-026 承諾「`state.js` 的全域在 D11 改成注入」，D11 只做了後端那一半，前端那一半 ADR-027 明說沒兌現，理由是**沒有等價於 pytest 的東西可以證明「行為不變」**。這一輪就是去把那個證據做出來：`scripts/dashboard_dom_lock.py` 用錄好的 35 個 API 罐頭重播六個分頁 × 兩種語言，把 `innerHTML` **原樣**存成 12 張快照，比對逐字元相等。時鐘、時區、亂數、輪詢（`setInterval`）全部釘死；一次開機只拍一個分頁（第一版「開一次點六頁」被穩定性檢查當場抓到 `tab-assistant` 會被後面的分頁點動——那個跨分頁耦合被記下來，沒有被剛好拍到的快照蓋掉）；**輸出不 normalize**，因為會折疊空白的美化器正好會吃掉「少一個空格」這種最典型的渲染回歸。
  **證明它鎖得住**：三個突變各自單獨套用、跑 `check`、還原——`esc()` 不再跳脫雙引號 → 4 張紅；`statusLabel` 的階梯最前面插一條先成立的分支（純順序，字串沒動）→ 2 張紅；`state.js` 的共享預設 `summaryView` 從 `day` 改成 `week` → 2 張紅（各多 2,240 字元）。**第一個突變在種子變兇之前是抓不到的**：第一版種子全是乖巧的中文，把 `esc()` 的 `&quot;` 整行刪掉，十二張快照一個字元都沒變。現在每一條渲染通道都帶一筆 `"雙引號" <b>粗體</b> & '單引號' <img src=x onerror=1>`，所以跳脫壞掉會紅——這也讓 repo 多了一件原本沒有的東西：**在沒有瀏覽器的機器上也跑得動的跳脫回歸測試**。
  **收據**：`pytest` 820 passed ＋ 2 skipped（新增 `tests/test_dashboard_dom_lock.py` 11 項，其中 10 項不需要瀏覽器，守的是**語料還有沒有資格當鎖**：快照不得過薄、必須含得到種子資料的痕跡、必須含跳脫過的惡意字串且不得含未跳脫的注入、分頁矩陣必須與 `index.html` 的 `data-tab` 對得上（加了第七個分頁沒擴充鎖就會紅）、語料不得夾帶機器路徑；第 11 項是真正的比對，設 `OMNI_DOM_LOCK=1` 才跑，實測 11 passed）；不裝 `[rag]` 807 passed ＋ 14 skipped（八個套件真的解除安裝後跑的）；`python main.py verify` 與這一輪之前**逐位元組相同**（本輪沒有動到任何產品程式碼）；`python -m build` ＋ `verify_release_artifacts.py` `status: passed`，且語料沒有混進 wheel。**守門測試自己也驗過會壞**：把種子清空重錄一次，十項裡有四項變紅，是真的跑出來的，不是推論的。
  **如實記下的代價**：語料是機器產物，35 個 JSON ＋ 12 個 HTML 約 340 KB 進 repo，改前端會看到快照一起變（那正是重點，但 review diff 會比較吵）；時鐘釘在錄製的那一刻，換機器重錄會整批變；覆蓋面只有「開機後的第一畫面」——**不鎖輪詢後的更新、不鎖互動、不鎖樣式、不鎖後端**，不要拿它當「前端有測試了」的通行證。**沒做的事**：預設不在 CI 跑（要在 runner 上裝 Chromium，那是另一個決定）；`state.js` 本身**這一輪沒有動**——這一輪交付的是證據，不是改動。
- ✅ 2026-09-20（第十五輪）：**D13——前端共享狀態分成具名 store ＋ 工廠**（[ADR-030](docs/ADR-030-frontend-state-stores.md) Accepted）。ADR-026 欠的那句「改成注入」到這一輪結清：`state.js` 的 49 個攤平欄位分成 **11 個具名 store**（`ui`／`feed`／`projects`／`focus`／`summaries`／`secretary`／`memory`／`rag`／`repos`／`settings`／`health`），欄位名同時縮短成在 store 裡讀得通的樣子（`state.ragChatHistory` → `state.rag.history`、`state.expandedProject` → `state.projects.expandedKey`）；**437 處存取全部改完，零殘留**。另外 `export const state = createAppState()`——**現在造得出第二份狀態**，D13 之前做不到。
  **動工前先擴充證據**：ADR-029 的鎖原本只釘「開機後的第一畫面」，而共享值大多是被互動讀寫的，所以照 ADR-029 自己寫的規矩先補了**五個唯讀互動場景**（展開專案卡、摘要切週、情報流切 Git、設定切 LLM 分頁、選 RAG 對話），快照 12 → 22 張、API 罐頭 35 → 40 個。場景最容易爛掉的方式是「選擇器還在但點了沒作用」，所以有一支測試比對「場景快照 ≠ 第一畫面」，實測會紅。
  **清點時翻出一個線上的錯並修掉**：`status.js` 的 `captureStateLabel(state)` 用參數把共享 `state` 遮蔽掉，`state.currentLang` 讀的是字串屬性——永遠 undefined，**中文介面的採集狀態標籤一直是英文**（`WAITING`／`UNSUPPORTED`／`N/A`）。修好之後 22 張裡只有兩張 zh-TW 快照變，而且把 `title` 遮起來後兩邊逐字元相同——證明只動到那 12 個標籤文字。順手清掉另外兩處同類遮蔽，並加測試擋住整個類別。
  **鎖在同一天就攔下一次真的回歸**：改 `renderRuntimeTrust` 參數名時漏掉函式尾端一行 `state === "stopped"`，它於是從「比對參數」變成「比對匯入的物件」，badge 從 `STOPPED` 變 `DISCONNECTED`；`check` 當場報 6 張不同並指到那一段。
  **收據**：`scripts/dashboard_dom_lock.py check` **22 張逐字元相同**（437 處改動、12 個檔案，輸出一個字元沒變）；`pytest` 829 passed ＋ 2 skipped（新增 `tests/test_frontend_state_stores.py` 6 項：11 store／49 欄位、每條 `state.<store>.<field>` 都必須存在、不准再有攤平存取、歸屬表對得上、用 `node` 真的載進來證明兩份互不相干；另加 `test_nothing_shadows_the_shared_state`）；不裝 `[rag]`（八個套件真的解除安裝）816 passed ＋ 14 skipped；`python main.py verify` 與改動前**逐位元組相同**；`python -m build` ＋ `verify_release_artifacts.py` `status: passed`。四支新測試都做過負面驗證：打錯欄位名、加一處沒宣告的跨模組存取、把遮蔽改回去、把場景步驟換成沒作用的點擊，各自都紅。
  **如實記下的代價**：**`export const state` 還在，整個程式還是共用它**——買到的是「可以另外造一份」與「欄位有歸屬」，不是「沒有全域」，和後端 D11 停在同一個地方。**沒有做完全的參數注入是刻意的**：119 個函式碰狀態，其中大量直接掛給 `addEventListener`，加一個有預設值的 `state` 參數會讓瀏覽器把 `MouseEvent` 當成狀態傳進去，而鎖只涵蓋第一畫面與五個場景，涵蓋不了那幾百個 listener 路徑——沒有證據就不做。存取也變長了（`state.currentLang` → `state.ui.currentLang` 光這個就 125 處）。**未涵蓋面**：表單送出、對話框、會 POST 的動作、輪詢之後的更新、樣式——那些路徑上的改動只有「語法正確 ＋ 路徑存在」兩層保證，沒有渲染層面的證據。
- ✅ 2026-09-16（第五輪）：**R1 D4——`core/server.py` 依領域切成 9 個 router**。動刀前先做安全網：`tests/test_api_route_snapshot.py` 從執行中的 app 抓下**全部 133 條路由**（98 條主服務 ＋ 31 條 DeskRAG）的（路徑、方法、handler 名稱）快照並鎖住——少一條、多一條、改名都會失敗。搬家用 AST 逐個頂層定義切出來，每個模組的 import 由「這個模組實際用到哪些名稱」自動推導，再用 pyflakes 確認零未定義、零未使用。結果：`core/server.py` **1,995 行 → 134 行**，只做四件事（建 app ＋ 安全邊界 middleware、掛 `/static`、掛 9 個 router ＋ RAG、為既有呼叫端保留 `asset_version`／`render_index_html`／`WEB_DIR` 的名稱）。新增 `core/api/{pages,system,events,activity,secretary,projects,repos,integrations,settings}.py`（每個 66～334 行）＋ `core/api/deps.py`（execution token 閘門，秘書與排程兩個 router 共用，只有一份）；33 個 Pydantic model 集中到 `core/schemas.py`；AI 事件的 `turn_key` 與 `response_status` 判定移到 `core/ingest.py`（那是 ADR-001 的 provenance 規則，不是 web 層的事）。**行為不變**：路由表、middleware 順序、403／401 的 detail 字面全部原封不動。測試只改 patch 目標的 import 路徑（14 個 `core.server.X` → 對應的 `core.api.X`，斷言一字未改）。**收據**：`pytest` 662 passed ＋ 1 skipped（新增路由快照 3 項）；不裝 `[rag]` 650 passed ＋ 12 skipped；`verify` 輸出與基底相同；`python -m build` ＋ `verify_release_artifacts.py` 通過且 wheel 含 `core/api/` 全部模組。
- ✅ 2026-09-22：**推廣路線 E1——`omni demo` 示範資料集設計定案**（[ADR-031](docs/ADR-031-omni-demo-dataset.md) Accepted，設計）。R0～R2 的減法與合併已於 2026-09-20 全部完成，TODO 沒有排下一段方向，於是本輪先把 §13.3 的三項推廣路線目標寫進 `docs/TODO.md` 新的 E 段（E1 demo 資料集／E2 `agent-transcripts` 獨立套件／E3 `omni init` 自動偵測），並先啃掉風險最大的設計決策：demo 資料要放哪裡、以什麼形式進系統。定案的邊界：獨立子指令 `omni demo`、固定隔離 home（`~/OmniContext-Demo`，透過既有 `OMNICONTEXT_HOME` 覆寫機制，不新增第二套家目錄邏輯，也永不共用使用者真實資料庫）；示範資料**走既有的 parser／watcher 進系統**（合成的 Claude Code／Codex transcript、`GIT_AUTHOR_DATE` 回填的假 commit、假檔案事件），不直接寫 DB——理由是本專案已經因 ADR-023／024／025 反覆糾正過「同一件事多寫一份」，demo 產生器沒有理由開第二條 ingestion 路徑；時間相對於執行當下回填，讓依賴「已結束的週／日」的功能（週回顧、模式提案、每日工作誌）吃得到資料而不會隨時間退化。**這一輪只寫決策，沒有新程式碼**：`pytest` 829 passed ＋ 2 skipped、`python main.py verify` 與本輪之前逐位元組相同（本輪未動任何產品程式碼）。**下一輪待辦**：依 ADR-031 實作 `demo` 子指令與示範語料，E2／E3 尚未開始。

---


## 12. 下一階段規劃（2026-09-13 檢視）

> §11 的三個時間桶（短期驗證債、中期 P4.3／P6、長期 P5-2 重啟）**都已完成**，成果紀錄留在上面。
> 這一節是接下來的方向與取捨；**待辦條目與完成判準一律以 [docs/TODO.md](docs/TODO.md) 為準**，這裡只寫「為什麼先做這個」。

### 12.1 現在的形狀

程式面已經走完 P0–P8 與 ADR-008 全階段，**剩下的缺口幾乎都不是「還沒寫的程式」**：

| 類型 | 內容 | 為什麼卡在這裡 |
| :--- | :--- | :--- |
| 🔴 能力缺口（1 項） | 全天 coverage ledger 實測（TODO A1） | 只能由 Windows 實機跨午夜連續運行產生；這是唯一還擋 `release_ready` 的能力型缺口 |
| 👤 實機收據（A2–A22，其中 A6／A21 已取得） | 雲端 provider 複測、Telegram 設定與 inline 批准、L2 draft→apply、P4.3 對帳、Repo 批次、記憶區、LINE 推播、問候卡、行事曆、模式提案、個人檔案、秘書桌面、每週回顧、文件落後、會議秘書 | 功能都有 contract tests 與容器 E2E，但本專案不把「測試通過」當「實機可用」 |
| ⚪ 技術債（B1–B4） | legacy AI rows 無 provenance、Extension 只驗過 ChatGPT/Claude.ai、PyPI 不在範圍、repo onboarding 動作不留收據 | 都已如實標記邊界，不回填假資料 |

**結論：下一階段的價值主要來自「把已實作變成已驗證」，而不是再加功能。** 功能候選只在使用者明確要求時啟動。

> 2026-09-04 起，A 段的收據不必自己一項項翻：**「06 系統設定 → 驗收中心」或 `python main.py verify`** 會直接去本機找收據，並依 §12.3 的條件回報四個 gate 現在缺什麼（[ADR-016](docs/ADR-016-acceptance-center.md)）。它只讀不做——不會替你執行任何驗收動作，`release_ready` 仍由人在文件裡改。

### 12.2 功能候選路線（依需求啟動，非排序）

1. **C5 私有網路遠端存取（工作量最大，安全邊界要改）**
   - 目標：手機用瀏覽器看完整儀表板，而不只是 Telegram 文字。
   - 前置：先寫 ADR——認證形狀（不是無認證的 `allow_remote_clients` 全有全無開關）、CIDR allowlist（預設只放行 Tailscale／WireGuard 網段）、失敗即拒、每次存取留收據；PWA 為選配。
   - 代價：這是**唯一會動到 [ADR-001](docs/ADR-001-p2-5-trust-boundary.md) loopback 邊界**的路線，必須獨立驗收；**不做公開反向代理**。

2. **C6 LINE 雙向（受平台限制，價值有限）**
   - 目標：LINE 也能提問與批准，而不只是收推播。
   - 前置：公開 HTTPS 入口（Cloudflare Tunnel／中繼）＋ `x-line-signature` 驗證＋ postback 按鈕，同樣要改 ADR-001。
   - 代價：與 C5 撞同一個邊界，但換來的能力 Telegram 已經有（[ADR-014](docs/ADR-014-multi-channel-push-and-arm-code.md) 已說明 LINE 只能推播的原因）。**建議 C5 先於 C6**；若真要雙向，優先走 Telegram。

3. **C3 其餘採集來源（增量小、可逐項評估）**
   - 已納入：行事曆（2026-09-04，[ADR-015](docs/ADR-015-local-calendar-source.md)）。
   - 剩餘候選：瀏覽器閱讀、terminal history、未 commit 的工作狀態。
   - 門檻不變：**每項先過「能否改變決策」檢驗**（像 ADR-015 開頭那張表一樣寫出來），過不了就不納入——採集越多不等於越有用，只會增加隱私面與噪音。

> **C7 會議秘書第一層已於 2026-09-08 實作**（[ADR-022](docs/ADR-022-meeting-secretary.md)），紀錄見 §11.2，實機收據為 TODO A22。
> **第二層（即時字幕／翻譯＝錄下其他人的聲音）刻意沒做**——要做得先過 ADR-022 D6 的五道門並另寫一份 ADR。

另有隨時可做的低風險項：C1 更多 L2 template（一次一個審查）、C2 更多 L0 可排程 template（L1/L2 永遠不可排程）、C4 更多配色（只加一組 CSS 變數）。

### 12.3 `release_ready: true` 的收斂條件

同時滿足才重評，缺一不改旗標：

1. TODO A1 全天 coverage ledger 取得實機收據，`meets_full_coverage: true`；
2. A2–A22 中屬於**預設開啟路徑**的收據齊備（雲端 provider 複測、大索引檢索、問候卡與行事曆呈現）；預設關閉的危險能力（L2、Telegram 批准、LINE）可標為 optional-verified；
3. `docs/RELEASE_CHECKLIST.md` 走完一輪，且跨平台 CI 在該 commit 上有自己的 run receipt（不沿用舊 run）；
4. STATUS.yaml 的 `known_blockers` 不再有 🔴 項目，且每個 quality gate 都是 `passed_*`（`implemented_*` 不算）。

---

## 13. 架構整頓與推廣方向（2026-09-16 檢視）

> 依據：[docs/REVIEW-2026-09-16-project-assessment.md](docs/REVIEW-2026-09-16-project-assessment.md)（現況、價值評估、架構體檢，每項附檔案：行號）。
> 這一節只寫「為什麼這樣排、先做什麼」；**待辦條目與完成判準一律在 [docs/TODO.md](docs/TODO.md) B5–B9 與 D 段**。

### 13.1 檢視結論

- 程式面 P0–P8 ＋ 22 份 ADR 全部落地、663 項測試容器全綠；**功能已經夠多，缺的是減法。**
- 專案唯一沒有替代品的能力是**讀本機 AI agent transcript 並還原成有 provenance 的工作脈絡**；
  其餘（RAG、Git 同步、Telegram／LINE、會議秘書）市面都有更成熟的替代品。
- 現在的形狀不適合對外：安裝 800 MB 起、449 行設定、視窗採集與桌面通知綁 Windows、
  兩套 LLM client、兩套向量記憶、四份活躍聚合、2,003 行的 `server.py` 與 6,055 行的 `app.js`。
- 因此 §12.2 的功能候選 **C5／C6／C3 全部暫停**，優先順序改為下面三個階段。

### 13.2 三個階段（每階段結束都要能 `pytest` 全綠、`verify` 結果不變）

**R0 減法（1 個 session，零設計決策）**——✅ 全部於 2026-09-16 完成（§11.2 兩條）
- 刪已核實的死碼與未用依賴（TODO B5–B9）：重複的 `SystemMaintenanceRequest`、`telegram_notifier.py`、`_std_scheduler_loop`、四個 `scripts/inspect_*`／`check_real_recent.py`、`pandas`／`pillow`／`sse-starlette`／`python-dotenv`、`clear-demo`。
- `rapidocr` 二選一：宣告成選用依賴，或移除 `image_parser.py`。
- `marked` 與字型改為隨 wheel 本機提供，恢復 local-first 宣稱。
- RAG 依賴鏈改成 `omnicontext[rag]` 選用依賴（TODO D1）；量預設安裝體積前後對照當收據。→ 實測 721 MB → 176 MB。

**R1 合併（2–3 個 session，小設計決策）**——✅ 全部於 2026-09-16 完成（§11.2 五條）
- ✅ 一個 LLM client（同步 `generate` ＋ 串流 `stream_chat`），`synthesizer/llm_client.py` 與 `rag/llm_gateway.py` 收成 `core/llm_client.py`（D2，2026-09-16 完成，§11.2）。
- ✅ 一個 `core/activity_sources`（`EVENT_SOURCES` ＋ `project_activity_matrix` ＋ `project_event_counts`），四處改吃它（D3，2026-09-16 完成，§11.2）。
- ✅ `core/server.py` 依領域切成 9 個 `APIRouter`（1,995 → 134 行），Pydantic model 集中到 `core/schemas.py`，AI ingest 邏輯搬到 `core/ingest.py`（D4，2026-09-16 完成，§11.2）。
- ✅ `desktop_notifier` 進 `ChannelAdapter`，扇出只剩 `secretary_push` 一處（D5，2026-09-16 完成，§11.2）。
- ✅ 六層旗標收成三個、22 個引擎參數回程式常數（D6，2026-09-16 完成，§11.2；ADR-008 Addendum D）。

**R2 重構（要先寫 ADR）**
- ✅ 兩套向量記憶二選一（D7，2026-09-16 完成，§11.2）：活動記憶＝`core/semantic_index`（核心）、文件＝RAG（選用），取捨與遷移見 [ADR-023](docs/ADR-023-one-activity-memory.md)。
- ✅ 秘書叢集 11 模組 → `core/secretary/` 四層 ＋ `Signal`／`Proposal` dataclass（D8，2026-09-16 完成，§11.2；ADR-024）。
- ✅ `agent_log_watcher` 拆成每平台一個 parser 模組 ＋ 「檔案在動、事件是零」的漂移警示（D9，2026-09-17 完成，§11.2；[ADR-025](docs/ADR-025-transcript-parsers-and-drift.md)）。
- ✅ `web/app.js` 拆 ES module、`I18N` 移到 JSON 語系檔、所有 fetch 走共用 helper（D10，2026-09-17 完成，§11.2；[ADR-026](docs/ADR-026-frontend-modules.md)）。
- ✅ 模組層可變全域狀態改注入（`core/runtime_state.py`），刪掉**五個** `_reset_*_for_tests` 鉤子，CORS 改設定不必重啟（D11，2026-09-17 完成，§11.2；[ADR-027](docs/ADR-027-injected-runtime-state.md)）。
- ✅ `acceptance.py` 改宣告式表格：一個 reading ＋ 一張階梯表，拆成 `core/acceptance/` 五個模組（D12，2026-09-18 完成，§11.2；[ADR-028](docs/ADR-028-declarative-acceptance.md)）。**ROADMAP §13 的 R0～R2 到此全部完成。**
- ✅ D11 欠的前端那一半有安全網了：`scripts/dashboard_dom_lock.py` ＋ DOM 快照（2026-09-19，§11.2；[ADR-029](docs/ADR-029-frontend-dom-lock.md)），2026-09-20 再補上五個互動場景（12 → 22 張）。
- ✅ D13 `state.js` 分成 11 個具名 store ＋ `createAppState()` 工廠，437 處存取改完、22 張快照逐字元相同（2026-09-20，§11.2；[ADR-030](docs/ADR-030-frontend-state-stores.md)）。**仍然不是「沒有全域」**——共用的那一份還在，理由（listener 陷阱）寫在 ADR-030。

### 13.3 推廣路線（R0 之後才啟動）

> **現況（2026-09-22）**：待辦條目與完成判準已移到 [docs/TODO.md](docs/TODO.md) E 段（E1／E2／E3，對應下方 1／2／3 項，執行順序改為 demo 先行，理由見 TODO E 段）。第 2 項（demo 資料集）設計已定案，[ADR-031](docs/ADR-031-omni-demo-dataset.md)；實作留待下一輪。

1. **獨立套件 `agent-transcripts`**：四種 transcript 格式 → 統一 turn 模型 ＋ provenance ＋ drift 偵測。是唯一別人會單獨想要的東西，也是學術貢獻的載體。
2. **`omni demo` 示範資料集**：匿名化假 transcript ＋ Git ＋ 檔案事件，五分鐘看到首頁與 Handoff；教學與展示都靠它。
3. **`omni init` 自動偵測** `~/.claude`／`~/.codex`／Antigravity 路徑並詢問匯入，取代手填 449 行設定。
4. **教學**：以 ADR、`NEXT_SESSION.md` 踩坑清單與 REVIEW §4 當案例教材，不要求學生安裝整套。
5. **學術**：先寫 experience report；等 `agent-transcripts` 有第二位使用者再談實證。

### 13.4 不做的事

- R0 完成前不加任何採集來源、通道或 L2 template。
- 不為了「更像人」引入 LLM 推斷個性或優先（ADR-018 的立場不變）。
- 不動 ADR-001 的 loopback 邊界（C5／C6 連帶暫停）。
