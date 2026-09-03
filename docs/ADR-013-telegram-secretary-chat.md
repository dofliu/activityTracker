# ADR-013：Telegram 小秘書對話（手機通道）

- 狀態：Accepted（2026-09-03 實作）
- 關聯：[ADR-007](ADR-007-proposal-only-secretary.md) proposal-only、[ADR-008](ADR-008-gated-agent-executor.md) 分級執行器與 P5-R4b inline 批准、[ADR-009](ADR-009-deskrag-worker-index-lifecycle.md) 檢索 worker 隔離、[ADR-012](ADR-012-secretary-memory.md) 記憶區

## Context

儀表板只在 `127.0.0.1` 上（`enforce_local_security_boundary` 只放行 loopback，`server.host` 預設 `127.0.0.1`），所以人不在電腦前就完全看不到、也問不到小秘書。使用者要的是「在手機上也能觀察、也能跟秘書講話」。

既有的 Telegram 通道已經走通一半：outbound-only 的 `getUpdates` 長輪詢（不開任何 inbound port）、綁定單一 chat、晨報／晚報推播、`/proposals` 與 inline 批准（P5-R4b）。缺的是**對話**——自由文字目前一律 `message_ignored`。

可選路徑與取捨：

| 方案 | 內容是否離開本機 | 需要的前置 | 涵蓋範圍 |
| :-- | :-- | :-- | :-- |
| Telegram 對話（本案） | 會（經 Telegram 伺服器） | 無，通道已存在 | 任何網路都能用 |
| 私有網路（Tailscale/WireGuard）＋手機瀏覽器 | 不會（端對端加密） | 兩端各裝一次、放寬 loopback 邊界、需加認證 | 完整儀表板 |
| 公開反向代理（Cloudflare Tunnel／ngrok） | 會（經第三方邊緣） | 需自建認證 | 完整儀表板 |

使用者選擇「只擴充 Telegram 對話」，權限到「含批准」。

## Decision

### D1 對話走與儀表板同一條管線

新增 `core.secretary_ask.ask_secretary()`：把交辦框那條管線（**記憶區脈絡（ADR-012）＋ RAG 檢索 ＋ LLM**）收斂成一個同步呼叫。檢索直接沿用 `rag.router._retrieve_citations`（worker／in_process 由設定決定），記憶區沿用 `memory_context()`——不另立一套規則，手機與網頁的答案來源一致。

`notifiers/telegram_chat.py` 是通道層：綁定 chat 的自由文字 → `ask_secretary` → 回覆答案＋引用檔名＋「🧠 參考記憶區 N 筆」。

### D2 獨立開關，預設關閉；隱私邊界寫在開關旁邊

`notifiers.telegram.chat.enabled`（預設 false）。關閉時行為與今天完全相同（只有通知、`/proposals`、inline 批准），自由文字仍然 `message_ignored`。

**這是本專案唯一會把「你的提問與秘書的回答」送出本機的通道**，因此：

- 預設關閉，並在設定卡片與 `config.example.yaml` 明講內容會經過 Telegram 伺服器；
- 引用**只送檔名**，不送被檢索到的文件內容切片；
- provider 可獨立設定（`notifiers.telegram.chat.provider`）——留 `ollama` 就只有 Telegram 看得到內容，選雲端供應商則內容另會送往該供應商（與網頁相同）。

### D3 指令與筆記

`/today`（上次做到哪＋早晨包＋前三個建議含「為什麼是現在」）、`/notes`、`/status`、`/proposals`（既有）、`/help`。「記下來：…／偏好：…／決定：…」（`/note` `/pref` `/decision` `remember:`）用**與網頁完全相同**的 `parse_note_command` 解析，直接寫進 `secretary_notes`（`source=telegram`），不送 LLM；偏好寫「不要提醒 X」立即在提案端生效。

### D4 批准：ADR-008 的兩道門不動，第三道門可選

- 能批准的動作仍只有 server 白名單的 L0/L1，L2 一律回儀表板走一次性確認碼。
- 批准通道仍必須先 arm（in-memory、有 TTL、服務重啟即失效）。
- 新增 `/arm <execution token>`，由 `executor.telegram_approvals.allow_remote_arm`（**預設關閉**）控制。開啟等於同意 execution token 會以訊息形式經過 Telegram；服務**一收到就刪除該則訊息**（Bot API `deleteMessage`），token 不進 log、不進任何 receipt，刪除失敗會明確要求使用者手動刪除。較安全的做法仍是在儀表板解鎖。
- `/disarm` 是降低權限的方向，**不受任何開關限制、隨時可用**——手機掉了也能立刻上鎖。

### D5 不阻塞、不並發

慢的問答丟到背景執行緒並先回「🤔 查一下…」，同一時間只允許一題（第二題回「上一題還在回答中」）；批准按鈕與其他指令不會被一題長回答卡住。問題長度上限 `max_question_chars`（預設 1000），答案超過 Telegram 單則上限時**分段送出而非截斷**。

### D6 poller 啟動條件放寬

長輪詢原本只在批准啟用時啟動；現在批准或對話任一啟用即啟動（`telegram_updates_poller_enabled`），兩者都關就完全不開。

## Alternatives considered

- **把儀表板開到區網／公網**：拒絕作為本次方案。`allow_remote_clients` 目前是「全有全無」且沒有任何認證，開下去等於整個儀表板對同網段全開；真要做需要另一個 ADR（CIDR allowlist＋登入憑證＋HTTPS），私有網路（Tailscale）是比公開代理更好的形狀。
- **讓 Telegram 也能執行 L2 或改設定**：拒絕。L2 的一次性確認碼與寫入型動作需要人在電腦前，ADR-008 不因通道方便而放寬。
- **把對話歷史存進 Telegram 當記憶**：拒絕。記憶只走 ADR-012 的 `secretary_notes`，來源可審、可刪。

## Consequences

- 手機上可以觀察（`/today`、`/notes`、`/status`）、對話、記筆記、批准 L0/L1——需要的三個開關全部預設關閉。
- 多一條會外送內容的通道，因此隱私邊界必須在 UI、config 註解與 USAGE 三處同時講清楚；`STATUS.yaml` 亦記為 known boundary。
- `/arm` 開關開啟時，execution token 的保密性下降到「Telegram 帳號的安全性」；文件如實標示，預設不開。
- 契約由 `tests/test_telegram_chat.py`（26 項）守門：開關分層、綁定 chat、問答流程與並發、筆記寫入、唯讀指令、arm/disarm 邊界與 token 不外洩、`ask_secretary` 的降級行為，以及「本功能不得讓主服務載入索引套件」的 ADR-009 契約。
