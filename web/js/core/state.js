// web/js/core/state.js — 共享可變狀態，分成十一個具名 store（ADR-030，TODO D13）
//
// D10 把 `app.js` 裡四十九個散落的模組層 `let` 收進**一個**具名物件（ADR-026）。看得見了，
// 但四十九個欄位全部攤平在同一層：`homeCache` 是誰的？`streamAbort` 誰會寫？沒有任何東西
// 回答得了，也沒有任何東西擋得住任意模組去改任意欄位。
//
// D13 做兩件事：
//
// 1. **分成十一個 store**，一個 store 對應一個關注點（`state.rag.history`、
//    `state.projects.expandedKey`、`state.feed.activeFilter`…）。欄位歸屬變成讀得出來的東西，
//    而且 `tests/test_frontend_state_stores.py` 有一張「哪個模組碰哪個 store」的表在守著——
//    新的跨模組寫入會讓測試紅，必須先把它寫進表裡，也就是先講出來。
// 2. **改成工廠**：`createAppState()` 每次回傳一份互不相干的狀態。D13 之前這是做不到的
//    （`export const state = { … }` 是唯一一份，誰都改得到，而且改了就回不去）。
//
// **如實記下**：`export const state` 仍然是整個程式共用的那一份，所以這一輪買到的是
// 「可以另外造一份」與「欄位有歸屬」，**不是「沒有全域」**——和後端 D11（ADR-027）
// 停在同一個地方，理由也一樣：要做到「每次渲染一份」得改一百一十九個函式的簽章，而其中
// 有大量函式是直接掛給 `addEventListener` 的，加參數會讓瀏覽器把 Event 當成狀態傳進去。
// 那個陷阱現有的證據（ADR-029 的鎖只涵蓋第一畫面與五個場景）涵蓋不了，所以不做。

export const API = "";
export const POLL_MS = 4000;

/** 每次呼叫回傳一份全新的、與其他份互不相干的狀態。 */
export function createAppState() {
  return {
    // 介面語言。唯一真的全應用共用的 UI 值，所以自己一個 store。
    ui: {
      currentLang: localStorage.getItem("omni-lang") || "zh-TW",
    },
    // 情報流與監控開關（01／06 的情報流面板）。
    feed: {
      activeFilter: "all",
      recentEvents: [],
      isMonitoring: false,
    },
    // 03 進行中工作：專案卡、未結事項、相似歷史。
    projects: {
      cache: [],
      loops: [],
      expandedKey: null,
      showAll: false,
      contextSessions: null,
      relatedContext: null,
      repoSnapshot: null,
    },
    // Focus Now 輪播。四個旗標分別代表「使用者按暫停」與「滑鼠移上去」，不可合併。
    focus: {
      items: [],
      index: 0,
      timer: null,
      userPaused: false,
      pointerPaused: false,
    },
    // 05 摘要與統計。
    summaries: {
      cache: [],
      dayMarkdown: "",
      checkpointMarkdown: "",
      view: "day",
    },
    // 01 小秘書：提案、今日、問候卡、桌面、排程任務。
    secretary: {
      proposals: null,
      today: null,
      greetingWindow: "today",
      greeting: null,
      home: null,
      scheduledTasks: null,
    },
    // 01 記憶區。
    memory: {
      notes: null,
      profile: null,
    },
    // 02 知識庫：對話與串流。`abort` 是進行中的 AbortController，不是資料。
    rag: {
      sessions: [],
      sessionId: "",
      history: [],
      streaming: false,
      abort: null,
      timedOut: false,
      progressTimer: null,
      retrievalTimer: null,
    },
    // 04 Git 同步中心。`overview: null` 代表尚未載入；`[]` 代表載入了但沒有 repo。
    repos: {
      syncCache: [],
      overview: null,
      overviewBatch: { fetch_all: true, pull_ff_only: true, push: false },
      overviewFilter: "all",
      onboardingReport: null,
    },
    // 06 系統設定：設定檔本身與它衍生的清單、各通道狀態。
    settings: {
      config: null,
      dirs: [],
      calendarPaths: [],
      repos: [],
      llmStatus: null,
      telegramStatus: null,
      githubStatus: null,
    },
    // 06 系統健康／驗收中心。
    health: {
      acceptance: null,
    },
  };
}

/** 整個應用共用的那一份。要另外造一份請用 `createAppState()`。 */
export const state = createAppState();
