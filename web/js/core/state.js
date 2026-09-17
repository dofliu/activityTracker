// web/js/core/state.js — 共享可變狀態（ADR-026，TODO D10）
//
// D10 之前這些是 app.js 裡四十九個散落的模組層 `let`。ES module 匯出的是唯讀繫結，
// 跨檔案就不能再重新賦值，所以這裡把它們收進一個具名物件——**看得見它們全部在哪**。
//
// 這不是終點：把狀態改成注入、刪掉那三個 `_reset_*_for_tests` 是 D11 的事。
// 在那之前，至少「哪些東西是共享可變的」有一個地方可以一次讀完。

export const API = "";
export const POLL_MS = 4000;

export const state = {
  currentLang: localStorage.getItem("omni-lang") || "zh-TW",
  currentConfig: null,
  activeFilter: "all",
  isMonitoring: false,
  expandedProject: null,
  recentEvents: [],
  projectsCache: [],
  loopsCache: [],
  summariesCache: [],
  currentSummaryMarkdown: "",
  currentCheckpointMarkdown: "",
  summaryView: "day",
  configDirs: [],
  configCalendarPaths: [],
  configRepos: [],
  githubStatus: null,
  showAllProjects: false,
  llmStatusCache: null,
  contextSessionsCache: null,
  relatedContextCache: null,
  secretaryProposalsCache: null,
  focusCarouselItems: [],
  focusCarouselIndex: 0,
  focusCarouselTimer: null,
  focusCarouselUserPaused: false,
  focusCarouselPointerPaused: false,
  repositorySyncCache: [],
  acceptanceCache: null,
  todayViewCache: null,
  greetingWindow: "today",
  greetingCache: null,
  homeCache: null,
  memoryCache: null,
  memoryProfileCache: null,
  repoSnapshotCache: null,
  scheduledTasksCache: null,
  telegramStatusCache: null,
  repoOverviewCache: null,  // null = 尚未載入；[] = 已載入但沒有 repo
  repoOverviewBatch: { fetch_all: true, pull_ff_only: true, push: false },
  repoOverviewFilter: "all",
  onboardingReportCache: null,
  ragSessionsCache: [],
  currentRagSessionId: "",
  ragChatHistory: [],
  isRagStreaming: false,
  streamAbort: null,
  streamTimedOut: false,
  ragProgressPollTimer: null,
  ragRetrievalPollTimer: null,
};
