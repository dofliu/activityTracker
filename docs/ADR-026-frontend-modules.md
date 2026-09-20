# ADR-026：前端拆成 ES module——一個分頁一個檔、字典是資料、事件不寫在字串裡

- 狀態：**Accepted**（2026-09-17 起草並於同日實作，TODO D10）
- 關聯：[ADR-001](ADR-001-p2-5-trust-boundary.md) 本機安全邊界、[ADR-019](ADR-019-secretary-desk-home.md) 秘書桌面（01 的版面）
- 依據：[docs/REVIEW-2026-09-16-project-assessment.md](REVIEW-2026-09-16-project-assessment.md) §4.4 第 2 點

## Context

`web/app.js` 是 **6,096 行的單一檔案**，用一個 `<script>` 標籤整包載入。裡面有六個分頁的全部邏輯、
一份 **682 行的 i18n 字典**、約 30 個模組層 `let` 當共享狀態，以及 17 處寫在 HTML 字串裡的 `onclick=`。

具體的痛：

- **找不到東西**。「知識庫的引用面板在哪」只能靠搜字串；改一個分頁要在六千行裡定位，而且沒有任何邊界
  說明「動這裡不會影響別的分頁」。
- **字典和程式綁在一起**。要加一個語言、或請人幫忙校對翻譯，都得改 `app.js`；而且**沒有任何東西
  保證兩份字典的 key 集合一樣**——少一個 key 不會報錯，它會安靜地 fallback 到中文，英文介面就這樣
  夾一句中文，沒人發現。
- **九處裸 `fetch()`**。專案已經有 `getJSON`／`postJSON` 兩個 helper（會檢查 `res.ok`、把後端的
  `detail` 取出來當錯誤訊息），但九個呼叫點繞過它們自己寫 `fetch` ＋ 自己判斷狀態碼，錯誤處理各寫各的。
- **`onclick=` 寫在字串裡**。它要求被呼叫的函式掛在 `window` 上（於是模組化直接卡住），而且
  參數是用字串拼進 HTML 的——今天靠 `esc()` 擋住，但那是把跳脫正確性押在每一個拼字串的人身上。

## Decision

### 1. 一個分頁一個模組，共用的東西在 `web/js/core/`

```
web/js/
  main.js              進入點：載字典 → 初始化各分頁 → 排程輪詢
  core/state.js        共享可變狀態（一個具名物件，不是散落的 let）
  core/api.js          getJSON／postJSON／sendJSON——唯一碰 fetch 的地方
  core/i18n.js         載入 JSON 字典、t()、applyLanguage()
  core/ui.js           分頁切換、主題、可收合面板、toast、事件委派
  tabs/assistant.js    01 小秘書（提案、桌面、問候、今日、脈絡記憶）
  tabs/memory.js       01 記憶區
  tabs/knowledge.js    02 知識庫（DeskRAG 對話、索引管理）
  tabs/projects.js     03 進行中工作（專案卡、情報流、快速動作）
  tabs/repos.js        04 Git 同步中心（同步、全覽批次、onboarding）
  tabs/summaries.js    05 摘要與統計（含 checkpoint）
  tabs/settings.js     06 系統設定（設定表單、排程任務、Telegram、LINE）
  tabs/github.js       GitHub 區塊
  tabs/status.js       頂部狀態列與採集器卡片
  tabs/health.js       06 系統健康與驗收中心
```

`index.html` 只載入 `main.js`，`type="module"`。**沒有打包步驟**——這是本機工具，使用者用瀏覽器直接開，
多一個 build step 就多一個「我改了程式但畫面沒變」的踩坑源。瀏覽器原生 ES module 就夠。

**模組層可變狀態集中成 `state.js` 的一個物件**：ES module 匯出的是唯讀繫結，散落的 `let` 一旦跨檔就
不能再被別的模組重新賦值。這一輪先把它們收進一個具名物件（`state.projectsCache`），**不是**最終形狀——
把狀態改成注入、刪掉 `_reset_*_for_tests` 是 D11 的事，這裡只做到「看得見它們全部在哪」。
（後續：D11／[ADR-027](ADR-027-injected-runtime-state.md) 做了後端那一半；前端這一半到 D13／[ADR-030](ADR-030-frontend-state-stores.md) 才分成十一個具名 store ＋ 工廠，**而且仍然不是「沒有全域」**，理由寫在 ADR-030。）

### 2. 字典是資料，不是程式

682 行的 `I18N` 物件原封不動搬成 `web/i18n/zh-TW.json` 與 `web/i18n/en.json`，開機時用 `getJSON`
載入。**新增一條契約測試：兩份字典的 key 集合必須完全相同**——這正是目前唯一沒人守的地方，
少一個 key 的後果（英文介面安靜地夾中文）不會有任何錯誤訊息。

fallback 規則不變：查不到 key 就回中文，再查不到就回 key 本身。

### 3. 事件用委派，不用字串裡的 `onclick`

17 處 `onclick="window.foo('...')"` 改成 `data-action` ＋ `data-*` 參數，由所屬模組在容器上掛一個
委派 listener。好處有三：函式不必掛到 `window`（模組化的前提）、參數不再經過 HTML 字串（跳脫正確性
不再靠每個呼叫點自律）、而且**哪些動作存在**變成可以用一個測試列舉的東西。

### 4. 九處裸 `fetch()` 收回 helper

`getJSON`／`postJSON` 之外補一個 `sendJSON(url, { method })`（DELETE 與不帶 body 的 POST 用），
三個合起來是**唯一** `fetch(` 出現的地方——由契約測試把關：`web/js/` 底下除了 `core/api.js`
不准出現 `fetch(`。

### 5. 不做的事

- **不改任何畫面與行為**。版面、字串、API 呼叫時機、輪詢節奏全部不動；這一輪的價值是往後每一輪都更好改。
- **不引入打包器、框架或 TypeScript**。理由同上：本機工具，少一層是一層。
- **不處理 105 處 `innerHTML`**。它是下一個該處理的東西，但把它和模組化混在同一輪會讓「行為不變」
  變成無法驗證的宣稱。留在檢視報告 §4.4 第 2 點，不假裝做完了。

## Consequences

**好的**：改一個分頁只要打開一個 < 1,000 行的檔案；字典能單獨交給別人校對，而且少 key 會被測試抓到；
`fetch` 只有一個地方，錯誤處理自然一致；事件處理器不再需要全域命名空間。

**代價**：

- **多了一次網路往返**才能開始渲染（字典是 fetch 進來的）。都在 loopback，代價是幾毫秒；換到的是
  字典能被當成資料維護。
- **`?v=` cache-buster 只掛在進入點**。子模組靠 HTTP ETag 重新驗證——loopback 且沒有中間快取，
  實務上足夠；如果哪天真的遇到「改了沒生效」，該做的是給 `/static/js/` 加 no-cache 標頭，不是回去合成一個檔。
- **`state.js` 是過渡形狀**，不是好設計。它把散落的全域變成集中的全域——看得見了，但還是全域。
  D11 才會把它改成注入。這件事寫在這裡，免得下一個人以為這就是終點。
  （後續見 [ADR-030](ADR-030-frontend-state-stores.md)：D13 分成具名 store ＋ 工廠，但共用的那一份還在。）
