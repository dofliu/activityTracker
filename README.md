# 🌐 OmniContext — 個人全景活動追蹤與進行中工作智慧中樞

[![Language](https://img.shields.io/badge/Language-English%20%7C%20%E7%B9%81%E9%AB%94%E4%B8%AD%E6%96%87-orange)](README_en.md)
[![License: MIT](https://img.shields.io/badge/License-MIT-blue.svg)](LICENSE)
[![Python](https://img.shields.io/badge/Python-3.10%2B-blue)](https://www.python.org/)
[![FastAPI](https://img.shields.io/badge/FastAPI-0.110%2B-green)](https://fastapi.tiangolo.com/)

> **[English Documentation](README_en.md) | [繁體中文說明文件](README.md)**

**OmniContext** 是一個**本機優先（Local-First）、具有明確資料邊界**的個人上下文記憶中樞與工作進度追蹤系統。它捕獲跨平台 AI 對話（Claude Code、Codex、Antigravity、ChatGPT、Gemini 等）、程式碼提交、檔案與論文寫作異動、視窗時間分配，並整合 GitHub 倉庫與 Pull Request 狀態，最後由一位**只提案、不擅自行動的小秘書**把這些線索變成「現在該做什麼」。

它與單一 AI 的 memory／chat import 不同：**OmniContext 的 canonical context 屬於使用者與專案，不屬於任何一家 AI provider。** 完整定位與證據邊界見[產品定位](docs/PRODUCT_POSITIONING.md)。

隨時幫助你回答三個核心問題：

1. **「我現在正在進行哪些專案？」**
2. **「我上次做到哪裡、動了哪些檔案？」**
3. **「有哪些尚未收尾的未結事項（Open Loops）？」**

---

## 📊 目前狀態

**Personal Alpha — v1.3.0a5（已發佈為 [GitHub pre-release](https://github.com/dofliu/activityTracker/releases)，附 SHA-256 receipt）**

| 面向 | 現況 |
| :--- | :--- |
| 程式 | P0–P8 與 ADR-008 執行器全階段已落地；22 份 ADR 記錄每個決策的邊界 |
| 測試 | **68 個 contract test 模組、695 項**（694 passed + 1 skipped；不裝 `[rag]` extra 時 681 passed + 12 skipped）；Windows／Ubuntu／macOS × Python 3.10／3.12 CI 六個 job ＋ 一個「不裝 `[rag]`」job 全綠 |
| 資料 | SQLite schema migration **18/18**（append-only + checksum，升級前自動備份） |
| 發佈 | `release_ready: false` |

**剩下的缺口幾乎都不是「還沒寫的程式」，而是只能在使用者實機取得的收據。** 本專案不把「測試通過」當成「實機可用」——唯一還在擋發佈的**能力型**缺口是全天 coverage ledger 實測。

不必憑記憶：跑 `python main.py verify`（或看儀表板「06 系統設定 → 驗收中心」）就會列出每一項現在有沒有收據（[ADR-016](docs/ADR-016-acceptance-center.md)）。

**2026-09-16 專案檢視**：功能已經夠多，接下來是**減法**——刪死碼與未用依賴、RAG 改為選用安裝、合併兩套 LLM client 與兩套向量記憶，再把「讀本機 AI agent transcript」這個唯一無替代品的核心抽成獨立套件對外。功能候選（遠端存取、LINE 雙向、更多採集來源）暫停。**R0 已於同日完成：死碼與未用依賴移除、marked 本機化、CLI／API 同一組數字，以及知識庫依賴改為 `[rag]` 選用安裝（核心安裝 721 MB → 176 MB）；R1 已完成 D2（兩套 LLM client 合為 `core/llm_client.py`）、D3（活動來源收進 `core/activity_sources.py`）、D4（`server.py` 1,995 → 134 行，依領域切成 9 個 router）、D5（桌面通知併入 `ChannelAdapter`）與 D6（六層旗標收三層）；R2 已完成 D7（一份活動記憶，ADR-023）。** 評估全文見 [docs/REVIEW-2026-09-16-project-assessment.md](docs/REVIEW-2026-09-16-project-assessment.md)，三階段計畫見 [ROADMAP §13](ROADMAP.md#13-架構整頓與推廣方向2026-09-16-檢視)。

**文件入口：**[📚 文件總覽](docs/INDEX.md) · [使用手冊](docs/USAGE.md) · [開發規劃與成果](ROADMAP.md) · [待辦與判準](docs/TODO.md) · [機器可讀現況](STATUS.yaml) · [專案檢視 2026-09-16](docs/REVIEW-2026-09-16-project-assessment.md)

![OmniContext 架構與未來 Roadmap](docs/assets/omnicontext-architecture-roadmap-card-v1.png)

---

## 🌟 核心特色功能

```
┌──────────────────────────────────────────────────────────────────────────┐
│                        OmniContext 核心系統架構                          │
├──────────────────────────────────────────────────────────────────────────┤
│                                                                          │
│  [ 跨平台 AI 採集 ]      [ 本機檔案 / Git ]      [ GitHub 雲端整合 ]     │
│  • Claude Code 日誌       • Watchdog 檔案異動     • Public/Private repo  │
│  • Codex Sessions         • 遞迴 Git Scanner      • PR 狀態 / 分支流向   │
│  • Antigravity 對話       • 論文多檔案智能歸戶    • Actions CI 測試結果  │
│  • Chrome 擴充套件        • 本機 .ics 行事曆（唯讀）                     │
│          │                       │                       │               │
│          └───────────────────────┼───────────────────────┘               │
│                                  ▼                                       │
│                    [ 本機 SQLite 資料庫儲存 ]                            │
│             (omni_context.db · 本機儲存；cloud LLM 為 opt-in)            │
│                                  │                                       │
│          ┌───────────────────────┼───────────────────────┐               │
│          ▼                       ▼                       ▼               │
│  [ Web 視覺化儀表板 ]    [ DeskRAG 知識庫 ]      [ AI 摘要與主動提醒 ]   │
│  • 01 · 🤖 小秘書        • PDF/Office/Md 解析    • 多日自訂區間日報回顧  │
│    （桌面：問候＋焦點    • FastEmbed + ChromaDB  • 兩層增量微摘要        │
│     ＋記得一則＋交辦）   • Jieba + BM25 關鍵字   • 桌面通知／Telegram／  │
│  • 02 · 知識庫           • Hybrid RRF 混合檢索     LINE 多通道推播       │
│  • 03 · 進行中工作       • 多模型 SSE 串流問答   • 多供應商 (Ollama /    │
│  • 04 · Git 同步中心     • 常駐檢索 worker 隔離    Gemini/Claude/OpenAI) │
│  • 05 · 摘要與統計 · 檔案總管精準定位                                    │
│  • 06 · 系統設定（左欄 11 區塊：秘書與自動化／Telegram／LINE／           │
│         監控路徑／採集來源／摘要與 LLM／使用時間／GitHub／               │
│         維運：即時情報流／系統健康／驗收中心）                           │
└──────────────────────────────────────────────────────────────────────────┘
```

> 下面是「有什麼」。**「怎麼用」一律見 [使用手冊](docs/USAGE.md)，「為什麼這樣設計」見對應 ADR。**

### 1. 🎯 專案根目錄智能歸戶（Hierarchy Project Resolver）

* **消除子目錄碎片化**：自動將巢狀目錄（如 `core/`、`synthesizer/`、`Draft_Paper/`）歸戶至真實的專案或論文根名稱（如 `activityTracker`、`AI_PapersResearch`）。
* **工作階段多檔案聚合**：同一階段動到的多個檔案在專案卡上整合成單一條目，展開可看每個檔案的字數變更與路徑。

### 2. 🤖 跨平台 AI 對話全景記錄（來源可追溯）

* **本機 CLI / IDE Agent**：Claude Code（`~/.claude/projects/`）、Claude Desktop local-agent、Codex（`~/.codex/sessions/**`）、Antigravity（`.gemini/brain/**`）。
* **瀏覽器擴充套件（Chrome MV3）**：ChatGPT、Gemini、Claude.ai；以獨立 ingest token 實施 write-only capability boundary，並以穩定 turn key upsert。
* **明確邊界**：一般 Claude Desktop 雲端聊天只偵測 cache 存在，**不解析 Chromium LevelDB，也不宣稱已取得對話內容**。
* **來源故障隔離**：單一來源遇到權限或解析錯誤時只跳過該來源，不讓整輪採集中止。

### 3. 🐙 GitHub 雲端追蹤與 🔁 本機 Git 同步中心

* **雙軌認證**：自動探測本機 `gh` CLI 憑證，或使用 Fine-Grained／Classic PAT。
* **雲端 metadata**：同步所有 Public／Private 倉庫與 PR 的標題、狀態、分支流向、CI 結果與審查狀態。
* **本機同步中心（逐項確認）**：顯示各 repo 的 branch、upstream、ahead/behind 與 worktree 變更，可 `Fetch` → 條件式 `Pull --ff-only`／`Commit staged`／`Push`。
* **安全預設**：沒有排程自動同步、不會 `git add`、不提供 force push；灰掉的按鈕會**帶著這個 repo 的實際數字**說明為什麼不能動。
* **全覽與批次**：一張表列出全部 repo，批次 Pull／Push 先列清單讓你確認、執行時逐一重檢；批次 Push 另有開關且預設關閉。小秘書可排程 L0 `repo_sync_report` 產生每日同步報告。詳見 [ADR-011](docs/ADR-011-safe-local-repository-sync.md)。
* **Repo Onboarding**：資料夾尚未 `git init`、repo 沒有 remote、GitHub repo 尚未 clone——三種情境各有單一目標的確認式流程，不覆寫非空目錄、不批次 create/clone、永不代為 push。

### 4. ⏱️ 使用時間、背景任務與 coverage

* **每日主要介面使用時間**：Claude、Codex、ChatGPT、Gemini、Antigravity、VS Code 等的 foreground active time 與 AI turns，可設每日目標、里程碑、通知語氣、quiet hours 與 cooldown。
* **可驗證背景 Agent／CLI 任務時間**：只有 prompt start 與明確 final completion timestamp **成對存在**才結算；平行任務以時間聯集計算，避免 double counting（[ADR-010](docs/ADR-010-verified-background-agent-task-time.md)）。
* **continuous coverage ledger**：記錄採集器實際被觀測運作的時間段；覆蓋率達門檻才顯示 `observed`，否則顯示 `partial` 與實際比例，**中斷或休眠的時間永不回補**。
* **邊界**：這些數值只代表已觀察到的前景時間，**不等於生產力或實際工時**；`FOCUS`／`WEB`／`LOG` 三種訊號互不替代。

### 5. 🧠 記憶層：Semantic Index、`omni ask` 與 Related History

* 以 loopback Ollama `bge-m3` 將 AI turns、Git commits、檔案 metadata、Open Loops 與 Project State 建立 1024 維**本機**索引，資料不送 cloud provider（[ADR-005](docs/ADR-005-local-semantic-index-and-ask.md)）。
* `content_hash + embedding_model` 增量更新；每筆保留 SQLite `source_ref`、project、timestamp、trust status 與 embedding input 降級模式。
* `omni ask` 可只看 retrieval evidence，也可由本機 Ollama 生成含 `[S1]` 引用的答案。
* **Related History 與 Work Sessions**：依 project + inactivity gap 整理為 derived work session，不新增資料表、不改寫原始事件（[ADR-006](docs/ADR-006-derived-context-sessions-and-related-history.md)）。
* **邊界**：similarity 不是來源真實性或 coverage 的證明；session span 只是首末事件時間差，不代表實際工時或專注品質。

### 6. 📚 DeskRAG 本地知識庫與文件智慧問答（選用安裝：`pip install "omnicontext[rag]"`）

* **單一 Web 入口、獨立索引 worker**：Dashboard 與 API 維持在 `http://127.0.0.1:8765`；掃描、解析、embedding、刪除與空間維護由另一個本機 process 執行，長時間索引不佔用主服務（[ADR-009](docs/ADR-009-deskrag-worker-index-lifecycle.md)）。
* **全方位解析器**：PDF（PyMuPDF，保留頁碼）、Word／PowerPoint／Excel、Markdown 與程式碼、WebVTT 逐字稿，另把 Project State 與 Open Loops 併成虛擬切片。
* **混合檢索**：FastEmbed（ONNX，`BAAI/bge-small-zh-v1.5`）+ ChromaDB 向量庫，Jieba + BM25Okapi 關鍵字，支援 Hybrid RRF、Weighted Fusion、Vector Only、BM25 Only。
* **常駐檢索 worker**：檢索在子程序執行，主服務**不載入** Chroma／BM25／embedding（有乾淨直譯器契約測試把關）；啟動後背景預熱，逾時即終止並自動重啟。
* **多模型問答**：本機 Ollama 或雲端 Gemini／Claude／OpenAI，SSE 逐字串流與來源引文卡片，Windows 可從引文一鍵在檔案總管定位該檔。
* **受控生命週期與真實容量**：移除資料夾索引與清空全部索引都要明確確認，且不刪來源檔或對話；容量數字來自 worker 的最近驗證收據，未驗證時顯示「待驗證」，**不以估算值冒充實測**。
* **空間回收**：Chroma 的 `delete_collection` 只做邏輯刪除——刪掉索引不會讓磁碟變小。「🧹 回收 Chroma 空間」會分開回答兩件事：邏輯上不見了嗎、磁碟真的少了嗎；讀不到內部結構就一律不刪（fail-closed）。

### 7. 🧩 主動小秘書：建議 → 批准 → 代辦

* **Proposal-only 基座**：把 Project State、actionable Open Loops 與診斷訊號整理成**附 evidence refs** 的下一步建議；規則引擎不寫事件資料、不執行 command（[ADR-007](docs/ADR-007-proposal-only-secretary.md)）。
* **LLM 參考註解（選用，預設關閉）**：LLM 只能為既有建議加一句判斷提示，**不能增刪或執行**；不可用時自動回退純規則。
* **分級執行器 L0／L1／L2（選用，三個獨立開關，全部預設關閉）**（[ADR-008](docs/ADR-008-gated-agent-executor.md)）：
  * **L0／L1**：逐項批准後代辦白名單動作（產生 Handoff、`git fetch`、fast-forward pull、標記 stale）。execute API 只接受 `proposal_id`，動作由 server 白名單 template 決定、不開 shell，需獨立 execution token，每次留 audit receipt。
  * **L2 調度本機 agent CLI**：三道門（token ＋ 單鍵批准 ＋ 一次性 6 碼確認碼）＋冷卻後，調度**你本機已登入的** Claude Code／Codex CLI 起草行動計畫。子行程 argv 白名單禁 shell、cwd 限該專案、環境變數 allowlist 重建（**任何 API key 都不轉發**）、逾時即 kill、執行中可取消。
  * **L2 寫入模式**：兩段式批准——你先讀過 agent 起草的計畫，再讓 CLI 依**那份計畫全文**改檔；dispatch 前 worktree 必須乾淨，**永不 commit／push**，改動留給你 `git diff` 驗收。
* **可排程的 L0 任務**：晨間包、每日工作誌、同步報告、週／月報 rollup、每週回顧、會議紀錄等**唯讀** template 可自訂排程；**L1／L2 永遠不可排程**（模組載入即強制，有 allowlist 測試把關）。
* **文件落後偵測**：比對文件檔最後異動與其後的 commit 數，把「文件落後了」變成一張可執行的卡（[ADR-021](docs/ADR-021-docs-behind-code.md)）；沒有文件紀錄的 repo 一律不提。

### 8. 💬 小秘書的個人化：它記得、也照你說的做

* **記憶區（大腦）**（[ADR-012](docs/ADR-012-secretary-memory.md)）：對話框打「記下來：…」「偏好：不要提醒 repo_needs_push」「決定 @專案：…」直接寫進本機筆記；每次提問自動帶入今日狀態、前三個提案與筆記（有字數上限、附收據、可檢視），秘書觀察可一鍵刪除。
* **每日工作誌**：L0 template `daily_digest` 每天把活動 reduce 成一則工作誌與幾則專案觀察——**採集到 ≠ 秘書知道**，要秘書記得就得有東西進 `secretary_notes`。
* **模式感知提案**（[ADR-017](docs/ADR-017-pattern-aware-proposals.md)）：用（專案 × 日）活動矩陣看出「你有 N 天在工作但還沒有每日排程」「X 被冷落了」，並為主線專案加權。**只算已結束的日子。**
* **宣告式個人檔案**（[ADR-018](docs/ADR-018-declared-profile.md)）：「偏好：優先：<專案>」「偏好：語氣：簡潔」——**你自己說的，不是推測的**；宣告的權重壓過推出來的，語氣只改措辭、不改數字。
* **秘書桌面（01 是首頁）**（[ADR-019](docs/ADR-019-secretary-desk-home.md)）：由確定性規則挑「焦點一張」與「記得一則」，工具自身的提醒（例如 Extension heartbeat）不佔焦點；完整清單降為詳情。
* **每週回顧：說的 vs 做的**（[ADR-020](docs/ADR-020-weekly-review-said-vs-done.md)）：把「你說 X 優先」與「上週 X 只有 1 天在動」兩個事實並列——**兩個事實放在一起就是洞見，不需要推測原因**。
* **問候卡**：01 最上方「🤗 小秘書的話」說明今天做了什麼再接一句鼓勵。每個數字都能回溯到資料表；沒被採集到的（例如郵件）卡上如實寫明。LLM 潤飾預設關閉，且**不得多出統計裡沒有的數字**，違反就退回規則版。

### 9. 📅 行事曆與會議秘書

* **本機行事曆（唯讀 .ics）**（[ADR-015](docs/ADR-015-local-calendar-source.md)）：把 Outlook／Google／Apple 匯出或同步的 `.ics` 放進本機資料夾即可；晨報多一段「📅 今日行程」、首頁多一行「下一場 14:00 …」。**只取時間／標題／地點／狀態**，描述、與會者、連結一律不落地；**不連任何雲端 API**；沒設路徑就是停用。
* **會議秘書（第一層：會後逐字稿）**（[ADR-022](docs/ADR-022-meeting-secretary.md)）：把 Teams 等匯出的逐字稿放進一個資料夾，`meeting_notes` 會產出摘要與**候選**待辦，並依時間配對到當天的行事曆事件。
  * 「知道你在開會」只用**兩個確定性訊號**：行事曆上正在進行的事件 ＋ 前景視窗是會議軟體（只取應用程式名稱）。
  * **摘要預設走本機 provider（`ollama`）**；改用雲端就等於把與會者的話送到那家供應商，設定頁與卡片都會明白寫出來。prompt 與回應原文一律不落地。
  * **候選待辦要你點了才成為未結事項**——沒點的不進入任何計數。
  * **刻意不做**：錄音、讀會議軟體視窗內容、呼叫 Teams／Graph API、自動下載逐字稿。**即時字幕／翻譯是第二層**，需另寫 ADR 並通過 ADR-022 D6 的五道門。

### 10. 🔔 通知：桌面、Telegram、LINE 與每日入口檔

* **Windows 原生桌面通知**：直接呼叫 WinRT Toast，**不需安裝套件、不需申請帳號**——晨間簡報、今日回顧、停滯提醒；`--dry-run` 可先預覽。
* **每日入口簡報**：自動產出 `OMNICONTEXT_TODAY.md` / `.html`，HTML 版每 5 分鐘自動刷新，可設為瀏覽器首頁。
* **多通道推播**（[ADR-014](docs/ADR-014-multi-channel-push-and-arm-code.md)，皆預設關閉）：同一份內容自動用各平台格式呈現。**能力邊界**：LINE Messaging API 沒有輪詢介面，接收訊息需要公開 webhook（會打破「只在 127.0.0.1」的邊界），所以 **LINE 只做推播**。
* **手機上的小秘書（Telegram，預設關閉）**（[ADR-013](docs/ADR-013-telegram-secretary-chat.md)）：在綁定的對話裡打字就是提問，走與儀表板同一條管線；`/today` `/notes` `/status` `/proposals` 為指令，inline 可批准 L0／L1。**邊界**：這是唯一會把提問與回答送出本機的通道，因此預設關閉。
* **一次性解鎖碼**：`/arm` 用儀表板簽發的 6 位數短效碼（單次、5 分鐘失效、猜錯即焚），手機不必持有長期 secret；`/disarm` 永遠可用。

### 11. ⚡ 摘要引擎：自訂區間與兩層增量

* **任意日期範圍報告**：Web UI 選起訖日期或用 `今日`／`昨日`／`本週`／`近 7 天`／`近 30 天` 快捷標籤。
* **兩層增量（map-reduce）日報**：每次週期 checkpoint 後用本機 Ollama 把該時段壓成 ≤100 字微摘要（零 API 成本），日報只讀「微摘要時間軸＋缺漏時段原文回退」——雲端 token 用量約降一個數量級，Ollama 不可用時自動回退原文，**日報永遠可產生**。
* **多供應商**：預設本機 Ollama；亦支援 Google Gemini、Anthropic Claude、OpenAI。`python main.py llm-test` 可診斷各 provider 連線。
* **未結事項萃取**：摘要時自動提煉 Open Loops 並同步至清單供勾選結案。

### 12. ✅ 驗收中心：還有哪些收據沒拿到

* 把 [docs/TODO.md](docs/TODO.md) A 段每一項的完成判準變成**可重跑的唯讀查詢**（[ADR-016](docs/ADR-016-acceptance-center.md)）：`python main.py verify` 或「06 系統設定 → 驗收中心」。
* **只讀不做**：不替你執行任何驗收動作、不跑 git、不連網。
* 狀態字彙嚴格區分「**沒發生**」與「**查不到**」；人工署名永不覆蓋機器判定；記憶體內才有的數字（例如檢索 worker 狀態）標為 `runtime_only`，而不是謊報「還沒做」。

### 13. 🌐 介面：雙語 × 明暗 × 配色

* 頂列一鍵切換 `🌐 English` / `🌐 繁體中文`。
* 外觀是兩個獨立軸：`data-theme`（深／淺）× `data-accent`（火影橘／森林綠／海洋藍），可組成 6 種外觀。
* 偏好只存瀏覽器 `localStorage`，**不寫入 `config.yaml`、不送往後端**。

---

## 🚀 快速開始

需求環境：**Python 3.10+**

```console
# 複製專案
git clone https://github.com/dofliu/activityTracker.git
cd activityTracker

# Source checkout／開發模式（含知識庫依賴與測試工具）
python -m pip install -e ".[dev]"
# 只要核心（採集、秘書、Git 同步、通知；不含知識庫索引）：約 170 MB
#   python -m pip install -e .
# 之後想開知識庫再補：python -m pip install -e ".[rag]"

# 建立本機設定、目錄與 browser ingest token
python main.py init --watch "/your/project/root"

# 啟動 Web 儀表板與背景採集
python main.py
```

啟動後開啟 **[http://127.0.0.1:8765](http://127.0.0.1:8765)**。

若使用已建置的 Alpha wheel：

```console
python -m pip install omnicontext-1.3.0a5-py3-none-any.whl            # 核心
python -m pip install "omnicontext-1.3.0a5-py3-none-any.whl[rag]"     # 核心＋知識庫（DeskRAG）
omnicontext init --watch "/your/project/root"
omnicontext assets-status
```

**知識庫（DeskRAG）是選用安裝**：`[rag]` extra 帶入 ChromaDB／FastEmbed／BM25／jieba 與 PDF／Office 解析器（約 550 MB）。沒裝時其他功能照常，「02 知識庫」會直接說缺哪些套件與安裝指令，對話仍可用但不帶文件脈絡。

Installed wheel 預設把 config、database 與 reports 放在使用者可寫的 `~/OmniContext`，不寫入 `site-packages`；可用 `OMNICONTEXT_HOME` 或 `OMNICONTEXT_CONFIG` 覆寫。

### LLM 金鑰（選用）

預設全部走本機 Ollama。若要用雲端 provider，把金鑰放在**作業系統環境變數**——`config.yaml` 只保存 `api_key_env` 變數名稱，不保存明文金鑰：

```powershell
[Environment]::SetEnvironmentVariable("GEMINI_API_KEY", "your-gemini-api-key", "User")
```

Windows 上即使 OmniContext 的父程序較早啟動，後端也會回讀 User／Machine environment；設定後可在「設定 → 摘要與 LLM」按「重新檢查」。

> **Extension 配對、里程碑設定、備份與故障排查的完整流程，見 [docs/USAGE.md](docs/USAGE.md)。**

---

## 💻 CLI 指令

Installed wheel 可將 `python main.py` 改為 `omnicontext` 或較短的 `omni`。

| 指令 | 說明 |
| :--- | :--- |
| `python main.py` / `run` / `web` | 啟動 Web 儀表板與背景採集服務 |
| `init` | 建立／更新跨平台設定與 extension token（`--show-token` 顯示金鑰） |
| `now` | 一秒查詢當前進行中專案、最近活動與未結事項 |
| `resume` | 產出專案接續 Context Handoff（`--copy` 一鍵複製貼入 AI） |
| `summary` | 生成 AI 摘要日報（支援自訂區間與強制更新） |
| `checkpoint` | 手動打包最近時段活動為 Markdown 快照 |
| `brief` | 產出每日簡報檔案至每日入口目錄 |
| `notify` | 手動觸發提醒（`--dry-run` 預覽、`--channel` 指定通道） |
| `status` | 查看資料庫累積指標與採集器運行狀態 |
| `github status` / `github sync` | 查看或同步 GitHub 倉庫與 PR |
| `index` / `ask` | 建立本機 semantic index／查詢跨 AI 歷史並列出來源 |
| `sessions` / `recall` | 整理 derived work sessions／查詢相似歷史（query 不保存） |
| `open-loop` / `open-loop-reconcile` | 人工複核 Open Loop lifecycle／回填 fingerprint 收斂重複項 |
| `verify` | **驗收中心**：查 TODO A 段每一項的本機收據（唯讀，不做任何動作） |
| `llm-test` | 診斷各 LLM provider 連線與設定 |
| `backup` / `restore-drill` | 建立並驗證備份／在隔離暫存 DB 驗證，不覆蓋 live DB |
| `migration-status` | 唯讀查看目前／最新 schema version 與相容性 |
| `maintain` / `heal` / `wal-checkpoint` | 資料庫生命週期維護／採集器自我修復／WAL 截斷 |
| `assets-status` / `extension-path` | 檢查 packaged assets／顯示 Extension Load unpacked 目錄 |

---

## ⚙️ 設定

`main.py init` 會依 **[config.example.yaml](config.example.yaml)** 產生本機 `config.yaml`；該範本是**設定項目的唯一權威來源**，每個區塊都有行內註解說明邊界，這裡不重複列出。多數設定可在儀表板「06 系統設定」即時儲存與熱更新。

第一次安裝通常只需要動這幾項：

| 設定 | 作用 |
| :--- | :--- |
| `project_resolution.search_roots` | 你的專案根目錄（決定歸戶結果，**建議明確設定**） |
| `watchers.file_watcher.watch_directories` / `extensions` | 要監控的資料夾與副檔名 |
| `watchers.git_watcher.repositories` | 要遞迴掃描的 Git 根目錄 |
| `synthesizer.provider` | 摘要供應商（預設 `ollama` 全本機） |
| `integrations.github.token` | 留空時自動使用本機 `gh auth token` |

**危險能力全部預設關閉**：秘書執行器（含只排 L0 的自訂排程）、L2、L2 寫入、Telegram 對話、Telegram inline 批准（含 `/arm`）、LINE、問候卡 LLM 潤飾；行事曆與會議秘書預設開但沒設路徑就等於停用。

---

## 🧩 Chrome 瀏覽器擴充套件

`python main.py extension-path` 顯示 Load unpacked 目錄，`python main.py init --show-token` 取得 ingest token 貼進 popup。只有帶有效 token 的支援網站事件才能寫入本機 `/api/v1/events/ai`。

配對完成後可從 `http://127.0.0.1:8765/extension-monitor` 查看逐站 observed 狀態。**完整步驟與 live 驗證流程見 [docs/USAGE.md §3](docs/USAGE.md)。**

---

## 📂 專案檔案架構

```text
activityTracker/
├── main.py                     # 主入口與 CLI 分發
├── config.example.yaml         # 設定範本（init 依此產生本機 config.yaml）
├── pyproject.toml              # 跨平台安裝、CLI entry point 與 pytest 設定
├── README.md / README_en.md    # 繁中／English 說明
├── ROADMAP.md / STATUS.yaml    # 開發規劃與成果／機器可讀現況快照
│
├── docs/                       # 📚 文件（入口見 docs/INDEX.md）
│   ├── USAGE.md                # 使用手冊
│   ├── TODO.md                 # 待辦與完成判準
│   ├── NEXT_SESSION.md         # 開發接手指南
│   ├── ADR-001 ~ ADR-022       # 架構決策紀錄（為什麼這樣設計、邊界在哪）
│   └── archive/                # 已歸檔的一次性規劃書與完成報告
│
├── core/                       # 核心服務
│   ├── server.py               # FastAPI app 組裝：安全邊界、靜態資源、router 掛載
│   ├── api/                    # 依領域切分的 9 個 APIRouter（路由表由快照測試鎖住）
│   ├── schemas.py              # API 請求結構（33 個 Pydantic model）
│   ├── ingest.py               # AI 事件的 turn_key 與 response_status 判定
│   ├── llm_client.py           # 唯一的 LLM client：Ollama / Gemini / Claude / OpenAI，同步與串流
│   ├── activity_sources.py     # 唯一的活動來源定義：哪些表算活動、專案歸戶、日界
│   ├── manager.py              # 採集器統籌與 supervise_and_heal 自我修復
│   ├── database.py / migrations.py / models.py   # SQLite 與 append-only migration
│   ├── security.py / secret_resolver.py          # Origin 邊界與金鑰解析
│   ├── data_lifecycle.py       # 線上備份、WAL checkpoint、修剪與 integrity receipt
│   ├── project_engine.py / project_paths.py      # 專案歸戶與根目錄定位
│   ├── semantic_index.py / context_memory.py     # 本機 embeddings 與 Related History
│   ├── handoff_engine.py       # Provider-neutral Context Handoff
│   ├── proactive_secretary.py / secretary_advisor.py   # 提案引擎與 LLM 註解層
│   ├── agent_executor.py / agent_dispatch.py / scheduled_tasks.py  # L0/L1/L2 與排程
│   ├── secretary_memory.py / secretary_profile.py      # 記憶區與宣告式個人檔案
│   ├── secretary_home.py / secretary_greeting.py / secretary_packs.py  # 桌面／問候／每日包
│   ├── activity_digest.py / activity_patterns.py / weekly_review.py    # 工作誌／模式／週回顧
│   ├── meeting_transcripts.py  # 會議秘書（WebVTT、配對、事實閘、候選待辦）
│   ├── docs_freshness.py       # 文件落後程式偵測
│   ├── ics_parser.py / calendar_agenda.py        # 本機 .ics 行事曆（唯讀）
│   ├── repo_sync.py / repo_onboarding.py / repo_sync_report.py  # Git 同步中心
│   ├── acceptance.py           # 驗收中心（TODO A 段的可執行副本）
│   ├── usage_analytics.py / capture_coverage.py / coverage_ledger.py
│   ├── background_tasks.py / triage_signals.py / status_draft.py
│   └── platform_services.py / runtime_paths.py / fs_utils.py / time_utils.py
│
├── rag/                        # 📚 DeskRAG 子系統
│   ├── router.py               # /api/v1/rag/* REST API 與 SSE 串流問答
│   ├── scanner.py / index_worker.py / jobs.py / lifecycle.py   # 受控索引 worker
│   ├── retrieval_worker.py / retrieval_client.py               # 常駐檢索 worker
│   ├── parsers/ chunker.py embeddings.py vector_store.py retriever.py
│   ├── storage.py              # 容量報告與 Chroma 空間回收
│   ├── report_indexer.py       # 秘書寫出的報告檔切片（活動記憶在 core/semantic_index）
│
├── watchers/                   # 多源採集器
│   ├── file_watcher.py git_watcher.py window_watcher.py
│   ├── agent_log_watcher.py    # Claude Code/Desktop、Codex、Antigravity
│   ├── calendar_watcher.py     # 本機 .ics
│   └── browser_extension/      # Chrome MV3 擴充套件
│
├── synthesizer/                # 摘要與排程引擎
│   ├── aggregator.py prompt_templates.py scheduler.py
│   └── micro_summarizer.py / rollup.py    # 兩層增量微摘要與週/月報
│
├── notifiers/                  # 通知推播
│   ├── messages.py / channels.py          # 內容與呈現分離、adapter 能力宣告
│   ├── desktop_notifier.py                # Windows WinRT Toast transport（零依賴）
│   ├── telegram_chat.py / telegram_approvals.py / telegram_setup.py
│   └── line_setup.py / secretary_push.py
├── integrations/github_client.py          # GitHub API Client
├── exporters/daily_brief.py               # OMNICONTEXT_TODAY.md/.html
│
├── web/                        # 儀表板前端（6 分頁 + extension-monitor）
│   └── index.html / app.js / style.css
│
├── scripts/                    # 驗證、清理、autostart 與 E2E 腳本
├── tests/                      # 68 個 contract test 模組（695 項）
├── logs/checkpoints/           # 週期性活動快照
└── reports/                    # 每日／區間 Markdown 報告
```

---

## 🗺️ 這個專案的差異化定位

市面同類工具（ActivityWatch、RescueTime、Timing）追蹤的是**時間**；Rewind、Screenpipe 錄螢幕再做 OCR，隱私成本與資源消耗都高。

**目前沒有主流工具在讀本機 AI agent 的 transcript。** `~/.claude/projects/`、`~/.codex/sessions/`、`.gemini/antigravity/brain/` 這些檔案就在硬碟上，不需錄螢幕、不需額外權限，而裡面記錄的是真正的思考過程——問了什麼、AI 怎麼答、最後決定怎麼做。

從「日誌」到「記憶」是這個專案的主線：現階段的重點不是繼續擴大收集，而是讓既有資料**可被檢索、可被秘書使用**。

2026-09-16 的檢視把這句話再往前推一步：**現階段的重點也不是繼續加功能，而是做減法**——把 transcript 解析抽成別人能單獨安裝的套件、讓預設安裝不帶 RAG 依賴鏈、合併重複的子系統。詳見 [專案檢視](docs/REVIEW-2026-09-16-project-assessment.md) §3／§6。

> 收集越多不等於越有用：檔案事件曾從 3,575 筆噪音 → 4,327 筆 → 收斂至 789 筆。
> **新增採集來源必須先通過「能否改變決策」的檢驗。**

完整階段規劃、實測數據對比與成果紀錄見 **[ROADMAP.md](ROADMAP.md)**；下一階段方向與取捨見 §12。

### 目前的使用前提

本專案現階段為**個人優先（personal-first）**設計：

* 視窗採集、桌面通知與開機排程僅支援 **Windows**；其餘功能跨平台（CI 涵蓋 Windows／Ubuntu／macOS）。
* `project_resolution.search_roots` 未設定時才沿用 file/Git watcher 的 roots，首次安裝仍應明確設定自己的目錄。
* PyPI 發佈不在目前範圍，只發 GitHub pre-release（wheel/sdist + SHA-256 receipt）。

---

## 🔒 隱私與安全聲明

* **事件本機儲存**：活動事件保存在本機 SQLite（`omni_context.db`），不含第三方 analytics telemetry。
* **LLM 資料邊界**：選擇 Gemini／Anthropic／OpenAI 產生摘要時，組裝後的工作脈絡會傳送至該 provider；**選擇 Ollama 才是完整本機推論**。會議逐字稿摘要的 provider 另有獨立設定，預設 `ollama`。
* **Local API**：deny-by-default Origin boundary、loopback-only 預設、敏感設定遮蔽與 browser-extension ingestion capability（[ADR-001](docs/ADR-001-p2-5-trust-boundary.md)）。
* **資料可信度**：canonical AI event 必須具備 `turn_key`、source provenance 與 `response_status`；partial／legacy 回應不作為摘要或 handoff 結論。
* **備份生命週期**：`backup` 使用 SQLite Online Backup API 並輸出 integrity／SHA-256；`restore-drill` 於隔離暫存 DB 驗證 schema 與 row counts，**不覆蓋 live DB**。
* **Schema migration**：append-only registry 保存 version/name/checksum，升級前自動產生 verified backup；checksum mismatch 或未知較新版本 **fail-closed**（[ADR-003](docs/ADR-003-versioned-sqlite-migrations.md)）。
* **Artifact 邊界**：wheel/sdist content receipt 會拒絕夾帶 `config.yaml`、SQLite database 或 local secrets。
* **Git 提交防護**：資料庫檔案、API 金鑰與個人 Markdown 報告已預設加入 `.gitignore`。

---

## 📄 授權條款

本專案採用 [MIT License](LICENSE) 授權。
