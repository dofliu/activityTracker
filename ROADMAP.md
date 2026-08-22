# OmniContext 開發規劃 — P0 ~ P2

> 建立日期：2026-08-22　｜　依據：2026-08-22 全專案程式碼審查與資料庫實測
> 本文件是後續開發的唯一依據，每完成一個階段請同步更新 `STATUS.yaml` 的 `progress` 與 `next_milestone`。

---

## 0. 現況盤點（為什麼需要這份規劃）

系統骨架（Watcher → SQLite → Aggregator → LLM → Web UI）已完整，但**實際捕捉到的有效資料幾乎為零**。
目前 `reports/Daily_Summary_2026-08-22.md` 的內容約 90% 來自 `python main.py seed-demo` 寫入的示範假資料，不是真實活動紀錄。

### 0.1 資料庫實測（2026-08-22 單日）

| 來源 | 設定 | 實際筆數 | 判定 |
| :--- | :--- | :--- | :--- |
| AI 對話 | 4 個網頁平台 + Claude Code | 10 筆（7 筆 Antigravity + 3 筆假資料） | 幾乎沒抓到 |
| 檔案異動 | Documents / Papers / 期刊資料夾 | 3575 筆，其中 3574 筆為 `Documents/Codex/2026-08-22` 的 `.py` 噪音，真實論文僅 1 筆 | 訊噪比 1:3574 |
| Git commits | `D:\Project_CodingSimulation` | 1 筆（假資料） | 完全沒掃到 |
| 視窗焦點 | 5 秒輪詢 | 145 筆 | 唯一正常運作 |

### 0.2 已確認的六個缺陷

| # | 缺陷 | 位置 | 後果 |
| :-- | :--- | :--- | :--- |
| D1 | 時區混用：採集端寫 `datetime.utcnow()`，查詢端用 `datetime.now()` | `watchers/file_watcher.py:71`、`watchers/window_watcher.py:100`、`core/server.py:177,195,219,230` vs `synthesizer/aggregator.py:181` | Checkpoint 永遠是空的；同一個 DB 兩種時區混存，Live Feed 排序錯亂 |
| D2 | `config.yaml` 的 `ignore_patterns` 從未被任何程式讀取 | `watchers/file_watcher.py`（全專案 grep 無第二處） | 一次 pip 安裝就灌入 3467 筆垃圾事件 |
| D3 | Git 監控要求路徑本身是 repo，且不遞迴 | `watchers/git_watcher.py:60` | `D:\Project_CodingSimulation` 底下 49 個 repo 一個都沒掃到 |
| D4 | Claude Code 日誌解析是空殼 | `watchers/agent_log_watcher.py:124`（僅一行註解） | 主力工具的紀錄一筆都沒有 |
| D5 | 擴充套件去重雜湊使 AI 回應永遠被丟棄 | `watchers/browser_extension/background.js` | 只存得到「問了什麼」，存不到「答了什麼」；MV3 worker 休眠後又會重複寫入 |
| D6 | 設定頁六個 AI 工具開關未被讀取 | `web/index.html:188-208` vs `web/app.js:313 saveSettings()` | UI 承諾了不存在的控制能力 |

### 0.3 設計層缺口（比缺陷更關鍵）

需求是「**隨時知道進行中的工作、提醒我別忘記**」，但目前系統做的是「**每天 23:30 產一份日報**」：

- **沒有「進行中」的概念**：資料模型只有 event，沒有 thread / open-loop，也沒有專案維度聚合。每天的報告彼此獨立、不跨日接續，無法回答「MSG-IRAG 上次做到哪、卡在哪」。
- **沒有主動推送**：所有結果都要自己開 `127.0.0.1:8765` 才看得到。
- **不會自動啟動**：需手動 `python main.py`，關掉終端即停止。「不需要太多設定」目前不成立。
- **最大宗的一手資料完全沒接**：本機已存在但未使用的日誌見附錄 A。

---

## P0 — 讓現有功能真的能用

> 目標：不新增功能，只讓已寫好的東西產出**真實且乾淨**的資料。
> 預估：0.5 ~ 1 天　｜　完成後 `progress: 50`

### 任務

1. **統一時間基準（修 D1）**
   - 全專案採集端改用本地時間（`datetime.now()`），或全面改為 timezone-aware UTC 並在查詢／顯示層轉換。
   - 建議選前者：本系統是單機單時區工具，本地時間最直觀，改動面最小。
   - 需修改：`core/models.py`（`default=datetime.utcnow` → 本地時間函式）、`watchers/file_watcher.py:71`、`watchers/window_watcher.py:75,100,131`、`core/server.py:177,195,219,230-231`、`synthesizer/aggregator.py:318,325`。
   - 撰寫一次性遷移腳本 `scripts/migrate_timezone.py`，將既有 `file_activity_events`、`window_events` 的時間戳 +8 小時，並記錄遷移旗標避免重複執行。

2. **實作 `ignore_patterns` 與噪音過濾（修 D2）**
   - `file_watcher` 讀取 `ignore_patterns` 並以 `fnmatch` / `pathlib.match` 比對完整路徑。
   - 追加預設排除：`site-packages`、`.venv`、`node_modules`、`dist-info`、`__pycache__`、`.git`、暫存檔（`~$*`、`*.tmp`、`*.crdownload`）。
   - 追加「同檔案 N 分鐘內只記一次」的合併機制（目前 debounce 僅 2 秒），避免編輯器自動存檔灌爆事件表。
   - 清理既有 3574 筆噪音資料（`scripts/cleanup_noise.py`）。

3. **Git 監控改為遞迴掃描（修 D3）**
   - `git_watcher` 支援「根目錄」模式：自動尋找指定深度（預設 3 層）內所有 `.git` 目錄。
   - 加入 `max_depth` 與 `exclude_dirs` 設定；掃描結果快取，避免每 5 分鐘重複遍歷 49 個 repo。
   - 移除「只取今天 commits」的限制，改以「最後掃描時間」為斷點，補齊歷史。

4. **修復擴充套件去重（修 D5）**
   - `background.js` 去重鍵改為 `platform + prompt_hash + hasResponse`，讓「補上回應」的第二次請求能通過。
   - 後端 `POST /api/v1/events/ai` 改為 upsert：同一 `conversation_id + prompt_text` 已存在時補寫 `response_text`，而非新增列。
   - MV3 service worker 的去重集合改存 `chrome.storage.session`，避免休眠後失效。
   - 本機伺服器未啟動時，事件先進 `chrome.storage.local` 佇列，連線恢復後補送。

5. **移除或接上假開關（修 D6）**
   - `web/app.js:saveSettings()` 補上六個 checkbox 對應的 config 欄位（`watchers.*.enabled` 與新增的 `watchers.browser.platforms`）。
   - 若短期不實作，先在 UI 標示「規劃中」並停用，不要留下無作用的控制項。

### 驗收標準

- 執行 `python main.py checkpoint --hours 2` 後，產出的 Checkpoint 內容與該時段實際工作吻合（非空白）。
- 單日 `file_activity_events` 新增數 < 200 筆，且 `.py` 噪音占比 < 20%。
- `git_activity_events` 能掃到 `D:\Project_CodingSimulation` 底下所有 repo 的當日 commits。
- 在 claude.ai 提問一次，DB 中該筆事件的 `response_text` 不為 `NULL`。
- 資料庫中不再存在 seed-demo 假資料（提供 `python main.py clear-demo`）。

---

## P1 — 接上主力 AI 日誌與專案狀態層（核心價值）

> 目標：把「事件流」升級為「**進行中工作的狀態**」，這才是真正解決「不記得自己在做什麼」的階段。
> 預估：2 ~ 3 天　｜　完成後 `progress: 75`

### 任務

1. **新增 `claude_code_watcher`（補 D4 空殼）**
   - 讀 `~/.claude/history.jsonl`：每筆含 `display`（提問全文）、`timestamp`（epoch ms）、`project`（絕對路徑）。最易解析，一次可回填數月歷史。
   - 讀 `~/.claude/projects/**/*.jsonl`：取 `type == "user"` 與 `type == "assistant"` 的 `message.content`，附帶 `cwd`、`gitBranch`、`sessionId`，可還原完整對話脈絡。
   - 注意：`projects/*.jsonl` 內含編碼異常的舊資料，解析需 `errors="ignore"` 並跳過非 UTF-8 內容。
   - 去重鍵改為 `sessionId + uuid`（現行以 `prompt_text` 全文比對，效能與正確性都不足）。

2. **新增 `codex_watcher`**
   - 讀 `~/.codex/history.jsonl` 與 `~/.codex/sessions/YYYY/MM/DD/rollout-*.jsonl`（本機現有 276 個 session）。
   - 以檔名日期分區掃描，只處理上次掃描後的新檔。

3. **強化 `antigravity` 解析**
   - 現行只讀「今天修改過」的 transcript（`agent_log_watcher.py:72`），本機實際有 291 個 transcript，歷史全丟。
   - 改為以 `last_scan_time` 為斷點增量掃描，並解析 `conversation_id` 對應的專案路徑。

4. **新增專案歸戶與狀態層（新資料表）**
   - `project_states`：`project_key`（正規化後的 repo／cwd 路徑）、`display_name`、`category`、`last_activity_at`、`last_action_summary`、`status`（active / idle / stale）、`updated_at`。
   - `open_loops`：`project_key`、`title`、`source_type`、`source_event_id`、`created_at`、`resolved_at`、`confidence`。用於承載「待測試」「待求證」「卡住的問題」。
   - 歸戶規則：AI 事件用 `cwd` / `project`，Git 事件用 `repo_path`，檔案事件用所在 repo 或最近上層專案目錄，視窗事件用標題比對。無法歸戶者落入 `unassigned`。

5. **新增「進行中工作」API 與儀表板頁籤**
   - `GET /api/v1/projects/active`：回傳依 `last_activity_at` 排序的專案清單，含最近三筆動作、未結事項數、閒置天數。
   - Web UI 新增「🎯 進行中工作」頁籤，取代目前以 event 為中心的 Live Feed 作為預設首頁。

6. **LLM 摘要改為跨日接續**
   - `prompt_templates.py` 增補：輸入除當日事件外，額外帶入「前一日的未結事項」與「各專案最後狀態」。
   - 產出結構改以**專案**為主軸（而非以資料來源為主軸），每個專案輸出：今日進展／卡點／下一步。

### 驗收標準

- 單日 `ai_prompt_events` 從 10 筆提升至數百筆真實紀錄，且 `platform` 涵蓋 `claude_code`、`codex`、`antigravity`。
- 可一次性回填至少 3 個月的歷史 AI 對話。
- `GET /api/v1/projects/active` 能正確列出當前活躍專案，並標示「已 N 天未動」。
- 日報內容以專案為單位，且能引用前一日未完成事項。

---

## P2 — 免設定啟動與主動提醒

> 目標：從「我要去看它」變成「它會來提醒我」。
> 預估：1 ~ 2 天　｜　完成後 `progress: 90`

### 任務

1. **開機自動啟動**
   - `scripts/install_autostart.ps1`：以 `schtasks` 註冊登入時自動執行的工作排程，背景執行（`pythonw.exe`，無終端視窗）。
   - `scripts/uninstall_autostart.ps1` 與健康檢查（`/api/v1/health` 逾時自動重啟）。
   - 加上單一實例鎖（port 佔用偵測），避免重複啟動造成寫入衝突。

2. **Telegram 主動推播**
   - 沿用既有的 Telegram bot 通道，新增 `notifiers/telegram_notifier.py`。
   - 三種推播情境：
     - **早報（08:30）**：昨天做到哪／今天建議優先處理的 3 件事。
     - **晚報（22:00）**：今日進展摘要／尚未收尾的事項。
     - **停滯提醒**：專案閒置超過 N 天（預設 5 天）且 `status != completed` 時提醒一次。
   - 推播內容控制在手機可讀範圍（每則 < 800 字），詳細內容附本機儀表板連結。

3. **隨時查詢介面**
   - CLI：`python main.py now` — 一秒回答「我現在在做什麼、剛剛做了什麼、有什麼沒收尾」。
   - Telegram 指令：`/now`、`/project <名稱>`、`/todo`。

4. **與 `Project_CodingSimulation` 研究儀表板整合**
   - 依全域規則，每次專案有重大進展時自動更新該專案的 `STATUS.yaml`（`last_updated`、`progress`）。
   - 可選：將每日摘要輸出至 Obsidian（`exporters.obsidian` 已預留設定，尚未驗證）。

### 驗收標準

- 重開機後無需任何手動操作，儀表板與監控自動就緒。
- 每日早晚各收到一則 Telegram 推播，內容為真實活動。
- 在手機上發送 `/now` 可在 5 秒內取得當前工作狀態。

---

## 附錄 A：本機可用資料來源清單

以下皆為 2026-08-22 實地確認存在、但目前系統**尚未使用**的一手資料：

| 來源 | 路徑 | 規模 | 價值 |
| :--- | :--- | :--- | :--- |
| Claude Code 提問歷史 | `~/.claude/history.jsonl` | 全歷史，單檔 | 含 timestamp 與專案絕對路徑，最易解析 |
| Claude Code 完整對話 | `~/.claude/projects/**/*.jsonl` | 25 個 session | 含 `cwd`、`gitBranch`、完整問答 |
| Codex CLI | `~/.codex/history.jsonl`、`~/.codex/sessions/` | 276 個 session | 依日期分目錄，增量掃描容易 |
| Antigravity | `~/.gemini/antigravity/brain/**/transcript.jsonl` | 291 個 transcript | 現行只讀當日，歷史未取用 |
| Gemini CLI | `~/.gemini/history/` | 依專案分目錄 | 待評估格式 |

## 附錄 B：跨階段技術注意事項

- **資料庫效能**：事件量提升後（P1 後預估單日數千筆），需啟用 SQLite WAL 模式並為 `timestamp` 加上複合索引。
- **隱私**：`omni_context.db` 內含完整 AI 對話與檔案路徑，`.gitignore` 已排除，**任何情況下不得提交至 GitHub**；未來若加雲端同步需先加密。
- **時間戳一致性**：P0 完成後，新增任何 watcher 一律使用統一的 `core.utils.now()`，禁止直接呼叫 `datetime.utcnow()`。
- **回填安全性**：所有歷史回填腳本必須具備冪等性（可重複執行不產生重複資料）。
