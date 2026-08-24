# 🌐 OmniContext — 個人全景活動追蹤與進行中工作智慧中樞

[![Language](https://img.shields.io/badge/Language-English%20%7C%20%E7%B9%81%E9%AB%94%E4%B8%AD%E6%96%87-orange)](#-language--%E8%AA%9E%E8%A8%80)
[![License: MIT](https://img.shields.io/badge/License-MIT-blue.svg)](LICENSE)
[![Python](https://img.shields.io/badge/Python-3.10%2B-blue)](https://www.python.org/)
[![FastAPI](https://img.shields.io/badge/FastAPI-0.110%2B-green)](https://fastapi.tiangolo.com/)

> **[English Documentation](README_en.md) | [繁體中文說明文件](README.md)**

**OmniContext** 是一個**本機優先（Local-First）、注重絕對隱私**的個人上下文記憶中樞與工作進度追蹤系統。它能全自動捕獲您在電腦上的跨平台 AI 對話（Claude Code、Codex、Antigravity、ChatGPT、Gemini 等）、程式碼提交、檔案與論文寫作異動、視窗時間分配，並深度整合 GitHub 雲端倉庫與 Pull Request (PR) 狀態。

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
│                    (omni_context.db · 零外洩)                            │
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

### 3. 🤖 跨平台 AI 對話全景記錄（問答完整解析）
* **本機 CLI / IDE Agent**：
  * **Claude Code**（`~/.claude/projects/`）：完整記錄命令、提問與對話細節。
  * **Codex**（`~/.codex/sessions/**`）：解析 Rollout JSONL 與 Assistant 訊息回覆。
  * **Antigravity**（`.gemini/brain/**`）：即時擷取對話與執行工具。
* **瀏覽器擴充套件（Chrome Extension MV3）**：
  * 支援 **ChatGPT**、**Gemini**、**Claude.ai**、**Manus**。
  * 具備 10 分鐘滑動窗口 Upsert 去重，同時保存**使用者提問**與 **AI 完整回答內容**。

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

---

## 🚀 快速開始

### 1. 安裝環境與依賴

需求環境：**Python 3.10+**

```bash
# 複製專案
git clone https://github.com/dofliu/activityTracker.git
cd activityTracker

# 安裝 Python 依賴套件
pip install -r requirements.txt
```

### 2. 設定 LLM API 金鑰

系統預設使用 `Google Gemini`，請設定環境變數或於 `config.yaml` 中配置：

```bash
# Windows PowerShell
$env:GEMINI_API_KEY="your-gemini-api-key"

# 或若使用 Anthropic / OpenAI
$env:ANTHROPIC_API_KEY="your-anthropic-api-key"
$env:OPENAI_API_KEY="your-openai-api-key"
```

### 3. 啟動 Web 儀表板與後台監控

```bash
python main.py
```

啟動後於瀏覽器開啟：**[http://127.0.0.1:8765](http://127.0.0.1:8765)**

---

## 💻 CLI 指令完全指南

OmniContext 支援完整的終端命令列操作：

| 指令 | 說明 | 範例 |
| :--- | :--- | :--- |
| `python main.py` | 啟動 Web 儀表板與背景採集服務 | `python main.py` |
| `python main.py resume` | 產出專案接續 Context Handoff（支援 `--copy` 一鍵複製貼入 AI） | `python main.py resume activityTracker -c` |
| `python main.py now` | 一秒查詢當前進行中專案、最近 5 筆活動與未結事項 | `python main.py now` |
| `python main.py summary` | 生成 AI 摘要日報（支援自訂區間與強制更新） | `python main.py summary --start 2026-08-20 --end 2026-08-23` |
| `python main.py github status` | 查看當前 GitHub 連線帳號、倉庫數與 API 額度 | `python main.py github status` |
| `python main.py github sync` | 手動觸發同步 GitHub 所有 Public/Private 倉庫與 PRs | `python main.py github sync` |
| `python main.py checkpoint` | 手動打包最近時段活動為 Markdown 快照 Log | `python main.py checkpoint --hours 2` |
| `python main.py brief` | 產出每日簡報檔案至每日入口目錄 | `python main.py brief --notify` |
| `python main.py notify` | 手動觸發提醒（預設桌面通知） | `python main.py notify briefing --dry-run` |
| `python main.py status` | 查看資料庫累積數據指標與採集器運行狀態 | `python main.py status` |

---

## ⚙️ 設定檔說明 (`config.yaml`)

系統設定檔支援 Web 介面即時儲存與熱更新：

```yaml
app:
  port: 8765
  host: "127.0.0.1"

watchers:
  file_watcher:
    enabled: true
    watch_directories:
      - "D:/Project_CodingSimulation"
      - "D:/Dropbox/Project_Academic/Paper_and_Patent/01.JournalPapers"
    extensions: [".tex", ".docx", ".md", ".pdf", ".py", ".txt"]
  
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
4. 選擇本專案中的 `watchers/browser_extension/` 資料夾。
5. 安裝完成後，當您造訪 ChatGPT、Gemini、Claude.ai 或 Manus 時，提問與 AI 回覆將自動同步至本地 OmniContext！

---

## 📂 專案檔案架構

```text
activityTracker/
├── config.yaml                     # 系統設定檔（支援 Web UI 熱更新）
├── main.py                         # 主入口與 CLI 命令列分發
├── requirements.txt                # 專案相依套件清單
├── README.md                       # 繁體中文說明文件
├── README_en.md                    # English Documentation
│
├── core/                           # 核心服務模組
│   ├── database.py                 # SQLite 連線與 Session 管理
│   ├── models.py                   # SQLAlchemy 資料庫模型 (Events, Projects, PRs)
│   ├── server.py                   # FastAPI REST API 與靜態伺服器
│   ├── project_engine.py           # 專案智能歸戶、多檔案聚合與未結事項引擎
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
│
├── logs/checkpoints/               # 週期性活動快照儲存目錄
└── reports/                        # 每日/區間 Markdown 報告儲存目錄
```

---

## 🗺️ 開發路線與現況

### 目前累積的資料資產

```
2,047 筆 AI 對話 · 1,775 筆完整問答配對 · 約 236 萬字元
時間跨度 2025-05-19 ~ 2026-08-24（15 個月）
70 個專案狀態 · 57 個 GitHub repos · 266 筆 PR
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
| **P3** | 記憶層 | 專案接續 Context Handoff、本機語意檢索、`omni ask` 問自己的歷史、重複工作偵測、Session 敘事層 |
| **P4** | 收集層補完 | 瀏覽器閱讀內容、行事曆與會議、終端機指令歷史、未 commit 的工作狀態 |
| **P5** | 效率工具層 | `STATUS.yaml` 自動維護、停滯 PR 提醒、週報月報 rollup、知識圖譜 |
| **P6** | 開源整備 | 抽離硬編碼路徑、`main.py init` 引導、pyproject 與測試、跨平台抽象 |

> 收集越多不等於越有用：檔案事件曾從 3,575 筆噪音 → 4,327 筆 → 收斂至 789 筆。
> 新增採集來源必須先通過「能否改變決策」的檢驗。

完整規劃與驗收標準見 **[ROADMAP.md](ROADMAP.md)**。

### 目前的使用前提

本專案現階段為**個人優先（personal-first）**設計，尚未針對他人環境整備：

* 部分歸戶邏輯仍硬編碼專案根路徑（`core/project_engine.py`）。
* 視窗採集、桌面通知與開機排程僅支援 **Windows**。
* 尚無 `pyproject.toml` 與測試，暫不支援 `pip install`。
* 首次啟動需手動編輯 `config.yaml` 的絕對路徑。

以上將於 **P6 開源整備** 階段處理。

---

## 🔒 隱私與安全聲明

* **100% 本機儲存**：所有活動事件均保存在本機 SQLite 資料庫中（`omni_context.db`）。
* **零雲端遙測**：系統不包含任何外部追蹤代碼或第三方分析工具。
* **Git 提交防護**：資料庫檔案、API 金鑰與個人 Markdown 報告均已預設加入 `.gitignore`，確保私密對話與工作日誌絕不上傳公開倉庫。

---

## 📄 授權條款

本專案採用 [MIT License](LICENSE) 授權。
