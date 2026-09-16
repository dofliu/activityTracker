# 專案檢視 2026-09-16：現況、價值評估與架構體檢

> 這是一份**當日快照**（同 `VERIFICATION-2026-08-25-next-stage.md` 的性質），回答三個問題：
> 現在做到哪、值不值得推廣（用處／學術／教學）、架構哪裡該改或該刪。
> **後續行動不寫在這裡**：整頓方向見 [ROADMAP.md §13](../ROADMAP.md#13-架構整頓與推廣方向2026-09-16-檢視)，
> 待辦與完成判準見 [TODO.md](TODO.md) B／D 段。
>
> 數字來源：本次在乾淨容器（Python 3.11、Linux）重新安裝並跑完整測試；程式規模用 `wc -l`；
> 架構結論由兩輪獨立程式碼審查得出，每一項都回到原始碼核對過（附檔案：行號）。

## 1. 一分鐘結論

- **進度**：程式面規劃的 P0–P8 與 22 份 ADR 全部落地，626 項測試在容器內全綠（625 passed ＋ 1 conditional skip）。
  `release_ready` 仍為 false，卡的是使用者實機收據（TODO A 段），不是程式。
- **值得推廣的部分只有一個核心**：**讀本機 AI agent 的 transcript**（Claude Code／Codex／Antigravity）並還原成有 provenance 的工作脈絡。
  這件事目前沒有主流工具在做，而且不需錄螢幕、不需額外權限。其他部分（RAG 知識庫、Git 同步中心、Telegram／LINE、會議秘書）市面都有替代品。
- **現在的形狀不適合推廣**：安裝 800 MB 起跳、449 行設定檔、視窗採集與桌面通知綁 Windows、
  一個人兩週用 AI 協作寫出約 4.4 萬行程式，留下了可量化的架構債（下方 §4）。
- **學術價值目前是「有題目、沒證據」**（N＝1），**教學價值反而最高**——ADR／收據文化／閘門式執行器是現成的教材，
  這個 repo 本身也是「AI 協作開發兩週後架構會長成什麼樣」的一手案例。
- **建議**：先做一輪**減法**（刪死碼、把 RAG 變成選用依賴、合併重複子系統），再把 transcript 解析抽成獨立套件對外，
  而不是繼續加功能。功能候選 C5／C6／C3 全部暫停。

## 2. 現況與進度（實測）

| 面向 | 數字 | 備註 |
| :--- | :--- | :--- |
| 程式規模 | core 20.8k／web 8.5k／rag 4.4k／watchers 3.0k／notifiers 2.8k／synthesizer 1.9k／scripts 1.6k／main.py 1.1k 行 | 不含測試約 4.4 萬行 |
| 測試 | 62 個模組、626 項；容器實跑 **625 passed, 1 skipped, 52 s** | 與 README／STATUS 記載一致 |
| API | `core/server.py` 98 條路由 ＋ `rag/router.py` 31 條 | 前端呼叫其中 97 條 |
| 前端 | `web/app.js` 6,055 行、193 個頂層函式、無建置工具；`I18N` 字典佔 686 行 | 兩套語言字典手工並行維護 |
| 設定 | `config.example.yaml` 449 行、33 個 `enabled:` 旗標，其中 16 個預設 false | 見 §4.3 |
| 依賴 | `pyproject.toml` 28 個執行期依賴；乾淨 venv 安裝後 **797 MB**（尚未下載 embedding 模型） | 其中 4 個依賴程式碼沒有 import |
| 資料庫 | 28 張表、migration 18/18 append-only ＋ checksum | ADR-003，設計良好 |
| 開發節奏 | git 歷史自 2026-08-30 起 85 個 commit、12 個活躍日；單一作者 ＋ AI 協作 | ROADMAP 記載 08-22 起步，更早的歷史不在這條線上 |
| 發佈 | v1.3.0a5 GitHub pre-release；三 OS × 兩 Python 的 CI 六個 job | PyPI 不在範圍（B3） |

**進度的真實解讀**：文件說「剩下的不是還沒寫的程式，而是收據」——這句話**對程式功能是真的**，但對**產品成熟度不是**。
功能已經全部「有」，可是每一塊都只被一個人在一台 Windows 機器上用過，且架構債（§4）會讓第二個開發者難以接手。

## 3. 值得推廣嗎？

### 3.1 用處：誰會需要它

| 對象 | 需要的是 | 現在能不能給 |
| :--- | :--- | :--- |
| **重度使用多個 AI coding agent 的人**（作者自己就是） | 「我上次跟 Claude／Codex 討論到哪、決定了什麼」跨工具可查、可接手 | ✅ 這是專案唯一沒有替代品的能力（`watchers/agent_log_watcher.py`＋Handoff） |
| 研究生／論文寫作者 | 寫作進度、檔案異動歸戶、每週回顧 | ⚠️ 能用，但 ActivityWatch／Obsidian 插件也能做七成，且安裝門檻高得多 |
| 一般知識工作者 | 時間追蹤、會議紀錄、行事曆整合 | ❌ 這些是市面成熟品的主場；本專案的版本更受限（LINE 只推播、會議只吃逐字稿） |
| 想「餵自己的知識庫給 LLM」的人 | RAG 問答 | ❌ Open WebUI／AnythingLLM／Khoj 更成熟，本專案的 RAG 沒有差異化 |

結論：**有一個明確、真實、目前無人填補的利基**（AI agent transcript → 個人工作記憶），
但它被包在一個涵蓋十幾個領域的大系統裡，外人看不到它，也裝不起來。

### 3.2 學術價值：有題目，還沒有證據

可以成為研究貢獻的三個方向，以及現在缺什麼：

1. **跨 AI agent 的 provenance-aware 工作脈絡重建**（資料／方法貢獻）
   四種私有 transcript 格式的解析、`turn_key` 去重、`response_status`（final／partial／unverified）分級、derived work session（ADR-006）——
   這一套若抽成獨立套件並附上格式演化紀錄，是 mining software repositories／developer tooling 社群會引用的工具型貢獻。
   **缺**：只有作者一人的資料；需要至少數位使用者的匿名化統計（turn 數、final 比例、format drift 次數）。
2. **只提案、閘門式執行的個人 agent 安全設計**（HCI／設計案例）
   ADR-007／008 的 proposal-only → L0／L1／L2 三級、一次性 confirm code、worktree 乾淨才寫、永不 commit——
   這是一個完整、可重跑、有測試的「人在迴路」設計案例，適合 CHI／CSCW／UIST 的 late-breaking work 或 workshop paper。
   **缺**：使用者研究（哪些提案被批准、被 mute、被忽略；批准後的後悔率）。目前只有一個人的直覺。
3. **「說的 vs 做的」反思式個人資訊學**（ADR-018／020）
   把宣告的優先與實際活躍天數並列、不推測原因——這與 personal informatics 的 reflection 階段研究可以對話。
   **缺**：同上，N＝1。

誠實的定位：**現在是一篇好的 experience report 的材料，不是一篇實證論文。**
若要往學術走，最便宜的一步是把第 1 項抽成獨立套件並公開一份匿名化 schema，讓別人能產生資料。

### 3.3 教學價值：最高，但不是拿來「用」

適合作為教材的，是**做法**而不是**軟體**：

| 可教的東西 | 在 repo 的哪裡 | 適合的課 |
| :--- | :--- | :--- |
| ADR 導向開發：先寫邊界再寫程式，功能落地時補 addendum | `docs/ADR-0xx`（22 份，含被 revert 的 ADR-007） | 軟體工程、系統設計 |
| 「收據」文化：測試通過 ≠ 實機可用，判準寫成可重跑的唯讀查詢 | `docs/TODO.md` A 段 ↔ `core/acceptance.py` | 軟體品質、DevOps |
| Append-only schema migration ＋ 升級前備份 ＋ fail-closed | `core/migrations.py`、ADR-003 | 資料庫 |
| Agent 執行的安全閘門（白名單 argv、環境變數重建、逾時 kill、永不 commit） | `core/agent_dispatch.py`、ADR-008 | AI 應用安全 |
| 子程序協定設計（stdout 只承載協定、stdin 關閉即退出） | `rag/retrieval_worker.py:126` | 系統程式 |
| **反面教材**：AI 協作兩週產出 4.4 萬行後的架構債 | §4 全部 | 「與 AI 協作開發」課程的案例研討 |
| `docs/NEXT_SESSION.md` 的「踩過的坑」 | 23 條，每條都有日期與根因 | 任何工程課的 debugging 單元 |

**不適合**讓學生實際安裝執行：800 MB 依賴、Windows 綁定、449 行設定、需要自己的 AI transcript 才有資料。
若要在課堂用，需要一個 `omnicontext demo` 之類的離線示範資料集（現在只有 `clear-demo` 的反向操作）。

### 3.4 若要推廣，障礙排序

1. **安裝重量**：RAG 依賴鏈（chromadb／fastembed／onnxruntime／pymupdf／office parsers）佔了絕大部分，但主服務程序本來就不 import 它們（ADR-009）——
   改成 `pip install omnicontext[rag]` 是純打包工作。
   → **同日已完成（D1）**：核心安裝 176 MB，`[rag]` 另 550 MB，沒裝時每條路徑都說得出缺什麼（ROADMAP §11.2）。
2. **平台**：`watchers/window_watcher.py`、`notifiers/desktop_notifier.py`、autostart 腳本綁 Windows；`core/manager.py:182` 在其他平台仍無條件啟動視窗採集器、靜默無效。
3. **設定門檻**：449 行設定裡有約 25 個是秘書引擎的評分權重（`proactive_secretary.*_boost`、`*_min_days`），那是程式常數不是使用者設定。
4. **首次啟動沒有資料**：沒有示範資料、沒有「偵測到你有 Claude Code 記錄，要不要匯入」的引導。
5. **local-first 宣稱有一個破口**：`web/index.html:12` 從 jsdelivr 載入 `marked`、第 8–10 行載入 Google Fonts——離線就壞、且對外發請求。
   → **同日已完成（B8）**。

## 4. 架構體檢

兩輪獨立審查（一輪看 `core/`＋`main.py`＋`web/`，一輪看 `rag/`／`watchers/`／`synthesizer/`／`notifiers/`／`scripts/`／依賴），每項都回原始碼核對。

### 4.1 做得好、要保留的

- **安全邊界**：`core/security.py` 的 Origin allowlist、Extension write-only ingest token、loopback 綁定（ADR-001）。
- **Migration**：append-only、checksum、升級前驗證備份、fail-closed（`core/migrations.py:965-1005`）。
- **閘門式執行器**的批准模型本身：token → confirm code → cooldown → receipt，argv 白名單、環境變數 allowlist 重建、永不 commit。
- **常駐檢索 worker 的協定**：`rag/retrieval_worker.py:126` 把 stdout 重導到 stderr 讓第三方 print 不會污染協定；stdin 關閉即退出；逾時 kill 自動重啟；`rag/retrieval/catalog.py` 讓主程序查策略清單時不碰 Chroma。
- **推播三層**：`notifiers/messages.py`（內容）→ `channels.py`（呈現＋傳輸，能力宣告）→ `secretary_push.py`（扇出）。三個通道的能力真的不對稱，這個抽象是賺到的。
- **Logging**：每模組一個 `OmniContext.<Component>` logger，`core/` 幾乎沒有 `print`；Extension token 錯誤的節流警告（`core/server.py:110-120`）。
- **路由層不碰 ORM**：`core/server.py` 沒有任何 `db.query`，都委派給 `core/*`。
- **測試品質**：595 個測試函式、2,225 個 assert，主流寫法是對記憶體 SQLite 跑真實 parser／engine，不是字串比對。

### 4.2 已核實的缺陷與死碼（可以直接刪）

> **同日已處理**（R0 第一輪，收據見 [ROADMAP §11.2](../ROADMAP.md) 2026-09-16 條目）：下表九項全部完成；`clear-demo` 連同 `scripts/cleanup_noise.py` 一起移除，字型改為全本機字型堆疊而非把 Noto Sans TC 打進 wheel。

| # | 位置 | 問題 |
| :-- | :--- | :--- |
| 1 | `core/server.py:332` vs `:860` | `SystemMaintenanceRequest` 定義兩次；第一個（含 `do_backup`／`checkpoint_mode`）沒有任何使用者，純死碼，且讓讀者以為 API 支援那兩個欄位 |
| 2 | `pyproject.toml`／`requirements.txt` | `pandas`、`pillow`、`sse-starlette`、`python-dotenv` 四個依賴在非測試程式碼裡**零 import**（SSE 實際用 `StreamingResponse`，`rag/router.py:547`） |
| 3 | `rag/parsers/image_parser.py:15` | import `rapidocr_onnxruntime` 但沒有在任何地方宣告；圖片 OCR 永遠靜默退化成檔名 stub |
| 4 | `notifiers/telegram_notifier.py` | ADR-014 之後是純轉呼叫 `telegram_channel()` 的殼，全 repo 零 importer |
| 5 | `synthesizer/scheduler.py:194-385` | `_std_scheduler_loop`：APScheduler `ImportError` 時的手刻備援（約 170 行，重複宣告八個排程）；APScheduler 是硬依賴，這段永遠跑不到，但會靜默漂移 |
| 6 | `scripts/inspect_logs.py`、`inspect_codex.py`、`inspect_assistants.py`、`check_real_recent.py` | 開發期除錯草稿，無呼叫者、無文件，卻被 `pyproject.toml` 的 `scripts*` 打進 wheel |
| 7 | `scripts/migrate_timezone.py`、`purge_legacy_data.py`、`windows_milestone_e2e.py` | 一次性遷移／被 live-acceptance 腳本取代；應歸檔 |
| 8 | `main.py` `clear-demo` | 清除示範假資料的指令留在正式 CLI；示範資料本身早已不存在 |
| 9 | `web/index.html:8-12` | 對外載入 Google Fonts 與 jsdelivr `marked`，違反 local-first 宣稱 |

### 4.3 重複與該合併的（需要一點設計）

1. **兩套 LLM client**：`synthesizer/llm_client.py:88`（同步，Ollama 走 `/api/generate`）與 `rag/llm_gateway.py:24`（非同步串流，Ollama 走 `/api/chat`），讀同一組設定鍵，
   預設模型已經漂移（`gemini-3.7-flash` vs `gemini-2.5-flash`）；`core/semantic_index.py:451` 還有第三條 Ollama 呼叫；`rag/embeddings.py:71` 第四次實作 OpenAI 金鑰解析。
   → **同日已完成（D2）**：合為 `core/llm_client.py`，四處都改走它（ROADMAP §11.2）。
2. **兩套向量記憶**：`core/semantic_index.py`（Ollama bge-m3、向量存 SQLite BLOB、純 Python cosine）與 `rag/activity_indexer.py:54`（FastEmbed＋Chroma＋BM25）
   對**同樣五種實體**（ProjectState／OpenLoop／AI turn／Git／File）各做一次 embedding、各有查詢路徑與 UI（`omni ask` vs `/api/v1/rag/chat`）。這是 repo 裡最大的一塊重複。
3. **四份「每專案每日活躍」聚合**：`weekly_review.py:83`、`activity_patterns.py:243-306`、`activity_digest.py:77`、`secretary_greeting.py:97`，各自從同一組事件表算一遍。
   → **同日已完成（D3）**，但**這條當時的描述只對一半**：其中兩組早就互相委派了。真正的重複是「哪些表算活動、專案名在哪個欄位、一天從哪到哪」各寫三遍，已收進 `core/activity_sources.py`（ROADMAP §11.2）。
4. **秘書叢集 11 個模組、4,109 行、沒有共同型別**：全部是回傳 `dict[str, Any]` 的自由函式（`build_*`／`collect_*_signals`），
   五個訊號收集器與 `build_action_proposals`（`proactive_secretary.py:242`）之間的契約是未定型的 dict。`secretary_ask`（202 行）、`secretary_profile`（150 行）、`secretary_home`（242 行）不值得各自一個模組。
5. **桌面通知在抽象之外**：`notifiers/desktop_notifier.py`（282 行）自己再扇出一次晨報／交接／停滯／里程碑，沒走 `ChannelAdapter`。
6. **循環依賴用延遲 import 撐著**：`core/` 內 110 處函式內 import；`proactive_secretary ↔ secretary_memory ↔ secretary_home ↔ agent_executor` 是一個真的環。
7. **模組層可變全域狀態**：`_PENDING_L2_CONFIRMS`（`agent_executor.py:72`）、`_ARMED_UNTIL`／`_PROCESSED_CALLBACK_IDS`（`telegram_approvals.py:74-76`）、advisor／project 快取、
   `server.py:79-80` 在 import 時凍結的 CORS 來源（改設定不重啟不生效）——並因此長出三個 `_reset_*_for_tests` 鉤子。
8. **四種檢索策略**（Hybrid RRF／Weighted Fusion／Vector Only／BM25 Only）對個人工具多了一到兩種。

### 4.4 結構問題（大工程，要排期）

1. **`core/server.py` 2,003 行、98 條路由、零 `APIRouter` 切分**；34 個 Pydantic model 散落各處；AI 事件 ingest 的去重／turn key／狀態分級邏輯內嵌在路由（`:1370-1445`）。
   `rag/router.py`（655 行、31 條）也把資料夾 CRUD、job、儲存、檔案瀏覽、檢索、對話 session 混在一起。
2. **`web/app.js` 6,055 行單檔**：約 30 個模組層 `let` 當狀態、105 處 `innerHTML`、9 處繞過共用 fetch helper 的裸 `fetch()`、126 個 `catch` 只有 13 個記 log、17 處字串內嵌 `onclick=`。
3. **`watchers/agent_log_watcher.py` 1,066 行**解析四種**無穩定性保證的私有格式**（Codex 還有 json／jsonl 兩套 parser）；測試用合成 fixture，鎖的是今天的形狀，**格式一變就是靜默零事件**。
4. **六層巢狀預設關閉旗標**（`executor` → `l2` → `l2.allow_write` → `scheduled_tasks` → `telegram_approvals` → `allow_remote_arm`）守著約 2,800 行預設安裝永不執行的程式。
5. **`core/acceptance.py` 1,564 行**：22 個手寫 `_check_aN` ＋ 200 行 `_ITEMS`——把 TODO A 段做成可執行是好主意，但 3% 的程式碼在做自我證明，應改為少數幾個通用探針上的宣告式表格。
6. **API 錯誤契約不一致**：48 處 `HTTPException` 與多處回 HTTP 200 的 `{"status": "skipped"/"error"}` 並存；87 個 `except Exception` 大多靜默吞掉。
7. **CLI 與 API 詞彙分家**：`main.py cmd_status` 自己多算 `ai_nonempty_count`／`checkpoint_errors`，儀表板拿不到。
8. **`legacy_*` provenance 哨兵值**穿過六個模組（B1 的 337 筆 rows）——pre-migration 相容路徑至今全活著。

## 5. 判斷：該刪、該合併、該停

| 動作 | 對象 | 理由 |
| :--- | :--- | :--- |
| **刪** | §4.2 全部九項 | 零風險，當天可完成，直接減少 wheel 體積與誤導 |
| **改成選用依賴** | RAG 依賴鏈 → `omnicontext[rag]` | 主程序本來就不 import；預設安裝可少約九成體積 |
| **合併** | 兩套 LLM client → 一個（同步＋串流） | 預設模型漂移已是活的 bug 溫床 |
| **合併** | 四份活躍聚合 → 一個 `project_activity_matrix()` | 每週回顧、模式提案、工作誌、問候卡數字才會永遠一致 |
| **合併** | 秘書 11 模組 → 訊號／聚合／呈現／記憶四層，加 `Signal`／`Proposal` dataclass | 讓第二個人能接手 |
| **二選一** | 兩套向量記憶 | 建議依 ADR-005／009 的原意分工：**活動記憶＝`semantic_index`（核心、無重依賴）；文件＝RAG（選用）**；RAG 對話要引用活動時透過檢索 worker 查 `semantic_index`，刪掉 `rag/activity_indexer.py` 的重複 embedding。反向（全部進 Chroma）也可行，但會讓「記憶」綁死在選用依賴上 |
| **收斂** | 六層旗標 → 三個（`executor`／`l2`／`l2.allow_write`）；`scheduled_tasks.enabled` 併入 executor、`allow_remote_arm` 併入 telegram approvals | 安全等級不變，設定面少一半 |
| **收斂** | 檢索策略四 → 二（Hybrid RRF ＋ BM25 only 作退化） | 少兩個要維護的路徑 |
| **停** | C5 遠端存取、C6 LINE 雙向、C3 更多採集來源 | 全部會加面積；在減法完成前不動 |
| **凍結** | LINE 推播（A11 未取得收據） | 只推播的通道價值有限；若三個月內沒人用就移除 |
| **保留但縮** | 驗收中心 | 概念要留，實作改宣告式 |
| **保留** | 閘門式執行器的安全模型、migration、檢索 worker 協定、推播三層 | 是這個專案最值得教的部分 |

## 6. 對「推廣」的具體建議

1. **先減法再對外**：§4.2 ＋ 選用依賴 ＋ 兩個合併（LLM client、活躍聚合），目標是 `pip install omnicontext` 在 100 MB 以內、`omni init` 能自動偵測 `~/.claude`／`~/.codex` 並問「要匯入嗎」。
2. **把 transcript 解析抽成獨立套件**（暫名 `agent-transcripts`）：只做四種格式 → 統一 turn 模型 ＋ provenance，附格式演化紀錄與 drift 偵測。
   這是唯一別人會單獨想要的東西，也是學術貢獻（§3.2 第 1 項）的載體。
3. **示範資料集**：一份匿名化、可公開的假 transcript ＋ Git ＋ 檔案事件，讓 `omni demo` 五分鐘內看到首頁與 Handoff——教學與展示都需要。
4. **教學用途**：直接以 ADR、`NEXT_SESSION.md` 的踩坑清單與本文 §4 當案例教材；不要求學生安裝整套系統。
5. **學術用途**：先寫 experience report（設計決策 ＋ 兩週 AI 協作的量化觀察），等第 2 項套件有第二位使用者再談實證。

## 7. 這份檢視沒有做的事

- 檢視本身沒有改程式碼；§4.2 的九項在同日 R0 第一輪處理完（ROADMAP §11.2），整頓項目仍在 TODO D 段。
- 沒有在 Windows 實機驗證任何功能；A 段收據狀態以 `python main.py verify` 為準。
- 沒有評估 LLM 產出的品質（摘要、提案註解）；那需要使用者的實機資料。
