# ADR-014：多通道推播（LINE）與一次性解鎖碼

- 狀態：Accepted（2026-09-03 實作）
- 關聯：[ADR-001](ADR-001-p2-5-trust-boundary.md) loopback 信任邊界、[ADR-008](ADR-008-gated-agent-executor.md) D4 execution token 邊界、[ADR-013](ADR-013-telegram-secretary-chat.md) Telegram 手機通道

## Context

使用者希望「可以選 LINE 或 Telegram 不同方式來聯繫」。研究後有兩個事實決定了可行範圍：

### 1. LINE 沒有輪詢介面

Telegram 提供 `getUpdates` 長輪詢，本機服務因此是**主動往外拿訊息**——不需要開任何 inbound port，`enforce_local_security_boundary` 的 loopback-only 邊界（ADR-001）完全不受影響。ADR-013 的手機對話就是建立在這個性質上。

**LINE Messaging API 只有 webhook**：要收到使用者的訊息，LINE 平台必須主動 HTTPS POST 到一個公開網址。那需要在本機開對外入口（Cloudflare Tunnel／ngrok／中繼 VPS），也就是 ADR-013 明確拒絕的形狀。

| 能力 | Telegram | LINE |
| :-- | :-- | :-- |
| 推播（outbound） | ✅ | ✅ |
| 接收訊息 | ✅ 長輪詢 | ❌ 只有 webhook |
| 按鈕批准 | ✅ | ❌（postback 同樣要 webhook） |
| 刪除使用者訊息 | ✅ | ❌ 無此 API |
| 富文字 | HTML | 純文字 |
| 費用 | 免費、無則數上限 | 免費方案有**每月推播則數上限**（依地區方案） |

### 2. 推播內容與呈現原本是綁死的

`telegram_notifier.py` 在組裝訊息時就寫死 `<b>` 標籤。LINE 的文字訊息不支援 HTML／Markdown，所以在加任何通道之前必須先把內容與呈現分開，否則每個通道都要複製一份組裝邏輯。

### 3. `/arm` 送的是長期 secret

ADR-013 的 `/arm <execution token>` 依賴「收到就刪訊息」來降低風險，但這代表**手機必須持有長期的 execution token**，而且刪訊息這件事只有 Telegram 做得到。

## Decision

### D1 通道 adapter 層

- `notifiers/messages.py`：通道中立的 `Message`／`Section` 模型與組裝函式（晨報、晚間交接、每日日報、停滯提醒），**不含任何標記語法**；`render_plain`（LINE／CLI）與 `render_telegram_html`（Telegram，內容一律 escape）各自負責呈現。
- `notifiers/channels.py`：`TelegramChannel`／`LineChannel` adapter，各自宣告能力（`receive`／`buttons`／`delete_message`／`rich_text`），呼叫端不必知道平台細節。
- `notifiers/secretary_push.py`：組裝一次、扇出到所有啟用的通道；**每個通道各自 try/except**，一個失敗不影響另一個，receipt 逐通道記錄。
- `TelegramNotifier` 保留為相容外殼（只推 Telegram），`main.py notify --dry-run` 改用同一組組裝函式——預覽與實際送出的內容從此保證一致（原本是兩份重複的格式字串）。

### D2 LINE 只做推播，且在三處明講

`notifiers/line_setup.py` 與 Telegram 對稱：憑證解析（環境變數優先、不複製進檔案）、即時連線測試（`/v2/bot/info` 驗 token ＋ 實發測試訊息）、驗證通過才寫 config（fail-closed）。channel access token **只走 `Authorization` header**，絕不進 URL、log 或任何 receipt。

`notifiers.line.enabled` 預設 false。介面、`config.example.yaml` 與 USAGE 都寫明：LINE 只能推播，提問／記筆記／批准仍走 Telegram，原因是 webhook 會打破 loopback-only 邊界。`GET /api/v1/notifications/channels` 也如實回報 `receive: false`。

**不做**公開 webhook：那需要對外入口＋簽章驗證＋修改 ADR-001，屬於另一個決策（留在 TODO C5／C6）。

### D3 一次性解鎖碼取代 execution token

`/arm` 改收儀表板簽發的 **6 位數短效碼**：

- `POST /api/v1/telegram/approvals/arm-code`（需 execution token）簽發；**只有雜湊留在記憶體**，回傳值是唯一一次看到明碼的機會，不寫 log。
- 預設 300 秒失效（`arm_code_ttl_seconds`）、**只能用一次**、**猜錯一次就作廢**（避免對同一組碼反覆猜測）、`/disarm` 與服務重啟都會銷毀。
- 手機因此**永遠不需要持有長期的 execution token**。訊息仍會被刪除，但那降級為多一層防護而非安全性的依賴——這也讓「無法刪訊息的通道」（例如未來若接 LINE inbound）能安全解鎖。
- arm 成功後的授權窗仍是 24h（碼的短效只約束「解鎖」這個動作本身）。
- 能批准的動作、`allow_remote_arm` 開關（預設關閉）與 `/disarm` 永遠可用，一律不變。

## Alternatives considered

- **LINE Notify**：官方服務已終止，不採用。
- **公開 webhook（Cloudflare Tunnel／ngrok）讓 LINE 也能對話**：拒絕於本 ADR。它需要對外入口、`x-line-signature` 驗證與一套認證，且與 ADR-001 衝突；使用者本次也明確選擇「抽通道層＋LINE 推播」。
- **自建中繼 VPS 收 webhook、本機去輪詢那台**：可恢復 outbound-only，但要維運一台伺服器且訊息會落在上面，成本與信任邊界都不划算。
- **把 Telegram 的 HTML 直接送 LINE**：拒絕。LINE 不解析 HTML，使用者會看到裸標籤。
- **保留 `/arm <execution token>` 作為相容路徑**：拒絕。留著就等於留著「長期 secret 進聊天室」的路，與本 ADR 的目的相反。

## Consequences

- 晨報／晚報／日報／停滯提醒可以選 Telegram、LINE 或兩者；提問與批准仍只有 Telegram。
- 新增通道的成本降到「一個 adapter ＋ 一個 renderer」；Slack／Discord 之後若要加，不必再動組裝邏輯。
- LINE 使用者需注意免費方案的每月推播則數；本專案預設一天只推晨報與晚報。
- 行為變更（需在 release notes 標明）：`/arm <execution token>` 不再被接受，改用 `/arm <6 位數碼>`。
- 契約由 `tests/test_notification_channels.py`（29 項）與 `tests/test_telegram_chat.py`（29 項，含改寫後的 arm code 契約）守門：渲染分離與 escape、組裝的降級行為、adapter 能力宣告與分段／額度錯誤、扇出隔離、LINE 設定的失敗分類與 fail-closed、token 不進 URL／receipt、arm code 的單次性／過期／猜錯即焚／不回顯。
