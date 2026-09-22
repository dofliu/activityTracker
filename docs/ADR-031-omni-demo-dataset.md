# ADR-031：`omni demo` 示範資料集（設計定案；實作留待下一輪）

- 狀態：Accepted（設計）／實作 Not started（[TODO E1](TODO.md#e-推廣路線roadmap-13324)）
- 關聯：[ROADMAP §13.3](../ROADMAP.md#133-推廣路線r0-之後才啟動) 推廣路線第 2 項、[ADR-016](ADR-016-acceptance-center.md)（誠實文化、狀態字彙）、[ADR-023](ADR-023-one-activity-memory.md)（活動一律進 `semantic_index`）、[ADR-025](ADR-025-transcript-parsers-and-drift.md)（四種 transcript parser）、`core/runtime_paths.py`（`OMNICONTEXT_HOME` 隔離）

## Context

ROADMAP §13.3 把「五分鐘看到首頁與 Handoff」列為推廣路線第二項，理由是教學與展示都需要它：現在唯一能看到儀表板長什麼樣的方式，是把 449 行的 `config.example.yaml` 填成自己的真實路徑，接上真的 AI agent 記錄夾、真的 Git repo、真的檔案監看，等採集器跑一段時間才有東西可看。這對「想先看看這東西在幹嘛」的人是不合理的門檻。

這一輪只做**決策**，不寫程式：先把「示範資料要長什麼樣、放在哪裡、怎麼進系統」定案，下一輪照這份 ADR 實作，理由是這件事有好幾個會影響實作形狀、且事後改起來成本高的邊界問題（會不會混到使用者真實資料？算不算「實機收據」？走不走既有的採集路徑？），值得先寫清楚再動手。

需要先答的問題，都是本專案已經因為踩過坑而定型的鐵律：

1. **不得偽造「實機收據」**——TODO A 段的判準是「本機真的取得的證據」，示範資料是合成的，絕對不能被驗收中心或任何下游誤判成使用者的真實使用軌跡。
2. **沒有第二條資料寫入路徑**——ADR-023／ADR-024 的教訓是「重複＝三件事各寫三遍」；示範資料如果直接寫 DB 表，等於在既有的採集→解析→事件表這條路徑之外，另開一條繞過 schema 與 parser 的路徑，遲早會跟真正的 ingestion 邏輯漂移。
3. **危險能力預設關閉**——執行器、L2、Telegram 等不應該因為「試用示範」而被打開。
4. **本機優先、不連網**——demo 不能呼叫真的 LLM 或雲端 API，否則沒裝 API key、沒開 ollama 的人跑不起來，違反「五分鐘看到」的目的本身。
5. **不能誤刪使用者的真實資料**——2026-09-16 R0 移除的舊指令 `clear-demo`（清除混在同一顆 DB 裡的示範假資料）本身就是這類設計的風險示範：示範資料與真實資料若共用一顆資料庫，就需要一個「清除示範資料」的動作，而那個動作永遠有刪錯東西的可能。

## Decision

### D1：`omni demo` 是獨立子指令，輸出到獨立、隔離的 home，永不碰使用者現有資料

新增 `main.py` 子指令 `demo`（沿用既有 argparse 子指令模式），語意上與 `init` 平行但目的完全不同：`init` 是幫使用者建立**自己的**設定，`demo` 是產生一份**合成的**展示資料。兩者不合併，避免使用者以為要填自己的路徑。

- `omni demo`：若示範 home 不存在就建立並灌入資料；若已存在就重用（idempotent，不重複灌）。完成後印出下一步：`OMNICONTEXT_HOME=<demo home> omni run` 並附上網址。
- `omni demo --reset`：整個示範 home 刪掉重建（不是「清除示範資料」，是重建整個隔離目錄——因為目錄本來就只有示範資料，不存在「哪些是真實、哪些是示範」需要分辨的問題）。

**示範 home 固定為 `Path.home() / "OmniContext-Demo"`**（與真實預設 home `Path.home() / "OmniContext"` 並列、不重疊），透過既有的 `OMNICONTEXT_HOME` 覆寫機制生效（`core/runtime_paths.application_home()` 已經支援）——**不新增第二套「家目錄在哪」的邏輯**，只是換一個固定值。`demo` 指令執行期間在自己的行程內設定這個 override，不影響呼叫端原本的環境變數；示範資料庫、config.yaml、reports、logs 全部落在這個目錄下，與使用者真實的 `~/OmniContext`（或使用者自訂的 `OMNICONTEXT_HOME`）完全不相交。

這解決了 Context 第 5 點：不需要「清除示範資料」這種要分辨真假的動作，`--reset` 只是刪一個已知只有示範內容的目錄。

### D2：示範資料走既有的採集／解析路徑，不直接寫 DB

示範資料由三種**本機、離線、確定性**的來源產生，全部透過現有的 collector／parser 進系統，理由見 Context 第 2 點：

| 來源 | 產生方式 | 進系統的路徑 |
| :--- | :--- | :--- |
| AI transcript | 內建於 repo 的合成逐字稿檔（覆蓋 [ADR-025](ADR-025-transcript-parsers-and-drift.md) 至少兩種平台格式：Claude Code JSONL、Codex JSON），寫進示範 home 底下的假 `~/.claude`／`~/.codex` 對應目錄 | `watchers/transcripts/{claude_code,codex}.py` 的 `discover`／`parse`（同一份 parser，不另寫） |
| Git 活動 | 在示範 home 內用 `git init` 建 2–3 個假 repo，用 `GIT_AUTHOR_DATE`／`GIT_COMMITTER_DATE` 回填過去 1–2 週的假 commit（訊息與檔案皆為合成內容，不含任何真實使用者資料） | `watchers/git_watcher.py`（或既有的 git 事件採集，沿用既有表與欄位） |
| 檔案事件 | 在假的監看目錄下建立／修改幾個檔案 | `watchers/file_watcher.py` 既有邏輯 |

時間一律用「相對於執行當下」回填（例如「今天」「昨天」「上週」），不是寫死某個日期——這樣：

- 每次重新產生時 demo 看起來都是「最新的」；
- 依賴「已結束的日子」的功能（週回顧 ADR-020、模式提案 ADR-017、每日工作誌 ADR-012 Addendum A）都吃得到已結束的區間，不會因為時間過去而失效。

**代價**：這代表 `omni demo` 內部需要跑一次性的採集流程（呼叫 parser／watcher 的既有函式，而不是啟動背景服務），比直接 `INSERT INTO` 慢，但換到的是「示範資料的形狀由 parser 保證跟得上真實格式」——parser 改了，demo 資料的產生方式不用跟著改一次。

### D3：示範資料要能讓「這一週的工作模式」被看見，不是只灌一天

至少涵蓋**連續 10–14 天**、2–3 個示範專案，且各專案的活躍程度刻意不同（一個持續活躍、一個中途被冷落、一個近期才開始）——這是為了讓 ADR-017（模式感知提案：沒有每日排程／被冷落的專案）、ADR-020（每週回顧：說的 vs 做的）、ADR-019（秘書桌面焦點與記得）都有真實的東西可挑，而不是空的。至少包含一份會後逐字稿（.vtt），讓 ADR-022 會議秘書卡也有東西可展示。

具體天數、專案數與逐字稿內容由下一輪實作時定案並寫進 contract test（本 ADR 只定邊界，不定死用例）。

### D4：示範資料要「一看就知道是示範」

所有合成的專案名稱、repo 名稱、逐字稿內容統一用 `demo-` 前綴或明顯虛構的名字（例如 `demo-inventory-service`），commit 訊息與逐字稿內容不得看起來像是抄自真實專案。這不是技術上必要（D1 的隔離 home 已經在資料層面做到互不相交），而是避免使用者截圖示範畫面時，被誤以為是真實工作內容外流。

### D5：示範資料不得被驗收中心當成實機收據

驗收中心（[ADR-016](ADR-016-acceptance-center.md)／[ADR-028](ADR-028-declarative-acceptance.md)）的判準都是「查得到收據就算」，不分資料來源。如果有人在示範 home 裡跑 `python main.py verify`，A 段很多項目自然會顯示已有收據（因為確實有活動事件、有工作誌可算）——**這是符合邏輯的**，因為驗收中心本來就只保證「這個資料庫裡查得到某種資料」，不保證「這是使用者的真實一天」。因此本 ADR 不新增「demo 資料要讓 verify 說謊」這種特殊邏輯，而是靠 D1 的隔離：**只要示範 home 與使用者真實 home 不是同一個資料庫，A 段收據永遠只反映使用者自己那顆資料庫裡有什麼**，不會被示範資料污染。實作時要補一項契約測試：對示範 home 跑 `verify` 不會影響、也讀不到真實 home 的任何檔案。

### D6：不自動啟動 web server

`omni demo` 只負責「把資料準備好」，不擅自 `omni run`。原因：使用者可能已經在別的 port／別的 `OMNICONTEXT_HOME` 開著自己的真實服務，`demo` 指令自動起一個服務容易撞埠或讓人搞混現在看到的是哪一份資料。完成後印出下一步指令，由使用者自己決定何時、用什麼方式啟動。

## Alternatives considered

- **直接寫 SQL／ORM 塞資料到既有表**：拒絕（Context 第 2 點）。這是本專案已經因 D2/D3/D4/D9 反覆糾正過的「重複的資料寫入路徑」錯誤形狀，示範資料没有理由是例外，而且 schema／欄位一改，demo 產生器就要跟著改一次，形同第二份 ingestion 邏輯。
- **共用使用者現有的 `OMNICONTEXT_HOME`，用 `demo`／專案名字首碼分辨，再靠 `clear-demo` 式指令清除**：拒絕（Context 第 5 點）。這正是 2026-09-16 R0 移除的舊設計，複雜度來自「示範與真實混在一顆資料庫」本身，隔離 home 從根本消掉這個問題，不需要清除邏輯。
- **呼叫真的 LLM 產生更「有變化」的假逐字稿**：拒絕（Context 第 4 點）。違反本機優先與可重現性——contract test 需要固定語料才能斷言，呼叫真的 LLM 會讓 demo 資料每次不同、且需要網路與 API key，違背「五分鐘看到」的初衷。
- **寫死一個過去的日期（例如固定在 2026-01-01 那一週）**：拒絕（D2 已說明）。任何「已結束的週／日」邏輯會隨著現實時間流逝而不再命中那個固定區間，demo 過一陣子就會退化成「安靜的一天」，看起來像壞掉。
- **重用 `tests/fixtures/` 現成的 contract test 語料**：部分採用其設計精神（既有的 WebVTT／JSONL 格式片段可以參考），但直接拿來當 demo 資料集不夠——那些語料是為了單一斷言最小化的，不足以呈現「一週的工作模式」，仍需另外準備一組更完整、涵蓋 D3 所有分頁的資料。

## Consequences

- 本輪只有這份 ADR 與 `docs/TODO.md` 的 E1 條目，**沒有新程式碼、沒有新 migration、沒有新危險能力**；`pytest`／`python main.py verify` 不受影響（收據見對應的 ROADMAP §11.2 條目）。
- 下一輪實作範圍依本 ADR 展開：新增 CLI 子指令 `demo`（`main.py` argparse ＋ 一個 `core/demo_seed.py` 或等效模組負責產生語料並呼叫既有 parser／watcher）、內建的合成逐字稿與假 repo 腳本、contract test（鎖 D1 隔離、D2 走既有路徑不直寫 DB、D5 不污染真實 home）、README／USAGE 加一段 quickstart。
- 示範資料集本身會成為 `agent-transcripts`（TODO E2）獨立套件未來的一個天然範例輸入——兩者不互相阻塞，但共用同一批合成逐字稿可以省一份維護。
- 刻意不做的事：demo 不會自動開啟任何危險能力（執行器／L2／Telegram／LINE 全部維持設定檔預設關閉）；demo 不會呼叫任何網路服務；demo 不會嘗試让 verify 對示範資料给出「release_ready」之類的假象——A1（全天 coverage ledger）等本來就要求「使用者自己 Windows 實機」的項目，不因為有 demo 資料而改變判準。
