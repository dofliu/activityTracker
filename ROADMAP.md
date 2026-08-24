# OmniContext 開發規劃與成果紀錄 — P0 ~ P6

> 最新更新日期：2026-08-24　｜　當前進度：92% (全系統採集器穩定運作、AI 結論配對達 95.6%、論文目錄收斂完備)
> 本文件記錄 OmniContext 從 0 到 1 的缺陷修復歷程、已完成之架構改造與未來的維運與延伸規劃。

---

## 0. 系統進化歷程與實測數據對比 (最新實測校準)

經過深度代碼審查與連續運行實測，各項核心指標已全面校準至最嚴格的真實數據：

| 評估指標 | 初始狀態 (2026-08-22) | 現行實測成果 (2026-08-23 校準) | 改善效益與判定 |
| :--- | :--- | :--- | :--- |
| **AI 對話捕捉總量** | 10 筆 (7 筆當日 + 3 筆假資料) | **2,034 筆真實對話** | 跨 3 大本機 Agent 工具，清除 257 筆 orphan 重複紀錄 |
| **AI 真實結論配對率** | 0% (僅單向問句) | **95.6% (1,944 筆實質回答)** | **嚴格排除佔位符 (剩餘 0 筆)**：Codex 97.9%、Antigravity 94.8%、Claude Code 88.0% |
| **檔案監控噪音比** | 3574 筆雜訊 / 1 筆論文 | **單日 ~70 筆真實寫作/代碼** | 移除 .txt、過濾自身 logs 與 CASE-* 實驗數據，設單日 5 次單檔上限 |
| **Git 倉庫覆蓋率** | 0 個 (要求根目錄為 repo) | **49+ 個 Git Repos 遞迴探索** | 90+ 筆真實 Commits 跨專案納管與 PR 即時追蹤 |
| **專案分類正確性** | 全數落入論文 (單一 .md 誤判) | **Top-Down Canonical Resolver** | 81 個碎片化子目錄收斂為清楚的論文與代碼主專案 |
| **Open Loops 歸戶率** | 0% (全落入 General) | **100% 精準指派至各專案** | 清洗 Markdown 前綴、支援含空格與中文專案名稱 (`113-01 離岸風電實務`) |
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
   - **Codex CLI**：解析 `rollout-*.jsonl`，過濾工具調用字串（配對率 97.9%）。
   - **Antigravity**：重構 `transcript.jsonl` 解析器，精準提取 PLANNER_RESPONSE 最終文字結論（配對率 94.8%）。
   - **Claude Code**：深度解析 `projects/**/*.jsonl`，清除 `history.jsonl` 無回答的 orphan 紀錄，成對累積多輪對話（配對率 88.0%）。
   - **完全清除佔位符**：資料庫與 Prompt 組裝全面過濾 `[external_agent_tool_call]` 與 `[Codex CLI Session]` 等佔位字串，剩餘佔位符歸零。
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
4. **主動通知器 (`notifiers/telegram_notifier.py`)**：
   - 預設啟用推播開關，支援 `python main.py notify briefing --dry-run` 與實際發送。

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

> Claude Code 的 53% 是資料來源限制，非缺陷：`~/.claude/history.jsonl` 本身只存提問不存回應，
> 這批紀錄永遠無法配對；`projects/**/*.jsonl` 來源的配對率則正常。

### 視窗採集器心跳 `watchers/window_watcher.py`
- 每 5 分鐘（`heartbeat_minutes`）記錄一次實際讀到的前景視窗，讀不到時以 WARNING 標示。
- 目的是讓下次靜默失效能直接從日誌判斷是「讀不到」還是「寫不進」。

---

## 2. 維運清單 (Remaining Maintenance)

1. **Chrome MV3 擴充套件實機載入**：
   - 於 Chrome Developer Mode 載入 `watchers/browser_extension`，測試 claude.ai / chatgpt 網頁端對話捕捉。
   - 目前資料庫中 `platform` 尚無 gemini / chatgpt / claude / manus 任何一筆，此路徑未經實機驗證。
2. **自動開機排程佈署 (`scripts/install_autostart.ps1`)**：
   - 註冊 Windows Task Scheduler 工作排程，支援背景靜默啟動（`pythonw.exe`）。
3. **視窗採集器持續觀察**：
   - 2026-08-23 重啟服務後恢復正常（當日 27 筆）。心跳日誌已就位，若再次靜默可從日誌判斷是讀取端或寫入端。

---

# 第二階段規劃：從「日誌」到「記憶」

> 規劃日期：2026-08-24
> 依據：15 個月實際使用資料的價值評估

## 3. 現況定位與缺口分析

### 3.1 已累積的資料資產

```
2,047 筆 AI 對話 · 1,775 筆完整問答配對 · 約 236 萬字元
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
| 核心邏輯硬編碼個人路徑 | `project_engine.py:415-418` 寫死 `D:/Project_CodingSimulation` 四層 | 🔴 他人安裝後歸戶會壞 |
| 僅支援 Windows | 視窗採集、桌面通知、開機排程綁 win32 / PowerShell | 🟡 Mac / Linux 使用者無法進入 |
| 無測試、無打包 | 缺 `tests/`、`pyproject.toml`、`setup.py` | 🟡 無法 `pip install`，重構風險高 |
| 首次啟動需手改 config | 無引導，`config.example.yaml` 全是個人絕對路徑 | 🔴 十分鐘內裝不起來即流失 |
| 必須自備 LLM 金鑰 | 無金鑰時只剩事件流 | 🟡 Ollama 路徑已在，可作免金鑰預設 |

前兩項決定「能不能用」，後三項決定「願不願意留下」。

---

## 4. P3：記憶層（最高優先）

> 目標：讓 236 萬字元從「存著」變成「用得到」。**不需要任何新的採集器。**

### P3-1 專案接續 Context Handoff（建議首先實作）

- 新增 `python main.py resume <專案>`，產出可直接貼進任何 AI session 的接續 prompt：
  這個專案在做什麼、上次做到哪、未結事項、近期關鍵決策。
- **不需 embedding**，用現有 `project_states` + `open_loops` + 最近 N 筆對話即可組出。
- 解決每天在不同 AI 之間重複交代背景的成本，半天內可驗證方向。
- 驗收：對任一活躍專案執行後，產出的 prompt 貼進新 session 即可無縫接續，無需人工補充。

### P3-2 本機語意檢索索引

- 以本機 embedding（Ollama / RTX 4080）對 1,775 筆完整問答建索引，**資料不出本機**。
- 新增 `ai_embeddings` 資料表與增量索引流程，沿用既有的 `(mtime, size)` 增量掃描模式。
- 驗收：對歷史問題的語意查詢能回傳正確的原始對話，冷啟動建索引時間可接受。

### P3-3 `omni ask`：問自己的歷史

- `python main.py ask "我上次怎麼解決 SQLite database locked?"`
- 建立在 P3-2 之上，回傳原始對話出處（時間、平台、專案）與結論摘要。
- 直接解決「跨 AI 切換、記不住」的原始需求。

### P3-4 重複工作偵測

- 新提問與歷史高相似度時主動提示「3 週前問過 Gemini 幾乎相同的問題」。
- 價值在於避免重做已解決的事。

### P3-5 Session 敘事層

- 現行階層為 `event → project`，缺少中間的 `session`。
- 人記憶工作的單位是「那天下午在弄時區那件事」，不是「23:47 修改了 file_watcher.py」。
- 將 2 小時內同專案事件聚成 session 並生成一句話標題，提升整體可讀性。

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

## 6. P5：效率工具層

1. **`STATUS.yaml` 自動維護**：全域規則要求每個專案都需具備，49 個 repo 手動維護不可行。
   系統已知各專案最後活動與進度線索，可自動起草、使用者確認。
2. **停滯 PR 與分支提醒**：GitHub 資料已在庫中（57 repos / 266 PRs），只差把
   「此 PR 已開啟 12 天無進展」推送到眼前。
3. **週報與月報 rollup**：日報是原料，週報才是可交付成果（研究進度、投稿追蹤）。
4. **實體抽取與知識圖譜**：從對話抽出人名、論文、概念、決策並連成圖。
   此為 MSG-IRAG 研究方法的真實應用場景，可同時產出論文素材。

---

## 7. P6：開源整備（在 P3 完成後再進行）

1. 將 `project_engine.py` 的硬編碼路徑抽成設定項。
2. 新增 `python main.py init` 互動式引導，自動偵測本機 Agent 日誌路徑與 Git 根目錄。
3. 建立 `pyproject.toml` 與基本測試（優先覆蓋 `is_cli_artifact`、`resolve_project_from_path`、
   `summarize_action` 這類純函式）。
4. 無 LLM 金鑰時預設走 Ollama，確保零金鑰也能完整體驗。
5. 跨平台：視窗採集與桌面通知抽象出平台介面，Windows 以外先降級為停用而非報錯。

---

## 8. 建議執行順序

```
P3-1 resume（半天，無需 embedding）
  → P3-2 語意索引（半天）
  → P3-3 omni ask（半天）
  → P3-5 session 敘事層（一天）
  → P5-1 STATUS.yaml 自動維護
  → P4 收集層擇一補完
  → P6 開源整備
```

理由：P3-1 至 P3-3 不需要任何新採集器，資料已經在庫裡卻從未被使用；
它們同時解決原始需求（跨 AI 切換、大量專案、記不住），
而「對自己的 AI 對話做檢索與接續」也正是本專案對外最具辨識度的能力。

