# 🌐 OmniContext: 個人全景活動追蹤 & AI 智慧回顧中樞

> 一個完全由你掌控、注重隱私的個人上下文記憶系統。全天候自動捕捉跨平台 AI 對話、論文寫作、程式碼開發、檔案異動與時間分配，並在每日結算時透過 LLM 自動產出結構化的工作回顧、專案進度與明日提醒。

---

## 🌟 4 大核心特色

1. **視覺化 Web 儀表板 (一站式監控、配置與閱讀)**
   * 內建精美現代化 Web UI (`http://127.0.0.1:8765/`)，直接在瀏覽器上查看即時活動流 (Live Feed)、今日統計指標與歷史 Markdown 報告。
2. **動態選擇監控項目 (免重啟設定)**
   * **論文與文件**：視覺化新增/刪除監控資料夾，自訂副檔名（`.tex`, `.docx`, `.md`, `.pdf`, `.py` 等）。
   * **程式碼倉庫**：自由增減 Git 專案庫路徑。
   * **AI 工具與瀏覽器**：個別切換 Gemini, ChatGPT, Claude, Manus, Claude Code, 視窗焦點等開關。
   * **模型設定**：切換 Gemini, Claude 3.5, GPT-4o 或本機 Ollama，並設定定時排程。
3. **一鍵「開始監控 / 停止監控」控制**
   * 儀表板提供即時狀態指示燈與開關按鈕，隨時可一鍵暫停或恢復背景監控。
4. **週期性活動快照日誌 (Checkpoint Logs) 與 AI 每日總結**
   * **固定週期日誌**：每隔固定時間（如每 2 小時）自動打包最新活動日誌存檔於 `logs/checkpoints/`。
   * **智慧回顧總結**：支援每晚定時自動調用 AI 或在網頁上一鍵點擊 **「⚡ 生成今日摘要」**。

---

## 🚀 快速上手使用指南

### 1. 啟動 Web 視覺化儀表板與監控服務

```bash
cd D:\Project_CodingSimulation\PersonalHelper\activityTracker
python main.py
```
* 打開瀏覽器訪問 **`http://127.0.0.1:8765`** 即可進入全功能儀表板！

### 2. 安裝 Chrome / Edge 瀏覽器擴充套件 (用於擷取 ChatGPT / Gemini / Claude / Manus)

1. 打開 Chrome 或 Edge，前往 `chrome://extensions/`（或 `edge://extensions/`）。
2. 開啟右上角的 **「開發人員模式 (Developer Mode)」**。
3. 點擊 **「載入未封裝項目 (Load unpacked)」**。
4. 選擇資料夾：`D:\Project_CodingSimulation\PersonalHelper\activityTracker\watchers\browser_extension`。

---

## 💻 CLI 指令操作

除了網頁介面外，你也可以透過終端機執行各項功能：

| 指令 | 說明 |
| :--- | :--- |
| `python main.py` | 啟動 Web 儀表板與後台監控服務 (`http://127.0.0.1:8765`) |
| `python main.py summary --force` | 手動觸發 AI 生成今日摘要報告 |
| `python main.py checkpoint --hours 2` | 手動產出最近 2 小時的活動快照日誌 |
| `python main.py status` | 查看當前資料庫累積事件統計與監控狀態 |
| `python main.py seed-demo` | 寫入測試範例數據以供體驗 |

---

## 📂 專案目錄結構

```text
omni-context/
├── config.yaml                     # 系統全域設定檔 (支援 Web UI 熱更新)
├── requirements.txt                # Python 套件依賴清單
├── main.py                         # 系統主入口與 CLI 工具
│
├── core/                           # 核心服務
│   ├── manager.py                  # 集中管理所有 Watcher 生命週期
│   ├── server.py                   # FastAPI REST API & Web 靜態伺服器
│   ├── config.py                   # 設定檔載入器
│   ├── database.py                 # SQLite ORM 與 Session 管理
│   └── models.py                   # 事件資料模型 (AI, File, Git, Window, Summary)
│
├── web/                            # Web 視覺化儀表板前端
│   ├── index.html                  # 儀表板首頁 (支援 Tabs 導航)
│   ├── app.js                      # 前端控制邏輯與即時輪詢
│   └── style.css                   # 現代卡片式風格樣式
│
├── watchers/                       # 數據採集器
│   ├── browser_extension/          # Chrome / Edge MV3 擴充套件 (Gemini, ChatGPT, Claude, Manus)
│   ├── file_watcher.py             # 論文與檔案異動監聽器
│   ├── git_watcher.py              # Git 倉庫 Commits 掃描器
│   ├── window_watcher.py           # 視窗焦點與時間統計
│   └── agent_log_watcher.py        # 本機 Agent (Claude Code / Antigravity) 日誌解析器
│
├── synthesizer/                    # 智慧總結與排程引擎
│   ├── aggregator.py               # 每日數據聚合、Checkpoint 日誌與 Prompt 構建
│   ├── prompt_templates.py         # 專業每日回顧結構化 Prompt
│   ├── llm_client.py               # 支援 Gemini / Claude / GPT / Ollama 的多模型客戶端
│   └── scheduler.py                # 支援每日定時與週期性 Checkpoint 排程
│
├── logs/checkpoints/               # 週期性活動快照日誌目錄
└── reports/                        # 每日生成的 Markdown 報告存檔目錄
```
