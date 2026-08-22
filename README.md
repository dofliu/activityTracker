# 🌐 OmniContext — 個人全景活動追蹤與 AI 回顧中樞

> 一個本機優先、注重隱私的個人上下文記憶系統。目標是自動捕捉跨平台 AI 對話、論文寫作、程式開發、檔案異動與時間分配，讓你隨時知道「我正在進行哪些工作、上次做到哪、有什麼還沒收尾」。

**目前狀態：開發中（progress 35%）。骨架完整，資料採集正確性修復中。**
完整開發規劃見 [ROADMAP.md](ROADMAP.md)，專案狀態見 [STATUS.yaml](STATUS.yaml)。

---

## ⚠️ 使用前必讀：目前的真實能力

這個專案的架構已經完成，但**多數採集器尚未產出可信資料**。在 P0 修復完成前，請不要把產出的報告當成真實工作紀錄。

| 功能 | 狀態 | 說明 |
| :--- | :--- | :--- |
| Web 儀表板（即時流／設定／報告） | ✅ 可用 | `http://127.0.0.1:8765` |
| 視窗焦點與時間統計 | ✅ 可用 | 唯一穩定運作的採集器 |
| LLM 每日摘要生成 | ✅ 可用 | 但輸入資料品質受下列問題影響 |
| 週期性 Checkpoint 快照 | ❌ 產出空白 | 時區混用造成查不到事件（缺陷 D1） |
| 檔案／論文異動 | ⚠️ 噪音嚴重 | `ignore_patterns` 未實作，訊噪比約 1:3574（D2） |
| Git commits | ❌ 掃不到 | 路徑需自身為 repo 且不遞迴（D3） |
| Claude Code 紀錄 | ❌ 未實作 | 目前僅有一行預留註解（D4） |
| 瀏覽器 AI 對話（ChatGPT/Gemini/Claude/Manus） | ⚠️ 只存問不存答 | 去重邏輯丟棄回應（D5） |
| 設定頁的 AI 工具開關 | ❌ 無作用 | 前端未寫入設定（D6） |
| 主動提醒／開機自動啟動 | ❌ 未實作 | 規劃於 P2 |

缺陷代號 D1~D6 的詳細位置與修法見 [ROADMAP.md](ROADMAP.md) 第 0.2 節。

---

## 🚀 快速上手

### 1. 安裝與啟動

```bash
cd D:\Project_CodingSimulation\PersonalHelper\activityTracker
pip install -r requirements.txt
python main.py
```

啟動後開啟 **`http://127.0.0.1:8765`** 進入儀表板。目前需手動啟動，關閉終端即停止（自動啟動規劃於 P2）。

### 2. 設定 LLM API 金鑰

依 `config.yaml` 的 `synthesizer.provider` 選擇供應商，並設定對應環境變數：

| Provider | 環境變數 | 預設模型 |
| :--- | :--- | :--- |
| `gemini`（預設） | `GEMINI_API_KEY` | gemini-2.5-flash |
| `anthropic` | `ANTHROPIC_API_KEY` | claude-3-5-sonnet |
| `openai` | `OPENAI_API_KEY` | gpt-4o |
| `ollama` | 無（本機 `localhost:11434`） | llama3.1:8b |

### 3. 安裝瀏覽器擴充套件（選用）

1. Chrome／Edge 開啟 `chrome://extensions/`，啟用「開發人員模式」。
2. 點「載入未封裝項目」，選擇 `watchers/browser_extension/`。
3. 注意：目前僅能擷取提問，AI 回應會被去重邏輯丟棄（D5 修復中）。

---

## 💻 CLI 指令

| 指令 | 說明 |
| :--- | :--- |
| `python main.py` | 啟動 Web 儀表板與後台監控 |
| `python main.py summary --force` | 手動生成今日 AI 摘要報告 |
| `python main.py checkpoint --hours 2` | 產出最近 2 小時活動快照 |
| `python main.py status` | 查看資料庫累積統計與監控狀態 |
| `python main.py seed-demo` | 寫入示範假資料（**注意：會污染真實統計**） |

> `seed-demo` 寫入的是虛構的論文與 commit 紀錄。若曾執行過，`reports/` 內的報告即包含假資料，請勿據此判斷實際工作進度。

---

## 📂 專案結構

```text
activityTracker/
├── STATUS.yaml                     # 專案狀態（研究儀表板掃描用）
├── ROADMAP.md                      # P0~P2 開發規劃與缺陷清單
├── config.yaml                     # 系統設定（支援 Web UI 熱更新）
├── main.py                         # 主入口與 CLI
│
├── core/                           # 核心服務
│   ├── manager.py                  # Watcher 生命週期管理
│   ├── server.py                   # FastAPI REST API 與靜態伺服器
│   ├── config.py                   # 設定載入器（singleton）
│   ├── database.py                 # SQLite Session 管理
│   └── models.py                   # 事件資料模型
│
├── web/                            # Web 儀表板前端（原生 JS）
│   ├── index.html / app.js / style.css
│
├── watchers/                       # 數據採集器
│   ├── browser_extension/          # Chrome MV3 擴充（4 個 AI 平台）
│   ├── file_watcher.py             # 檔案異動（watchdog）
│   ├── git_watcher.py              # Git commits 掃描
│   ├── window_watcher.py           # 視窗焦點與時間統計
│   └── agent_log_watcher.py        # 本機 Agent 日誌（Antigravity 已實作）
│
├── synthesizer/                    # 摘要與排程引擎
│   ├── aggregator.py               # 事件聚合、Checkpoint、Prompt 組裝
│   ├── prompt_templates.py         # 每日回顧 Prompt
│   ├── llm_client.py               # 多供應商 LLM 客戶端
│   └── scheduler.py                # 每日定時 + 週期性快照排程
│
├── logs/checkpoints/               # 週期性活動快照
└── reports/                        # 每日 Markdown 報告
```

---

## 🗺️ 開發路線

| 階段 | 目標 | 預估 |
| :--- | :--- | :--- |
| **P0** | 修復 D1~D6，讓現有功能產出真實乾淨的資料 | 0.5~1 天 |
| **P1** | 接上 Claude Code／Codex 日誌，新增專案狀態與未結事項追蹤 | 2~3 天 |
| **P2** | 開機自動啟動、Telegram 主動提醒、`/now` 隨時查詢 | 1~2 天 |

詳細任務與驗收標準見 [ROADMAP.md](ROADMAP.md)。

---

## 🔒 隱私

所有資料留在本機 `omni_context.db`（SQLite），不上傳任何雲端。資料庫、報告與 `.env` 已列入 `.gitignore`。

**資料庫內含完整 AI 對話內容與檔案路徑，任何情況下都不要提交至 GitHub。**

---

## 📄 授權

MIT License — 見 [LICENSE](LICENSE)。
