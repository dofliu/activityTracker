# 🌐 OmniContext — 個人全景活動追蹤與進行中工作智慧中樞

[![Language](https://img.shields.io/badge/Language-English%20%7C%20%E7%B9%81%E9%AB%94%E4%B8%AD%E6%96%87-orange)](#-language--%E8%AA%9E%E8%A8%80)
[![License: MIT](https://img.shields.io/badge/License-MIT-blue.svg)](LICENSE)
[![Python](https://img.shields.io/badge/Python-3.10%2B-blue)](https://www.python.org/)
[![FastAPI](https://img.shields.io/badge/FastAPI-0.110%2B-green)](https://fastapi.tiangolo.com/)

> **[English Documentation](README_en.md) | [繁體中文說明文件](README.md)**

> **目前狀態：Personal Alpha。** Windows milestone WinRT Toast E2E、schema 7/7、formal package+DB rollback、P3-2～P3-5 與跨平台 CI 已通過；ChatGPT 真實 DOM selectors 已修復，Claude Desktop Cowork／local-agent transcript 已完成 Windows E2E。Claude.ai／Manus authenticated Browser capture 與 Extension live heartbeat 仍待真實 receipt，因此尚非 release-ready。

**文件入口：**[完整使用說明](docs/USAGE.md) · [開發規劃](ROADMAP.md) · [目前狀態](STATUS.yaml) · [測試策略](docs/TEST_STRATEGY.md)

![OmniContext 架構與未來 Roadmap](docs/assets/omnicontext-architecture-roadmap-card-v1.png)

**OmniContext** 是一個**本機優先（Local-First）、具有明確資料邊界**的個人上下文記憶中樞與工作進度追蹤系統。它能捕獲跨平台 AI 對話（Claude Code、Codex、Antigravity、ChatGPT、Gemini 等）、程式碼提交、檔案與論文寫作異動、視窗時間分配，並整合 GitHub 雲端倉庫與 Pull Request (PR) 狀態。

它與單一 AI 的 memory／chat import 不同：**OmniContext 的 canonical context 屬於使用者與專案，不屬於任何一家 AI provider。** 除了多個 AI 的對話與工作狀態，也把 local Repository、branch/commit、檔案異動、IDE/terminal、foreground activity 與 Open Loops 納入同一條可追溯時間線，再產生 provider-neutral Context Handoff。完整定位與證據邊界見[產品定位](docs/PRODUCT_POSITIONING.md)。

隨時幫助您回答三個核心問題：
1. **「我現在正在進行哪些專案？」**
2. **「我上次做到哪裡、動了哪些檔案？」**
3. **「有哪些尚未收尾的未結事項（Open Loops）？」**

---

## 🌟 核心特色功能

```
┌──────────────────────────────────────────────────────────────────────────┐
│                        OmniContext 核心系統架構                          │
├──────────────────────────────────────────────────────────────────────────┤
│                                                                          │
│  [ 跨平台 AI 採集 ]      [ 本機檔案 / Git ]      [ GitHub 雲端整合 ]     │
│  • Claude Code 日誌       • Watchdog 檔案異動     • 48+ Public/Private   │
│  • Codex Sessions         • 遞迴 Git Scanner      • PR 狀態 / 分支流向   │
│  • Antigravity 對話       • 論文多檔案智能歸戶    • Actions CI 測試結果  │
│  • Chrome 擴充套件                                                       │
│          │                       │                       │               │
│          └───────────────────────┼───────────────────────┘               │
│                                  ▼                                       │
│                    [ 本機 SQLite 資料庫儲存 ]                            │
│             (omni_context.db · 本機儲存；cloud LLM 為 opt-in)            │
│                                  │                                       │
│          ┌───────────────────────┴───────────────────────┐               │
│          ▼                                               ▼               │
│  [ Web 視覺化儀表板 ]                        [ AI 摘要與排程回顧 ]       │
│  • 01 · 進行中工作 (Workstreams)             • 多日自訂區間日報回顧      │
│  • 02 · 即時情報流 (Live Feed)               • 週期性 Checkpoint 快照    │
│  • 03 · 監控配置 (Settings)                  • Telegram 晨間簡報與停滯警示 │
│  • 04 · 每日摘要 (Summaries)                 • 多供應商 (Gemini 3.7 /    │
│  • 05 · 活動快照 (Checkpoints)                 Claude / OpenAI / Ollama) │
│  • 🌐 中英文 i18n 動態切換 / 深淺色主題                                  │
└──────────────────────────────────────────────────────────────────────────┘
```

### 1. 🎯 專案根目錄智能歸戶（Hierarchy Project Resolver）
* **消除子目錄碎片化**：自動將巢狀目錄（如 `core/`、`synthesizer/`、`Draft_Paper/`、`Daily_Report/`）整合歸戶至對應的真實專案或論文根名稱（如 `activityTracker`、`AI_PapersResearch`）。
* **工作階段多檔案聚合**：同一工作階段內動到的多個檔案，在專案卡片上整合成單一條目（如「`異動 A.md, B.py 等共 6 個檔案`」），點開手風琴即可展開所有檔案的字數變更與路徑清單。

### 2. 🐙 GitHub 雲端全專案與 PR 智慧追蹤（GitHub Cloud Intel）
* **雙軌認證**：
  * **一鍵免密連線**：自動探測本機登入之 `gh` CLI 憑證（具備 `repo`, `read:org`, `workflow`, `gist` 完整 scope），無需手動建立 PAT。
  * **Token 支援**：支援 Fine-Grained 與 Classic Personal Access Token。
* **全量倉庫與 PR 狀態撈取**：
  * 自動同步所有 Public / Private 倉庫。
  * 提取各 PR 的標題、狀態（Open / Merged / Draft）、分支流向（`head -> base`）、CI 測試結果（`SUCCESS` / `PENDING` / `FAILURE`）與審查狀態。
  * Web 儀表板提供直接點擊跳轉至 GitHub PR 的超連結。

### 3. 🤖 跨平台 AI 對話全景記錄（來源可追溯；P2.5 強化中）
* **本機 CLI / IDE Agent**：
  * **Claude Code**（`~/.claude/projects/`）：完整記錄命令、提問與對話細節。
  * **Claude Desktop Cowork／local-agent**：自動偵測 application data 中的結構化 project JSONL；Windows extended-path 與最近 7 天首次回補已支援。
  * **Codex**（`~/.codex/sessions/**`）：解析 Rollout JSONL 與 Assistant 訊息回覆。
  * **Antigravity**（`.gemini/brain/**`）：即時擷取對話與執行工具。
* **瀏覽器擴充套件（Chrome Extension MV3）**：
  * 支援 **ChatGPT**、**Gemini**、**Claude.ai**、**Manus**。
  * 以獨立 ingest token 實施 write-only capability boundary，並以穩定 turn key Upsert。
* **明確邊界**：一般 Claude Desktop 雲端聊天目前只偵測 cache 存在，不解析 Chromium LevelDB，也不宣稱已取得對話內容。

### 4. ⚡ 自訂日期區間 AI 工作回顧（LLM Synthesis Engine）
* **任意日期範圍報告**：支援從 Web UI 選擇起訖日期（`FROM ~ TO`）或使用 `今日`、`昨日`、`本週`、`近 7 天`、`近 30 天` 快捷標籤，一鍵產出多日全景回顧。
* **多模型支援**：預設採用 Google Gemini (`gemini-3.7-flash`)，亦支援 Anthropic Claude、OpenAI GPT-4o 及本機 Ollama。
* **未結事項萃取**：AI 生成摘要時自動提煉「待收尾與未結事項 (Open Loops)」，並同步至首頁右側清單供勾選結案。

### 5. 🌐 完整中英文多語言介面（Bilingual i18n & Theme）
* Web 儀表板頂部提供 `🌐 English` / `🌐 繁體中文` 一鍵即時切換。
* 支援淺色（Light）與深色（Dark）主題，所有使用者偏好自動儲存於 `localStorage`。

### 6. 🔔 零設定主動提醒（桌面通知 + 每日入口檔案）
* **Windows 原生桌面通知**：直接呼叫 WinRT Toast，**不需安裝任何套件、不需申請帳號**。
  * 晨間簡報（08:30）：進行中專案 + 最優先的未收尾事項。
  * 今日回顧（22:00）：今天推進了哪些專案。
  * 停滯提醒：專案閒置超過 5 天自動示警。
  * 點擊通知直接開啟儀表板；`--dry-run` 可先預覽內容。
* **每日入口簡報**：自動產出 `OMNICONTEXT_TODAY.md` / `.html` 到你每天會打開的目錄，
  HTML 版每 5 分鐘自動刷新，可直接設為瀏覽器首頁或書籤。
* 提供 Windows 開機自動背景啟動安裝腳本（`scripts/install_autostart.ps1`）。
* Telegram 通道保留為選用（預設關閉）。

### 7. ⏱️ 每日主要介面使用時間與里程碑（P2.6 Alpha）
* 主頁顯示 Claude、Codex、ChatGPT、Gemini、Manus、Antigravity、VS Code 等介面的每日 **foreground active time** 與 AI turns。
* 使用者可設定每日目標、里程碑、通知語氣、quiet hours 與 cooldown；SQLite receipt 防止重啟後重複通知。
* 數值只代表已觀察到的前景時間，不等於生產力或實際工時；coverage ledger 尚未完成前一律標示 `partial`。
* 主頁 `DATA CAPTURE` 將 `FOCUS`、`WEB`、`LOG` 三種獨立訊號濃縮在同一區塊；任何一欄 `OBSERVED` 都不能替代另外兩欄。
* `http://127.0.0.1:8765/extension-monitor` 是 Browser Extension 的進階診斷頁，負責 enabled／observed、heartbeat 與逐站狀態；token pairing 仍只能在 Extension popup 完成。

### 8. 🧠 本機 Semantic Index 與 `omni ask`（P3-2 / P3-3 Alpha）
* 以 loopback Ollama `bge-m3` 將 AI turns、Git commits、file activity metadata、Open Loops 與 Project State 建立 1024 維本機索引，資料不送至 cloud provider。
* `content_hash + embedding_model` 增量更新；每筆保留原始 SQLite `source_ref`、project、timestamp、trust status 與 embedding input 降級模式。
* `omni ask` 可先 retrieval-only，也可由本機 Ollama 生成含 `[S1]` 引用的答案；similarity 不是來源真實性或 coverage 證明。

### 9. 🧭 Related History 與 Work Sessions（P3-4 / P3-5 Alpha）
* 主頁將已歸戶的 AI、Git 與檔案事件依 project + inactivity gap 整理為 derived work session；每段保留穩定 session ID、來源計數與 SQLite `source_ref`，不新增資料表、不改寫原始事件。
* `omni recall` 與主頁 `RELATED HISTORY` 使用 loopback Ollama 尋找相似歷史；查詢不保存，Ollama 不可用時不 fallback 到 cloud。
* Session 是 temporal inference，不代表實際工時、連續專注或成果品質；similarity 也不能證明工作重複、歷史答案正確或仍然適用。架構決策見 [ADR-006](docs/ADR-006-derived-context-sessions-and-related-history.md)。

---

## 🚀 快速開始

### 1. 安裝環境與依賴

需求環境：**Python 3.10+**

```console
# 複製專案
git clone https://github.com/dofliu/activityTracker.git
cd activityTracker

# Source checkout／開發模式
python -m pip install -e ".[dev]"

# 建立本機設定、目錄與 browser ingest token
python main.py init --watch "/your/project/root"
```

若使用已建置的 Alpha wheel：

```console
python -m pip install omnicontext-1.3.0a4-py3-none-any.whl
omnicontext init --watch "/your/project/root"
omnicontext assets-status
```

Wheel 尚未公開發布。Installed wheel 預設將 config、database 與 reports 放在使用者可寫的 `~/OmniContext`，不寫入 `site-packages`；可用 `OMNICONTEXT_HOME` 或 `OMNICONTEXT_CONFIG` 覆寫。

### 2. 設定 LLM API 金鑰

發布範本預設使用本機 `Ollama` 且關閉排程摘要。若主動選用 `Google Gemini`、Anthropic 或 OpenAI，請把金鑰保存在作業系統環境變數；`config.yaml` 只保存 `api_key_env` 變數名稱，不保存明文金鑰。Dashboard「監控配置 → 摘要與排程」會顯示是否已偵測及來源，但不會把金鑰送到瀏覽器。

```bash
# Windows PowerShell：持久保存於目前使用者環境
[Environment]::SetEnvironmentVariable("GEMINI_API_KEY", "your-gemini-api-key", "User")

# 或若使用 Anthropic / OpenAI
[Environment]::SetEnvironmentVariable("ANTHROPIC_API_KEY", "your-anthropic-api-key", "User")
[Environment]::SetEnvironmentVariable("OPENAI_API_KEY", "your-openai-api-key", "User")
```

Windows 上即使 OmniContext 的父程序較早啟動，後端也會回讀 User／Machine environment；設定後可在配置頁按「重新檢查」。

### 3. 啟動 Web 儀表板與後台監控

```bash
python main.py
```

啟動後於瀏覽器開啟：**[http://127.0.0.1:8765](http://127.0.0.1:8765)**

完整的 Extension 配對、里程碑設定、備份與故障排查流程見 **[docs/USAGE.md](docs/USAGE.md)**。

---

## 💻 CLI 指令完全指南

OmniContext 支援完整的終端命令列操作：

| 指令 | 說明 | 範例 |
| :--- | :--- | :--- |
| `python main.py` | 啟動 Web 儀表板與背景採集服務 | `python main.py` |
| `python main.py init` | 建立／更新跨平台設定與 extension token | `python main.py init --watch D:/Projects` |
| `python main.py resume` | 產出專案接續 Context Handoff（支援 `--copy` 一鍵複製貼入 AI） | `python main.py resume activityTracker -c` |
| `python main.py now` | 一秒查詢當前進行中專案、最近 5 筆活動與未結事項 | `python main.py now` |
| `python main.py summary` | 生成 AI 摘要日報（支援自訂區間與強制更新） | `python main.py summary --start 2026-08-20 --end 2026-08-23` |
| `python main.py github status` | 查看當前 GitHub 連線帳號、倉庫數與 API 額度 | `python main.py github status` |
| `python main.py github sync` | 手動觸發同步 GitHub 所有 Public/Private 倉庫與 PRs | `python main.py github sync` |
| `python main.py checkpoint` | 手動打包最近時段活動為 Markdown 快照 Log | `python main.py checkpoint --hours 2` |
| `python main.py brief` | 產出每日簡報檔案至每日入口目錄 | `python main.py brief --notify` |
| `python main.py notify` | 手動觸發提醒（預設桌面通知） | `python main.py notify briefing --dry-run` |
| `python main.py status` | 查看資料庫累積數據指標與採集器運行狀態 | `python main.py status` |
| `python main.py open-loop` | 人工複核 Open Loop lifecycle | `python main.py open-loop 12 resolved --note "done"` |
| `python main.py backup` | 使用 SQLite Online Backup API 建立並驗證備份 | `python main.py backup` |
| `python main.py restore-drill` | 在隔離暫存 DB 驗證最新／指定備份，不覆蓋 live DB | `python main.py restore-drill` |
| `python main.py migration-status` | 唯讀查看目前／最新 schema version、pending 與相容性 | `python main.py migration-status` |
| `python main.py assets-status` | 檢查 packaged config/Web/Extension assets | `python main.py assets-status` |
| `python main.py extension-path` | 顯示 Chrome/Edge Load unpacked 目錄 | `python main.py extension-path` |
| `python main.py index` | 建立／增量更新本機 semantic index | `python main.py index --json` |
| `python main.py ask` | 查詢自己的跨 AI／Repository 歷史並列出來源 | `python main.py ask "上次如何處理 rollback?" --project activityTracker` |
| `python main.py sessions` | 將近期 evidence 整理為 derived work sessions | `python main.py sessions --project activityTracker --hours 72` |
| `python main.py recall` | 查詢相似歷史工作，不保存 query | `python main.py recall "formal rollback rehearsal" --project activityTracker` |

Installed wheel 可將表中的 `python main.py` 改為 `omnicontext` 或較短的 `omni`。

---

## ⚙️ 設定檔說明 (`config.yaml`)

系統設定檔支援 Web 介面即時儲存與熱更新：

```yaml
server:
  port: 8765
  host: "127.0.0.1"

security:
  allowed_origins:
    - "http://127.0.0.1:8765"
    - "http://localhost:8765"
  allow_remote_clients: false
  browser_extension_ingest_token_env: "OMNICONTEXT_INGEST_TOKEN"

data_lifecycle:
  backups_dir: "~/OmniContext/backups"
  backup_retention_days: 30
  auto_backup_on_start: false

watchers:
  file_watcher:
    enabled: true
    watch_directories:
      - "D:/Project_CodingSimulation"
      - "D:/Dropbox/Project_Academic/Paper_and_Patent/01.JournalPapers"
    extensions: [".tex", ".docx", ".md", ".pdf", ".py"]
  
  git_watcher:
    enabled: true
    repositories:
      - "D:/Project_CodingSimulation"
  
  agent_log_watcher:
    enabled: true
    claude_code: true
    codex: true
    antigravity: true

  browser:
    gemini: true
    chatgpt: true
    claude_web: true
    manus: true

synthesizer:
  provider: "gemini"
  gemini:
    model: "gemini-3.7-flash"
  schedule:
    enabled: true
    time: "23:30"
  periodic_checkpoint:
    enabled: true
    interval_hours: 2

integrations:
  github:
    enabled: true
    token: ""  # 空白時自動使用本機 gh auth token
```

---

## 🧩 安裝 Chrome 瀏覽器擴充套件

1. 開啟 Chrome 或 Edge 瀏覽器，進入 `chrome://extensions/`。
2. 開啟右上角 **「開發人員模式」 (Developer mode)**。
3. 點選 **「載入未封裝項目」 (Load unpacked)**。
4. 執行 `python main.py extension-path`（wheel 安裝則為 `omnicontext extension-path`），將輸出的資料夾選為 Load unpacked。
5. 執行 `python main.py init --show-token`，將 token 貼到擴充套件 popup 後儲存。
6. 只有帶有效 token 的支援網站事件，才能寫入本機 `/api/v1/events/ai`。
7. popup 顯示「配對成功」後，可由 `http://127.0.0.1:8765/extension-monitor` 查看各網站是否已有 observed event。

---

## 📂 專案檔案架構

```text
activityTracker/
├── config.yaml                     # 系統設定檔（支援 Web UI 熱更新）
├── main.py                         # 主入口與 CLI 命令列分發
├── pyproject.toml                  # 跨平台安裝、CLI entry point 與 pytest 設定
├── MANIFEST.in                     # sdist assets 與 privacy exclusions
├── requirements.txt                # 專案相依套件清單
├── README.md                       # 繁體中文說明文件
├── README_en.md                    # English Documentation
├── docs/USAGE.md                   # 安裝、配對、日常操作、備份與故障排查
├── docs/ADR-003-versioned-sqlite-migrations.md  # Schema migration 架構決策
├── docs/ADR-004-packaged-runtime-layout.md       # Wheel/sdist runtime layout 決策
│
├── core/                           # 核心服務模組
│   ├── database.py                 # SQLite 連線與 Session 管理
│   ├── migrations.py               # Append-only schema registry、checksum 與升級守門
│   ├── runtime_paths.py            # Source/wheel application home 與 packaged assets
│   ├── models.py                   # SQLAlchemy 資料庫模型 (Events, Projects, PRs)
│   ├── server.py                   # FastAPI REST API 與靜態伺服器
│   ├── security.py                 # Origin、secret redaction 與 extension token boundary
│   ├── platform_services.py        # Windows/macOS/Linux argv 型 OS 整合
│   ├── data_lifecycle.py           # SQLite online backup 與 integrity receipt
│   ├── project_engine.py           # 專案智能歸戶、多檔案聚合與未結事項引擎
│   ├── semantic_index.py           # 本機 embeddings、provenance retrieval 與 omni ask
│   ├── context_memory.py           # Related History 與 derived work-session grouping
│   ├── fs_utils.py                 # 本機原生檔案總管/瀏覽對話框工具
│   └── time_utils.py               # 統一本地時區解析工具
│
├── integrations/                   # 外部雲端整合
│   └── github_client.py            # GitHub API Client (Public/Private Repos, PRs, CI)
│
├── watchers/                       # 多源活動數據採集器
│   ├── file_watcher.py             # Watchdog 檔案異動監控與字數統計
│   ├── git_watcher.py              # Git 遞迴多倉庫掃描與 Commit 追蹤
│   ├── window_watcher.py           # 視窗焦點切換與時間分配統計
│   ├── agent_log_watcher.py        # Claude Code / Codex / Antigravity 日誌解析
│   └── browser_extension/          # Chrome MV3 擴充套件 (ChatGPT/Gemini/Claude/Manus)
│
├── synthesizer/                    # AI 摘要與排程回顧引擎
│   ├── aggregator.py               # 多日區間資料聚合與報告管線
│   ├── prompt_templates.py         # 結構化 Prompt 樣板
│   ├── llm_client.py               # 多供應商 LLM 客戶端 (Gemini/Claude/OpenAI/Ollama)
│   └── scheduler.py                # 每日定時總結與週期快照定時器
│
├── notifiers/                      # 通知推播模組
│   └── telegram_notifier.py        # Telegram Bot 每日摘要與停滯專案警示
│
├── web/                            # Web 儀表板前端
│   ├── index.html                  # 儀表板主結構 (支援 i18n 標籤)
│   ├── app.js                      # 前端控制器 (i18n 多語言引擎、GitHub 狀態、手風琴視圖)
│   └── style.css                   # 暗橘風格主題與雙欄版型
│
├── scripts/                        # 自動化與維護腳本
│   ├── install_autostart.ps1       # Windows 開機自動啟動註冊腳本
│   └── uninstall_autostart.ps1     # 移除開機自動啟動腳本
├── tests/                          # security/data/lifecycle/portability contract tests
├── docs/                           # ADR、test strategy 與 hardening acceptance 文件
│
├── logs/checkpoints/               # 週期性活動快照儲存目錄
└── reports/                        # 每日/區間 Markdown 報告儲存目錄
```

---

## 🗺️ 開發路線與現況

### 目前累積的資料資產

```
2,418 筆 AI event rows · 2,053 筆非空回應 · 1,890 筆 final candidates · 66 筆 partial（2026-08-24 16:35 快照）
時間跨度 2025-05-19 ~ 2026-08-24（15 個月）
72 個專案狀態 · 57 個 GitHub repos · 266 筆 PR
```

### 這個專案的差異化定位

市面同類工具（ActivityWatch、RescueTime、Timing）追蹤的是**時間**；
Rewind、Screenpipe 錄螢幕再做 OCR，隱私成本與資源消耗都高。

**目前沒有主流工具在讀本機 AI agent 的 transcript。** `~/.claude/projects/`、
`~/.codex/sessions/`、`.gemini/antigravity/brain/` 這些檔案就在硬碟上，
不需錄螢幕、不需額外權限，而裡面記錄的是真正的思考過程——
問了什麼、AI 怎麼答、最後決定怎麼做。

### 下一階段：從「日誌」到「記憶」

現階段 236 萬字元**只有一種存取方式：時間排序**。因此下一階段的重點
不是繼續擴大收集，而是讓既有資料可被檢索與再利用：

| 階段 | 內容 | 說明 |
| :--- | :--- | :--- |
| **P2.5** | 可信度與安全 hardening | API security boundary、ingestion provenance/finalization、Open Loop lifecycle、pytest 與跨平台基線 |
| **P3** | 記憶層 | ✅ P3-1 Context Handoff、P3-2 本機語意檢索、P3-3 `omni ask`、P3-4 Related History、P3-5 derived Session 敘事層均完成 Alpha |
| **P4** | 收集層補完 | 瀏覽器閱讀內容、行事曆與會議、終端機指令歷史、未 commit 的工作狀態 |
| **P5** | 主動秘書 AI 與自主執行 | 主動情境推論與前瞻提案、三級安全守門員（L0/L1/L2）、Agent Dispatcher 調度自主執行、Telegram/Web 一鍵批准、晨間前瞻與晚間交接、`STATUS.yaml` 自動維護 |
| **P6** | 開源整備 | `1.3.0a3` candidate、formal rollback，以及 Windows／Ubuntu／macOS × Python 3.10／3.12 GitHub Actions matrix 已通過；仍待 Extension live receipts 與發佈授權 |

> 收集越多不等於越有用：檔案事件曾從 3,575 筆噪音 → 4,327 筆 → 收斂至 789 筆。
> 新增採集來源必須先通過「能否改變決策」的檢驗。

完整規劃與驗收標準見 **[ROADMAP.md](ROADMAP.md)**。

### 目前的使用前提

本專案現階段為**個人優先（personal-first）**設計，尚未針對他人環境整備：

* 部分歸戶邏輯仍硬編碼專案根路徑（`core/project_engine.py`）。
* 視窗採集、桌面通知與開機排程僅支援 **Windows**。
* `pyproject.toml`、schema migration 7/7、formal rollback，以及 Windows／Ubuntu／macOS 的 wheel/sdist build、install、API/assets smoke 已完成。
* `main.py init --watch <path>` 已取代手動複製設定；複雜來源仍需於 `config.yaml` 調整。

剩餘項目將於 **P6 開源整備** 階段持續處理。

---

## 🔒 隱私與安全聲明

* **事件本機儲存**：活動事件保存在本機 SQLite（`omni_context.db`），不含第三方 analytics telemetry。
* **LLM 資料邊界**：選擇 Gemini、Anthropic 或 OpenAI 產生摘要時，組裝後的工作脈絡會傳送至該 provider；選擇 Ollama 才是完整本機推論。
* **Local API**：採 deny-by-default Origin boundary、loopback-only 預設、敏感設定遮蔽與 browser-extension ingestion capability，避免一般網頁跨來源讀取本機工作紀錄。
* **資料可信度**：canonical AI event 必須具備 `turn_key`、source provenance 與 `response_status`；partial／legacy 回應不作為摘要或 handoff 結論。
* **備份生命週期**：`python main.py backup` 使用 SQLite Online Backup API 並輸出 integrity／SHA-256；`python main.py restore-drill` 於隔離暫存 DB 驗證 schema 與 row counts並保存 JSON receipt，不覆蓋 live DB。Formal package+DB rollback rehearsal 已通過；自動 retention pruning 尚未完成。
* **Schema migration**：append-only registry 保存 version/name/checksum；既有 DB upgrade 前自動產生 verified backup。Checksum mismatch 或未知較新版本會 fail-closed，不允許舊版 runtime 繼續開啟。
* **Artifact 邊界**：wheel/sdist content receipt 會檢查必要 assets，並拒絕夾帶 `config.yaml`、SQLite database 或 local secrets。
* **Git 提交防護**：資料庫檔案、API 金鑰與個人 Markdown 報告已預設加入 `.gitignore`，降低誤提交私密資料的風險。

---

## 📄 授權條款

本專案採用 [MIT License](LICENSE) 授權。
