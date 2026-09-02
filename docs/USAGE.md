# OmniContext 使用說明

> 適用版本：`1.3.0a5` Personal Alpha / P2.6 coverage ledger + P3 context memory + P7 DeskRAG + P8 hardening
>
> 主要驗證平台：Windows 11、Python 3.12、Chrome/Edge MV3

本文件提供可直接執行的安裝、啟動、Browser Extension 配對、每日使用、備份與故障排查流程。架構決策另見 [ADR-001](ADR-001-p2-5-trust-boundary.md)、[ADR-002](ADR-002-extension-monitor-and-usage-milestones.md)、[ADR-003](ADR-003-versioned-sqlite-migrations.md)、[ADR-004](ADR-004-packaged-runtime-layout.md)、[ADR-006](ADR-006-derived-context-sessions-and-related-history.md)、[ADR-007](ADR-007-proposal-only-secretary.md)、[ADR-011](ADR-011-safe-local-repository-sync.md) 與 [Release Checklist](RELEASE_CHECKLIST.md)。

## 1. 安裝與初始化

### 1.1 建立環境

需求：Python 3.10 以上。Windows 建議使用 PowerShell。

```powershell
git clone https://github.com/dofliu/activityTracker.git
Set-Location activityTracker
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
python -m pip install -e ".[dev]"
```

若不需要開發測試套件，可改用：

```powershell
python -m pip install -r requirements.txt
```

本機建置出的 Alpha wheel 可安裝為：

```powershell
python -m pip install .\dist\omnicontext-1.3.0a3-py3-none-any.whl
omnicontext assets-status
```

目前沒有公開 registry release。Wheel 模式預設使用 `~/OmniContext` 作為 writable application home；source checkout 為保持相容，仍使用 checkout root。可用 `OMNICONTEXT_HOME` 覆寫完整目錄，或以 `OMNICONTEXT_CONFIG` 指定設定檔；相對 database/report 路徑不會寫入 `site-packages`。

### 1.2 建立本機設定

```powershell
python main.py init --watch "D:\Projects"
```

Wheel 安裝可改用 `omnicontext init --watch "D:\Projects"`。

- `--watch` 可重複使用，加入多個監控根目錄。
- 指令會建立本機 `config.yaml`、必要資料目錄與 Browser Extension ingest token。
- `config.yaml` 與 `omni_context.db` 包含本機路徑或私人資料，不應提交至 Git。

如需讓 Project State 與 Context Handoff 在新電腦上可靠回推專案資料夾，請在 `config.yaml` 明確填入自己的 roots；未設定時才會沿用 file/Git watcher 的設定：

```yaml
project_resolution:
  search_roots:
    - "~/Projects"
    - "~/Documents/Research"
  self_project_path: "" # 可選；留空時由安裝位置推導
```

### 1.3 本機 Git 同步中心

Dashboard「03 · Git 同步中心」分頁（2026-09-02 起獨立成分頁，切到分頁時才掃描 Git 狀態）與「設定」內的 GitHub 雲端整合是兩套不同功能。同步中心先以最後一筆本機 commit 時間選出最近的 10 個 repo（可由設定調整），再讀取這些 repo 的完整 worktree 與同步狀態；畫面會另外顯示目前項目的 worktree 修改時間：

- **GitHub 雲端整合**只讀取 GitHub repo／PR metadata，不會對本機檔案或 branch 執行 Git 指令。
- **本機 Git 同步中心**只管理 `watchers.git_watcher.repositories` 明示設定 root 下發現的 repositories；頁面初次載入只讀取本機 cached remote-tracking refs，不會自動連網。

建議工作順序：先按各 repo 的 **Fetch** 更新遠端參照，再依卡片條件選擇 **Pull (FF only)**、**Commit staged** 或 **Push**。`ahead / behind` 在 Fetch 前只代表本機最後保存的遠端資訊，不保證是即時遠端狀態。

安全規則：

- Pull 只允許 clean worktree 且可以 fast-forward；分歧、conflict、rebase／merge 中一律不提供 Pull。
- Push 不提供 force push，且只在 clean worktree、沒有落後或分歧時啟用。
- Commit 必須自行先在 Git/IDE stage 指定檔案並輸入 message；系統不會 `git add`，也不會提交 untracked 或未 staged 的檔案。
- 不會排程自動 `fetch`、`pull`、`commit` 或 `push`。

#### 全覽與批次（2026-09-02，ADR-011 Addendum B）

同步中心分頁的「全覽與批次」區塊讓你一次看完**全部** repo，而不只是近期 10 個：

- **📋 載入全覽**：表格列出每個 repo 的 branch → upstream、狀態（↑↓ 數字）、worktree、**上次 fetch 時間**（從未 fetch 會標「從未」）與逐一動作按鈕；篩選 chip 可切到「需 pull／需 push／分歧／worktree 未提交／無 upstream」。狀態一律是本機 cached 的遠端參照，所以「上次 fetch」欄就是這個判斷的時效。
- **🔄 全部 Fetch**：對全部 repo 執行 `fetch --prune`，只更新遠端參照，不動任何 worktree；完成後表格會反映真正的落後／領先。
- **⬇ 批次 Pull (FF only)**：先列出**目前符合條件**（clean、只落後、無分歧）的 repo 清單與被排除的原因，你確認這份清單後才逐一執行；執行當下不符的會跳過，永不 force。
- **⬆ 批次 Push**：同樣的清單確認模式，但因為會發佈 commit，**預設關閉**（`repository_sync.batch.allow_push: true` 才啟用）；單一 repo 的 Push 不受影響。

```powershell
Invoke-RestMethod "http://127.0.0.1:8765/api/v1/repos/sync-status?scope=all"
Invoke-RestMethod -Method Post -Uri "http://127.0.0.1:8765/api/v1/repos/sync-fetch-all" -ContentType "application/json" -Body '{"confirmation":"confirmed"}'
Invoke-RestMethod "http://127.0.0.1:8765/api/v1/repos/sync-batch-plan?action=pull_ff_only"
```

**讓小秘書每天確認**：在「設定 → 小秘書執行器 → 排程任務」新增 `repo_sync_report`（L0 唯讀），它每天掃描全部 repo 的 cached 狀態、寫報告到 `reports/repo_sync/RepoSync_YYYYMMDD.md`，並留下快照讓小秘書在提案與晨報中列出「N 個 repo 需要 pull／push」。需要 pull 的 repo 會附「批准執行」（L1 `repo_pull_ff`，可在儀表板或 Telegram inline 批准；執行時仍重檢 clean 與可 fast-forward）；需要 push 的只提供 fetch，push 請回同步中心確認。報告不會 fetch，所以它反映的是上次 fetch 之後的認知——想要即時，先按「全部 Fetch」或批准 fetch 提案。

同步中心分頁下方是 **Repo Onboarding／對帳（P4.3）**：按「🔍 掃描對帳」列出三種尚未納管的情況，每種都提供一次一個 repository 的確認式動作：

| 情況 | 對帳呈現 | 可執行的確認式動作 |
| --- | --- | --- |
| 一般本機資料夾（root 第一層、未 `git init`） | 「① 尚未 git init 的資料夾」 | `git init`——只建立空 `.git`，不 commit、不設 remote、不發布 |
| 本機已 `git init`、尚無 remote | 「② 沒有 remote 的本機 repo」 | 從**已同步的 GitHub 清單**選一個連結為 origin（不 fetch、不 push），或建立新的 GitHub repo（**預設 private**、遠端保持空 repo） |
| GitHub 有 repo、電腦尚未 clone | 「③ 尚未 clone 的 GitHub repo」 | 選定要放進哪個設定 root 後執行 clone；目的地已存在（含空目錄）一律拒絕 |

對帳的邊界（如實）：**已 clone 與否只以 remote URL 正規化比對**（https／ssh／`.git` 變體視為相同）；本機有同名目錄只會顯示「⚠ 同名（不自動配對）」提示，絕不自動關聯。所有動作單一目標、需在對話框明確確認；系統**永不代為 push**——`attach`／`create` 之後的首次發布由你自行 `git push -u origin <branch>` 完成。clone 一律用 https URL 且不夾帶 token，私有 repo 依賴你本機 Git credential manager 的既有認證，失敗會如實回報。

可依設備調整清單與單一指令逾時：

```yaml
repository_sync:
  command_timeout_seconds: 30
  status_timeout_seconds: 5
  status_parallelism: 8
  max_repositories: 80
  dashboard_recent_limit: 10
```

## 2. 啟動與確認服務

### 2.1 LLM API key

Cloud LLM key 應保存在作業系統環境變數，不放入 `config.yaml`。設定檔只記錄 `api_key_env` 名稱；Dashboard「設定 → 摘要與 LLM」只回報 `DETECTED / MISSING` 與來源，不會取得或顯示 secret value。

```powershell
[Environment]::SetEnvironmentVariable("GEMINI_API_KEY", "your-gemini-api-key", "User")
```

Windows 上長時間執行的 launcher 可能持有較舊的 Process environment。OmniContext 會先讀 Process environment，找不到時再回讀 Windows User／Machine environment；設定後可按「重新檢查」，不需要把 key 貼到瀏覽器表單。

```powershell
python main.py run
```

`python main.py` 與 `python main.py web` 目前等同 `run`。
長時間正式執行請直接使用整合啟動；`web --no-autostart` 再由 API 分段啟動只適合診斷，不作日常啟動方式。

啟動後可使用下列入口：

- 主儀表板：<http://127.0.0.1:8765/>
- Extension Monitor：<http://127.0.0.1:8765/extension-monitor>
- Health API：<http://127.0.0.1:8765/api/v1/health>

另開 PowerShell 確認採集器：

```powershell
python main.py status
```

應確認 `file_watcher`、`window_watcher`、`agent_log_watcher` 與 scheduler 的 runtime 狀態；`idle` 表示近期沒有新事件，不等於採集器故障。若使用者確實切換視窗或產生 Agent log，還要確認 `last_events` 與 event count 是否向前推進；thread 顯示 `running` 不能單獨證明採集成功。

`GET /api/v1/control/status` 的 `monitoring_state` 會彙整為 `healthy / degraded / stopped`，`degraded_collectors` 列出異常來源。`collector_diagnostics` 只回傳 probe 時間、連續失敗次數、來源名稱與 `permission_denied`、`probe_error` 等穩定診斷碼，不回傳 window title、本機 path、原始 exception message 或 secret。Windows 鎖定畫面可能短暫讀不到 foreground window；預設持續 30 秒才標成 degraded，下一次成功 probe 會恢復 healthy。

## 3. Browser Extension 安裝與配對

Extension popup 與 localhost Monitor 是兩個不同入口：

- Extension popup 負責保存 ingest token、送出支援網站事件與離線佇列。
- localhost Monitor 只顯示 service、pairing 與 observed event 狀態，不能存取 `chrome.storage`，因此不能取代 popup 配對。

安裝步驟：

1. 開啟 `chrome://extensions/` 或 `edge://extensions/`。
2. 啟用 Developer mode。
3. 選擇 Load unpacked。
4. 取得目前安裝版本的 Extension 目錄並載入：

```powershell
python main.py extension-path
# Wheel 安裝：omnicontext extension-path
```

5. 在本機 PowerShell 取得 token：

```powershell
python main.py init --show-token
```

6. 將 token 貼入 Extension popup 並儲存。
7. 在 `chrome://extensions/`／`edge://extensions/` 按一次 Reload，讓 Extension `1.3.2` background、timestamped Content Ready receipt、shared capture core 與 content scripts 生效。
8. popup 顯示 pairing 成功與近期 Heartbeat 後，開啟支援網站並完成一輪對話，再到 Extension Monitor 查看 `OBSERVED` 狀態。

目前支援 ChatGPT、Claude.ai 與 Gemini。Monitor 顯示 ONLINE 只證明 localhost service 正常；RECENT HEARTBEAT 代表 Extension 曾以正確 token 抵達 server；CONTENT READY 代表支援網站載入過 content script；只有 OBSERVED 才代表資料庫已有真實 Browser event。任何單一狀態都不代表完整 coverage。請勿把 token 放入截圖、issue、commit 或公開日誌。

如需旋轉 token：

```powershell
python main.py init --rotate-token --show-token
```

旋轉後必須重新貼入 Extension popup。

### 3.1 執行本輪 Extension Live Verification

在 <http://127.0.0.1:8765/extension-monitor> 的 `LIVE VERIFICATION` 勾選要驗證的平台並按「開始 10 分鐘驗證」。開始後依序：

1. Reload OmniContext Extension 並開啟 popup，產生新的 token-authenticated heartbeat。
2. 重新載入每個勾選的平台分頁，產生開始後的 Content Ready timestamp。
3. 每站使用一個新 Prompt，等待完整 assistant response。
4. Monitor 每 5 秒檢查；全部通過後下載 JSON receipt。

PASS 同時要求本輪的新 heartbeat、Content Ready、Browser event 與非空 response。歷史 `OBSERVED`、單獨 heartbeat、只有 Prompt 或桌面前景時間都不能通過。Receipt 不包含 token、URL、Prompt、Response 或本機 path；verification baseline 只存在目前 service process，重啟後失效。

也可用 API 建立與查詢：

```powershell
$run = Invoke-RestMethod -Method Post `
  -Uri http://127.0.0.1:8765/api/v1/extension/verification `
  -ContentType application/json `
  -Body '{"platforms":["claude"],"timeout_seconds":600}'

Invoke-RestMethod "http://127.0.0.1:8765/api/v1/extension/verification/$($run.verification_id)"
```

或使用一鍵驗收腳本：它會建立 baseline、把 harness 的 `next_actions` 翻成逐步指示並輪詢到 PASS／逾時，最後輸出非敏感 JSON receipt（不含 token、URL、Prompt、Response）：

```powershell
python scripts/extension_live_acceptance.py --platforms chatgpt,claude --timeout 600
```

PASS（exit code 0）代表本輪已取得新 heartbeat 與各平台的 Content Ready、event、非空 response，可將 receipt 記入 `STATUS.yaml`。

### 3.2 Claude Desktop 對話採集範圍

Claude Desktop 有兩種不同資料面：

- **Cowork／local-agent session**：OmniContext 可讀取 application data 內的 `.claude/projects/**/*.jsonl`，保存 user/assistant turn、來源位置與 `response_status`。
- **一般 Claude 雲端聊天**：目前只有 Chromium IndexedDB/LevelDB cache 可被偵測；OmniContext 不直接讀取執行中的二進位 cache，因此顯示 `快取存在／未解析`，不宣稱已取得對話。

Windows 上 Claude session 路徑常超過 `MAX_PATH`，`1.3.0a4` 起已使用 extended path。首次自動掃描預設只回補最近 7 天，可在 `config.yaml` 調整：

```yaml
watchers:
  agent_log_watcher:
    claude_desktop: true
    claude_desktop_initial_lookback_days: 7
```

主頁 `DATA CAPTURE` 是日常快速檢視：`FOCUS` 只代表前景時間，`WEB` 只代表 Extension 事件，`LOG` 才代表結構化本機對話來源，三者必須分開解讀。需要檢查 token、heartbeat 或逐站 enabled／observed 狀態時，再開啟 `/extension-monitor` 進階診斷頁。

## 4. 每日介面使用時間與里程碑

主頁的「今日前景使用與里程碑」依 `WindowEvent` 計算已觀察到的 foreground active time，並以 canonical AI turns 顯示互動次數。

這些數值不代表生產力、專注度或實際工時。coverage 由 **continuous coverage ledger** 判定：排程器每 5 分鐘（可調）記錄一次「視窗採集器實際被觀測運作」的 heartbeat，interval 的結束時間永遠取最後一次 heartbeat，中斷、休眠或當機的時間不會回補。

- `observed`：當日 ledger 覆蓋率達 `usage_tracking.coverage.full_coverage_ratio`（預設 0.95）。這只證明採集器在觀測，不證明使用者在場。
- `partial`：有資料但 ledger 覆蓋率不足（會顯示實際比例，如 `ledger_coverage_62_percent`），或尚無 ledger 資料。
- `unavailable`：目前平台不支援或 collector 不可用。

可用本機 API 查看任一天的 ledger 明細：

```powershell
Invoke-RestMethod "http://127.0.0.1:8765/api/v1/usage/coverage?date=2026-08-30"
```

ledger 參數在 `config.yaml` 的 `usage_tracking.coverage`（`heartbeat_interval_seconds`、`max_gap_seconds`、`full_coverage_ratio`）。

可直接在 Dashboard Settings 修改，或調整 `config.yaml`：

```yaml
usage_tracking:
  enabled: true
  goal_label: "AI 協作"
  goal_interfaces:
    - Claude Code
    - Claude
    - Codex
    - ChatGPT
  daily_goal_minutes: 360
  milestones_minutes: [120, 240, 360]
  max_interval_seconds: 3600
  notifications:
    enabled: false
    tone: encouraging
    quiet_hours_start: "22:00"
    quiet_hours_end: "08:00"
    cooldown_minutes: 60
    check_interval_minutes: 15
```

Release template 的里程碑通知預設關閉。啟用後，同一天相同 milestone 與 channel 只會通知一次，狀態保存在 SQLite receipt 中。

### 可驗證背景 Agent／CLI 任務時間

主頁的 `BACKGROUND AGENT TASKS` 是另一條獨立訊號，不會加入前景使用時間、里程碑或 AI turns。它目前只讀取 Claude Code、Claude Desktop local-agent 與 Codex 的本機 session transcript：來源內需同時有 user prompt 的 start timestamp，以及 Claude `end_turn` 或 Codex `final_answer` 的 completion timestamp，才會產生可加總的背景執行時間。

- 視窗縮小後，任務仍可在完成 receipt 出現時列入此卡片。
- 只有 start、尚未看到 final 的任務顯示為等待 completion evidence，不會預估時間。
- Generic Terminal／PowerShell、browser 對話、cloud-only session 與任何缺 local receipt 的工作不納入。
- 背景任務總時間採重疊 interval union；若兩個 Agent 平行執行，總數不會 double count。此數字仍不是生產力、工時或全天 Agent coverage。
- 來源 timestamp 出現倒退或超過 `max_task_duration_seconds`（預設 8 小時）時，該筆標為不可信並排除。

可在 `config.yaml` 調整或關閉：

```yaml
background_task_tracking:
  enabled: true
  platforms: [claude_code, claude_desktop, codex]
  max_task_duration_seconds: 28800
```

可用本機 API 檢視今天的 receipt 摘要；回應不含 Prompt、Response 或本機 source path：

```powershell
Invoke-RestMethod http://127.0.0.1:8765/api/v1/background-tasks/today
```

要驗收「每個平台都取得了成對 live receipt」（P2.7 known blocker），在實際跑過任務的機器上執行：

```powershell
python scripts/background_task_live_acceptance.py --platforms claude_code,claude_desktop,codex
```

每個要求的平台當日至少有 1 筆 completed receipt 才會 PASS（exit code 0），並輸出可貼回 `STATUS.yaml` 的建議段落。注意三件事：

- 以「當日」為界；剛過午夜時可用 `--date YYYY-MM-DD` 檢查前一天。
- `claude_code` 只會讀**本機** Claude Code CLI 的 transcript（`~/.claude/projects`）；雲端／網頁版 Claude Code session 不會產生本機 receipt。`claude_desktop` 需要 Cowork／local-agent 任務。
- 任務完成後 agent watcher 約每 60 秒增量掃描一次，完成後稍候再重跑。

預覽今日判定而不發送通知、也不寫入 receipt：

```powershell
Invoke-RestMethod `
  -Method Post `
  -Uri "http://127.0.0.1:8765/api/v1/usage/milestones/evaluate" `
  -ContentType "application/json" `
  -Body '{"dry_run":true}'
```

## 5. 常用操作

### 外觀：明暗 × 配色主題

儀表板頂列有兩個獨立的外觀控制：

- **☀ 淺色／☾ 深色**：切換明暗。
- **配色下拉**：🟠 火影橘（預設）、🟢 森林綠、🔵 海洋藍。配色會同時換掉強調色與表面色調（例如海洋藍的深色模式是偏藍的深底，不只是換橘色）。

兩者可自由組合成 6 種外觀，選擇只存在瀏覽器 `localStorage`（key：`omni-theme`、`omni-palette`），**不寫入 `config.yaml`、不送往後端**，因此換電腦或換瀏覽器要各自設定一次。Extension Monitor 頁面會跟隨同一個配色偏好。三種配色的按鈕文字對比皆為 5.3:1 以上（WCAG AA 門檻 4.5:1），警告／危險／成功等語意色跨配色維持固定含義不變。

### 小秘書首頁（01 分頁）

儀表板第一個分頁是「🤖 小秘書與知識庫」：最上方的對話框可直接向小秘書提問或交辦（走本機知識庫 RAG 與所選模型）；完整引用卡片、對話歷史與知識庫索引管理就在**同一分頁下方的「知識庫與 RAG」折疊區**（共用同一條對話）；其下依序是今日關鍵數字列（AI 協作前景時間、coverage、背景任務、待判斷建議數）與秘書建議卡（啟用 executor 後含「⚡ 批准執行」）。標題列徽章顯示 `RULES`（純規則建議）或 `LLM · 供應商`（已啟用 LLM 註解）。

### 查看目前工作

```powershell
python main.py now
python main.py resume activityTracker --copy
```

### 建立本機 Semantic Index 並詢問歷史

先確認 Ollama 已啟動且已有 `bge-m3:latest`（或在 `semantic_index.embedding_model` 指定的 model）：

```powershell
# 首次全量／日後增量；未變來源會依 content hash 跳過
omni index --json

# 只看 retrieval evidence，不執行答案生成
omni ask "上次如何處理 SQLite rollback?" --project activityTracker --no-synthesis

# 使用本機 Ollama 生成附 [S1] 引用的回答
omni ask "activityTracker 最近有哪些可靠性改善？" --project activityTracker
```

`semantic_index.allow_remote` 預設為 `false`，embedding 與 ask generation 只接受 loopback URL。Similarity 只做 evidence ranking；CLI 的 `source_ref`、trust status 與 `embedding_input_mode` 才是回查線索，不代表來源內容已被外部驗證。

### 整理工作階段與查詢相似歷史

```powershell
# 依 project + inactivity gap 整理最近 72 小時 evidence
omni sessions --project activityTracker --hours 72

# 從本機 semantic index 查相似歷史；query 不會寫入 SQLite
omni recall "formal rollback rehearsal" --project activityTracker
```

Dashboard「進行中工作」會同步顯示 `RECENT WORK SESSIONS` 與 `RELATED HISTORY`。Session 只納入能可靠歸戶的 AI、Git 與 file metadata；Window focus 尚未具備 canonical project identity，因此不混入 session。預設 gap 45 分鐘、related threshold 0.50，可在 `context_memory` 設定調整。0.50 是目前 `bge-m3` + 本機語料的 Alpha 起點，不是通用或真實性門檻。

若本機 Ollama 或 semantic index 不可用，Related History 會明確顯示 unavailable，不改送 cloud。Session 的 span 只是首末事件時間差，不代表實際工時、專注度或任務連續性。

### 管理 DeskRAG 知識庫與索引

在 Dashboard「小秘書與知識庫」分頁的知識庫區塊中，掃描按鈕會建立一個獨立本機 worker；主頁、採集器與 Health API 不會在同一個 process 內等待文件解析或 embedding。

1. **新增目錄與掃描**：新增資料夾或按「掃描索引」前，設定本次檔案上限與每檔間隔。預設為 500 檔、25 ms、單檔最多 50 MB；大型資料夾請分批執行。
2. **執行控制**：執行中可按「暫停／恢復／取消」。取消會在目前單一檔案或向量批次完成後生效，不會強制中斷寫入。
3. **安全移除**：「移除索引」只移除選定資料夾的 RAG metadata、Chroma vectors 與 BM25 chunks；原始檔案與 RAG 對話不會被刪除。作業完成後會執行 SQLite checkpoint、`VACUUM` 與一致性檢查。
4. **空間清空**：「清空所有 RAG 索引」需確認並輸入 `CLEAR`。它只清空 RAG 資料夾／檔案／向量／BM25，不會刪除來源檔與對話。
5. **一致性驗證**：容量卡片的向量、BM25 與 Chroma 空間來自獨立 worker 的最近驗證收據；初次升級或尚未驗證時顯示「待驗證」。按「驗證索引與空間」可取得實測計數。若 BM25 顯示不一致，可按「從 Chroma 重建 BM25」，此作業不會重新掃描來源資料夾。

API 也可用於本機自動化：

```powershell
# 啟動受限的索引工作
Invoke-RestMethod -Method Post -Uri "http://127.0.0.1:8765/api/v1/rag/scan" `
  -ContentType "application/json" -Body '{"max_files":500,"throttle_ms":25}'

# 讀取最新 job 與輕量儲存摘要
Invoke-RestMethod "http://127.0.0.1:8765/api/v1/rag/jobs/current"
Invoke-RestMethod "http://127.0.0.1:8765/api/v1/rag/storage"
```

不要以「索引完成」推論所有來源均已處理；若工作顯示 `completed_limited`，表示仍有檔案留待下一批。資料夾層的檔案數是掃描時看到的候選數，已索引檔案數與切片數則是已實際寫入的 metadata。

### 使用 DeskRAG 智慧文件問答與對話

DeskRAG 支援結合本機知識庫（PDF、Word、PPTX、Excel、代碼/Markdown）與專案日常活動的即時串流問答：

1. **模型提供者與下拉選單**：
   - **Ollama (本機離線)**：提供 4 款精選本機模型下拉切換，包含 `llama3.1:8b`（預設推薦）、`mistral:7b`、`gemma4:e4b`、`qwen3:4b`，完全離線運作。
   - **雲端 LLM**：可切換為 `Google Gemini`（`gemini-3.7-flash`）、`Anthropic Claude`（`claude-3-5-sonnet`）或 `OpenAI`（`gpt-4o`）。
2. **檢索融合策略**：
   - `Hybrid RRF (向量 + BM25 倒數排名融合)`（預設）：兼具語意概念理解與專有名詞精準匹配。
   - `Weighted Fusion (線性加權)`、`Vector Only (純向量)`、`BM25 Only (純關鍵字)`。
3. **對話工作階段（Chat Sessions）管理**：
   - **自動提問命名**：發送提問後，系統會自動擷取問題首句作為對話標題（如 `💬 OPC UA 時間序列 預測`），不再產生「新對話」泛稱。
   - **歷史切換**：點選下拉選單可隨時切換歷史對話，即時還原問答脈絡與引文卡片。
   - **開新與刪除**：點選 `➕ 建立新對話` 開啟新提問；點選垃圾桶 `🗑` 可刪除當前對話。
4. **來源引文與 Windows 總管開啟**：
   - 每筆回答皆會標註參考來源切片（包含 PDF 頁碼、PPT 投影片、Excel 工作表及關聯度評分）。
   - 點擊「`📂 在總管開啟`」可在 Windows 檔案總管中直接開啟並反白選中該實體檔案。

選擇 provider 時：**Ollama 是全本機**；選 Gemini／Anthropic／OpenAI 才會把該次問題與檢索到的文件切片送往該供應商。金鑰只由本機後端讀取（環境變數名稱依 `synthesizer.<provider>.api_key_env` 設定，Gemini 也接受 `GOOGLE_API_KEY`），**不會出現在請求 URL、log 或瀏覽器**。若金鑰沒設定，對話會直接回一則明確提示，而不是無回應。

串流保證：知識庫檢索有 60 秒硬性逾時（逾時就不帶文件脈絡、照常回答並註明），且無論檢索或供應商發生什麼錯誤，後端一定送出結束事件；瀏覽器端另有 120 秒閒置逾時做安全網——介面不會停在「回覆中」不動。

### 檢索 worker：索引不進主服務程序

檢索（Chroma 向量查詢、BM25、query embedding）預設在**常駐子程序**執行（`python -m rag.retrieval_worker`，由主服務以 stdin/stdout JSON lines 驅動），主服務只持有一條 pipe，不載入任何索引：

- **預熱**：服務啟動後若已有索引，會在背景把 BM25／Chroma／embedding 模型載進 worker，第一次提問不必等數十秒載入；沒有索引時不啟動任何子程序。知識庫區塊的「檢索 worker」卡片顯示狀態（尚未啟動／預熱中／就緒／失敗）、載入切片數、預熱耗時與 worker 記憶體；也可按「🔥 預熱檢索 worker」手動觸發，或按「💤 釋放記憶體」結束 worker（下次提問自動重啟）。
- **卡住可救**：檢索超過 60 秒時終止 worker 而不是讓主服務的 thread 永遠卡住；下一次提問自動重新啟動，重啟次數與最近錯誤都在狀態卡片與 `GET /api/v1/rag/retrieval/status` 可見。
- **設定**：`rag.retrieval.mode: worker | in_process`（預設 worker；`in_process` 為舊行為，在主服務內檢索）、`rag.retrieval.warmup_on_start: true | false`。

```powershell
Invoke-RestMethod "http://127.0.0.1:8765/api/v1/rag/retrieval/status"
Invoke-RestMethod -Method Post "http://127.0.0.1:8765/api/v1/rag/retrieval/warmup"
Invoke-RestMethod -Method Post "http://127.0.0.1:8765/api/v1/rag/retrieval/shutdown"
```

狀態卡片只描述 worker 程序狀態與載入計數，不代表檢索結果正確或索引完整；索引一致性仍以「驗證索引與空間」的 worker 收據為準。

### 查看 Proposal-only 主動秘書建議

主頁 `SECRETARY SUGGESTIONS` 會從 Project State、actionable Open Loops 與 Extension diagnostics 顯示可追溯建議。每張卡片都附 `project_states:<id>`、`open_loops:<id>` 或 `extension_status:live` 等 evidence refs。

也可唯讀查詢：

```powershell
Invoke-RestMethod http://127.0.0.1:8765/api/v1/secretary/proposals
```

P5-1 沒有批准或執行功能，不保存 proposal，也不修改檔案、Git 或外部系統。

**P5-R1 LLM 參考註解（選用，預設關閉）**：啟用後，秘書會請 LLM 對既有建議附加一句判斷提示與整體 summary——只能註解，不能新增、刪除或執行任何項目；LLM 不可用時自動回退純規則結果，卡片照常顯示。在 `config.yaml` 開啟：

```yaml
proactive_secretary:
  llm_advisor:
    enabled: true
    provider: ollama    # 全本機。選 gemini/anthropic/openai 代表同意外送建議的非敏感欄位
    timeout_seconds: 20
    cache_minutes: 10
```

模型沿用 `synthesizer.<provider>.model` 設定（Ollama 預設 `llama3.1:8b`）。送入 LLM 的內容僅限建議卡片本身的白名單欄位（標題、理由、建議行動、優先序等），不含 prompt 全文、evidence 路徑、URL 或 token。

**P5-R2 Gated Executor（選用，預設關閉；[ADR-008](ADR-008-gated-agent-executor.md)）**：啟用後，部分建議卡會出現「⚡ 批准執行」按鈕，讓秘書在您逐項批准下代辦白名單動作。首批動作全部是內部函式呼叫（不開 shell）：

| 動作 | 等級 | 觸發的建議類型 |
| :--- | :--- | :--- |
| 產生 Context Handoff（唯讀，自動複製到剪貼簿） | L0 | 停滯專案／多項未結事項等 |
| `git fetch` 更新本機 repo 的 remote-tracking | L1 | PR／issue 類建議（專案名可對應到唯一本機 repo 時） |
| 將單一未結事項標記為 `stale`（可用 open 復原） | L1 | 只含一筆 open loop 的停滯建議 |
| 調度本機 agent CLI 起草重啟行動計畫（P5-R3） | L2 | 停滯／未收尾建議（另需啟用 L2，見下） |

啟用與使用：

```yaml
proactive_secretary:
  executor:
    enabled: true
```

1. 執行 `python main.py init` 產生 execution token（`--show-token` 顯示；與 Extension token 分開）。
2. 重啟服務後，按下建議卡的「⚡ 批准執行」，首次會要求貼上 execution token（只存於瀏覽器 sessionStorage，關分頁即清除）。
3. 每次執行寫入 audit receipt，可由 `GET /api/v1/secretary/executions` 回查（只含模板、狀態、時間與輸出 digest，不含內容全文）。

安全邊界：execute API **只接受 proposal_id**（可選 `template_id` 在已註冊動作中挑選、`confirm_code` 供 L2）——任何呼叫端夾帶的 command／path 都沒有效果；提案的 evidence 一旦改變（例如 loop 已被處理），同一 proposal_id 會直接失效（404），不會執行過期提案；沒有設定 token 時一律 401。

**P5-R3 L2 Dispatcher（選用，獨立開關，預設關閉）**：啟用後，停滯／未收尾的建議卡會多一顆「🛡️ 批准執行（L2）」，讓秘書調度**你本機已登入的 agent CLI**（預設 `claude -p`，可改 `codex exec`）為該事項起草重啟行動計畫，結果自動複製到剪貼簿並存於 `agent_outputs/execution_<id>.md`。

不想手動編輯 YAML 的話，「設定」分頁最上方就是「小秘書執行器」卡片（常用設定預設展開）：兩個開關（執行器／L2）與 agent CLI 下拉選單，按「儲存並套用」即寫回 config.yaml 並熱套用；等效的手動設定如下：

```yaml
proactive_secretary:
  executor:
    enabled: true
    l2:
      enabled: true          # L2 獨立開關
      confirm_ttl_seconds: 300
      cooldown_seconds: 600  # 同一動作的最小間隔
    agent_cli:
      binary: claude           # 或 codex
      args: ['-p', '{prompt}'] # codex 範例：['exec', '{prompt}']
```

L2 每次執行都是**三道門**：execution token → 單鍵批准 → 回填 server 產生的一次性 6 碼確認碼（5 分鐘失效、錯一次即作廢）；同一動作有冷卻時間避免連點。子行程以 argv 白名單啟動（禁 shell）、工作目錄限定該專案的本機 repo、環境變數重建為位置類 allowlist——**你的任何 API key 都不會傳給子行程**（CLI 用它自己家目錄的登入憑證）。注意：這會消耗你 Claude Code／Codex 的訂閱或 API 額度；執行中的 job 可由 executions 面板取消（會真正終止行程）。

**L2 寫入模式（agent 實際代辦；第三開關 `l2.allow_write`，預設關閉）**：採**兩段式批准**——先用「起草計畫」產出一份你讀得到的計畫檔，24 小時內同一專案的建議卡才會出現「依已批准計畫實際修改檔案」按鈕；執行時把**那份計畫全文**餵給 CLI（Claude Code 以 `--permission-mode acceptEdits` 授權檔案編輯）。dispatch 前 repo worktree 必須乾淨（保護你未提交的工作），agent **永不 commit／push**——改動以未提交變更留在 worktree，回應會列出改了哪些檔案，`git diff` 檢視、滿意再自己 commit，`git checkout .` 可整批還原。receipt 只記檔案數與輸出摘要。

**P5-R5 自訂排程任務（選用，預設關閉）**：讓小秘書按時自動執行**唯讀白名單動作**。可排程的 template 只有四個，全部 L0（L1/L2 需要人在場批准，永遠不可排程）：

| Template | 內容 | 輸出 |
| :--- | :--- | :--- |
| `generate_handoff` | 產生指定專案的 Context Handoff | `reports/handoffs/Handoff_<專案>_<日期>.md` |
| `weekly_report_rollup` | 彙整**上一個完整週**的每日摘要成週報（缺日如實列出、不推測） | `reports/Weekly_Rollup_YYYY-Www.md` |
| `monthly_report_rollup` | 彙整**上一個完整月**的每日摘要成月報 | `reports/Monthly_Rollup_YYYY-MM.md` |
| `status_snapshot_draft` | 點名 `STATUS.yaml` 的 `last_updated` 落後觀測活動 ≥7 天的 repo（**草稿，絕不改 repo**） | `reports/status_drafts/Status_Draft_<日期>.md` |

啟用（「設定 → 小秘書執行器」卡片的第四個開關，或手動設定）：

```yaml
proactive_secretary:
  executor:
    enabled: true            # 排程任務疊加在 executor 總開關之上
    scheduled_tasks:
      enabled: true
      max_tasks: 20
```

同一張卡片下方即是排程管理：選 template、排程（每日／每週＋星期／每月＋日期 1–28）與時間後按「＋ 新增排程」；每列可停用、刪除或「立即執行」。新增／修改／刪除／立即執行都需要 execution token（與批准執行同一顆，只存 sessionStorage）；清單本身唯讀免 token。

排程語意與收據：任務建立後從**下一個排程時刻**開始生效（不會立刻補跑過去的時段）；服務停機錯過的排程，恢復後**只補跑一次**；rollup 週報／月報只彙整「已存在的每日摘要」（LLM 失敗自動回退 deterministic 拼接並如實標記）。每次執行都寫入與批准執行相同的 audit receipt（`approved_via=schedule`），可在 `GET /api/v1/secretary/executions` 回查。

### Telegram 推播：介面上完成設定與即時連線測試

「設定 → Telegram 通知」卡片提供完整設定流程，不必手動編輯 config.yaml 或設環境變數：

1. **建 bot**：在 Telegram 搜尋 `@BotFather` → `/newbot` → 複製 API Token 貼到卡片的 BOT TOKEN 欄。
2. **取得 chat id**：先在 Telegram 對你的 bot 送出任意訊息（例如 `/start`），再按「🔍 偵測 CHAT ID」——系統以 `getUpdates` 列出最近對話，點選即回填。
3. **即時測試**：「📡 測試連線」會現場呼叫 `getMe` 驗證 token，並向所選對話**實發一則固定內容的測試訊息**；結果（bot 名稱、訊息是否送達）立即顯示，失敗有明確原因（`invalid_token`／`chat_not_found`／`network_unreachable`）與下一步提示。
4. **儲存啟用**：「✅ 測試並儲存啟用」重跑同一組驗證，**全部通過才**寫入本機 `config.yaml` 並熱套用排程（晨報 09:00／晚報 23:30 可調）；驗證失敗時設定完全不動。

安全邊界：token 與 chat id 只存在本機（config.yaml 或環境變數），瀏覽器永遠拿不回明文——`GET /api/v1/config` 一律回 `***REDACTED***`，狀態 API 只回報「已設定／未設定」與來源；若已設定環境變數 `TELEGRAM_BOT_TOKEN`／`TELEGRAM_CHAT_ID` 則**優先使用且不會複製進檔案**；「解除」只清除 config 內的值，不動環境變數。測試訊息內容固定，不含任何工作資料。

### Telegram inline 批准與晚間交接（P5-R4b，預設關閉）

連線完成後，可讓晨報／晚間交接的建議附上「✅ 批准」按鈕，人在外面也能一鍵批准 L0/L1 白名單動作：

1. 在同一張 Telegram 卡片勾選「啟用 inline 批准」→ 按頁面的「儲存並套用」（這只是開通道，還不能批准）。
2. 按「🔓 解鎖遠端批准」並輸入 execution token——這就是 ADR-008 的「同一 execution token 邊界」：**解鎖狀態只存記憶體、預設 24 小時失效、服務重啟即自動上鎖**，需要再按一次才恢復。
3. 之後晨報（09:00）與晚間交接（23:30，皆可調）會推送建議清單；已解鎖時每則可執行建議附一顆「✅ 批准」按鈕，點按即執行並回報結果（寫入 `approved_via=telegram_inline` 的 audit receipt）。隨時可對 bot 送 `/proposals` 取回最新建議。

邊界（如實）：只處理**綁定 chat id** 的按鈕與訊息，其他對話一律靜默忽略；**只批 L0/L1**——L2 需要一次性確認碼，按到會立即作廢剛簽發的碼並提示回儀表板；回呼走 `getUpdates` 長輪詢（純 outbound HTTPS，不開任何本機 port）；晚間交接是唯讀盤點（今日推進專案＋未結事項），不歸檔、不改任何資料；bot 不是聊天介面，除 `/proposals`、`/start` 外的訊息不回應。

### 兩層增量摘要（日報 token 效率）

日報現在採 map-reduce：每次週期 checkpoint（預設每 2 小時）會順帶用本機模型（預設 Ollama）把該時段壓成 ≤100 字微摘要存入 SQLite（map，零 API 成本）；23:30 或手動產日報時，prompt 優先讀「微摘要時間軸＋原始統計」（reduce），token 用量約降一個數量級。微摘要失敗（如 Ollama 未啟動）或缺漏的時段自動回退原始節錄，日報永遠可產生。相關設定：

```yaml
synthesizer:
  micro_summary:
    enabled: true        # 關閉則日報一律使用原始節錄
    provider: ollama
  daily_from_micro: true
  max_prompt_chars: 180000   # 用 Ollama 產日報建議降至 60000
```

**秘書晨報（P5-R4）**：08:30 桌面晨間通知與 `OMNICONTEXT_TODAY.md/.html` 每日入口檔，現在會帶入小秘書的 top 建議（含 LLM 總評，若已啟用註解層）；建議僅供判斷、不會自動執行，秘書層失敗不影響晨報本體。

### 建立工作快照與摘要

```powershell
python main.py checkpoint --hours 2
python main.py summary --start 2026-08-20 --end 2026-08-24
python main.py brief
```

選擇 Gemini、Anthropic 或 OpenAI 產生摘要時，組裝後的工作脈絡會傳送至對應 provider；選擇 Ollama 才是完整本機推論。

### 預覽桌面通知

```powershell
python main.py notify briefing --channel desktop --dry-run
python main.py notify evening --channel desktop --dry-run
python main.py notify stagnation --channel desktop --dry-run
```

### 複核 Open Loop

```powershell
python main.py open-loop 12 resolved --note "已完成並驗證"
python main.py open-loop 12 open --note "需要重新處理"
python main.py open-loop-reconcile
```

## 6. Schema migration、備份與資料生命週期

唯讀查看目前 schema 狀態：

```powershell
python main.py migration-status
```

正常結果應為 `state: up_to_date`、`schema_version: 7/7`、`pending_versions: []`。這個指令不會建立或修改 database。

OmniContext 啟動時會執行 append-only migration registry。既有有資料 DB 只要存在 pending version，便先依 `data_lifecycle.auto_backup_before_migration` 建立 verified online backup；migration 成功後才寫入 `schema_migrations` receipt。若偵測 checksum mismatch、history gap 或未知較新版本，服務會拒絕繼續啟動。

建立 verified SQLite online backup：

```powershell
python main.py backup
```

在隔離暫存資料庫執行 restore drill：

```powershell
# 自動使用 backups_dir 中最新的 .db
python main.py restore-drill

# 或指定備份與 receipt 目錄
python main.py restore-drill `
  --backup "C:\Users\me\OmniContext\backups\omni_context-YYYYMMDD-HHMMSS.db" `
  --receipt-dir "C:\Users\me\OmniContext\backups\restore_drills"
```

成功輸出應包含：

- backup path
- `integrity: ok`
- table count
- file size
- SHA-256

備份預設位於 `~/OmniContext/backups`。`restore-drill` 會以 read-only 方式開啟來源備份、還原到 OS 暫存目錄，比對 integrity、table list、schema fingerprint 與 row counts，最後刪除暫存 DB，只保存不含 row content 的 JSON receipt。它不提供 live database destination，因此不會覆蓋正式資料。

Windows isolated wheel fresh/upgrade/assets smoke 與 formal package+DB rollback rehearsal 已通過。Rollback 必須同時回復相容 wheel 與 pre-migration online backup，且在服務停止後處理 `.db-wal/.db-shm`；只覆蓋 `.db` 可能讓新 WAL 重新套回。Windows／Ubuntu／macOS × Python 3.10／3.12 CI matrix 已於 run `32757498004` 通過；自動 retention pruning 仍屬 release gate。

## 7. 平台能力

| 功能 | Windows | macOS / Linux |
|---|---|---|
| FastAPI、SQLite、CLI log ingestion | source + wheel isolated smoke 已實測 | Ubuntu／macOS wheel build、install 與 API/assets smoke 已由 CI run `32757498004` 實測 |
| Browser Extension | Chrome/Edge Alpha | Chromium 理論可用，待實機 |
| Window foreground collector | 支援 | 明確降級，不宣稱可用 |
| Desktop notification | WinRT Toast／MessageBox fallback | 明確降級，待平台實作 |
| Autostart installer | Windows Task Scheduler | 尚未提供 |

## 8. 常見問題

### Extension Monitor 顯示 `configured_unverified`

表示 token 已設定，但 server 尚無近期 verified heartbeat。依序確認：

1. 本機 service 是否仍在 `127.0.0.1:8765` 執行。
2. Extension popup pairing 是否成功。
3. 對應平台是否在 `watchers.browser` 中啟用。
4. 到 `chrome://extensions/`／`edge://extensions/` 對 OmniContext 按 Reload。
5. 重新載入支援網站分頁並打開 popup，觸發立即 heartbeat。
6. Extension service worker 的非敏感 error code 與 offline queue。
7. 完成一輪包含 assistant response 的真實對話後重新整理 Monitor。

`last_error_code` 只會顯示如 `http_401`、`input_selector_not_found` 等診斷碼，不包含 URL、token 或對話內容。

### 使用時間看起來偏少

先看 coverage badge。視窗採集器停止、電腦休眠、平台不支援、未知視窗分類或超過 `max_interval_seconds` 的事件，都不應被補值或外推。

### 顯示 `MONITORING`，但資料時間停止更新

先讀取真實 collector 狀態與最後事件，不要只重新整理瀏覽器：

```powershell
Invoke-RestMethod http://127.0.0.1:8765/api/v1/control/status
Invoke-RestMethod http://127.0.0.1:8765/api/v1/usage/today
```

若前景視窗確實持續切換，但 `last_events.window_watcher` 與 `data_updated_at` 仍不前進，可先呼叫 `POST /api/v1/control/stop` 讓 collectors flush，再確認 8765 的 OwningProcess 確實是 OmniContext，停止該 PID 後使用 `python main.py run` 整合重啟。不要終止未確認的 Python 程序。

Agent log 採 source-level fault isolation：Claude Desktop 某個目錄無權限時會跳過該來源，Codex、Claude Code 與 Antigravity 仍應繼續更新。Extension heartbeat 是獨立通道；重啟 localhost service 不等於 Extension 已重新配對。

主頁頂端的 `MONITORING 部分採集異常` 與 `DATA TRUST · N DEGRADED` 代表 runtime probe 已偵測異常；`8/8 CONTRACT` 只表示靜態契約測試通過，不等同 runtime 健康。請展開即時情報流的 collector 卡片查看非敏感錯誤來源，再以事件時間是否前進判定是否恢復。

### Scheduler 顯示 `builtin_timer`

表示目前環境沒有使用 APScheduler backend，系統已自動使用內建 timer。`GET /api/v1/control/status` 的 `scheduled_jobs` 才是目前排程契約；backend 名稱不同不代表排程失效。

### 服務無法啟動或 8765 已被占用

```powershell
Get-NetTCPConnection -LocalPort 8765 -State Listen
```

先確認占用程序是否為既有 OmniContext instance；不要直接終止未確認的程序。OmniContext 具 single-instance lock，重複啟動應先關閉原實例。

## 9. 驗證與回報問題

開發者驗證：

```powershell
python -m pytest -q
python -m compileall -q core synthesizer notifiers watchers exporters tests
python main.py migration-status
python main.py assets-status
python scripts/verify_release_artifacts.py dist
git diff --check
```

完整 wheel/sdist 發布門檻、rollback triggers 與未完成項目見 [RELEASE_CHECKLIST.md](RELEASE_CHECKLIST.md)。Build 成功本身不能替代 fresh install、upgrade、assets、privacy exclusions 與多平台驗證。

回報問題時可附：OS、Python 版本、執行命令、錯誤文字、`python main.py status` 的非敏感部分與重現步驟。不要附上 `config.yaml`、database、完整 transcript、API key、Extension token 或私人檔案路徑。
