# ADR-032：唯讀 MCP Context Server

- 狀態：**Accepted**（2026-09-23 起草並定稿；動工留給 TODO E3／E4）
- 關聯：[ADR-001](ADR-001-p2-5-trust-boundary.md)（P2.5 信任邊界，本 ADR 不動它）、
  [ADR-008](ADR-008-gated-agent-executor.md)（分級執行器，本 ADR 明文不與它耦合）、
  [ADR-009](ADR-009-deskrag-worker-index-lifecycle.md)（`[rag]` optional extra 的先例）、
  [ADR-023](ADR-023-one-activity-memory.md)（活動記憶只有一份，`semantic_index` 是唯一查詢入口）、
  [ADR-031](ADR-031-omni-demo-dataset.md)（`omni demo`，MCP 第一版的驗收前提）
- 依據：[ROADMAP.md §14.2](../ROADMAP.md)、[docs/TODO.md](TODO.md) E2、
  [REVIEW-2026-09-22-competitive-landscape-and-P9.md](REVIEW-2026-09-22-competitive-landscape-and-P9.md) §A.4（D1–D6 安全契約的原始提案）

## Context

外部檢視（REVIEW-2026-09-22）指出本專案最大的結構性落差不是功能，是介面：十五個月累積的
canonical context（活動記憶、Handoff、Open Loops、每日工作誌）目前只有三個出口——瀏覽器、
CLI、剪貼簿。使用者的實際工作流是 Claude Code／Codex 這類 agent，不是瀏覽器；要讓這些
agent 讀得到 canonical context，得開一個它們原生看得懂的介面，也就是 MCP（Model Context
Protocol）。

該檢視提出了六個 tool 與 D1–D6 的安全契約方向，但明講兩件事沒回答，必須在動工前先定案：

1. **模組路徑該叫什麼**——不能是 top-level `mcp/`，會撞名。
2. **stdio 傳輸要不要吃官方 MCP SDK 相依**——要的話用什麼機制讓它不進核心安裝。

除此之外，MCP client 的輸入天生不可信（它可能是任何連上這個 stdio 管線的 agent），
而這個專案已經用三層開關（ADR-008）把「AI 可以改動這台機器」這件事守得很緊；如果 MCP 的
第一版夾帶任何 write tool，等於在旁邊開一道新門，繞過那三層開關存在的理由。

## Decision

### 1. 模組路徑：`mcp_server/`（top-level，不叫 `mcp/`，不掛在 `core/` 底下）

- **不得**建立 top-level `mcp/`：PyPI 官方 MCP SDK 發佈的 import 名稱就是 `mcp`，
  若本專案也有一個 top-level `mcp/` 目錄，一旦兩者都在 `sys.path` 上，import 順序決定
  哪個生效——這是靜默、環境相依的錯誤，不能靠文件警告解決。
- 選定名稱：**`mcp_server/`**（top-level，與 `rag/`、`notifiers/`、`watchers/`、
  `exporters/`、`synthesizer/`、`integrations/` 同一層級，是新的一個子系統，不是
  `core/` 的子模組——`core/` 目前收斂在「主服務組裝與核心邏輯」，MCP 是另一個獨立入口，
  跟 `rag/` 是同一種關係：**不需要主服務程序啟動**，可獨立以子程序執行）。
- `pyproject.toml` 的 `[tool.setuptools.packages.find].include` 白名單需新增
  `"mcp_server*"`（實作 PR 動手）；在那之前它不會被打包進 wheel，這也是白名單機制
  本身多一層防呆——忘了加就是打包驗收會抓到的錯，不是本 ADR 要解決的問題。
- `mcp_server/` 內部**不得** `import core.agent_executor`、`core.agent_dispatch`、
  `core.secretary.scheduled_tasks`（分級執行器與排程三個模組）——這是 D5（零耦合）的
  機器可查版本，留給 E3 寫成 AST／import 掃描的契約測試（呼應 ADR-025「模組不得
  import 資料庫」與 ADR-024「掃 import 的測試把關方向」的既有作法，同一套手法用在
  這裡）。

### 2. stdio 傳輸相依：比照 `[rag]`，做成 optional extra `[mcp]`

- 官方 MCP Python SDK（`mcp` package，提供 stdio JSON-RPC framing）**不得**進核心
  `dependencies`——理由與 ADR-009／D1（R0 把索引依賴收斂成 optional extra）相同：核心
  安裝已經從 721 MB 降到 176 MB，這是买來的性質，MCP 是給「用 agent 的那群人」的功能，
  不該讓每一個只想用儀表板／Telegram 的使用者多背一個不會用到的相依。
- `pyproject.toml` 新增 `[project.optional-dependencies].mcp = ["mcp>=<定案版本>"]`
  （實作 PR 決定下限版本），安裝方式 `pip install "omnicontext[mcp]"`，與 `[rag]` 同一
  種使用者心智模型。
- `mcp_server/availability.py` 複製 `rag/availability.py` 的形狀：只用
  `importlib.util.find_spec("mcp")`，不 import，回報「裝了沒」給 `omni mcp` 子指令與
  驗收中心用；沒裝時 `omni mcp` 印出安裝提示並 exit non-zero，**不**讓主服務或其他子
  指令因為這個模組存在而變慢或多背記憶體（比照 ADR-009「主服務不得 import 索引函式庫」
  的鐵律，這裡是「主服務不得 import MCP SDK」）。
- `mcp_server/` 內部**只讀資料層**（SQLAlchemy `core.database`／既有的
  `core.project_engine`、`core.handoff_engine`、`core.secretary.memory`、
  `core.semantic_index`、`core.activity_digest` 這些既有函式）**不需要** MCP SDK 才能
  跑——這一層本身没有第三方相依，只有「接上 stdio transport 並照協議打包成
  JSON-RPC」這一小塊需要 SDK。`omni mcp --selftest`（E3 的驗收動作）刻意**不經
  stdio**，直接呼叫 tool 的 Python 函式本體，這樣 CI 與不裝 `[mcp]` extra 的環境也能
  驗證六個 tool 的邏輯正確，只有「真的接上 Claude Code」這一步需要裝 extra。

### 3. 六個 tool：全部唯讀，全部帶 `source_ref`，全部是既有函式的薄封裝

不新增任何查詢邏輯或第二套資料存取路徑——每個 tool 就是把 `mcp_server/tools.py` 對應
到一個既有的、已有契約測試守著的函式，只做輸入正規化與輸出裁剪（拿掉 secret／絕對路徑，
見 D4）。順序（E3／E4 切片）：

| Tool | 封裝的既有函式 | `source_ref` 內容 |
| :--- | :--- | :--- |
| `omni_project_state` | `core.project_engine.get_active_projects_list` | 專案鍵 ＋ 最後活動時間戳 |
| `omni_handoff` | `core.handoff_engine.build_project_handoff` | 專案鍵 ＋ `open_loops`／`git_activity_events` 等來源列的 id |
| `omni_open_loops` | `core.project_engine.get_open_loops_list` | `open_loops.id` |
| `omni_work_sessions` | `core.activity_sources`（既有的（專案 × 日）聚合，沿用 ADR D3 的單一定義） | 專案鍵 ＋ 日期 |
| `omni_recent_digest` | `core.activity_digest.build_daily_digest`（唯讀查詢，不重新產生；沒有觀察的日期回「無觀察」，不得即時生成假裝有） | 日期 ＋ 對應的 `secretary_notes` id（若有落記憶） |
| `omni_search_history` | `core.semantic_index.semantic_search`（**不是** `ask_local_context`——後者會呼叫 LLM 做摘要合成，這裡刻意只做 retrieval，合成交給呼叫端 agent 自己做；Ollama 不可用時比照 `ask_local_context` 的既有判斷回明確 `unavailable`，不 fallback 雲端、也不用空陣列冒充「沒發生」） | 命中切片的來源列 id（`source_id`／`source_type`，沿用 `SourceDocument` 既有欄位） |

- 六個 tool 一律**不做寫入**、**不呼叫任何 LLM provider**（`omni_search_history` 也不
  例外——它是 retrieval-only）、**不轉發任何 API 金鑰**。
- 六個 tool 的輸出結構定案於 E3／E4 實作時逐鍵鎖進契約測試（比照 ADR-024
  `Proposal.to_dict()` 的做法：輸出形狀是對外契約，一鍵一鍵比對，不是「大致長這樣」）。

### 4. D1–D6 安全契約（沿用 REVIEW-2026-09-22 §A.4 的提案，本 ADR 定案為機器可查的判準）

| # | 契約 | 機器可查的判準 |
| :-- | :--- | :--- |
| D1 唯讀 | 六個 tool 呼叫前後，SQLite 全部資料表列數不變（真 tmp DB 上跑 selftest） | `omni mcp --selftest` 的契約測試斷言 |
| D2 預設關閉 | `config.example.yaml` 新增 `mcp.enabled: false`；`omni mcp` 在 `false` 時拒絕啟動並印出「請先在設定檔開啟 `mcp.enabled` 後重試」 | 啟動路徑的單元測試 |
| D3 不轉發金鑰 | `mcp_server/` 原始碼零出現 `secret_resolver`／任何 provider API key 相關 import | 契約測試掃 `mcp_server/*.py` 的 import |
| D4 輸出無 secret 與絕對路徑 | 六個 tool 的回傳值跑一次正則掃描（token 樣式、`/home/`／`C:\Users\` 這類絕對路徑前綴），不得命中 | 契約測試對每個 tool 的樣本輸出跑掃描 |
| D5 與執行器零耦合 | `mcp_server/` 原始碼零出現 `core.agent_executor`／`core.agent_dispatch`／`core.secretary.scheduled_tasks` import | 契約測試掃 import（同第 1 節） |
| D6 留 receipt 但不記 query 原文 | 每次 tool call 寫一筆 receipt（tool 名稱、時間戳、耗時、成功／失敗），**不**存呼叫參數或 `omni_search_history` 的 query 字串本體 | receipt schema 的單元測試（欄位白名單，多一欄就紅） |

D1、D5、D6 的落地方式（存哪張表、selftest 用什麼 tmp DB 夾具）留給 E3 實作 PR 決定，
本 ADR 只鎖判準與方向。

### 5. Transport：只做 stdio，不做 HTTP／SSE，不動 ADR-001 的 loopback 邊界

- 第一版**只支援 stdio**（子程序管線，Claude Code／Codex 原生支援的模式）。stdio 是
  「父行程啟動子行程、用 stdin/stdout 交換 JSON-RPC」，不開任何 TCP/HTTP port，因此
  不落入 ADR-001「Local API 採 deny-by-default Origin boundary」的範圍——那條邊界管的
  是網路介面，stdio 沒有網路介面可管。
- 若日後要做 HTTP／SSE transport（讓遠端 agent 連），**必須另寫 ADR**，因為那時才會
  真正碰到 ADR-001 的信任邊界（誰能連、要不要認證），現在不預先決定。

### 6. 隱私邊界要出現在三個地方（不是只寫在 ADR 裡）

- 本 ADR（已寫）。
- `config.example.yaml` 的 `mcp:` 區塊行內註解：講清楚「唯讀、預設關閉、不會把資料傳到
  網路上（只有 stdio）」。
- `docs/USAGE.md` 新增一節：講清楚啟用方式、六個 tool 各自回什麼、以及「這不是給遠端
  用的」。

### 7. 不做清單（第一版刻意排除，避免範圍蔓延）

- **不做任何 write tool**——MCP 第一版純粹是讀取介面；要讓 agent 透過 MCP 觸發動作，
  是完全不同量級的信任決策，得先問「這跟 ADR-008 的三層閘門是什麼關係」，不在本 ADR
  範圍內，未來若要做必須另寫 ADR。
- **不做 HTTP／SSE transport**（見第 5 節）。
- **不暴露 RAG 文件切片**——`omni_search_history` 查的是 `core.semantic_index`
  （ADR-023 定案的「一份活動記憶」），不是 `rag/` 的文件知識庫索引；文件內容不透過
  MCP 外流，這條線刻意畫在「活動記憶」與「文件知識庫」之間，跟 ADR-023 的既有分工
  一致，不需要另外決定。

## Consequences

**好的**：

- 使用者現在的工作流（Claude Code／Codex）可以直接讀到 canonical context，不必開瀏覽器
  或貼剪貼簿——這是唯一「今天就有人用」的推廣路線項目（ROADMAP §14.1）。
- `mcp_server/` 完全獨立於主服務程序與執行器，關掉／移除它不影響任何既有功能；六個
  tool 全是既有函式的薄封裝，沒有第二套業務邏輯要維護。
- optional extra 的形狀完全複製 `[rag]` 的既有心智模型，使用者與貢獻者不需要學新規則。

**代價，如實記下**：

- 多一組六個 tool 的輸出 schema 要維護，且是對外契約（其他 agent 會依賴它的形狀），
  改動時比改內部函式簽章更貴——這也是為什麼第 3 節要求輸出形狀在 E3／E4 逐鍵鎖進契約
  測試。
- D1／D4／D5／D6 四道機器可查的門檻，每加一個新 tool 都要重新過一次，是額外的開發
  成本，但這正是「MCP client 輸入不可信」這個前提換來的，不能省。
- `mcp_server/` 若未來真的需要 write tool 或 HTTP transport，會是完全獨立的另一份 ADR
  與另一輪信任邊界討論，不能靠「已經有唯讀版本」順勢擴權——這是刻意的設計，不是遺漏。

## 尚未決定、留給實作 PR

- `[project.optional-dependencies].mcp` 裡 `mcp` SDK 的版本下限——留給 E3 動工時查
  當時的穩定版本。
- receipt 要落在既有的哪一張表或新開一張——若新開，是 append-only migration（進
  `core/migrations.py` registry），實作 PR 決定。
- `omni_search_history` 對 Ollama 不可用時的確切回應 schema——沿用
  `ask_local_context` 既有的錯誤字面（`looks_like_llm_error()` 那類判斷），但
  `omni_search_history` 本身不呼叫 LLM，這裡指的是底層 embedding provider（Ollama
  embeddings）不可用時的行為，細節留給 E3。

## 完成判準（收據，供 TODO E2 核對；E3／E4 是後續的實作驗收，不在本項範圍）

- ✅ 本 ADR 定稿（Accepted），且已回答外部檢視沒回答的兩件事：模組路徑
  （`mcp_server/`，不叫 `mcp/`）與 SDK 相依處理方式（optional extra `[mcp]`）。
- ✅ 六個 tool 的封裝對象、`source_ref` 內容、D1–D6 判準、不做清單、config／USAGE／ADR
  三處隱私邊界的分工，全部在本文件定案，E3／E4 動工時不需要再回頭做設計決策。
- 待 E3／E4：實作、契約測試、`docs/INDEX.md` 收錄本 ADR（本輪已加）、
  `python main.py verify` 判定不因本 ADR 的落地而改變（新增 A23–A26 是 E4 的範圍，
  不影響既有 22 項的既有判準）。
