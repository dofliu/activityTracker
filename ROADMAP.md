# OmniContext 開發規劃與成果紀錄 — P0 ~ P6

> 最新更新日期：2026-08-26　｜　目前狀態：**personal alpha / P2.6 + P3 context memory + P5-1 proposal-only alpha**。P3-1～P3-5、Windows Toast E2E、formal rollback、Windows/macOS/Linux CI、P5-1、collector runtime diagnostics 與 Extension 1.3.1 live-verification harness 已完成；ChatGPT live selectors 已修復。Claude.ai 本輪 PASS receipt 與 Extension live heartbeat 尚未完成，整體不具 release-ready 或 autonomous-ready 資格。
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

**2026-08-25 實作證據：**localhost 主頁與 `/extension-monitor` 已完成 live smoke；usage API coverage 維持 `partial`。Windows 隔離 milestone E2E 已走過真實 WinRT Toast submission、SQLite sent receipt 與 duplicate suppression；正式資料庫未寫入測試 event。coverage ledger 尚未完成，因此維持 Alpha。

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
- [ ] 新版 Extension heartbeat 實機 receipt、ChatGPT/Claude、真實達標 Toast、macOS/Linux CI/實機仍待完成。
- [x] Contract tests 已覆蓋 interval merge、跨午夜、缺失平台、通知去重與內建 scheduler job contract。
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
- [ ] 取得 Claude Code 與 Claude Desktop local-agent 的 live completed receipt；此項未完成前不宣稱平台全天背景工作 coverage。

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
  → P5-1 Proposal-only 主動建議（不執行修改）
  → P5 executor 獨立安全驗收
  → P4 收集層補完
  → P6 開源整備
```

理由：
1. P3-1 已證明 Context Handoff 的產品價值，但 final-response 與 Open Loop 仍需可信度 gate。
2. P3-2 + P3-3 只有建立在可追溯 turn contract 上，語意檢索結果才可被引用與回查。
3. P5 先做 proposal-only；任何自主修改都必須具備 allowlist、dirty-worktree check、timeout、cancel、audit receipt 與分級批准。
