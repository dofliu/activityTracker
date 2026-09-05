# 下一個 Session 接手指南

> 最後更新：2026-09-05（session `claude/activity-tracker-next-steps-en44qo`：**驗收中心 ADR-016 已合併進 main**（PR #14，合併後 main CI 六個 job 全綠）＋**同步中心 pull/push 前置條件修正 ADR-011 Addendum C**＋**每日工作誌 ADR-012 Addendum A**；前一輪 `claude/stoic-hamilton-4oicm4`：秘書記憶區 ADR-012、Telegram 手機對話 ADR-013、多通道推播與短效解鎖碼 ADR-014、系統設定左欄切換、小秘書問候卡、本機行事曆採集 ADR-015）。
>
> 這頁是給「下一個開發 session（人或 AI）」的**最短接手路徑**，只放現況、地圖與環境備忘。
> 細節一律不在這裡重寫：**做過什麼**看 [ROADMAP.md](../ROADMAP.md) §11、**為什麼這樣設計**看對應 ADR、
> **還要做什麼**看 [TODO.md](TODO.md)（每項附完成判準）、**機器可讀現況**看 [STATUS.yaml](../STATUS.yaml)。

## 一分鐘現況

| 項目 | 現況 |
| :--- | :--- |
| 版本 | v1.3.0a5 已發佈為 GitHub pre-release（release workflow 自動 build → verify → release，SHA-256 receipt 交叉驗證）。`release_ready: false`，唯一**能力型**缺口是全天 coverage ledger 實測（TODO A1）。 |
| 還缺什麼 | 別憑記憶：跑 `python main.py verify`（或看「06 系統設定 → 驗收中心」）就會列出 A1–A13 每一項現在有沒有收據，以及 ROADMAP §12.3 四個 gate 缺什麼。 |
| Schema | migration **18/18**（append-only + checksum；**新表一律進 registry，不得靠 `create_all` 繞過**）。017 = `secretary_notes`、018 = `calendar_events`。 |
| 測試 | **55 個 contract test 模組、481 項**（480 passed + 1 skipped）。容器缺 xdg-open 時 `test_open_command_is_argv_not_shell_string` 會條件 skip 並標註原因，不是失敗。 |
| 導覽 | 6 分頁：01 小秘書（三欄）／02 知識庫／03 進行中工作／04 Git 同步中心／05 摘要與統計／06 系統設定（左欄 11 區塊，末項為**驗收中心**）。桌面與 494px 皆無水平溢出（Playwright 實測）。 |
| 外觀 | 兩個獨立軸：`data-theme`（dark/light）× `data-accent`（naruto/forest/ocean），CSS 全走 `var(--accent)`；新配色只需加一組變數區塊。偏好存 localStorage（`omni-theme`／`omni-palette`／`omni-settings-pane`）。 |
| 危險能力 | 執行器、L2、L2 寫入、自訂排程、Telegram 對話、`allow_remote_arm`、LINE、問候卡 LLM 潤飾——**全部預設關閉**；行事曆預設開但沒設路徑就等於停用。 |

## 功能地圖（要改哪裡就看這張表）

| 功能 | 主要程式 | 契約測試 | 決策 |
| :--- | :--- | :--- | :--- |
| 採集器與自我修復 | `watchers/*.py`、`core/manager.py`（`supervise_and_heal`、`get_status`） | `test_collector_self_healing.py` | — |
| 本機行事曆（.ics 唯讀） | `core/ics_parser.py`、`watchers/calendar_watcher.py`、`core/calendar_agenda.py` | `test_calendar_source.py`（17） | [ADR-015](ADR-015-local-calendar-source.md) |
| 專案歸戶與 Open Loops | `core/project_engine.py`、`core/project_paths.py` | `test_project_paths.py`、`test_project_engine_concurrency.py`、`test_open_loop_lifecycle.py` | — |
| Semantic index／omni ask | `core/semantic_index.py` | `test_semantic_index.py` | [ADR-005](ADR-005-local-semantic-index-and-ask.md) |
| RAG 對話與檢索 worker | `rag/router.py`、`rag/retrieval_worker.py`、`rag/retrieval_client.py` | `test_rag_chat_stream.py`（10）、`test_rag_retrieval_worker.py`（20） | [ADR-009](ADR-009-deskrag-worker-index-lifecycle.md) ＋ Addendum |
| 秘書提案（proposal-only） | `core/proactive_secretary.py`、`core/secretary_advisor.py` | `test_proactive_secretary.py`、`test_secretary_advisor.py` | [ADR-007](ADR-007-proposal-only-secretary.md) |
| 分級執行器 L0/L1/L2 | `core/agent_executor.py`、`core/agent_dispatch.py`、`core/scheduled_tasks.py` | `test_agent_executor.py`、`test_agent_dispatch_l2.py`、`test_scheduled_tasks.py` | [ADR-008](ADR-008-gated-agent-executor.md) |
| 秘書記憶區（大腦） | `core/secretary_memory.py` | `test_secretary_memory.py`（20） | [ADR-012](ADR-012-secretary-memory.md) |
| 每日工作誌（你做了什麼） | `core/activity_digest.py`（L0 template `daily_digest`，也是早晨包第四步） | `test_activity_digest.py`（17） | [ADR-012 Addendum A](ADR-012-secretary-memory.md) |
| 每日包與今日視圖 | `core/secretary_packs.py` | `test_secretary_packs.py`（9） | [ADR-008](ADR-008-gated-agent-executor.md) L0 |
| 問候卡（01 首頁＋晨報開頭） | `core/secretary_greeting.py` | `test_secretary_greeting.py`（23）＋晨報三項 | ROADMAP §11（2026-09-04） |
| 推播組裝與通道 | `notifiers/messages.py`、`notifiers/channels.py`、`notifiers/secretary_push.py` | `test_notification_channels.py`（32） | [ADR-014](ADR-014-multi-channel-push-and-arm-code.md) |
| Telegram 對話與批准 | `notifiers/telegram_chat.py`、`notifiers/telegram_approvals.py`、`core/secretary_ask.py` | `test_telegram_chat.py`（29） | [ADR-013](ADR-013-telegram-secretary-chat.md) |
| Git 同步中心與對帳 | `core/repo_sync.py`（`_sync_blocker` 產生逐 repo 的具體拒絕理由）、`core/repo_onboarding.py`、`core/repo_sync_report.py` | `test_repo_sync*.py`、`test_repo_onboarding.py` | [ADR-011](ADR-011-safe-local-repository-sync.md) ＋ Addendum A/B/C |
| Schema migration | `core/migrations.py`（`MIGRATIONS` registry） | `test_database_migration.py` | [ADR-003](ADR-003-versioned-sqlite-migrations.md) |
| 驗收中心（A 段收據） | `core/acceptance.py`（`_ITEMS` 是 TODO A 段的可執行副本）、`main.py cmd_verify` | `test_acceptance_center.py`（30） | [ADR-016](ADR-016-acceptance-center.md) |
| API 邊界與 secret | `core/security.py`、`core/secret_resolver.py` | `test_api_boundary.py`（18） | [ADR-001](ADR-001-p2-5-trust-boundary.md) |

前端只有三個檔：`web/index.html`（結構與 `data-i18n`）、`web/app.js`（`I18N` 中英字典、各分頁 `load*`／`render*`、設定的 load/save）、`web/style.css`（`var(--accent)` 為主）。新增字串要**同時**補 zh-TW 與 en。

## 踩過的坑（別再踩）

- **secret 解析要取 `.value`**：`resolve_secret_env()` 回傳 `SecretResolution` 物件，物件恆為真值——漏取 `.value` 會讓「未設金鑰」判斷失效，還會把 repr 帶進 URL。2026-09-01 曾因此讓所有雲端 provider 的 RAG 對話全滅。
- **主服務不得 import 索引函式庫**：`core.server` 一旦載入 chromadb／fastembed／rank_bm25／jieba 就會拖慢啟動並吃掉數百 MB；檢索走常駐 worker 子程序，`rag` 的 import 一律寫在函式內（乾淨直譯器契約測試會抓）。
- **SSE 一定要送 `done`**：瀏覽器只靠它解除「回覆中」狀態，`event_generator` 全程 try/finally。
- **`display` 會蓋掉 `hidden` 屬性**：專案已加全域 `[hidden] { display: none !important; }`，新元件不要再用行內 `style="display:flex"` 對抗它。
- **migration 測試會鎖版本清單**：加 migration 要同步改 `test_database_migration.py` 的 `[1..N]` 與「未知的更新版本」那筆（用 N+1）。
- **`pkill -f` 的 pattern 會殺到自己的 shell**（exit 144）：寫成 `main[.]py` 這種形式，且與啟動指令分開兩次呼叫。
- **要讓秘書「記得」，資料得進 `secretary_notes`**：採集到 ≠ 秘書知道。`memory_context()` 注入的是筆記與觀察，不是原始事件表——新的「秘書應該知道 X」需求，通常是缺一個把既有資料 reduce 成觀察的 L0 動作，而不是缺採集。
- **「clean worktree」不等於「沒有 untracked 檔案」**：pull/push 的門檻只看**已追蹤**檔案的未提交變更；把 untracked 算進去會讓幾乎每個真實專案（有 `.lock`、`build/`）永遠不能 pull，而且沒有多保護到任何東西——Git 自己對「untracked 會被覆蓋」已 fail-closed（ADR-011 Addendum C）。
- **不要用人類可讀訊息的關鍵字做分類**：批次結果曾靠比對「前置」「僅限」等字串決定 skipped/failed，文案一改就壞。用 `RepositorySyncRejected(kind=...)` 這種機器可讀欄位。
- **灰掉的按鈕要說為什麼**：同一句放諸四海的條件敘述等於沒說。理由要帶這個 repo 的實際數字，並直接顯示在列上，不要只放 tooltip。
- **「今天的比例」不是「全天的比例」**：`get_daily_coverage` 對當天的分母是已過的時間，早上跑三小時就能顯示 97%。任何宣稱「全天／完整期間」的判定都只能採計**已結束**的日子（2026-09-05 驗收中心 A1 就是這樣出現假綠燈的）。
- **記憶體狀態不能在 CLI 假裝查得到**：檢索 worker 的 `state`／預熱計數只存在主服務程序，另開一個 Python 程序永遠是 cold。驗收中心為此有 `runtime_only` 這一格——把它報成「還沒做」等於說謊。新增任何「查現況」功能時先問：這個數字在哪個程序裡？

## 待辦與下一步

- **待辦一律看 [TODO.md](TODO.md)**：A 段是等待使用者側 live 收據（👤 需在 Windows 實機操作，不是程式工作，A1 是唯一還擋 `release_ready` 的能力缺口）、B 段是已知問題與技術債、C 段是功能候選。
  **A 段的現況直接跑 `python main.py verify` 查**（[ADR-016](ADR-016-acceptance-center.md)）；改 A 段的判準時要同步改 `core/acceptance.py` 的 `_ITEMS`。
- **方向與取捨看 [ROADMAP.md](../ROADMAP.md) §12「下一階段規劃」**：三條候選路線（C5 私有網路遠端存取、C6 LINE 雙向、C3 其餘採集來源）各自的前置與代價都寫在那裡。
- 新增待辦請寫進 TODO.md、成果寫進 ROADMAP §11，**不要在本頁另開清單**——這頁保持一分鐘讀完。

## 工程慣例（照舊）

- **分支**：在當次 session 的指定分支開發 → push → 開 draft PR → 使用者 merge 進 `main`（所有成果都要落在 main）。
- **誠實文化**：每個聲明附 receipt／claim boundary；測試失敗如實回報；migration 永遠 append-only；危險能力預設關閉；沒被採集到的資料不推測。
- **文件同步**：功能落地時同步 USAGE（怎麼用）／ROADMAP §11（做了什麼）／STATUS（quality gate ＋ known_blockers）／TODO（實機收據判準）／必要時 README 與新 ADR，並更新 [INDEX.md](INDEX.md) 的 ADR 一覽。

## 遠端容器環境備忘（在 Claude Code 雲端 session 內開發時）

- 依賴裝在 scratchpad venv（系統 pip 缺新 setuptools，jieba 會建置失敗）；`pip install -e .` 後跑測試。
- Playwright 用 `executable_path=/opt/pw-browsers/chromium` ＋ `--no-sandbox`；**內建 ffmpeg 沒有 PNG 解碼器**，要完整 ffmpeg 用 `pip install imageio-ffmpeg`。
- E2E 對 localhost server 要用 **port 8765**（Origin allowlist 綁定預設埠）；另開 `OMNICONTEXT_HOME` 當測試家目錄，不要碰使用者資料。
- 容器沒有 xdg-open（對應測試會 skip）、git 憑證只能推分支不能推 tag（發佈走 release workflow 的 workflow_dispatch）。
- 檢索 worker 在容器內可真的啟動（`POST /api/v1/rag/retrieval/warmup`）；空索引預熱會觸發 fastembed 模型下載（約 4 秒，經 proxy），worker RSS 約 335 MB、主服務約 88 MB。
- Extension 寫入被拒時 server 回 403 detail `extension ingest token missing/mismatch`＋每 60 秒一則節流 WARNING。
