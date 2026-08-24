# OmniContext 使用說明

> 適用版本：`1.3.0a3` Personal Alpha / P2.6 + P3 semantic memory + P6 cross-platform gate
>
> 主要驗證平台：Windows 11、Python 3.12、Chrome/Edge MV3

本文件提供可直接執行的安裝、啟動、Browser Extension 配對、每日使用、備份與故障排查流程。架構決策另見 [ADR-001](ADR-001-p2-5-trust-boundary.md)、[ADR-002](ADR-002-extension-monitor-and-usage-milestones.md)、[ADR-003](ADR-003-versioned-sqlite-migrations.md)、[ADR-004](ADR-004-packaged-runtime-layout.md) 與 [Release Checklist](RELEASE_CHECKLIST.md)。

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

## 2. 啟動與確認服務

```powershell
python main.py run
```

`python main.py` 與 `python main.py web` 目前等同 `run`。

啟動後可使用下列入口：

- 主儀表板：<http://127.0.0.1:8765/>
- Extension Monitor：<http://127.0.0.1:8765/extension-monitor>
- Health API：<http://127.0.0.1:8765/api/v1/health>

另開 PowerShell 確認採集器：

```powershell
python main.py status
```

應確認 `file_watcher`、`window_watcher`、`agent_log_watcher` 與 scheduler 的 runtime 狀態；`idle` 表示近期沒有新事件，不等於採集器故障。

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
7. 在 `chrome://extensions/`／`edge://extensions/` 按一次 Reload，讓 Extension `1.3.0` background、shared capture core 與 content scripts 生效。
8. popup 顯示 pairing 成功與近期 Heartbeat 後，開啟支援網站並完成一輪對話，再到 Extension Monitor 查看 `OBSERVED` 狀態。

目前支援 ChatGPT、Claude.ai、Gemini 與 Manus。Monitor 顯示 ONLINE 只證明 localhost service 正常；RECENT HEARTBEAT 代表 Extension 曾以正確 token 抵達 server；CONTENT READY 代表支援網站載入過 content script；只有 OBSERVED 才代表資料庫已有真實 Browser event。任何單一狀態都不代表完整 coverage。請勿把 token 放入截圖、issue、commit 或公開日誌。

如需旋轉 token：

```powershell
python main.py init --rotate-token --show-token
```

旋轉後必須重新貼入 Extension popup。

## 4. 每日介面使用時間與里程碑

主頁的「今日前景使用與里程碑」依 `WindowEvent` 計算已觀察到的 foreground active time，並以 canonical AI turns 顯示互動次數。

這些數值不代表生產力、專注度或實際工時。coverage 顯示：

- `partial`：有資料，但無法證明整日連續覆蓋。
- `unavailable`：目前平台不支援或 collector 不可用。
- `complete`：只有具備連續 coverage ledger 後才可使用；目前 Alpha 不宣稱完整覆蓋。

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

預覽今日判定而不發送通知、也不寫入 receipt：

```powershell
Invoke-RestMethod `
  -Method Post `
  -Uri "http://127.0.0.1:8765/api/v1/usage/milestones/evaluate" `
  -ContentType "application/json" `
  -Body '{"dry_run":true}'
```

## 5. 常用操作

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
