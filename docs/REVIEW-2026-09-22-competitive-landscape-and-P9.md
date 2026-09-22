# REVIEW 2026-09-22：競品全景、差異化重評估與 P9 規劃

> 檢視日期：2026-09-22　｜　基準 commit：`main`（§11.2 第十五輪 / ADR-030 完成後）
> 本文性質：**外部競品快照 + 定位重評估 + 下一階段開發規劃**。
> 本文**不是**實測報告——§2 的競品資料來自 2026-09-22 的公開網路搜尋，未在本機安裝驗證任何一項；
> 所有數字與宣稱都標示來源（§10），過期或被廠商自報的部分已明確標記。
> 待辦條目與完成判準以 [`docs/TODO.md`](TODO.md) 為準，本文 §7 提供可直接貼入的條目。

---

## 校訂註記（2026-09-22 收錄時加上，正文一字未改）

本文是**外部產出**，收錄進 repo 時逐項對照過當時的 `main`。正文保持原樣，校訂寫在這裡。

**對照後成立的前提**

| 前提 | 收據 |
| :--- | :--- |
| 判斷三：本專案沒有 MCP server | 全 repo 搜 `mcp`／`MCP` 只有 `ROADMAP.md` 兩處**提及**（外部 Calendar MCP、`MCP/LabPagesCowork/` 目錄名），零實作、零相依 |
| 判斷二：差異化宣稱已過期 | 該絕對否定句確認存在於 `README.md`、`README_en.md`、`ROADMAP.md` §3.3、`docs/PRODUCT_POSITIONING.md` 四處（**已於本輪改寫**） |
| §A.2 六個 tool 都有既有模組可接 | `core/handoff_engine.py`、`core/semantic_index.py`、`core/project_engine.py`、`core/acceptance/` 皆存在 |
| 829 項測試 | 乾淨容器實測 `pytest tests/` → 829 passed ＋ 2 skipped，相符 |

**對照後不成立、或會誤導的前提**

1. **`ADR-031` 編號已被佔用**（影響最大）。`docs/ADR-031-omni-demo-dataset.md` 已於 2026-09-22 進 `main`（Accepted，`omni demo` 邊界）。
   本文 §5／§7 把 ADR-031 指派給 MCP server，照抄會覆蓋一份已定稿的 ADR。**MCP 的 ADR 編號改為 ADR-032。**
2. **`mcp/` 這個目錄名不能用**。`pyproject.toml` 的 `[tool.setuptools.packages.find].include` 是白名單，
   要讓 wheel 帶 `mcp/` 就得加 `"mcp*"`，於是 wheel 宣告一個 top-level `mcp` package，與 PyPI 上官方 MCP Python SDK 的 `mcp` 撞名。
   本文也**沒有回答** stdio server 要不要引入該 SDK 當相依——對一個剛把核心安裝從 721 MB 砍到 176 MB、把 RAG 降為 `[rag]` extra 的專案，這是頭等決策。兩者都留給 ADR-032 回答。
3. **§5 P9-C 少說了一半**。`python main.py verify` 當天的輸出是「🔴 仍擋 `release_ready`：**A1、A2**」；
   `docs/TODO.md` A 段的原句是「A1 是唯一還在擋 `release_ready` 的**能力型**缺口」，本文引用時掉了「能力型」三個字。
   此外 §12.3 的 G2 還缺 A2／A6／A12、G3 要走完 `RELEASE_CHECKLIST.md` 並在該 commit 有跨平台 CI run receipt、G4 要清掉 STATUS 的 `known_blockers`。
   **「2 週關掉 release_ready」不成立**，而且 A1 只能由使用者 Windows 實機跨午夜產生——它已經在 TODO A 段，不會在 E 段再寫一次。
4. **§5 P9-D 的 D-3（找 2–3 位外部使用者）同理**：是使用者側的事，不是開發輪次排得進的工作。
5. **數字微誤**：ADR 已是 **31** 份（本文寫 30）。

**未驗證、也無法在本 repo 驗證的部分**

§2 全部（五層競品、CVE 編號、暴露統計、benchmark 爭議、star 數）來自公開網路搜尋，
**本專案沒有安裝、執行或比對過任何一項**。本文 §11 自己也這樣寫。
因此這些內容**只用來排序工作，不進入任何對外宣稱**——
本輪改寫 README／ROADMAP 的字句時，刻意只陳述本專案做什麼，沒有引用 §2 的任何競品斷言。

**§7 條目的處置**

§7「可直接貼入 TODO」的 E1～E9 **未被採用**（編號衝突、順序與相依關係需調整、含兩項非開發工作）。
實際待辦以 [`docs/TODO.md`](TODO.md) E 段為準，排序理由見 [ROADMAP](../ROADMAP.md) §14。
本文其餘部分（§2 全景、§3 字句、§4 三項資產、§5 的 MCP 安全契約 D1–D6、§8 發表路線）維持有效參考。

---

## 0. 摘要：三個判斷

**判斷一：工程紀律是本專案在此賽道的異常值。**
30 份 ADR、829 項契約測試、差分收據（85 個合成狀態逐位元組比對，ADR-028）、DOM lock 突變驗證（ADR-029）、
三次自行偵測並修正「假綠燈」（A1 當天分母、A6 舊索引判綠、A21 running 當失敗）。
這些不是功能，是**可被第三方驗證的方法論資產**，而且在對照組裡找不到第二個。

**判斷二：README / ROADMAP §3.3 的差異化宣稱已經過期，必須改寫。**
> 現行字句：「**目前沒有主流工具在讀本機 AI agent 的 transcript。**」

2026 年 9 月這句話**不成立**。單平台 transcript 讀取工具已成生態（§2.4），
跨 agent session handoff 已有直接競品（§2.5）。
繼續用這句話對外，等於把唯一的技術宣稱建立在一個可被五分鐘搜尋推翻的前提上。
§3 提供替代字句。

**判斷三：最大的結構性落差不是功能，是介面——本專案沒有 MCP server。**
15 個月、約 393 萬字元的 canonical context，目前只有三種存取方式：
Web UI（要另開瀏覽器）、CLI（`omni ask` / `omni resume`）、剪貼簿（Context Handoff 貼進 AI）。
所有記憶層競品都已把記憶暴露成 agent 可直接讀取的介面。
**這是 P9-A 的全部理由，也是本文最高優先的建議。**

---

## 1. 檢視方法與邊界

| 項目 | 內容 |
| --- | --- |
| 資料來源 | 2026-09-22 公開網路搜尋（GitHub、廠商文件、資安研究報告、技術媒體） |
| 未做的事 | 未安裝、未執行、未 benchmark 任何競品；未取得任何競品的原始碼層比較 |
| 已知偏差 | 廠商自報數字（star 數、benchmark 分數、使用者數）一律標示為自報；資安事件以獨立研究機構報告為準 |
| 有效期 | 此領域 2026 年變動極快（Rewind 停止擷取、Screenpipe 換授權、OpenClaw 連續 CVE 均發生在 9 個月內）。**建議每季重跑一次本節** |

**不應從本文推論的事**：本文不證明任何競品「比較好」或「比較差」，只定位它們解決的問題與本專案是否重疊。

---

## 2. 競品全景（五層，不是一個賽道）

過去的內部比較（ROADMAP §3.3）只列了 ActivityWatch / RescueTime / Timing / Rewind / Screenpipe，
那是**第三層**。真正的競爭發生在五個不同的層，且只有兩層與本專案直接衝突。

| 層 | 代表 | 解決的問題 | 與本專案的關係 |
| --- | --- | --- | --- |
| L1 個人代理人執行體 | OpenClaw、Hermes Agent | 「替我做事、從哪裡叫得動」 | **不是競品，是下游消費者** |
| L2 記憶層 | mem0 / OpenMemory、Letta、Zep / Graphiti、Cognee、Supermemory、claude-mem | 「agent 跨 session 記得什麼」 | 部分重疊；**他們沒有 provenance** |
| L3 螢幕 / 活動擷取 | Screenpipe、Pieces、ActivityWatch、Rewind（已停） | 「我今天看過做過什麼」 | 本專案刻意不走 |
| L4 單平台 transcript 分析 | token-dashboard、vibe-log-cli、motocho、daily-claude-log 等 | 「Claude Code 花了多少 token、做了什麼」 | **正在吃掉本專案的宣稱** |
| L5 跨 agent session handoff | casr、cli-continues、`session-handoff` topic 諸專案 | 「換一個 CLI 接續同一個任務」 | **最直接的競品** |

### 2.1 L1 個人代理人執行體

**OpenClaw**（前身 Clawdbot → Moltbot）
- 自架開源個人 AI 助理，由 Peter Steinberger 建立，現由獨立的 OpenClaw Foundation（501(c)(3)）維護核心團隊與簽署發行。
- 透過 WhatsApp、Telegram、Slack、Discord、Signal、iMessage 等通道互動；plugin / skill 生態走 ClawHub；有 node 概念可配對遠端執行主機。
- 採用速度極快，三個月內成為 GitHub 星數最高的專案。

**安全記錄（這一段對本專案有戰略價值，見 §4.3）**
- CVE-2026-25253：Control UI 從 query string 接受 `gatewayUrl`、自動開 WebSocket 並送出已存 token，可導致 1-click 帳號接管 → RCE。
- 另有命令注入（CVE-2026-24763）、SSRF（CVE-2026-26322）、路徑穿越可讀本機檔案（CVE-2026-26329）。
- 2026-02 SecurityScorecard 觀察到 **40,214 個對外暴露執行個體，35.4% 被標記為有漏洞**；另有報導指出更高比例。
- Moltbook（agent 社群平台）資料庫零存取控制外洩，涉及約 150 萬筆 API token 與 3.5 萬筆 email。
- Koi Security 在 ClawHub 的 10,700 個 skill 中發現 **超過 820 個惡意項目**（數週前為 324 個）。
- 憑證以**明文**儲存，包含 LLM 供應商金鑰與各訊息平台 token。
- 2026-03 中國工信部限制國營企業與政府機關在辦公電腦執行 OpenClaw。

**Hermes Agent**（Nous Research，MIT）
- 主打**內建學習迴圈**：從經驗產生 skill、使用中改進 skill、提示自己保存知識、搜尋自己的過往對話、跨 session 建立對使用者的模型。
- 同一個 agent core 跑在 CLI、messaging gateway（約 20 個平台）、TUI 與 Electron 桌面版；可跑在 VPS 上，不綁筆電。
- 架構不變式明文寫在 `AGENTS.md`：**per-conversation prompt caching 神聖不可侵犯**，除 context compression 外不做任何會讓快取失效的事。
- **內建 OpenClaw 遷移**：`hermes setup` 自動偵測 `~/.openclaw` 並詢問是否匯入設定、記憶、skill 與 API key（`hermes claw migrate`）。

**結論：L1 與本專案根本不在比同一件事。**
它們比「能替我做多少事、從哪裡叫得動」；本專案比「我做過的事能不能被證明、被引用、被交接」。
**硬要在執行廣度上追它們是輸定的**，而且會把 ADR-008 建立的安全邊界拆掉。

### 2.2 L2 記憶層

| 系統 | 形狀 | 與本專案的差別 |
| --- | --- | --- |
| mem0 / OpenMemory | user / session / agent 三層 scope，vector + graph + kv 混合；OpenMemory 是本機優先的 MCP memory server，可搭配 Claude Desktop、Cursor、Windsurf、VS Code | **有 MCP、無 provenance**；記憶是 LLM 抽取後的斷言，不保留原始 row 指標 |
| Letta / MemGPT | agent 跑在框架**裡面**；core / recall / archival 三層，由 LLM 決定何時取回 | 記憶決策繼承 LLM 的不透明性——本專案 ADR-016/017/018 刻意拒絕的方向 |
| Zep / Graphiti | 時序知識圖譜，處理「事實隨時間改變」 | 本專案的 `last_seen_at` / `superseded` 是同一個問題的簡化版 |
| claude-mem | 擷取 agent 行為、AI 壓縮、注入未來 session；宣稱支援 Claude Code、OpenClaw、Codex、Gemini、Hermes、Copilot、OpenCode | **跨平台且已有 hook 整合**；但是壓縮後的摘要，不可回溯到原始 turn |
| mem0 Claude Code plugin | 本機擷取 evidence，記憶抽取送往 Mem0 平台背景 worker，新 session 自動 recall | 需要雲端 API key；與本專案 local-first 立場相反 |

**benchmark 現況（重要）**：LongMemEval 等指標正在信任危機中——
同一系統在不同 harness 下分數差異巨大（有報告指出 Mem0 的成績在另一家廠商的 harness 下掉到 73.8%），
該領域目前的建議是**永遠追問誰跑的、用什麼 harness**。
**這對本專案是利多**：「可驗證」正在變成稀缺品，而驗收中心（ADR-016）已經內建。

### 2.3 L3 螢幕 / 活動擷取

- **Rewind**：母公司 Limitless 被 Meta 收購後，擷取功能於 2025-12-19 停用。原始願景（本機螢幕錄製 + AI 搜尋）結束。
- **Screenpipe**（YC S26）：本機 24/7 擷取螢幕與音訊、以 accessibility API 取文字（OCR 為後備）、Whisper 本機轉錄；**已從 MIT 改為 source-available 的商業授權**，重新定位為「給 AI agent 的 workflow memory」。
- **Microsoft Recall**：2026-03 有研究者取出已加密的 Recall 資料。
- **Pieces**：開發者取向的**選擇性**記憶（非全量擷取），Long-Term Memory 維持約九個月滾動視窗，可問時間性問題；已提供 MCP server，免費層可走本機 Ollama。
- **ActivityWatch**：仍是本機時間追蹤的開源基準；已有 `daytrace` 這類工具把它的資料轉成日摘要與 handoff JSON。

**結論**：本專案「不錄螢幕」的立場在 2026 年被市場驗證是對的（Rewind 收場、Recall 出事、Screenpipe 換授權）。
但 **L3 已經全面往「給 agent 用的 memory」轉型**，這代表本專案的下一個競爭位置不在擷取，在介面。

### 2.4 L4 單平台 transcript 分析（宣稱過期的來源）

讀 `~/.claude/projects/` 的工具已經是一個生態，以下為抽樣：

| 工具 | 做什麼 |
| --- | --- |
| token-dashboard | 讀 Claude Code JSONL → 逐 prompt 成本分析、工具 / 檔案熱點、subagent 歸因、cache 分析、專案比較；全本機、無遙測、無 API 呼叫 |
| vibe-log-cli | 分析 Claude Code 與 Codex session 產生 **standup 摘要**，透過本機 AI（ACP）執行，資料不出機器 |
| motocho | 桌面儀表板：session transcript 瀏覽 / 搜尋、token 與成本、MCP server 管理、報告生成；來源包含 Claude Code 與 Codex |
| claude-hindsight | Rust；OTLP 接收器 + 檔案監看 + SSE，node hierarchy 樹狀重建 |
| daily-claude-log | 從 Claude Code transcript 收集檔案 / 工具 / commit / PR / ticket 進本機 SQLite，按本地日期切分（跨午夜正確歸戶），再產日報 |
| simonw/claude-code-transcripts | 把 session 轉成可發佈的 HTML |

**重疊分析**：它們幾乎都是**單平台（Claude Code）＋成本 / 統計取向**，
沒有跨 AI 合一、沒有專案歸戶、沒有 open loop、沒有 provenance 契約。
但 `daily-claude-log` 的「跨午夜按本地日期切分 + SQLite + 日報」與本專案 P2.6 / activity_digest **高度相似**。

### 2.5 L5 跨 agent session handoff（最直接的競品）

| 專案 | 做什麼 | 與 P3-1 Context Handoff 的關係 |
| --- | --- | --- |
| **casr**（`cross_agent_session_resumer`） | 透過 canonical IR 轉換 Codex、Claude、Gemini 等 session 格式，讓同一個 session 在任一 CLI 接續；明確訴求換模型不重建脈絡、供應商當機 / 限流時轉移 | **核心宣稱與 P3-1 重疊**；但它轉的是 session 格式，不是專案狀態 |
| **cli-continues** | 宣稱可跨 16 種 CLI 工具找出上千個 session，產生 handoff 文件讓接手 agent 知道動過哪些檔案、跑過哪些指令、還剩什麼 | **與 P3-1 的 handoff 文件形狀幾乎相同** |
| `session-handoff` GitHub topic | 多個 local-first CLI：checkpoint、跨 agent 訊息、從 live pane 交接 | 生態已成形 |

**另一個結構性訊號**：AGENTS.md 於 2025-08 由 OpenAI 發布，2025 年底**移交 Linux Foundation 的 Agentic AI Foundation（AAIF）**，
與 Anthropic 的 MCP、Block 的 Goose 並列。
→ **跨工具脈絡正在標準化，而標準的載體是 MCP 與檔案，不是儀表板。**

---

## 3. 差異化宣稱重評估（需要改寫的具體字句）

### 3.1 必須改寫

| 位置 | 現行字句 | 問題 | 建議字句 |
| --- | --- | --- | --- |
| `README.md` §「這個專案的差異化定位」 | 「目前沒有主流工具在讀本機 AI agent 的 transcript」 | **事實已不成立**（§2.4） | 「讀本機 AI agent transcript 的工具在 2026 年已成生態，但幾乎都是**單一平台、成本 / 統計取向**。OmniContext 的差別是把多個 AI 的 transcript 與 Git、檔案、前景活動、GitHub PR **收斂到同一個 canonical project state**，且每個結論都保留可回查的 `source_ref` 與 trust status。」 |
| `README_en.md` 對應段落 | 同上 | 同上 | 同上英譯 |
| `ROADMAP.md` §3.3 | 「沒有主流工具在讀本機 AI agent 的 transcript。這是本專案的差異化切入點。」 | 同上 | 改為指向 §4 的三項，並註記「competitive snapshot 見 REVIEW-2026-09-22」 |
| `docs/PRODUCT_POSITIONING.md` | （需逐條複查） | 可能含同一宣稱 | 同步更新，並新增 non-claim：「不宣稱是唯一讀取本機 transcript 的工具」 |

### 3.2 可以保留但要加限定詞

- 「canonical context 屬於使用者與專案，不屬於任何一家 AI provider」→ **保留**，這句仍然成立且 §2.2 的競品多半做不到（記憶多半是 LLM 抽取後的斷言）。
- 「不需錄螢幕、不需額外權限」→ **保留並強化**，L3 的 2026 事件（§2.3）是這個立場的外部驗證，值得在 README 直接引用。

### 3.3 應該新增的宣稱

> **「本專案唯一堅持的事：任何顯示給你的結論，都指得回一筆 SQLite row。」**

這句在 §2 的五層裡沒有第二個專案做得到，而且是 22 項驗收、ADR-016/028、三次假綠燈修正的**共同主題**。
建議把它放在 README 第一屏。

---

## 4. 刨乾淨之後，仍然無可替代的三件事

### 4.1 多源合一的 canonical project state

競品各做一種來源：token-dashboard 做 token、casr 做格式轉換、mem0 做斷言抽取。
本專案把 AI turns + git commits + 檔案異動 + 前景時間 + 行事曆 + GitHub PR
收斂到**同一個專案身分**下（`core/project_engine` 的 Top-Down Canonical Resolver + `core/activity_sources` 的單一定義）。
→ casr 轉得動格式，轉不出「這個專案上週有 5 天在動、論文只有 1 天」（ADR-020）。

### 4.2 證據等級的誠實性

`turn_key` / `response_status` / coverage ledger 的 `observed` vs `partial` /
fail-closed migration / `not_configured` 與 `runtime_only` 分開記帳 /
similarity 不作真實性證明的明文邊界 / 署名永不覆蓋機器判定（ADR-016）。
→ **對照組裡零個專案在做這件事**，而 §2.2 的 benchmark 信任危機正在讓它變值錢。

### 4.3 安全執行架構（被低估最嚴重的資產）

把 §2.1 的 OpenClaw 事故逐項對照 ADR-008：

| OpenClaw 的失效 | 本專案的既有對應 |
| --- | --- |
| Control UI 從 query string 接 gatewayUrl → token 外洩 | ADR-001 deny-by-default Origin 邊界、loopback-only、`extra=forbid` schema |
| 命令注入 | `create_subprocess_exec` argv 白名單、**禁 `shell=True`**（P2.5-S1 起全域強制） |
| 憑證明文儲存 | `config.yaml` 只存 `api_key_env` 變數名；env allowlist 重建，**任何 API key 都不轉發給子程序** |
| 4 萬個對外暴露執行個體 | 從未提供 `allow_remote_clients` 以外的對外路徑；C5 因此暫停 |
| 惡意 skill 供應鏈 | 沒有 skill marketplace；動作只能是 server 註冊的白名單 template |
| 無審核的自主執行 | 三層開關（executor → l2 → l2.allow_write）預設全關 + 一次性 6 碼 + audit receipt + 髒 worktree 即拒 + 永不 commit/push |

**這不只是安全功能，是一篇可發表的對照研究**（見 §8）。

---

## 5. P9 規劃

> 排序理由：**P9-A 改變本專案的存取形狀（影響最大、風險最低）；P9-B 是唯一有外部需求的資產；
> P9-C 是唯一擋住 release 的能力缺口；P9-D 是最大的存活風險。**
> P9-A / P9-C / P9-D 可平行；P9-B 依賴 D9（已完成）。

### P9-A　唯讀 MCP Context Server（最高優先，目標 4 週）

#### A.0 為什麼是現在

- 本專案所有價值目前困在 `127.0.0.1:8765` 的 HTML 與剪貼簿裡。
- 使用者的實際工作流是 Claude Code / Codex / Antigravity，**不是瀏覽器**。
- MCP 已移交 Linux Foundation AAIF（§2.5），是跨工具脈絡的事實標準載體。
- 競品（OpenMemory、Pieces、claude-mem）都已走這條路，**但沒有一個帶 provenance**。
- 做完之後，OpenClaw 與 Hermes 從競爭者變成**消費者**——它們都能讀本專案的脈絡層。

#### A.1 先寫 ADR-031（Proposed → Accepted 才動工）

**決策**：以獨立、預設關閉、**唯讀**的 MCP server 暴露 canonical context。

**Transport 選擇：第一版只做 stdio**
- 理由：stdio 是子程序管線，**不是網路介面**，因此完全不動 ADR-001 的 loopback 邊界，不需要新 port、不需要新認證形狀。
- Claude Code / Codex / Cursor 皆支援 stdio MCP。
- HTTP / SSE transport 若要做，**另寫一份 ADR**（與 C5 同一個邊界問題）。

**實作形狀**
```
mcp/
├── server.py        # MCP protocol handler（stdio, JSON-RPC）
├── tools.py         # 六個 tool 的 schema 與 dispatch
├── readers.py       # 唯讀查詢：只呼叫既有 core 模組，不經過 FastAPI
└── receipts.py      # tool call receipt（不含 query 原文）
```
- CLI entry：`omni mcp`（wheel 安裝為 `omnicontext mcp`）
- **不要求主服務在跑**：直接讀 SQLite。runtime-only 狀態（檢索 worker）誠實回 `unavailable`，
  比照 ADR-016 的 `runtime_only` 字彙，**不以「查不到」冒充「沒發生」**。
- 語意檢索走既有 `core/semantic_index` 的 loopback Ollama；Ollama 不可用時降級為明確 `unavailable`，**不 fallback 到 cloud**（ADR-023 邊界不變）。

#### A.2 第一版 Tool 介面（六個，全部唯讀）

| Tool | 參數 | 回傳重點 |
| --- | --- | --- |
| `omni_project_state` | `project?`, `status?`(active/idle/stale), `limit?` | canonical 專案名、`last_activity`、`idle_days`、repo path、git 狀態 chip（附 `last_fetch_at`）、open loop 計數 |
| `omni_handoff` | `project`, `turns?` | 既有 `core/handoff_engine` 產物：provider-neutral markdown + structured fields |
| `omni_search_history` | `query`, `project?`, `since?`, `limit?` | **retrieval-only**，每筆帶 `[S1]` 風格 id、`source_ref`、project、timestamp、trust、similarity |
| `omni_open_loops` | `project?`, `status?` | open / stale / resolved / superseded、`last_seen_at`、fingerprint、來源 |
| `omni_work_sessions` | `project?`, `hours?` | derived sessions（ADR-006）：穩定 session ID、AI/Git/file 計數、`source_ref` |
| `omni_recent_digest` | `date?` 或 `weeks_back?` | 讀取**已存在**的工作誌 / 每週回顧觀察；沒有就誠實回「該日無觀察」，**不即時產生** |

**刻意的分工**：`omni_search_history` **不做 LLM 合成**。
合成交給呼叫端 agent（它本來就是 LLM），本 server 只負責「找得到、指得回」。
這讓 server 保持零 LLM 依賴，也避免兩層 LLM 疊加造成不可歸因的答案。

#### A.3 第一版**明確不做**

| 不做 | 理由 |
| --- | --- |
| **任何 write tool** | MCP client 的輸入是不可信的（indirect prompt injection 的標準入口）。這正是 §2.1 OpenClaw 出事的地方 |
| 與 executor / dispatcher / scheduled_tasks 的任何耦合 | 同上。契約測試**禁止 import** `core.agent_executor`、`core.agent_dispatch`、`core.secretary.scheduled_tasks` |
| 暴露 config、token、secret、本機絕對路徑 | 比照既有 API privacy 契約（P2.5-S1） |
| 暴露 RAG 文件切片 | 那是使用者資料夾內容，另一個隱私面。第一版不含，待評估後另議 |
| HTTP / SSE transport | 動到 ADR-001，另寫 ADR |

#### A.4 安全契約（寫進 ADR-031 D1–D6）

- **D1 唯讀**：`mcp/` 模組原始碼不得出現 `INSERT` / `UPDATE` / `DELETE` / `session.add` / `commit`；
  契約測試比照 `core/acceptance`：**跑完一輪所有資料表列數不變**。
- **D2 預設關閉**：`mcp.enabled: false`；開啟位置在「06 系統設定 → 秘書與自動化」旁新增一格。
- **D3 不轉發金鑰**：env allowlist 重建（沿用 `core/agent_dispatch` 既有做法）。
- **D4 輸出邊界**：無 token / secret / 本機絕對路徑；`source_ref` 是 SQLite row 指標，不是檔案路徑。
- **D5 不可執行**：MCP surface 與執行器零耦合，契約測試守門。
- **D6 可觀察**：每次 tool call 寫一筆 receipt（tool 名、結果筆數、耗時、成功/失敗），
  **不記 query 原文**（沿用 ADR-013「query 不保存」原則）。

#### A.5 隱私邊界（必須在 ADR、config 註解、USAGE 三處寫明）

> MCP client 是**你自己啟動的本機 agent**。你透過它取得的 transcript 節錄、handoff 與檢索結果，
> **會進入該 agent 的 context**；若該 agent 使用雲端供應商，這些內容會送往該供應商。
> 這與儀表板上選 Gemini / Claude / OpenAI 產生摘要是同一個邊界，但**觸發者是 agent 不是你**，所以預設關閉。
> `mcp.redact.*` 可設定只回 metadata 不回內容。

#### A.6 驗收（新增 A23–A26 至驗收中心）

| 項目 | 判準 | 機器可查？ |
| --- | --- | --- |
| A23 | `omni mcp --selftest` 六個 tool 各回一次，全部帶 `source_ref` | ✅ |
| A24 | 實機以 Claude Code 掛上 MCP，問「我上次在 X 做到哪」，答案的引用回查得到 SQLite row | ❌ `needs_human` |
| A25 | 唯讀證明：selftest 前後所有資料表列數不變 | ✅ |
| A26 | tool call receipt 存在且**不含** query 原文 | ✅ |

#### A.7 Contract tests：`tests/test_mcp_server.py`（預估 ~20 項）

必含的守門測試：
1. `mcp/` 不得 import 執行器三模組（掃 import）
2. `mcp/` 原始碼零寫入關鍵字
3. 跑完一輪 selftest 後列數不變（真 tmp DB）
4. 六個 tool 的回傳 schema 逐鍵比對（比照 ADR-024 `Proposal.to_dict()` 的做法）
5. 每筆結果必有 `source_ref` 且回查得到
6. `mcp.enabled: false` 時 `omni mcp` 拒絕啟動並說出原因
7. 輸出不含 token / secret / 絕對路徑（正則掃描）
8. Ollama 不可用時 `omni_search_history` 回 `unavailable` 而非空陣列
9. receipt 不含 query 原文
10. `omni_recent_digest` 對沒有觀察的日期回「無觀察」而非編造

#### A.8 P9-A 的收據要求（沿用既有標準）

- `pytest` 全綠；不裝 `[rag]` 亦全綠
- `python main.py verify` 輸出與基底 commit **逐位元組相同**（本階段不動既有產品程式碼）
- `python -m build` + `verify_release_artifacts.py` `status: passed`，wheel 含 `mcp/` 全部模組
- `scripts/dashboard_dom_lock.py check` 22 張逐字元相同（若動到設定 UI 則需重錄並說明）

---

### P9-B　抽出 `agent-transcripts` 獨立套件（目標 6 週，可在 P9-A 後啟動）

#### B.1 為什麼提前

ROADMAP §13.3 已列為推廣路線第一項。§2.5 的外部證據顯示**需求已被驗證**（casr、cli-continues 有人做、有人用），
但**沒有一個帶 drift 偵測**——而那才是真正的難題：
> 四種格式都是別家工具的私有格式；格式一變，parser 不會拋例外，它會正常跑完、產出零筆事件。
> `healthy` 只證明沒有拋例外，不證明有採集到東西。（ADR-025）

D9 已經把 parser 拆成每平台一個模組並實作漂移警示，**抽出成本是本專案歷史上最低的一次**。

#### B.2 套件邊界

**進套件**（零 OmniContext 依賴、零 DB、零網路）：
```
agent_transcripts/
├── types.py      # Turn dataclass: platform, turn_key, role, text, timestamp,
│                 #   source_path, source_position, response_status, session_id
├── base.py       # discover(cfg, *, full_history, now) / parse(path, *, cfg, now)
├── claude_code.py / claude_desktop.py / codex.py / antigravity.py
├── drift.py      # detect_drift(...) -> DriftReport（純函式）
└── registry.py   # 第三方 parser 註冊介面
```

**留在 OmniContext**：ingest、去重 / upsert、專案歸戶、SQLite、provenance 寫入、健康頁串接。

#### B.3 驗收

- OmniContext 改吃套件後：`python main.py verify` 輸出與改動前**逐位元組相同**
- D9 的 23 項測試搬到套件後仍全綠；OmniContext 端保留「服務不認識任何格式」的守門測試
- 套件自帶匿名 fixture（四種格式各 ≥ 2 個），**不得夾帶任何真實 prompt / 路徑 / 機器名**（契約測試守門）
- PyPI 發佈 + 英文 README + 一篇技術文章（主題見 §8）

---

### P9-C　關掉 `release_ready` 的最後一哩（並行，目標 2 週）

**唯一剩下的 🔴 能力缺口是 TODO A1：全天 coverage ledger 實機收據。**

具體怎麼拿（含 §11.2 已學到的陷阱）：
1. 選一天，機器**不關機、不休眠**，OmniContext 背景服務連續運行滿 24 小時（跨午夜）。
2. **隔日**執行 `python main.py verify --item A1`。
3. ⚠️ **當天的比例不算數**——`get_daily_coverage` 對當天的分母是「今天到目前為止經過的時間」，
   早上跑三小時可以得到 97% 但那不是全天 coverage。A1 只採計 `offset ≥ 1` 的已結束日子。
4. 取得後更新 STATUS.yaml `known_blockers`，並依 ROADMAP §12.3 的四個條件逐項複查。

接著：走完 `docs/RELEASE_CHECKLIST.md`，在該 commit 上重跑跨平台 CI（**不沿用舊 run**），打 `v1.3.0` 正式 tag。

---

### P9-D　外部使用者（最大的存活風險）

**現況**：829 項測試、30 份 ADR、22 項驗收、**0 star、0 fork、1 個使用者**。
所有驗收收據都是自己給自己的。第二個使用者會找出 22 項驗收永遠找不到的東西。

| 步驟 | 內容 | 完成判準 |
| --- | --- | --- |
| D-1 | `omni demo` 匿名示範資料集（假 transcript × 4 平台 + Git + 檔案事件 + 2 週活動矩陣） | 乾淨環境 5 分鐘內看到首頁焦點卡與一份 Handoff；示範資料**不得**混入正式 DB（獨立 `OMNICONTEXT_HOME`） |
| D-2 | `omni init` 自動偵測 `~/.claude` / `~/.codex` / Antigravity 路徑並詢問匯入 | 不需手改 `config.yaml` 即可完成首次啟動；偵測不到時明確說出找了哪些路徑 |
| D-3 | 找 2–3 位實際使用 Claude Code 的使用者安裝 | 每人回報一份 `python main.py verify --json`；至少一項驗收在他人機器上失敗並修掉 |

---

## 6. 停做 / 延後清單（附理由）

| 項目 | 處置 | 理由 |
| --- | --- | --- |
| **C6 LINE 雙向** | **砍掉**，不是延後 | ADR-014 已自行論證：需要公開 HTTPS webhook，打破 ADR-001；換來的能力 Telegram 已完全具備 |
| **C5 私有網路遠端存取** | 延到 P9-A 之後重評 | MCP 做完後「手機看儀表板」的需求會明顯下降。若仍需要，優先評估 Tailscale Serve 形狀（維持 loopback） |
| **會議秘書第二層（即時音訊）** | 延後 / 不做 | Screenpipe 已在做本機 24/7 音訊擷取與 Whisper 本機轉錄（§2.3）。本專案在這條線上不具比較優勢；ADR-022 D6 的五道門仍然有效 |
| **DeskRAG 繼續擴張** | 凍結在現狀 | REVIEW-2026-09-16 自述「市面都有更成熟的替代品」。D7 已把活動記憶與文件分開，維持 `[rag]` 選用即可 |
| **C3 其餘採集來源** | 維持「能否改變決策」檢驗 | 門檻不變。**P9-A 完成前不新增任何採集來源** |
| **新增 L2 template** | P9-A 完成前凍結 | 每多一個可執行動作，MCP 的唯讀邊界就多一個要證明的東西 |

---

## 7. 可直接貼入 `docs/TODO.md` 的條目

```markdown
### E 段：P9 介面化與外部化（2026-09-22 新增，見 REVIEW-2026-09-22）

- [ ] **E1　ADR-031 唯讀 MCP Context Server（Proposed）**
      完成判準：ADR 含 transport 選擇理由、六個 tool schema、D1–D6 安全契約、
      隱私邊界三處（ADR / config 註解 / USAGE）、不做清單。Accepted 後才動工。

- [ ] **E2　`mcp/` 實作 + `omni mcp` CLI**
      完成判準：tests/test_mcp_server.py ~20 項全綠（含唯讀列數不變、禁 import 執行器、
      schema 逐鍵比對、輸出無 secret/絕對路徑、Ollama 不可用回 unavailable、receipt 無 query 原文）；
      pytest 全綠且不裝 [rag] 亦全綠；verify 輸出與基底逐位元組相同；
      build + verify_release_artifacts passed 且 wheel 含 mcp/。

- [ ] **E3　驗收中心新增 A23–A26**
      完成判準：A23/A25/A26 機器可查；A24 標 needs_human；
      四項納入 checklist API 與 06 設定頁；宣告式表格（ADR-028）形狀不變。

- [ ] **E4　A24 實機收據**：以 Claude Code 掛上 MCP 問一次「我上次在 X 做到哪」，
      答案的引用回查得到 SQLite row。（👤 使用者實機）

- [ ] **E5　抽出 `agent-transcripts` 套件**
      完成判準：零 OmniContext 依賴；D9 的 23 項測試搬過去全綠；
      OmniContext 改吃套件後 verify 逐位元組相同；fixture 不含真實 prompt/路徑/機器名；
      PyPI 發佈 + 英文 README。

- [ ] **E6　README / README_en / ROADMAP §3.3 / PRODUCT_POSITIONING 的差異化字句改寫**
      完成判準：移除「沒有主流工具在讀本機 AI agent transcript」；
      改為 REVIEW-2026-09-22 §3.1 的建議字句；新增 non-claim「不宣稱是唯一讀取本機 transcript 的工具」。

- [ ] **E7　`omni demo` 示範資料集**
      完成判準：乾淨環境 5 分鐘內看到首頁焦點卡與一份 Handoff；
      示範資料走獨立 OMNICONTEXT_HOME，絕不混入正式 DB（契約測試守門）。

- [ ] **E8　`omni init` 自動偵測 agent 日誌路徑**
      完成判準：不改 config.yaml 即可完成首次啟動；偵測失敗時列出找過哪些路徑。

- [ ] **E9　2–3 位外部使用者安裝**
      完成判準：每人回報一份 `python main.py verify --json`；
      至少一項驗收在他人機器上失敗並修掉。（👤 使用者實機）
```

**A1 條目補充**（既有 TODO A1 加註）：
```markdown
      ⚠️ 陷阱提醒：當天的 coverage 比例不算數（分母是「今天到目前為止」）。
      必須機器不休眠連續跑滿一天，隔日以 offset ≥ 1 查前一天。
```

---

## 8. 學術與對外發表路線

本專案有兩個罕見素材，都比「我做了一個 tracker」有價值：

### 8.1 主線：自動化驗收本身會說謊

**素材**：三次自行偵測並修正的假綠燈——
- A1：`get_daily_coverage` 對當天的分母是已過時數，半天 97% 被當成全天 coverage 通過（2026-09-05）
- A6：`index_present()` 只看目錄存不存在不看 chunk 數；預熱 idempotent 短路導致用舊收據判綠（2026-09-07 兩輪）
- A21：進行中的 VACUUM 被報成「沒有完成」，指向一個會被拒絕的下一步（2026-09-08）

**題目方向**：*Evidence-grade instrumentation for AI-assisted development: provenance contracts and the false-green problem*

**論點**：在 AI 輔助開發中，「測試通過」與「功能可用」的距離被系統性低估；
本文提出 provenance contract（`turn_key` / `response_status` / coverage ledger）
與 declarative acceptance ladder（ADR-028）作為可重跑的機器判準，並以三個真實假綠燈案例說明
**狀態訊息指向錯誤的下一步也是 bug**。

**形式**：先寫 experience report。等 `agent-transcripts` 有第二位使用者再談實證。

### 8.2 副線：個人 agent 的安全邊界對照研究

以 §4.3 的表格為骨架，對照 OpenClaw 2026 年的公開 CVE 與暴露統計，
論證「分級開關 + argv 白名單 + env allowlist + 一次性確認碼 + audit receipt + 永不 commit/push」
是個人 agent 可行的最小安全集合。**這篇的時效性很強，建議在 §2.1 的資料過期前寫。**

---

## 9. 定位一句話（建議放上 README 第一屏）

> **從「個人全景活動追蹤」改為「AI 工作脈絡的證據層」。**
> 不跟 OpenClaw / Hermes 比執行力，比「你做過的事能不能被引用、被驗證、被交接」。
> 然後把它開成 MCP，讓它們都來讀。

---

## 10. 參考來源（2026-09-22 擷取）

**L1 個人代理人執行體**
- OpenClaw 官方 repo：https://github.com/openclaw/openclaw
- OpenClaw（Wikipedia，含中國限制措施）：https://en.wikipedia.org/wiki/OpenClaw
- Hermes Agent：https://github.com/NousResearch/hermes-agent
- Hermes `AGENTS.md`（架構不變式）：https://github.com/NousResearch/hermes-agent/blob/main/AGENTS.md

**OpenClaw 安全事件**
- Giskard（資料外洩與 prompt injection）：https://www.giskard.ai/knowledge/openclaw-security-vulnerabilities-include-data-leakage-and-prompt-injection-risks
- IBM X-Force（agentic AI 風險分類、ClawJacked）：https://www.ibm.com/think/x-force/what-openclaw-reveals-about-agentic-ai-security-risks
- Sangfor（CVE-2026-25253 等、SecurityScorecard 暴露統計）：https://www.sangfor.com/blog/cybersecurity/openclaw-ai-agent-security-risks-2026
- Dark Reading（惡意 skill 統計、Oasis Security 揭露）：https://www.darkreading.com/application-security/critical-openclaw-vulnerability-ai-agent-risks
- DigitalOcean（明文憑證、Moltbook 外洩）：https://www.digitalocean.com/resources/articles/openclaw-security-challenges
- Adversa（時間軸與 hardening）：https://adversa.ai/blog/openclaw-security-101-vulnerabilities-hardening-2026/

**L2 記憶層**
- mem0 State of AI Agent Memory 2026（OpenMemory MCP 定位）：https://mem0.ai/blog/state-of-ai-agent-memory-2026
- mem0 Claude Code plugin：https://docs.mem0.ai/integrations/claude-code
- Mnemoverse Q3 2026 比較（benchmark 再現性爭議）：https://mnemoverse.com/docs/library/ai-memory-solutions-2026-q3
- Graphlit（memory vs context 分層）：https://www.graphlit.com/blog/survey-of-ai-agent-memory-frameworks
- Atlan（框架選型）：https://atlan.com/know/best-ai-agent-memory-frameworks-2026/
- claude-mem：https://github.com/thedotmack/claude-mem

**L3 螢幕 / 活動擷取**
- Screenpipe（Rewind 停止擷取、授權變更）：https://screenpipe.com/blog/rewind-ai-alternative-2026
- Screenpipe 個人記憶現況（Recall 資料被取出）：https://screenpipe.com/blog/personal-ai-memory-2026
- Screenpipe 替代品比較（Pieces LTM 九個月視窗、授權議題）：https://www.usecarly.com/blog/screenpipe-alternatives/
- Pieces MCP：https://docs.pieces.app/products/mcp/continue-dev

**L4 單平台 transcript 分析**
- token-dashboard：https://github.com/nateherkai/token-dashboard
- vibe-log-cli：https://github.com/vibe-log/vibe-log-cli
- motocho：https://github.com/dinogit/claude-code-dashboard
- claude-hindsight：https://github.com/codestz/claude-hindsight
- daily-claude-log：https://pypi.org/project/daily-claude-log/
- simonw/claude-code-transcripts：https://github.com/simonw/claude-code-transcripts

**L5 跨 agent handoff / 標準化**
- casr：https://github.com/Dicklesworthstone/cross_agent_session_resumer
- cli-continues：https://github.com/yigitkonur/cli-continues
- `session-handoff` topic：https://github.com/topics/session-handoff
- AGENTS.md 與 Linux Foundation AAIF：https://codex.danielvaughan.com/2026/05/27/agent-instruction-files-agents-md-claude-md-cross-tool-portability-codex-cli/

---

## 11. 本文的邊界（如實記下）

- §2 全部來自公開搜尋，**未安裝驗證任何一項**。star 數、使用者數、benchmark 分數凡屬廠商自報者已標示。
- §2.4 / §2.5 的「重疊分析」是讀 README 與說明頁的判斷，**不是原始碼比對**。
  若要作為對外宣稱的依據，需實際安裝 casr 與 cli-continues 各跑一次並留收據。
- §5 的工作量估計（4 週 / 6 週 / 2 週）是規劃值，**沒有依據**，僅供排序用。
- §8 的學術路線未經任何同儕確認，只是題目建議。
- 本文**不改** `STATUS.yaml`，也不改 `release_ready`；那仍由人在走完 ROADMAP §12.3 四項後決定。
