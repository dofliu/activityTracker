# ADR-025：每個 AI 平台一個 transcript parser，外加「檔案在動、事件是零」的漂移警示

- 狀態：**Accepted**（2026-09-17 起草並於同日實作，TODO D9）
- 關聯：[ADR-006](ADR-006-derived-context-sessions-and-related-history.md) 對話輪次與 provenance、[ADR-010](ADR-010-verified-background-agent-task-time.md) 可驗證背景 Agent 任務時間
- 依據：[docs/REVIEW-2026-09-16-project-assessment.md](REVIEW-2026-09-16-project-assessment.md) §4.3 第 3 點

## Context

`watchers/agent_log_watcher.py` 是 1,066 行的單一檔案，裡面塞了兩件性質完全不同的事：

1. **採集服務的骨架**：執行緒、自我修復、`IngestionCheckpoint` 讀寫、寫入 `AIPromptEvent` 的 upsert、
   來源層級的故障隔離與 diagnostics。這一層與「解析哪一種格式」無關。
2. **四個平台、五套 parser**：Claude Code 的 `projects/**/*.jsonl`、Claude Code 的 `history.jsonl` 備援、
   Claude Desktop 的 transcript（與 Claude Code 同一種格式、不同的探索規則）、Codex 的 `.json` 舊版
   session、Codex 的 `.jsonl` rollout、Antigravity 的 `transcript.jsonl`。

這造成兩個具體問題：

- **改一個平台要讀全部**。Codex 的 `item_completed` 事件形狀變了，要改的那段程式夾在 Claude 的配對邏輯
  與 Antigravity 的脫殼規則中間；沒有任何邊界說明「動這裡不會影響別人」。
- **四種格式都沒有穩定性保證，而失效是靜默的**。這四個都是別家工具的私有格式，沒有版本、沒有 schema、
  不會公告變更。測試用的是合成 fixture——它鎖的是**今天的形狀**。真的格式一變，parser 不會拋例外，
  它會**正常跑完、產出零筆事件**：checkpoint 照常前移、diagnostics 一片 `healthy`、系統健康頁全綠，
  而使用者要等到某天問「我上週跟 Codex 討論什麼」得到空白，才會發現已經漏了一個月。

第二點才是真正的風險。本專案的收據文化是「測試通過不等於實機可用」；在這裡它的具體形式是：
**`healthy` 只證明沒有拋例外，不證明有採集到東西**。

## Decision

### 1. 一個平台一個模組，服務骨架留在原處

```
watchers/transcripts/
  base.py            共同介面：TranscriptTurn／TurnEvidence／TranscriptSource ＋ 跨平台的文字與時間工具
  claude_code.py     Claude 的 JSONL 格式（探索、配對、Claude 專用的 user／assistant 取文）
  claude_desktop.py  Claude Desktop 的探索規則（回看視窗），格式沿用 claude_code
  codex.py           Codex 的 history.jsonl ＋ .json 舊版 session ＋ .jsonl rollout
  antigravity.py     Antigravity 的 transcript.jsonl（USER_REQUEST 脫殼、PLANNER_RESPONSE 取答）
  drift.py           漂移判定（純函式，不碰 DB 也不碰檔案系統）
```

`watchers/agent_log_watcher.py` 保留**服務**：執行緒與自我修復、`_should_scan_file`／`_mark_file_scanned`
的 checkpoint 規則、`_upsert_ai_event`、來源隔離與 diagnostics。它不再知道任何一種格式長什麼樣。

**共同介面只有兩個函式**，每個平台模組各實作一次：

```python
discover(cfg, *, full_history) -> Iterable[Path]      # 這個平台的 transcript 檔在哪
parse(path, *, cfg)            -> Iterator[Transcript Turn]   # 這個檔案裡有哪些輪次
```

`parse` 是**產生器且不碰資料庫**：它把一輪對話描述成 `TranscriptTurn`（含 `response_status`、
`source_path`／`source_position` 這些 provenance 欄位，以及需要時的 `TurnEvidence` 背景工作證據），
由服務決定怎麼寫入。好處是每個 parser 都能在沒有資料庫、沒有設定檔的情況下單獨測。

`claude_desktop.py` **明著** import `claude_code.parse`：Desktop 寫的就是同一種 JSONL，差別只在檔案在哪、
以及首次啟用只回補近期資料。與其複製一份配對邏輯，不如讓這個依賴看得見——格式模組是 `claude_code`，
探索模組是 `claude_desktop`，這一行 import 就是這件事的說明。

### 2. 漂移警示：檔案在動、事件是零

`watchers/transcripts/drift.py` 是一個純函式：

```
某平台被判定為漂移 ⟺ 這次掃描看到的最新檔案 mtime 落在視窗內
                    ∧ 該平台在視窗內沒有任何 AIPromptEvent
```

兩個條件缺一不可，這正是它不會誤報的原因：

- 使用者這一週沒開 Codex → 沒有檔案被更新 → 不警示（「沒用」不是故障）。
- 使用者關掉了某個來源 → 該來源不進入掃描 → 不警示。
- 平台目錄不存在 → 探索回傳空 → 不警示。
- 使用者昨天用了 Codex、parser 也正常 → 視窗內有事件 → 不警示。
- 使用者昨天用了 Codex、parser 悄悄失效 → **檔案有更新、事件是零 → 警示**。

視窗是程式常數 `DRIFT_WINDOW_DAYS = 3`，不開設定鍵：D6 才把 22 個調校鍵移出設定檔，這裡不該再加一個。
三天的理由是它要跨得過週末的使用空檔，又不至於讓一個月的靜默無人發現。

警示出現在 `get_diagnostics()["drift"]`，經 `core/manager.py` 進 `collector_diagnostics.agent_log_watcher`，
在系統健康頁與採集器卡片上顯示；並且**把 `agent_log_watcher` 的 collector health 標成 `degraded`**——
這是重點：沉默的零事件本來就是故障，它必須讓整體監控狀態變色，而不是躲在一個沒人展開的欄位裡。

漂移判定所需的兩個輸入都在掃描時就算好（每個來源探索到的最新 mtime、一次 group-by 查出的各平台最後事件時間），
存在服務的 diagnostics 快照裡，所以狀態 API 不會為了這個警示多打資料庫。

### 3. 不做的事

- **不改任何解析規則**。這一輪是搬家：`response_status` 的判定、CLI 雜訊過濾、背景工作證據的條件、
  Antigravity 的 `url` 用未 resolve 的路徑而 `source_path` 用 resolve 過的——全部逐字保留，
  包括看起來像瑕疵的地方。要改行為就另開一輪，帶自己的收據。
- **不做「格式自我驗證」**（例如讓 parser 宣告它預期看到哪些欄位、看不到就報錯）。那是更好的答案，
  但需要先有真實格式變更的樣本才能定義「預期」；沒有樣本就寫規則，只會做出一個會誤報的守門員。
  漂移警示是**在沒有樣本的情況下**能做到的最誠實的偵測：它不猜格式，只比對「檔案在動」與「事件是零」。

## Consequences

**好的**：改一個平台只需要打開一個 < 300 行的檔案；每個 parser 能離線單測；靜默失效從「幾個月後才發現」
變成「三天後健康頁變色」。

**代價**：

- 多了一層間接——服務與 parser 之間隔著 `TranscriptTurn`。讀一次完整流程要開兩個檔案，
  換來的是改一個平台只要開一個。
- 漂移警示只看得到**整個平台**的靜默。同一平台有兩種 parser（Codex 的 `.json` 與 `.jsonl`）時，
  其中一種失效而另一種還在產出，不會被這個警示抓到。這是視窗的邊界，如實寫在這裡。
- `claude_desktop → claude_code` 的 import 讓兩個平台不是完全獨立。這是事實的反映（格式真的相同），
  不是偷懶；如果哪天 Desktop 的格式分家，那一天就把配對邏輯複製過去。
