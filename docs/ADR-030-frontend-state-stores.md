# ADR-030：前端共享狀態分成具名 store ＋ 工廠（D13）

- 狀態：**Accepted**（2026-09-20 起草並於同日實作，TODO D13）
- 關聯：[ADR-026](ADR-026-frontend-modules.md) 前端模組化與它欠的那句承諾、[ADR-027](ADR-027-injected-runtime-state.md) 後端狀態改注入（同一件事的另一半）、[ADR-029](ADR-029-frontend-dom-lock.md) 這一輪唯一的證據來源
- 依據：[docs/REVIEW-2026-09-16-project-assessment.md](REVIEW-2026-09-16-project-assessment.md) §4.4、[docs/TODO.md](TODO.md) D13

## Context

ADR-026 把 `app.js` 裡四十九個模組層 `let` 收進一個具名物件，並寫下「改成注入是 D11 的事」。
D11 只做了後端（ADR-027），前端那一半明說沒兌現，理由是沒有能證明「行為不變」的東西。
ADR-029 把那個東西做出來了（六分頁 × 兩語言的 DOM 快照 ＋ 五個互動場景，逐字元比對）。
D13 是拿著那把鎖去動 `state.js`。

攤平的四十九個欄位有兩個不會拋錯的代價：

1. **打錯字是靜默的**。`state.homeCahe` 讀到 `undefined`，畫面少一塊，沒有任何訊息。
2. **沒有歸屬**。`streamAbort` 是誰的？誰會寫 `homeCache`？沒有東西回答得了，也沒有東西
   擋得住任意模組去改任意欄位。

還有第三件事是動工前清點時翻出來的，它不是理論上的風險而是**已經在線上的錯**：
`status.js` 的 `captureStateLabel(state)` 用一個叫 `state` 的參數把共享的 `state` 遮蔽掉，
`state.currentLang` 讀的是字串的屬性——永遠 `undefined`，所以中文介面的採集狀態標籤一直是
英文。已於前一個 commit 修掉並加測試擋住整個類別（詳見該 commit 與
`test_nothing_shadows_the_shared_state`）。**「一個叫 `state` 的共用物件」這個設計本身在招這種錯**，
這是 D13 的第三個理由。

## Decision

### 1. 四十九個欄位分成十一個具名 store

`ui`（語言）、`feed`（情報流與監控開關）、`projects`、`focus`（Focus Now 輪播）、
`summaries`、`secretary`、`memory`、`rag`、`repos`、`settings`、`health`。
欄位名同時縮短成在 store 裡讀得通的樣子：`state.ragChatHistory` → `state.rag.history`、
`state.expandedProject` → `state.projects.expandedKey`、`state.repoOverviewFilter` →
`state.repos.overviewFilter`。四百三十七處存取全部改完，**一個舊路徑都沒有殘留**（有測試掃）。

### 2. `createAppState()` 工廠

`export const state = createAppState();` 仍然是整個程式共用的那一份，但**現在造得出第二份**。
D13 之前 `export const state = { … }` 是唯一一份，誰都改得到，改了就回不去。
契約測試用 `node` 真的把模組載進來，造兩份、改其中一份，證明另一份與預設那份都沒被動到。

### 3. 「哪個模組碰哪個 store」是一張要維護的表

`tests/test_frontend_state_stores.py` 的 `OWNERSHIP` 是**宣告**不是觀察報告：
多一處跨模組存取就會紅，必須先寫進表裡——也就是**先講出來**。

這張表一寫出來就看到一件原本看不見的事：**`core/i18n.js` 會讀四個 store 的快取**
（`projects.relatedContext`、`health.acceptance`、`memory.notes`、`secretary.home`），
用來決定切語言時要重畫哪些區塊。那不是錯，但它是一條跨四個子系統的相依，
D13 之前沒有任何地方寫著它。

### 4. 打錯字會紅

每一條 `state.<store>.<field>` 都要在 `createAppState()` 裡真的存在。
實測過：把一處 `state.rag.history` 打成 `histroy`，那支測試就紅。

## Consequences

**好的**：

- 欄位有歸屬，而且歸屬是可測的，不是靠註解。
- 打錯 store 或欄位名從「靜默的 undefined」變成「測試紅字」。
- 造得出第二份狀態（D13 之前做不到）。
- `i18n.js` 那條跨四個子系統的相依從隱性變成表格上的一列。

**代價，如實記下**：

- **`export const state` 還在，而且整個程式還是共用它。** 這一輪買到的是「可以另外造一份」
  與「欄位有歸屬」，**不是「沒有全域」**——和後端 D11 停在同一個地方。
- **沒有做完全的參數注入**，這是刻意的，理由具體：一百一十九個函式碰狀態，其中**大量是
  直接掛給 `addEventListener` 的**（`addEventListener("click", loadUsagePanels)`）。
  給它們加一個有預設值的 `state` 參數，瀏覽器會把 `MouseEvent` 當成狀態傳進去；
  要避開就得把每個裸 listener 包成箭頭函式，那是幾百處人工判斷。**而 ADR-029 的鎖只涵蓋
  第一畫面與五個互動場景，涵蓋不了那幾百個 listener 路徑**——沒有證據就不做，
  和 ADR-027 當初擋下自己的理由一樣。要做的前提是先把鎖的互動覆蓋擴到那些路徑。
- **四百三十七處存取變長了**（`state.currentLang` → `state.ui.currentLang`，光這個就 125 處）。
  換到的是讀得出歸屬。
- **十一個 store 裡有兩個只有一個欄位**（`ui`、`health`）。不硬湊：`currentLang` 是真的全應用
  共用，`acceptance` 是真的只屬於驗收面板。

## 怎麼證明行為沒變

`scripts/dashboard_dom_lock.py check`：**22 張快照逐字元相同**（六分頁 × 兩語言的第一畫面，
加上五個互動場景 × 兩語言）。四百三十七處改動、十二個檔案，輸出一個字元都沒變。

過程中鎖也真的攔下一次錯：前一個 commit 改 `renderRuntimeTrust` 的參數名時漏掉函式尾端
一行 `state === "stopped"`，那一行於是從「比對參數」變成「比對匯入的物件」，badge 從
`STOPPED` 變成 `DISCONNECTED`。`check` 當場報六張不同並指到那一段。**這一輪的鎖不是裝飾，
它在同一個下午攔了一次真的回歸。**

**鎖沒有涵蓋的地方照樣要講**：表單送出、對話框、任何會 POST 的動作、輪詢之後的更新、
樣式。那些路徑上的 `state.x` → `state.store.x` 改動**只有「語法正確 ＋ 路徑存在」這兩層保證**
（`node --check` 十七個檔案全過、契約測試掃過每一條路徑），沒有渲染層面的證據。
這是這一輪最大的未涵蓋面，寫在這裡而不是藏起來。
