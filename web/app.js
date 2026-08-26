// web/app.js — OmniContext Intel Board controller

const API = "";
const POLL_MS = 4000;

let currentLang = localStorage.getItem("omni-lang") || "zh-TW";
let currentConfig = null;
let activeFilter = "all";
let isMonitoring = false;
let expandedProject = null;
let recentEvents = [];
let projectsCache = [];
let loopsCache = [];
let summariesCache = [];
let currentSummaryMarkdown = "";
let currentCheckpointMarkdown = "";
let summaryView = "day";
let configDirs = [];
let configRepos = [];
let githubStatus = null;
let showAllProjects = false;
let llmStatusCache = null;
let contextSessionsCache = null;
let relatedContextCache = null;
let secretaryProposalsCache = null;

const $ = (id) => document.getElementById(id);
const esc = (t) => String(t == null ? "" : t)
  .replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;")
  .replace(/"/g, "&quot;").replace(/'/g, "&#039;");

// ---------------------------------------------------------------- i18n
const I18N = {
  "zh-TW": {
    lang_btn: "🌐 English",
    status_connected: "已連線",
    status_disconnected: "未連線",
    status_monitoring: "MONITORING 監控中",
    status_degraded: "MONITORING 部分採集異常",
    status_paused: "PAUSED 已暫停",
    btn_start_monitor: "開始監控",
    btn_pause_monitor: "暫停監控",
    btn_theme_light: "☀ 淺色",
    btn_theme_dark: "☾ 深色",
    btn_quick_checkpoint: "⏱️ 快照",
    btn_quick_summary: "⚡ 生成今日摘要",
    tab_projects: "01 · 進行中工作",
    tab_dashboard: "02 · 即時情報流",
    tab_settings: "03 · 監控配置",
    tab_summaries: "04 · 每日摘要",
    tab_checkpoints: "05 · 活動快照",
    resume_head: "RESUME HERE",
    resume_sub: "上次做到哪",
    context_sessions_title: "RECENT WORK SESSIONS",
    btn_refresh_sessions: "重新整理",
    ph_loading_sessions: "正在整理近期工作階段…",
    context_sessions_boundary: "依專案與事件間隔推定，不代表實際工時、連續專注或成果品質。",
    related_memory_title: "RELATED HISTORY",
    related_memory_placeholder: "輸入目前要處理的問題或工作…",
    btn_related_search: "查相似歷史",
    related_memory_empty: "輸入一段工作描述，從本機 semantic index 尋找相似紀錄。",
    related_memory_boundary: "查詢只送往本機 Ollama 且不保存；similarity 不代表工作重複或歷史結論正確。",
    secretary_title: "SECRETARY SUGGESTIONS",
    btn_refresh_proposals: "重新整理",
    ph_loading_proposals: "正在整理可追溯建議…",
    secretary_boundary: "只呈現本機 evidence 衍生建議；不保存、不呼叫 cloud LLM，也不會自動執行。",
    btn_reindex_projects: "🔄 重新歸戶",
    ph_loading_projects: "載入進行中工作…",
    ph_no_projects: "尚未識別到專案活動。進行程式開發、論文寫作或在 Claude / Codex 發問後將自動建立。",
    active_workstreams: "ACTIVE WORKSTREAMS",
    feed_title: "LIVE FEED",
    filter_all: "全部",
    filter_ai: "AI",
    filter_file: "檔案",
    filter_git: "Git",
    filter_window: "視窗",
    ph_loading_feed: "載入即時活動…",
    ph_no_feed: "目前尚無活動紀錄。",
    collectors_title: "COLLECTORS",
    collector_file: "檔案監控",
    collector_git: "Git 掃描",
    collector_window: "視窗焦點",
    collector_agent: "Agent 日誌",
    collector_scheduler: "定時排程",
    collector_enabled: "● 運作中",
    collector_disabled: "○ 已關閉",
    settings_p1_title: "監控路徑",
    label_file_dirs: "FILE DIRS (檔案目錄)",
    label_git_roots: "GIT ROOTS (Git 倉庫根目錄)",
    btn_browse: "📁 瀏覽…",
    btn_add: "新增",
    ph_abs_path: "絕對路徑…",
    ph_git_path: "Git 專案根目錄…",
    git_recursive_note: "已啟用遞迴探索模式，根目錄下所有子倉庫均會自動納入掃描。",
    settings_p2_title: "採集來源",
    settings_p2_note: "右欄標示目前可信度",
    settings_p3_title: "摘要與排程",
    llm_key_status_title: "API KEY 狀態",
    llm_key_env_label: "環境變數名稱",
    btn_recheck_llm_key: "重新檢查",
    llm_key_boundary: "金鑰只由 OmniContext 本機後端讀取；瀏覽器不會取得、顯示或寫入金鑰內容。建議保存在作業系統的使用者環境變數。",
    btn_save_apply: "儲存並套用",
    settings_save_note: "寫回 config.yaml 後即時熱更新",
    settings_p4_title: "GitHub 雲端整合 (全專案與 PR 追蹤)",
    usage_title: "TODAY · 前景使用與里程碑",
    btn_refresh_usage: "重新整理",
    usage_goal_label: "AI 協作前景使用時間",
    extension_monitor_title: "EXTENSION MONITOR",
    btn_open_monitor: "開啟完整監控",
    capture_status_title: "DATA CAPTURE",
    btn_extension_details: "診斷",
    extension_pairing_boundary: "localhost 可觀察採集狀態；ingest token 仍由 Extension popup 安全保存。",
    settings_usage_title: "每日使用時間與里程碑",
    settings_usage_note: "前景時間，不代表生產力或實際工時",
    usage_tracking_enabled: "啟用使用時間統計",
    usage_notifications_enabled: "啟用里程碑通知",
    usage_daily_goal: "DAILY GOAL / MIN",
    usage_milestones: "MILESTONES / MIN",
    usage_tone: "TONE",
    usage_quiet_start: "QUIET FROM",
    usage_quiet_end: "QUIET UNTIL",
    usage_cooldown: "COOLDOWN / MIN",
    usage_local_note: "預設只在本機聚合，不呼叫 cloud LLM；介面分類規則可在 config.yaml 調整。",
    gh_opt1_title: "快捷方式 1 (推薦)：本機 GITHUB CLI",
    btn_gh_auto_connect: "🔑 一鍵從本機 gh CLI 同步認證",
    gh_opt1_sub: "免手動輸入 Token，自動讀取本機登入之 GitHub 帳號",
    gh_opt2_title: "快捷方式 2：PERSONAL ACCESS TOKEN (PAT)",
    ph_gh_token: "ghp_xxxxxxxxxxxx (需勾選 repo 權限)",
    btn_connect: "連線",
    gh_opt2_sub: "支援 Fine-Grained 或 Classic PAT (讀取 Public & Private Repos/PRs)",
    btn_gh_sync: "🔄 立即同步 GitHub 專案與 PR 狀態",
    btn_gh_disconnect: "解除連線",
    label_date_range: "日期範圍 (RANGE)",
    chip_today: "今日",
    chip_yesterday: "昨日",
    chip_this_week: "本週",
    chip_7d: "近 7 天",
    chip_30d: "近 30 天",
    btn_generate_range: "⚡ 生成區間回顧",
    ph_loading_summaries: "載入歷史報告…",
    view_day: "日",
    view_week: "週",
    view_month: "月",
    btn_copy_markdown: "複製 Markdown",
    ph_summary_click: "請從左側點選報告日期，或點「生成」產出今日摘要。",
    btn_trigger_cp_now: "+ 產出快照",
    ph_loading_checkpoints: "載入快照日誌…",
    btn_copy_log: "複製 Log",
    ph_cp_click: "請從左側點選快照日誌以檢視期間活動細節。",
    rail_open_loops: "OPEN LOOPS",
    ph_loading_loops: "載入未結事項…",
    ph_no_loops: "無未結事項。",
    data_trust_title: "DATA TRUST",
    data_trust_desc: "8 項 contract gates 已通過；runtime 仍以 collector probes 判定，coverage 不完整時不得升格。",
    trust_d1: "統一時區 (Local Timezone)",
    trust_d2: "檔案防手震與噪音過濾 (Debounce)",
    trust_d3: "Git 遞迴多倉庫掃描 (64 Repos)",
    trust_d4: "Claude / Codex / Antigravity 日誌",
    trust_d5: "AI 提問與完整回應解析",
    trust_d6: "設定熱套用與開關聯動",
    trust_d7: "GitHub 雲端倉庫與 PR 狀態整合",
    trust_d8: "中英文多語言 i18n 與專案智能歸戶",
    status_active: "活躍中",
    status_idle: "閒置 {days} 天",
    open_loop_count: "未結",
    sec_files_modified: "RECENT MODIFIED FILES (本次工作異動檔案)",
    sec_timeline: "ACTIVITY TIMELINE (活動時間軸)",
    sec_open_loops: "OPEN LOOPS (未結事項)",
    sec_gh_pr: "GITHUB REPO & PULL REQUESTS (遠端倉庫與 PR 追蹤)",
    btn_snapshot_now: "產出此刻快照",
    files_modified_summary: "異動 {files} 等共 {count} 個檔案",
    btn_copy_handoff: "📋 複製接續 Prompt",
    btn_copy_handoff_short: "📋 接續 Prompt",
    btn_show_more_projects: "▼ 展開更多 ({count} 個超過 60 天未活躍專案)",
    btn_collapse_projects: "▲ 收合超過 60 天未活躍專案",
    title_click_to_open_project: "點擊跳轉並展開此專案",
    title_mark_resolved: "標記為已結案"
  },
  "en": {
    lang_btn: "🌐 繁體中文",
    status_connected: "Connected",
    status_disconnected: "Disconnected",
    status_monitoring: "MONITORING",
    status_degraded: "MONITORING DEGRADED",
    status_paused: "PAUSED",
    btn_start_monitor: "Start Monitoring",
    btn_pause_monitor: "Pause Monitoring",
    btn_theme_light: "☀ Light",
    btn_theme_dark: "☾ Dark",
    btn_quick_checkpoint: "⏱️ Checkpoint",
    btn_quick_summary: "⚡ Today's Summary",
    tab_projects: "01 · Active Workstreams",
    tab_dashboard: "02 · Live Feed",
    tab_settings: "03 · Settings",
    tab_summaries: "04 · Daily Summaries",
    tab_checkpoints: "05 · Checkpoints",
    resume_head: "RESUME HERE",
    resume_sub: "Where You Left Off",
    context_sessions_title: "RECENT WORK SESSIONS",
    btn_refresh_sessions: "Refresh",
    ph_loading_sessions: "Grouping recent work sessions…",
    context_sessions_boundary: "Inferred from project labels and event gaps; not actual work time, continuous focus, or result quality.",
    related_memory_title: "RELATED HISTORY",
    related_memory_placeholder: "Describe the task or question you are working on…",
    btn_related_search: "Find Related History",
    related_memory_empty: "Enter a task description to search the local semantic index.",
    related_memory_boundary: "The query goes only to local Ollama and is not stored; similarity does not prove duplicate work or correct conclusions.",
    secretary_title: "SECRETARY SUGGESTIONS",
    btn_refresh_proposals: "Refresh",
    ph_loading_proposals: "Deriving traceable suggestions…",
    secretary_boundary: "Local evidence-derived suggestions only; not persisted, sent to a cloud LLM, or executed automatically.",
    btn_reindex_projects: "🔄 Re-index",
    ph_loading_projects: "Loading active workstreams…",
    ph_no_projects: "No active projects detected yet. Edits, commits, and Claude / Codex / Antigravity sessions will populate this view.",
    active_workstreams: "ACTIVE WORKSTREAMS",
    feed_title: "LIVE FEED",
    filter_all: "ALL",
    filter_ai: "AI",
    filter_file: "FILE",
    filter_git: "GIT",
    filter_window: "WINDOW",
    ph_loading_feed: "Loading live feed…",
    ph_no_feed: "No activity recorded yet.",
    collectors_title: "COLLECTORS",
    collector_file: "File Watcher",
    collector_git: "Git Scanner",
    collector_window: "Window Focus",
    collector_agent: "Agent Logs",
    collector_scheduler: "Scheduler",
    collector_enabled: "● Active",
    collector_disabled: "○ Off",
    settings_p1_title: "Monitored Paths",
    label_file_dirs: "FILE DIRS",
    label_git_roots: "GIT ROOTS",
    btn_browse: "📁 Browse…",
    btn_add: "Add",
    ph_abs_path: "Absolute path…",
    ph_git_path: "Git root path…",
    git_recursive_note: "Recursive scanning enabled: all nested repositories under root will be indexed.",
    settings_p2_title: "Collector Sources",
    settings_p2_note: "Right column indicates current validation status",
    settings_p3_title: "Synthesis & Schedules",
    llm_key_status_title: "API KEY STATUS",
    llm_key_env_label: "ENVIRONMENT VARIABLE",
    btn_recheck_llm_key: "Recheck",
    llm_key_boundary: "Keys are read only by the local OmniContext backend. The browser never receives, displays, or writes secret values. Store them in the OS user environment.",
    btn_save_apply: "Save & Apply",
    settings_save_note: "Hot reloaded directly into config.yaml",
    settings_p4_title: "GitHub Cloud Integration (Public & Private Repos + PRs)",
    usage_title: "TODAY · FOREGROUND USE & MILESTONES",
    btn_refresh_usage: "Refresh",
    usage_goal_label: "AI collaboration foreground time",
    extension_monitor_title: "EXTENSION MONITOR",
    btn_open_monitor: "Open Full Monitor",
    capture_status_title: "DATA CAPTURE",
    btn_extension_details: "Details",
    extension_pairing_boundary: "localhost can observe capture state; the ingest token remains in the Extension popup.",
    settings_usage_title: "Daily Usage & Milestones",
    settings_usage_note: "Foreground time, not productivity or actual work hours",
    usage_tracking_enabled: "Enable usage analytics",
    usage_notifications_enabled: "Enable milestone notifications",
    usage_daily_goal: "DAILY GOAL / MIN",
    usage_milestones: "MILESTONES / MIN",
    usage_tone: "TONE",
    usage_quiet_start: "QUIET FROM",
    usage_quiet_end: "QUIET UNTIL",
    usage_cooldown: "COOLDOWN / MIN",
    usage_local_note: "Aggregated locally without a cloud LLM; interface rules remain configurable in config.yaml.",
    gh_opt1_title: "Option 1 (Recommended): Local GITHUB CLI",
    btn_gh_auto_connect: "🔑 1-Click Auth via Local gh CLI",
    gh_opt1_sub: "No manual PAT needed, automatically reads local logged-in GitHub account",
    gh_opt2_title: "Option 2: PERSONAL ACCESS TOKEN (PAT)",
    ph_gh_token: "ghp_xxxxxxxxxxxx (with repo scope)",
    btn_connect: "Connect",
    gh_opt2_sub: "Supports Fine-Grained or Classic PAT (fetches Public & Private Repos/PRs)",
    btn_gh_sync: "🔄 Sync GitHub Repos & PRs Now",
    btn_gh_disconnect: "Disconnect",
    label_date_range: "DATE RANGE",
    chip_today: "Today",
    chip_yesterday: "Yesterday",
    chip_this_week: "This Week",
    chip_7d: "Last 7 Days",
    chip_30d: "Last 30 Days",
    btn_generate_range: "⚡ Generate Range Review",
    ph_loading_summaries: "Loading history summaries…",
    view_day: "Day",
    view_week: "Week",
    view_month: "Month",
    btn_copy_markdown: "Copy Markdown",
    ph_summary_click: "Select a date on the left, or click Generate to synthesize today's review.",
    btn_trigger_cp_now: "+ New Checkpoint",
    ph_loading_checkpoints: "Loading checkpoint logs…",
    btn_copy_log: "Copy Log",
    ph_cp_click: "Select a checkpoint log on the left to inspect detailed activity records.",
    rail_open_loops: "OPEN LOOPS",
    ph_loading_loops: "Loading open loops…",
    ph_no_loops: "No open loops.",
    data_trust_title: "DATA TRUST",
    data_trust_desc: "Eight contract gates passed; runtime still depends on collector probes and incomplete coverage is never promoted.",
    trust_d1: "Unified Timezone (Local TZ)",
    trust_d2: "File Debounce & Noise Filter",
    trust_d3: "Git Recursive Multi-Repo Scan (64 Repos)",
    trust_d4: "Claude / Codex / Antigravity Logs",
    trust_d5: "AI Prompts & Full Assistant Responses",
    trust_d6: "Settings Hot Reload & Sync",
    trust_d7: "GitHub Cloud Integration & PR Tracking",
    trust_d8: "Bilingual i18n & Canonical Hierarchy",
    status_active: "Active",
    status_idle: "Idle {days}d",
    open_loop_count: "Open",
    sec_files_modified: "RECENT MODIFIED FILES",
    sec_timeline: "ACTIVITY TIMELINE",
    sec_open_loops: "OPEN LOOPS",
    sec_gh_pr: "GITHUB REPO & PULL REQUESTS",
    btn_snapshot_now: "Snapshot Now",
    files_modified_summary: "Modified {files} ({count} files total)",
    btn_copy_handoff: "📋 Copy Handoff Prompt",
    btn_copy_handoff_short: "📋 Handoff"
  }
};

function t(key, vars = {}) {
  const dict = I18N[currentLang] || I18N["zh-TW"];
  let str = dict[key] || (I18N["zh-TW"] && I18N["zh-TW"][key]) || key;
  for (const [k, v] of Object.entries(vars)) {
    str = str.replace(new RegExp(`\\{${k}\\}`, 'g'), v);
  }
  return str;
}

function applyLanguage(lang) {
  currentLang = lang;
  localStorage.setItem("omni-lang", lang);
  document.documentElement.lang = lang === "zh-TW" ? "zh-TW" : "en";

  // 更新所有 data-i18n 節點文字
  document.querySelectorAll("[data-i18n]").forEach(el => {
    const k = el.dataset.i18n;
    if (k && I18N[currentLang][k]) {
      el.textContent = t(k);
    }
  });

  // 更新所有 placeholder
  document.querySelectorAll("[data-i18n-ph]").forEach(el => {
    const k = el.dataset.i18nPh;
    if (k && I18N[currentLang][k]) {
      el.placeholder = t(k);
    }
  });

  // 更新語言按鈕標籤
  const langBtn = $("btn-lang");
  if (langBtn) langBtn.textContent = t("lang_btn");

  paintThemeBtn();
  refreshStatus();
  renderResume();
  renderProjects();
  renderOpenLoops();
  renderContextSessions();
  renderSecretaryProposals();
  if (relatedContextCache) renderRelatedContext(relatedContextCache);
  if ($("llm-key-status-badge")) renderLLMStatus();
}

function initLanguage() {
  const langBtn = $("btn-lang");
  if (langBtn) {
    langBtn.addEventListener("click", () => {
      const nextLang = currentLang === "zh-TW" ? "en" : "zh-TW";
      applyLanguage(nextLang);
    });
  }
  applyLanguage(currentLang);
}

async function getJSON(url) {
  const res = await fetch(API + url);
  if (!res.ok) throw new Error(url + " → " + res.status);
  return res.json();
}
async function postJSON(url, body) {
  const res = await fetch(API + url, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: body === undefined ? undefined : JSON.stringify(body)
  });
  if (!res.ok) throw new Error(url + " → " + res.status);
  return res.json();
}

document.addEventListener("DOMContentLoaded", () => {
  initLanguage();
  initTheme();
  initTabs();
  initControls();
  initSettingsForm();
  initGitHubSection();
  initSummariesTab();
  initCheckpointsTab();

  refreshStatus();
  refreshFeed();
  loadProjects();
  loadOpenLoops();
  loadConfig();
  loadGitHubStatus();
  loadSummaries();
  loadCheckpoints();
  loadUsagePanels();
  loadContextSessions();
  loadSecretaryProposals();

  setInterval(() => { refreshStatus(); refreshFeed(); }, POLL_MS);
  setInterval(loadUsagePanels, 30000);
});

// ---------------------------------------------------------------- theme
function initTheme() {
  const saved = localStorage.getItem("omni-theme");
  if (saved === "light" || saved === "dark") document.documentElement.dataset.theme = saved;
  paintThemeBtn();
  $("btn-theme").addEventListener("click", () => {
    const next = document.documentElement.dataset.theme === "dark" ? "light" : "dark";
    document.documentElement.dataset.theme = next;
    localStorage.setItem("omni-theme", next);
    paintThemeBtn();
  });
}
function paintThemeBtn() {
  const isDark = document.documentElement.dataset.theme === "dark";
  $("btn-theme").textContent = isDark ? t("btn_theme_light") : t("btn_theme_dark");
}

// ---------------------------------------------------------------- tabs
function initTabs() {
  document.querySelectorAll(".tab").forEach(tab => {
    tab.addEventListener("click", () => {
      document.querySelectorAll(".tab").forEach(t => t.classList.remove("active"));
      document.querySelectorAll(".pane").forEach(p => p.classList.remove("active"));
      tab.classList.add("active");
      const id = tab.dataset.tab;
      $(id).classList.add("active");
      if (id === "tab-projects") { loadProjects(); loadUsagePanels(); loadContextSessions(); loadSecretaryProposals(); }
      if (id === "tab-settings") loadConfig();
      if (id === "tab-summaries") loadSummaries();
      if (id === "tab-checkpoints") loadCheckpoints();
    });
  });
}

// ---------------------------------------------------------------- controls
function initControls() {
  $("btn-toggle-monitor").addEventListener("click", async () => {
    try {
      await postJSON(isMonitoring ? "/api/v1/control/stop" : "/api/v1/control/start");
      refreshStatus();
    } catch (e) { console.error(e); }
  });

  $("btn-quick-checkpoint").addEventListener("click", triggerCheckpoint);
  $("btn-trigger-cp-now").addEventListener("click", triggerCheckpoint);
  $("btn-quick-summary").addEventListener("click", () => generateSummary(null));
  $("btn-refresh-projects").addEventListener("click", () => loadProjects(true));
  $("btn-refresh-usage").addEventListener("click", loadUsagePanels);
  $("btn-refresh-sessions").addEventListener("click", loadContextSessions);
  $("btn-refresh-proposals").addEventListener("click", loadSecretaryProposals);
  $("btn-related-search").addEventListener("click", searchRelatedContext);
  $("input-related-question").addEventListener("keydown", event => {
    if (event.key === "Enter") searchRelatedContext();
  });

  document.querySelectorAll(".filters .chip").forEach(btn => {
    btn.addEventListener("click", () => {
      document.querySelectorAll(".filters .chip").forEach(b => b.classList.remove("active"));
      btn.classList.add("active");
      activeFilter = btn.dataset.filter;
      refreshFeed();
    });
  });
}

async function refreshStatus() {
  try {
    const data = await getJSON("/api/v1/control/status");
    isMonitoring = data.is_running;
    const monitoringState = data.monitoring_state || (isMonitoring ? "healthy" : "stopped");

    const pill = $("status-pill");
    pill.className = "pill " + (monitoringState === "degraded" ? "pill-warn" : isMonitoring ? "pill-on" : "pill-off");
    $("status-text").textContent = monitoringState === "degraded"
      ? t("status_degraded")
      : isMonitoring ? t("status_monitoring") : t("status_paused");
    $("btn-toggle-monitor").textContent = isMonitoring ? t("btn_pause_monitor") : t("btn_start_monitor");

    renderStats(data.metrics);
    renderCollectors(data.watchers, data.last_events || {}, data.collector_health || {}, data.collector_diagnostics || {});
    renderRuntimeTrust(monitoringState, data.degraded_collectors || []);
    $("last-refresh").textContent = "updated " + new Date().toLocaleTimeString();
  } catch (e) {
    $("status-text").textContent = t("status_disconnected");
    $("status-pill").className = "pill pill-off";
    renderRuntimeTrust("disconnected", []);
  }
}

function renderRuntimeTrust(state, degradedCollectors) {
  const badge = $("data-trust-runtime-badge");
  if (!badge) return;
  const degraded = state === "degraded";
  const healthy = state === "healthy";
  badge.className = `mono-mini runtime-trust-badge ${healthy ? "runtime-ok" : degraded ? "runtime-degraded" : "runtime-stopped"}`;
  if (healthy) {
    badge.textContent = "8/8 CONTRACT · RUNTIME OK ▾";
  } else if (degraded) {
    badge.textContent = `8/8 CONTRACT · ${degradedCollectors.length} DEGRADED ▾`;
  } else {
    badge.textContent = `8/8 CONTRACT · ${state === "stopped" ? "STOPPED" : "DISCONNECTED"} ▾`;
  }
}

function formatUsageDuration(seconds) {
  const totalMinutes = Math.max(0, Math.round(Number(seconds || 0) / 60));
  const hours = Math.floor(totalMinutes / 60);
  const minutes = totalMinutes % 60;
  if (!hours) return `${minutes}m`;
  return minutes ? `${hours}h ${minutes}m` : `${hours}h`;
}

async function loadUsagePanels() {
  const [usageResult, extensionResult, captureResult] = await Promise.allSettled([
    getJSON("/api/v1/usage/today"),
    getJSON("/api/v1/extension/status"),
    getJSON("/api/v1/capture/status")
  ]);
  if (usageResult.status === "fulfilled") renderUsagePanel(usageResult.value);
  else renderUsagePanelError();
  if (captureResult.status === "fulfilled") renderCaptureCoverage(
    captureResult.value,
    extensionResult.status === "fulfilled" ? extensionResult.value : null
  );
  else renderCaptureCoverageError();
}

function renderUsagePanel(data) {
  const goal = data.goal || {};
  const coverage = data.coverage_status || "unavailable";
  const coverageBadge = $("usage-coverage");
  coverageBadge.className = "trust " + (coverage === "complete" ? "ok" : coverage === "partial" ? "noisy" : "broken");
  coverageBadge.textContent = coverage.toUpperCase();

  $("usage-goal-value").textContent = formatUsageDuration(goal.foreground_seconds || 0);
  const progress = Number(goal.progress_percent || 0);
  const goalText = currentLang === "zh-TW"
    ? `${goal.label || "AI 協作"}：${Number(goal.foreground_minutes || 0).toFixed(1)} / ${goal.daily_goal_minutes || 0} 分鐘（${progress.toFixed(1)}%）`
    : `${goal.label || "AI collaboration"}: ${Number(goal.foreground_minutes || 0).toFixed(1)} / ${goal.daily_goal_minutes || 0} min (${progress.toFixed(1)}%)`;
  $("usage-goal-progress").textContent = goalText;
  $("usage-progress-bar").style.width = `${Math.min(100, Math.max(0, progress))}%`;
  const dataUpdated = data.data_updated_at ? new Date(data.data_updated_at).toLocaleString() : "—";
  $("usage-boundary").textContent = currentLang === "zh-TW"
    ? `只代表已觀察到的前景時間，不代表生產力或實際工時。Coverage：${data.coverage_note || coverage}；最後資料：${dataUpdated}`
    : `Observed foreground time only; not productivity or actual work hours. Coverage: ${data.coverage_note || coverage}; last data: ${dataUpdated}`;

  const rows = (data.interfaces || [])
    .filter(item => Number(item.foreground_seconds || 0) > 0 || Number(item.ai_interaction_count || 0) > 0)
    .slice(0, 8);
  $("usage-interface-list").innerHTML = rows.length ? rows.map(item => `
    <div class="usage-row">
      <span class="usage-interface">${esc(item.name)}</span>
      <span class="usage-duration">${formatUsageDuration(item.foreground_seconds)}</span>
      <span class="usage-interactions">${Number(item.ai_interaction_count || 0)} turns</span>
    </div>`).join("") : `<div class="placeholder">${currentLang === "zh-TW" ? "今日尚無已觀察到的介面使用資料。" : "No observed interface usage today."}</div>`;
}

function renderUsagePanelError() {
  $("usage-coverage").className = "trust broken";
  $("usage-coverage").textContent = "UNAVAILABLE";
  $("usage-interface-list").innerHTML = `<div class="placeholder">${currentLang === "zh-TW" ? "無法載入使用時間。" : "Unable to load usage data."}</div>`;
}

function captureStateLabel(state) {
  const labels = currentLang === "zh-TW" ? {
    observed: "已觀察", waiting: "等待資料", available_waiting: "可讀取／待掃描",
    cache_detected_unparsed: "快取存在／未解析", unsupported: "目前不支援", not_applicable: "不適用"
  } : {
    observed: "OBSERVED", waiting: "WAITING", available_waiting: "READY TO SCAN",
    cache_detected_unparsed: "CACHE / UNPARSED", unsupported: "UNSUPPORTED", not_applicable: "N/A"
  };
  return labels[state] || String(state || "UNKNOWN").toUpperCase();
}

function renderCaptureCoverage(data, extensionData) {
  const platforms = data.platforms || [];
  const observed = platforms.reduce((total, item) => total + [item.desktop_focus, item.web_capture, item.transcript_capture].filter(channel => channel.state === "observed").length, 0);
  const badge = $("capture-coverage-badge");
  badge.className = "trust " + (observed > 0 ? "ok" : "noisy");
  badge.textContent = `${observed} OBSERVED`;
  const extension = (extensionData && extensionData.extension) || null;
  $("capture-extension-summary").textContent = extension
    ? (currentLang === "zh-TW"
      ? `Browser Extension：今日 ${Number(extension.events_today || 0)} events · ${extension.heartbeat_verified ? "近期已連線" : "尚無近期 heartbeat"}`
      : `Browser Extension: ${Number(extension.events_today || 0)} events today · ${extension.heartbeat_verified ? "recent heartbeat" : "no recent heartbeat"}`)
    : (currentLang === "zh-TW" ? "Browser Extension 狀態目前無法取得。" : "Browser Extension status is unavailable.");
  const signal = (label, item, detail) => `<div class="capture-signal ${esc(item.state || "waiting")}" title="${esc(captureStateLabel(item.state))}">
    <span class="capture-signal-label">${label}</span>
    <span class="capture-signal-value">${detail}</span>
  </div>`;
  $("capture-coverage-list").innerHTML = platforms.map(item => {
    const focus = item.desktop_focus || {};
    const web = item.web_capture || {};
    const transcript = item.transcript_capture || {};
    return `<div class="capture-coverage-row">
      <div class="capture-platform-name">${esc(item.label)}</div>
      ${signal("FOCUS", focus, focus.state === "observed" ? formatUsageDuration(focus.foreground_seconds_today || 0) : "—")}
      ${signal("WEB", web, web.state === "observed" ? `${Number(web.turns_today || 0)} / ${Number(web.responses_today || 0)}` : "—")}
      ${signal("LOG", transcript, transcript.state === "observed" ? `${Number(transcript.turns_today || 0)} / ${Number(transcript.responses_today || 0)}` : "—")}
    </div>`;
  }).join("");
  $("capture-coverage-boundary").textContent = currentLang === "zh-TW"
    ? "FOCUS 只記前景時間；WEB 為 Extension turns／responses；LOG 為本機 transcript turns／responses。三者不能互相替代。"
    : "FOCUS is foreground time; WEB is Extension turns/responses; LOG is local transcript turns/responses. These signals are independent.";
}

function renderCaptureCoverageError() {
  $("capture-coverage-badge").className = "trust broken";
  $("capture-coverage-badge").textContent = "UNAVAILABLE";
  $("capture-extension-summary").textContent = currentLang === "zh-TW" ? "採集狀態暫時無法取得。" : "Capture status is unavailable.";
  $("capture-coverage-list").innerHTML = `<div class="placeholder">${currentLang === "zh-TW" ? "無法取得採集 coverage。" : "Capture coverage is unavailable."}</div>`;
}

function renderStats(m) {
  const items = [
    { tag: "AI TURNS", value: m.ai_prompts_count, label: "Claude / Codex / Web", trust: "ok" },
    { tag: "FILES", value: m.file_events_count, label: currentLang === "zh-TW" ? "論文與檔案異動" : "Paper & File Events", trust: "ok" },
    { tag: "COMMITS", value: m.git_commits_count, label: currentLang === "zh-TW" ? "Git commits 提交" : "Git Commits", trust: "ok" },
    { tag: "FOCUS", value: m.window_events_count, label: currentLang === "zh-TW" ? "視窗焦點切換" : "Window Focus", trust: "ok" },
    { tag: "STREAMS", value: projectsCache.length, label: currentLang === "zh-TW" ? "進行中工作" : "Active Workstreams", trust: "ok" }
  ];
  const dot = { ok: "var(--orange)", noisy: "var(--warn)", broken: "var(--danger)" };
  $("stats-strip").innerHTML = items.map(s => `
    <div class="stat">
      <div class="stat-top">
        <span class="mono-mini muted">${s.tag}</span>
        <span class="stat-dot" style="background:${dot[s.trust]}" title="${s.trust}"></span>
      </div>
      <div class="stat-value">${Number(s.value || 0).toLocaleString()}</div>
      <div class="stat-label">${s.label}</div>
    </div>`).join("");
}

function renderCollectors(w, lastEvents = {}, health = {}, diagnostics = {}) {
  const items = [
    { key: "file_watcher", name: t("collector_file"), on: w.file_watcher, last: lastEvents.file_watcher, h: health.file_watcher || "stale", d: diagnostics.file_watcher },
    { key: "git_watcher", name: t("collector_git"), on: w.git_watcher, last: lastEvents.git_watcher, h: health.git_watcher || "stale", d: diagnostics.git_watcher },
    { key: "window_watcher", name: t("collector_window"), on: w.window_watcher, last: lastEvents.window_watcher, h: health.window_watcher || "stale", d: diagnostics.window_watcher },
    { key: "agent_log_watcher", name: t("collector_agent"), on: w.agent_log_watcher, last: lastEvents.agent_log_watcher, h: health.agent_log_watcher || "stale", d: diagnostics.agent_log_watcher },
    { key: "scheduler", name: t("collector_scheduler"), on: w.scheduler, last: null, h: health.scheduler || "healthy", d: diagnostics.scheduler }
  ];

  const colorMap = {
    healthy: "var(--success, #22c55e)",
    idle: "var(--warn, #eab308)",
    degraded: "var(--danger, #ef4444)",
    stopped: "var(--danger, #ef4444)",
    stale: "var(--danger, #ef4444)",
    disabled: "var(--mu, #888)"
  };

  const labelMap = {
    healthy: currentLang === "zh-TW" ? "運作中" : "Active",
    idle: currentLang === "zh-TW" ? "待命中" : "Idle",
    degraded: currentLang === "zh-TW" ? "部分採集異常" : "Degraded",
    stopped: currentLang === "zh-TW" ? "已停止" : "Stopped",
    stale: currentLang === "zh-TW" ? "無有效資料" : "Stale",
    disabled: currentLang === "zh-TW" ? "已停用" : "Disabled"
  };

  $("watchers-grid").innerHTML = items.map(it => {
    let lastTimeStr = "";
    if (it.last) {
      const timePart = it.last.includes(" ") ? it.last.split(" ")[1] : it.last;
      lastTimeStr = `<span class="mono-mini" style="font-size:10px; opacity:0.85; display:block; margin-top:2px; color:var(--text-dim);">${currentLang === "zh-TW" ? "最後寫入" : "Last"}: ${timePart}</span>`;
    } else if (it.on && it.key !== "scheduler") {
      lastTimeStr = `<span class="mono-mini" style="font-size:10px; opacity:0.85; display:block; margin-top:2px; color:var(--danger, #ef4444); font-weight:600;">${currentLang === "zh-TW" ? "尚無紀錄 (待排查)" : "No data (Pending)"}</span>`;
    }
    const dotColor = colorMap[it.h] || "var(--mu)";
    const statusText = labelMap[it.h] || (it.on ? t("collector_enabled") : t("collector_disabled"));
    let diagnosticText = "";
    if (it.key === "window_watcher" && it.d && ["unavailable", "error"].includes(it.d.state)) {
      diagnosticText = currentLang === "zh-TW"
        ? `前景 probe 不可用 · ${Number(it.d.unavailable_seconds || 0)}s`
        : `Foreground probe unavailable · ${Number(it.d.unavailable_seconds || 0)}s`;
    }
    if (it.key === "agent_log_watcher" && it.d && it.d.sources) {
      const failed = Object.entries(it.d.sources).filter(([, value]) => value.state === "error").map(([key]) => key);
      if (failed.length) diagnosticText = `${currentLang === "zh-TW" ? "來源錯誤" : "Source error"}: ${failed.join(", ")}`;
    }

    return `
    <div class="collector">
      <div class="collector-name" style="display:flex; align-items:center; gap:6px;">
        <span style="width:7px; height:7px; border-radius:50%; background:${dotColor}; display:inline-block;"></span>
        ${it.name}
      </div>
      <div class="collector-state" style="color:${dotColor}">
        ${statusText}
        ${lastTimeStr}
        ${diagnosticText ? `<span class="collector-diagnostic">${esc(diagnosticText)}</span>` : ""}
      </div>
    </div>`;
  }).join("");
}

// ---------------------------------------------------------------- P3 context memory
async function loadContextSessions() {
  const box = $("context-sessions-list");
  try {
    contextSessionsCache = await getJSON("/api/v1/context/sessions");
    renderContextSessions();
  } catch (e) {
    if (box) box.innerHTML = `<div class="placeholder">${currentLang === "zh-TW" ? "近期工作階段暫時無法讀取。" : "Recent work sessions are temporarily unavailable."}</div>`;
  }
}

function formatContextTime(value) {
  const parsed = new Date(value);
  if (Number.isNaN(parsed.getTime())) return "—";
  return parsed.toLocaleString(currentLang === "zh-TW" ? "zh-TW" : "en", {
    month: "2-digit", day: "2-digit", hour: "2-digit", minute: "2-digit"
  });
}

function renderContextSessions() {
  const box = $("context-sessions-list");
  const badge = $("context-sessions-badge");
  if (!box || !badge) return;
  if (!contextSessionsCache) {
    badge.textContent = "LOADING";
    return;
  }
  const sessions = contextSessionsCache.sessions || [];
  badge.textContent = `${sessions.length} SESSIONS`;
  badge.className = `trust ${sessions.length ? "ok" : "noisy"}`;
  if (!sessions.length) {
    box.innerHTML = `<div class="placeholder">${currentLang === "zh-TW" ? "近 72 小時沒有可歸戶的 AI、Git 或檔案事件。" : "No canonical AI, Git, or file events in the last 72 hours."}</div>`;
    return;
  }
  box.innerHTML = sessions.map(session => {
    const counts = session.event_counts || {};
    const chips = [
      counts.ai_turn ? `AI ${counts.ai_turn}` : "",
      counts.git_commit ? `GIT ${counts.git_commit}` : "",
      counts.file_activity ? `FILE ${counts.file_activity}` : "",
      `${session.events_observed || 0} EVENTS`
    ].filter(Boolean).map(label => `<span class="context-memory-chip">${esc(label)}</span>`).join("");
    return `
      <article class="context-session" data-session-id="${esc(session.session_id)}">
        <div class="context-session-top">
          <span class="context-session-project">${esc(session.project_key)}</span>
          <span class="context-session-time">${esc(formatContextTime(session.ended_at))}</span>
        </div>
        <div class="context-session-headline">${esc(session.headline || session.narrative || "—")}</div>
        <div class="context-session-meta">${chips}</div>
      </article>`;
  }).join("");
}

async function searchRelatedContext() {
  const input = $("input-related-question");
  const button = $("btn-related-search");
  const box = $("related-memory-results");
  const question = (input.value || "").trim();
  if (question.length < 2) {
    showToast(currentLang === "zh-TW" ? "請先輸入至少兩個字。" : "Enter at least two characters.");
    return;
  }
  button.disabled = true;
  button.textContent = currentLang === "zh-TW" ? "查詢中…" : "Searching…";
  box.innerHTML = `<div class="placeholder">${currentLang === "zh-TW" ? "正在查詢本機 semantic index…" : "Searching the local semantic index…"}</div>`;
  try {
    relatedContextCache = await postJSON("/api/v1/context/related", {
      question,
      top_k: 8
    });
    renderRelatedContext(relatedContextCache);
  } catch (e) {
    relatedContextCache = null;
    box.innerHTML = `<div class="placeholder">${currentLang === "zh-TW" ? "本機 Ollama 或 semantic index 目前不可用；查詢內容未保存。" : "Local Ollama or the semantic index is unavailable; the query was not stored."}</div>`;
  } finally {
    button.disabled = false;
    button.textContent = t("btn_related_search");
  }
}

function renderRelatedContext(data) {
  const box = $("related-memory-results");
  if (!box || !data) return;
  const matches = data.matches || [];
  const advisory = currentLang === "zh-TW"
    ? (matches.length ? "找到語意相近的歷史紀錄；請檢視來源後再決定是否可沿用。" : "沒有超過門檻的相似紀錄，但不代表歷史中一定沒有相關工作。")
    : (matches.length ? "Semantically related history found. Review each source before reuse." : "No match crossed the threshold; related history may still exist outside current coverage.");
  const rows = matches.map(item => `
    <div class="related-memory-match">
      <div class="related-memory-match-title">${esc(item.title || item.source_ref)}</div>
      <div class="related-memory-match-meta">${esc(item.source_ref)} · SCORE ${Number(item.score || 0).toFixed(3)} · ${esc(item.trust_status || "observed")}${item.project_key ? ` · ${esc(item.project_key)}` : ""}</div>
    </div>`).join("");
  box.innerHTML = `<div class="related-memory-advisory">${esc(advisory)}</div>${rows || ""}`;
}

// ---------------------------------------------------------------- P5-1 proposal-only secretary
async function loadSecretaryProposals() {
  const box = $("secretary-proposals-list");
  try {
    secretaryProposalsCache = await getJSON("/api/v1/secretary/proposals?limit=6");
    renderSecretaryProposals();
  } catch (e) {
    if (box) box.innerHTML = `<div class="placeholder">${currentLang === "zh-TW" ? "建議暫時無法讀取。" : "Suggestions are temporarily unavailable."}</div>`;
  }
}

function renderSecretaryProposals() {
  const box = $("secretary-proposals-list");
  const badge = $("secretary-proposals-badge");
  if (!box || !badge) return;
  if (!secretaryProposalsCache) return;
  const proposals = secretaryProposalsCache.proposals || [];
  badge.textContent = `${proposals.length} ${currentLang === "zh-TW" ? "項" : "ITEMS"}`;
  badge.className = `trust ${proposals.length ? "noisy" : "ok"}`;
  if (!proposals.length) {
    box.innerHTML = `<div class="placeholder">${currentLang === "zh-TW" ? "目前沒有超過規則門檻的建議；不代表所有工作都已完成。" : "No suggestion crossed the current rule threshold; this does not prove all work is complete."}</div>`;
    return;
  }
  box.innerHTML = proposals.map(item => {
    const refs = (item.evidence_refs || []).map(ref => `<span class="proposal-ref">${esc(ref)}</span>`).join("");
    const priority = String(item.priority || "medium").toUpperCase();
    return `
      <article class="proposal-card">
        <div class="proposal-card-top">
          <span class="proposal-project">${esc(item.project_key || "OmniContext")}</span>
          <span class="trust ${item.priority === "high" ? "broken" : "noisy"}">${esc(priority)}</span>
        </div>
        <div class="proposal-title">${esc(item.title)}</div>
        <div class="proposal-reason">${esc(item.reason)}</div>
        <div class="proposal-action"><span>${currentLang === "zh-TW" ? "建議" : "Suggested"}</span>${esc(item.suggested_action)}</div>
        <div class="proposal-meta"><span>${esc(item.risk_level || "L0_READ_ONLY")}</span><span>${currentLang === "zh-TW" ? "不執行" : "NOT EXECUTABLE"}</span></div>
        <div class="proposal-refs">${refs}</div>
      </article>`;
  }).join("");
}

// ---------------------------------------------------------------- live feed
async function refreshFeed() {
  try {
    const events = await getJSON(`/api/v1/events/recent?limit=60&event_type=${activeFilter}`);
    if (activeFilter === "all") recentEvents = events;
    const box = $("feed-list");
    if (!events.length) { box.innerHTML = `<div class="placeholder">${t("ph_no_feed")}</div>`; return; }
    box.innerHTML = events.map(e => `
      <div class="frow">
        <span class="ftime">${esc((e.timestamp || "").split(" ")[1] || "")}</span>
        <span class="fbadge ${e.type}">${esc(String(e.badge || e.type).toUpperCase())}</span>
        <div class="fbody">
          <span class="ftitle">${esc(e.title)}</span>
          <span class="fdetail">${esc(e.detail || "")}</span>
        </div>
      </div>`).join("");
  } catch (e) { /* 保留上次畫面 */ }
}

// ---------------------------------------------------------------- projects
async function loadProjects(force) {
  try {
    projectsCache = await getJSON("/api/v1/projects/active");
    if (force || !recentEvents.length) {
      try { recentEvents = await getJSON("/api/v1/events/recent?limit=200&event_type=all"); } catch (e) {}
    }
    renderResume();
    renderProjects();
    $("projects-count").textContent = `${t("active_workstreams")} · ${projectsCache.length}`;
  } catch (e) {
    $("projects-list").innerHTML = `<div class="placeholder">${t("ph_loading_projects")}</div>`;
  }
}

function statusLabel(p) {
  if (p.status === "active") return t("status_active");
  return t("status_idle", { days: p.idle_days });
}

// ---------------------------------------------------------------- Quick Actions & Toast
function showToast(msg, duration = 3200) {
  let box = $("toast-container");
  if (!box) {
    box = document.createElement("div");
    box.id = "toast-container";
    document.body.appendChild(box);
  }
  const el = document.createElement("div");
  el.className = "toast";
  el.textContent = msg;
  box.appendChild(el);
  setTimeout(() => {
    el.style.opacity = "0";
    el.style.transition = "opacity 0.3s ease";
    setTimeout(() => el.remove(), 300);
  }, duration);
}

async function runOpenAction(path, action = "explorer", url = null) {
  try {
    const res = await postJSON("/api/v1/control/open_path", { path, action, url });
    if (res && res.status === "success") {
      showToast("⚡ " + res.message);
    } else {
      showToast("⚠️ " + (res.message || "無法開啟目標"));
    }
  } catch (e) {
    showToast("⚠️ 操作失敗: " + e.message);
  }
}

function renderActionGroup(p) {
  if (!p) return "";
  const ghUrl = p.github_url || (p.github && p.github.html_url);
  const path = p.local_path;
  const vsCodeUri = path ? "vscode://file/" + encodeURI(path.replace(/\\/g, "/")) : "";
  const folderUri = path ? "openfolder:///" + encodeURI(path.replace(/\\/g, "/")) : "";

  const folderBtn = path
    ? `<a class="action-btn" href="${esc(folderUri)}" data-act="folder" data-path="${esc(path)}" title="${currentLang === 'zh-TW' ? '開啟本機資料夾 (' + esc(path) + ')' : 'Open Folder (' + esc(path) + ')'}">📁</a>`
    : `<button class="action-btn disabled" title="${currentLang === 'zh-TW' ? '尚未定位到本機路徑' : 'No local path'}">📁</button>`;

  const vsCodeBtn = path
    ? `<a class="action-btn" href="${esc(vsCodeUri)}" title="${currentLang === 'zh-TW' ? '在 VS Code 中開啟專案' : 'Open in VS Code'}">💻</a>`
    : `<button class="action-btn disabled" title="${currentLang === 'zh-TW' ? '尚未定位到本機路徑' : 'No local path'}">💻</button>`;

  const ghBtn = ghUrl
    ? `<a class="action-btn" href="${esc(ghUrl)}" target="_blank" rel="noopener noreferrer" title="${currentLang === 'zh-TW' ? '前往 GitHub 專案頁面' : 'Open on GitHub'}">🐙</a>`
    : `<button class="action-btn disabled" title="${currentLang === 'zh-TW' ? '未綁定 GitHub 倉庫' : 'No GitHub repo'}">🐙</button>`;

  return `
    <div class="action-group" onclick="event.stopPropagation()">
      ${folderBtn}
      ${vsCodeBtn}
      ${ghBtn}
    </div>`;
}

function attachActionGroupListeners(parentEl) {
  parentEl.querySelectorAll("[data-act]").forEach(btn => {
    btn.addEventListener("click", async (ev) => {
      ev.stopPropagation();
      const act = btn.dataset.act;
      if (act === "folder") {
        const pth = btn.dataset.path;
        if (pth) {
          try {
            await navigator.clipboard.writeText(pth);
          } catch (_) {}
        }
        runOpenAction(pth, "explorer");
      }
    });
  });
}

async function copyProjectHandoff(projectKey, displayName) {
  try {
    showToast(currentLang === "zh-TW" ? "⏳ 正在提煉專案接續記憶..." : "⏳ Building context handoff...");
    const res = await getJSON(`/api/v1/projects/${encodeURIComponent(projectKey)}/handoff?turns=5`);
    if (res && res.markdown) {
      await navigator.clipboard.writeText(res.markdown);
      const name = displayName || res.display_name || projectKey;
      showToast(currentLang === "zh-TW" ? `⚡ 已複製 [${name}] 接續 Prompt！可直接貼入任何 AI 開工` : `⚡ Copied [${name}] handoff prompt to clipboard!`);
    } else {
      showToast("⚠️ 無法生成接續 Prompt");
    }
  } catch (e) {
    showToast("⚠️ 複製失敗: " + e.message);
  }
}

function renderResume() {
  const box = document.querySelector("#resume-card .resume-body");
  const p = projectsCache[0];
  if (!p) {
    box.innerHTML = `<div class="placeholder">${t("ph_no_projects")}</div>`;
    return;
  }
  box.innerHTML = `
    <div style="min-width:0">
      <div class="resume-title">${esc(p.display_name)}</div>
      <div class="resume-action">${esc(p.last_action_summary || "無紀錄")}</div>
      <div class="resume-meta">${esc(p.last_activity_at)} · ${esc(p.category || "")} · ${t("open_loop_count")} ${p.open_loops_count}</div>
    </div>
    <div style="display:flex; align-items:center; gap:8px; flex-shrink:0; flex-wrap:wrap;">
      ${renderActionGroup(p)}
      <button class="btn" data-copy-handoff="${esc(p.project_key)}" data-name="${esc(p.display_name)}" style="background:var(--s2); border:1px solid var(--bd); color:var(--tx); font-weight:600; font-size:12px; padding:6px 12px; cursor:pointer;" title="${currentLang === 'zh-TW' ? '一鍵複製結構化接續 Prompt 貼入 AI 開工' : 'Copy structured handoff prompt for AI'}">${t("btn_copy_handoff")}</button>
      <button class="btn btn-primary" data-resume="${esc(p.project_key)}">${currentLang === "zh-TW" ? "接續 →" : "Resume →"}</button>
    </div>`;
  attachActionGroupListeners(box);
  const copyBtn = box.querySelector("[data-copy-handoff]");
  if (copyBtn) {
    copyBtn.addEventListener("click", (ev) => {
      ev.stopPropagation();
      copyProjectHandoff(p.project_key, p.display_name);
    });
  }
  const btn = box.querySelector("[data-resume]");
  if (btn) btn.addEventListener("click", () => expandProject(p.project_key, true));
}

function renderProjects() {
  const box = $("projects-list");
  if (!projectsCache.length) {
    box.innerHTML = `<div class="placeholder">${t("ph_no_projects")}</div>`;
    return;
  }

  const activeProjects = projectsCache.filter(p => (p.idle_days == null || p.idle_days <= 60));
  const idleProjects = projectsCache.filter(p => (p.idle_days != null && p.idle_days > 60));

  // 決定渲染之專案清單
  let listToRender = projectsCache;
  if (!showAllProjects) {
    if (expandedProject && !activeProjects.some(p => p.project_key === expandedProject)) {
      const exp = idleProjects.find(p => p.project_key === expandedProject);
      listToRender = exp ? [...activeProjects, exp] : activeProjects;
    } else {
      listToRender = activeProjects;
    }
  }

  const pCountEl = $("projects-count");
  if (pCountEl) {
    pCountEl.textContent = `${t("active_workstreams")} · ${showAllProjects ? projectsCache.length : activeProjects.length + ' / ' + projectsCache.length}`;
  }

  let projectsHtml = listToRender.map(p => {
    const bar = p.status === "active" ? "var(--orange)"
      : (p.open_loops_count > 0 ? "var(--warn)" : "var(--bd)");
    const loopColor = p.open_loops_count >= 3 ? "var(--orange)"
      : (p.open_loops_count > 0 ? "var(--tx)" : "var(--mu)");
    const open = expandedProject === p.project_key;

    let ghBadge = "";
    if (p.github) {
      const prs = p.github.prs || [];
      if (prs.length) {
        const latest = prs[0];
        const isMerged = latest.state === "merged";
        const bg = isMerged ? "rgba(168, 85, 247, 0.16)" : "rgba(242, 106, 15, 0.16)";
        const color = isMerged ? "#c084fc" : "var(--orange)";
        ghBadge = `<span class="trust" style="background:${bg}; color:${color}; font-size:9.5px; margin-left:6px;" title="${esc(latest.title)}">PR #${latest.number} ${latest.state.toUpperCase()}</span>`;
      } else {
        ghBadge = `<span class="trust ok" style="font-size:9.5px; margin-left:6px;">🐙 ${p.github.is_private ? "PRIVATE" : "PUBLIC"}</span>`;
      }
    }

    const isIdleOver60 = p.idle_days != null && p.idle_days > 60;
    const idleBadge = isIdleOver60 ? `<span class="trust" style="background:var(--s2); color:var(--mu); font-size:9px; margin-left:4px;">>60d</span>` : "";

    return `
      <div class="pitem" data-key="${esc(p.project_key)}">
        <div class="prow">
          <span class="pbar" style="background:${bar}"></span>
          <div style="min-width:0">
            <div class="pname" style="display:flex; align-items:center;">
              <span>${esc(p.display_name)}</span>
              ${ghBadge}
              ${idleBadge}
            </div>
            <div class="pmeta">${esc(p.category || "")} · ${statusLabel(p)}</div>
          </div>
          <div class="paction">${esc(p.last_action_summary || "無紀錄")}</div>
          ${renderActionGroup(p)}
          <div class="ploops" style="color:${loopColor}">${p.open_loops_count}<span>${t("open_loop_count")}</span></div>
          <div class="plast">${esc((p.last_activity_at || "").replace(/^\d{4}-/, ""))}</div>
          <div class="pchev">${open ? "▾" : "▸"}</div>
        </div>
        <div class="pdetail-slot"></div>
      </div>`;
  }).join("");

  let toggleHtml = "";
  if (idleProjects.length > 0) {
    toggleHtml = `
      <div style="text-align:center; padding:12px 0 10px; margin-top:6px; border-top:1px dashed var(--bd);">
        <button id="btn-toggle-idle-projects" class="btn" style="background:var(--s2); border:1px solid var(--bd); color:var(--tx); font-size:11.5px; font-weight:600; padding:6px 16px; cursor:pointer;" title="切換超過 60 天未活躍的專案">
          ${showAllProjects
            ? t("btn_collapse_projects")
            : t("btn_show_more_projects", { count: idleProjects.length })
          }
        </button>
      </div>`;
  }

  box.innerHTML = projectsHtml + toggleHtml;

  attachActionGroupListeners(box);

  const toggleBtn = box.querySelector("#btn-toggle-idle-projects");
  if (toggleBtn) {
    toggleBtn.addEventListener("click", (ev) => {
      ev.stopPropagation();
      showAllProjects = !showAllProjects;
      renderProjects();
    });
  }

  box.querySelectorAll(".pitem").forEach(item => {
    item.querySelector(".prow").addEventListener("click", () => {
      const key = item.dataset.key;
      expandProject(expandedProject === key ? null : key);
    });
  });

  if (expandedProject) renderProjectDetail(expandedProject);
}

function expandProject(key, scroll) {
  if (key) {
    // 若要展開的專案屬於 60 天以上閒置專案，自動切換至顯示全部
    const isIdle = projectsCache.some(p => p.project_key === key && p.idle_days > 60);
    if (isIdle) showAllProjects = true;
  }
  expandedProject = key;
  renderProjects();
  if (key && scroll) {
    setTimeout(() => {
      const el = document.querySelector(`.pitem[data-key="${CSS.escape(key)}"]`);
      if (el) window.scrollTo({ top: el.getBoundingClientRect().top + window.scrollY - 160, behavior: "smooth" });
    }, 50);
  }
}

async function renderProjectDetail(key) {
  const item = document.querySelector(`.pitem[data-key="${CSS.escape(key)}"]`);
  if (!item) return;
  const slot = item.querySelector(".pdetail-slot");
  slot.innerHTML = `<div class="pdetail"><div class="placeholder">${t("ph_loading_projects")}</div></div>`;

  const proj = projectsCache.find(p => p.project_key === key);
  let events = [];
  try {
    events = await getJSON(`/api/v1/events/recent?limit=30&project=${encodeURIComponent(key)}`);
  } catch (e) {
    events = recentEvents.filter(e => (e.project || "") === key || (proj && (e.project || "") === proj.display_name));
  }

  let loops = [];
  try { loops = await getJSON(`/api/v1/open-loops?project=${encodeURIComponent(key)}`); } catch (e) {}

  // 提取該專案近期異動的檔案清單
  const fileEvents = events.filter(e => e.type === "file");
  const distinctFiles = [];
  const seenPaths = new Set();
  for (const f of fileEvents) {
    if (!seenPaths.has(f.detail || f.title)) {
      seenPaths.add(f.detail || f.title);
      distinctFiles.push(f);
    }
  }

  const dotOf = { ai: "var(--orange)", git: "var(--warn)", window: "var(--mu)", file: "var(--bd)" };
  const tl = events.length
    ? events.slice(0, 10).map(e => `
        <div class="tl">
          <span class="tl-time">${esc((e.timestamp || "").split(" ")[1] || "")}</span>
          <span class="tl-dot" style="background:${dotOf[e.type] || "var(--bd)"}"></span>
          <span class="tl-text">${esc(e.title)} <small class="muted">(${esc(e.response || "")})</small></span>
        </div>`).join("")
    : `<div class="placeholder" style="padding:0">${t("ph_no_feed")}</div>`;

  const fileListHtml = distinctFiles.length
    ? distinctFiles.map(f => `
        <div style="padding: 5px 8px; border: 1px solid var(--bd); margin-bottom: 4px; background: var(--s2); font-size: 11px; display: flex; justify-content: space-between; align-items: center;">
          <div style="overflow: hidden; text-overflow: ellipsis; white-space: nowrap; max-width: 80%;">
            <strong style="color: var(--tx);">${esc(f.title.replace(/^\[[^\]]+\]\s*/, ''))}</strong>
            <span class="muted mono-mini" style="margin-left: 6px; font-size: 9.5px;">${esc(f.detail || '')}</span>
          </div>
          <span class="mono-mini accent" style="font-size: 9.5px; flex-shrink: 0;">${esc(f.response || '')}</span>
        </div>`).join("")
    : '<div class="placeholder" style="padding:0">無檔案異動紀錄。</div>';

  const ll = loops.length
    ? loops.map(l => `<div class="pl"><b>·</b><span>${esc(l.title)}</span></div>`).join("")
    : `<div class="placeholder" style="padding:0">${t("ph_no_loops")}</div>`;

  let ghSection = "";
  if (proj && proj.github) {
    const gh = proj.github;
    const prsHtml = (gh.prs && gh.prs.length)
      ? gh.prs.map(pr => {
          const isMerged = pr.state === "merged";
          const stateClass = isMerged ? "ok" : (pr.state === "open" ? "noisy" : "broken");
          const ciBadge = pr.ci_status !== "neutral" ? ` · CI: ${esc(pr.ci_status.toUpperCase())}` : "";
          return `
            <div style="padding: 7px 9px; border: 1px solid var(--bd); margin-bottom: 6px; background: var(--s2); font-size: 11.5px;">
              <div style="display:flex; justify-content:space-between; align-items:center; gap: 8px;">
                <a href="${esc(pr.html_url)}" target="_blank" style="color:var(--orange); font-weight:700; text-decoration:none; overflow:hidden; text-overflow:ellipsis; white-space:nowrap;">
                  #${pr.number} ${esc(pr.title)} ↗
                </a>
                <span class="trust ${stateClass}" style="flex-shrink:0;">${esc(pr.state.toUpperCase())}</span>
              </div>
              <div class="mono-mini muted mt-2" style="font-size:10px;">
                ${esc(pr.branch)} · ${esc(pr.author || "")}${ciBadge}
              </div>
            </div>`;
        }).join("")
      : '<div class="placeholder" style="padding:0">此倉庫目前無近期 PR 紀錄。</div>';

    ghSection = `
      <div style="grid-column: 1 / -1; margin-top: 10px; padding-top: 10px; border-top: 1px dashed var(--bd);">
        <div style="display:flex; justify-content:space-between; align-items:center; margin-bottom:8px;">
          <span class="mono-label">${t("sec_gh_pr")}</span>
          <a href="${esc(gh.html_url)}" target="_blank" class="mono-mini" style="color:var(--orange); text-decoration:none;">${esc(gh.full_name)} (${gh.is_private ? "Private" : "Public"}) ↗</a>
        </div>
        <div>${prsHtml}</div>
      </div>`;
  }

  // 快捷本機操作路徑條
  let quickBar = "";
  if (proj) {
    quickBar = `
      <div style="grid-column: 1 / -1; margin-bottom: 14px; padding: 10px 14px; background: var(--s1); border: 1px solid var(--bd); display: flex; justify-content: space-between; align-items: center; gap: 12px; flex-wrap: wrap;">
        <div style="min-width:0; display:flex; align-items:center; gap:8px;">
          <span class="mono-mini accent" style="font-weight:700; flex-shrink:0;">LOCAL PATH:</span>
          <code style="font-size:11.5px; background:transparent; color:var(--tx); word-break:break-all;">${esc(proj.local_path || '尚未定位到本機路徑')}</code>
        </div>
        <div style="display:flex; gap:8px; align-items:center; flex-shrink:0;">
          <button class="btn" data-detail-handoff="${esc(proj.project_key)}" data-name="${esc(proj.display_name)}" style="background:var(--s2); border:1px solid var(--bd); color:var(--tx); font-weight:600; font-size:11.5px; padding:4px 10px; cursor:pointer;" title="${currentLang === 'zh-TW' ? '一鍵複製結構化接續 Prompt 貼入 AI 開工' : 'Copy structured handoff prompt for AI'}">${t("btn_copy_handoff")}</button>
          ${renderActionGroup(proj)}
        </div>
      </div>`;
  }

  slot.innerHTML = `
    <div class="pdetail">
      ${quickBar}
      <div>
        <span class="mono-label">${t("sec_files_modified")}</span>
        ${fileListHtml}
      </div>
      <div>
        <span class="mono-label">${t("sec_timeline")}</span>
        ${tl}
      </div>
      <div>
        <span class="mono-label">${t("sec_open_loops")}</span>
        ${ll}
        <div class="pdetail-actions">
          <button class="btn btn-primary btn-sm" data-cp>${t("btn_snapshot_now")}</button>
        </div>
      </div>
      ${ghSection}
    </div>`;

  attachActionGroupListeners(slot);
  const detailHandoffBtn = slot.querySelector("[data-detail-handoff]");
  if (detailHandoffBtn && proj) {
    detailHandoffBtn.addEventListener("click", (ev) => {
      ev.stopPropagation();
      copyProjectHandoff(proj.project_key, proj.display_name);
    });
  }
  const cpBtn = slot.querySelector("[data-cp]");
  if (cpBtn) cpBtn.addEventListener("click", (ev) => { ev.stopPropagation(); triggerCheckpoint(); });
}


// ---------------------------------------------------------------- open loops rail
async function loadOpenLoops() {
  try {
    loopsCache = await getJSON("/api/v1/open-loops");
    renderOpenLoops();
  } catch (e) {
    $("open-loops-list").innerHTML = `<div class="placeholder">${t("ph_loading_loops")}</div>`;
  }
}

function renderOpenLoops() {
  $("loop-tally").textContent = String(loopsCache.length);
  const box = $("open-loops-list");
  if (!loopsCache.length) {
    box.innerHTML = `<div class="placeholder">${t("ph_no_loops")}</div>`;
    return;
  }
  box.innerHTML = loopsCache.map(l => `
    <div class="loop" data-id="${l.id}" data-project="${esc(l.project_key)}" title="${t('title_click_to_open_project')}">
      <div class="loop-main">
        <div class="loop-text">${esc(l.title)}</div>
        <div class="loop-src"><span style="color:var(--orange); font-weight:700;">${esc(l.project_key)}</span> · ${esc(l.created_at || "")}</div>
      </div>
      <button class="loop-resolve-btn" data-resolve="${l.id}" title="${t('title_mark_resolved')}">✓</button>
    </div>`).join("");

  box.querySelectorAll(".loop").forEach(el => {
    // 點選主要區域：跳轉至 01 進行中工作頁籤並展開該專案
    el.addEventListener("click", (ev) => {
      const pKey = el.dataset.project;
      if (pKey) {
        // 切換到 01 專案頁籤
        const tabBtn = document.querySelector('.tabs button[data-tab="tab-projects"]');
        if (tabBtn) tabBtn.click();

        // 若屬於 60 天以上閒置專案，自動切換至顯示全部
        const isIdle = projectsCache.some(p => p.project_key === pKey && p.idle_days > 60);
        if (isIdle) showAllProjects = true;

        expandProject(pKey, true);
        showToast(currentLang === "zh-TW" ? `🎯 已定位並展開專案: ${pKey}` : `🎯 Focused project: ${pKey}`);
      }
    });

    // 點選結案按鈕：確認解決此未結事項
    const resBtn = el.querySelector("[data-resolve]");
    if (resBtn) {
      resBtn.addEventListener("click", (ev) => {
        ev.stopPropagation();
        resolveLoop(el);
      });
    }
  });
}

async function resolveLoop(el) {
  const id = el.dataset.id;
  if (el.classList.contains("done")) return;
  el.classList.add("done");
  const resBtn = el.querySelector(".loop-resolve-btn");
  if (resBtn) {
    resBtn.style.background = "var(--ok)";
    resBtn.style.borderColor = "var(--ok)";
    resBtn.style.color = "#fff";
  }
  try {
    await postJSON(`/api/v1/open-loops/${id}/resolve`);
    loopsCache = loopsCache.filter(l => String(l.id) !== String(id));
    $("loop-tally").textContent = String(loopsCache.length);
    showToast(currentLang === "zh-TW" ? "⚡ 未結事項已標記為已結案！" : "⚡ Marked open loop as resolved!");
    setTimeout(() => { renderOpenLoops(); loadProjects(); }, 550);
  } catch (e) {
    el.classList.remove("done");
    if (resBtn) {
      resBtn.style.background = "";
      resBtn.style.borderColor = "";
      resBtn.style.color = "";
    }
    showToast("⚠️ 結案失敗: " + e.message);
  }
}

// ---------------------------------------------------------------- settings
function initSettingsForm() {
  $("btn-add-dir").addEventListener("click", () => {
    const input = $("input-new-dir");
    const v = input.value.trim();
    if (v && !configDirs.includes(v)) { configDirs.push(v); renderTagList("dir-list", configDirs, removeDir); input.value = ""; }
  });
  $("btn-browse-dir").addEventListener("click", async () => {
    try {
      const res = await postJSON("/api/v1/utils/browse-folder");
      if (res && res.status === "success" && res.path) {
        $("input-new-dir").value = res.path;
        if (!configDirs.includes(res.path)) {
          configDirs.push(res.path);
          renderTagList("dir-list", configDirs, removeDir);
        }
      }
    } catch (e) { console.error("Browse dir error", e); }
  });

  $("btn-add-repo").addEventListener("click", () => {
    const input = $("input-new-repo");
    const v = input.value.trim();
    if (v && !configRepos.includes(v)) { configRepos.push(v); renderTagList("repo-list", configRepos, removeRepo); input.value = ""; }
  });
  $("btn-browse-repo").addEventListener("click", async () => {
    try {
      const res = await postJSON("/api/v1/utils/browse-folder");
      if (res && res.status === "success" && res.path) {
        $("input-new-repo").value = res.path;
        if (!configRepos.includes(res.path)) {
          configRepos.push(res.path);
          renderTagList("repo-list", configRepos, removeRepo);
        }
      }
    } catch (e) { console.error("Browse repo error", e); }
  });

  $("btn-save-settings").addEventListener("click", saveSettings);
  $("btn-recheck-llm-key").addEventListener("click", loadLLMStatus);
  $("select-llm-provider").addEventListener("change", (e) => {
    const p = e.target.value;
    const defaults = { gemini: "gemini-3.7-flash", anthropic: "claude-3-5-sonnet-20241022", openai: "gpt-4o", ollama: "llama3.1:8b" };
    const envDefaults = { gemini: "GEMINI_API_KEY", anthropic: "ANTHROPIC_API_KEY", openai: "OPENAI_API_KEY", ollama: "" };
    $("input-model-name").value = (currentConfig && currentConfig.synthesizer && currentConfig.synthesizer[p] && currentConfig.synthesizer[p].model) || defaults[p] || "";
    $("input-llm-key-env").value = (currentConfig && currentConfig.synthesizer && currentConfig.synthesizer[p] && currentConfig.synthesizer[p].api_key_env) || envDefaults[p] || "";
    $("input-llm-key-env").disabled = p === "ollama";
    renderLLMStatus();
  });
}

function renderTagList(id, list, onRemove) {
  const box = $(id);
  if (!list.length) { box.innerHTML = '<div class="muted small">尚未設定</div>'; return; }
  box.innerHTML = list.map((v, i) => `<div class="tag"><span>${esc(v)}</span><span class="tag-x" data-i="${i}">✕</span></div>`).join("");
  box.querySelectorAll(".tag-x").forEach(x => x.addEventListener("click", () => onRemove(Number(x.dataset.i))));
}
function removeDir(i) { configDirs.splice(i, 1); renderTagList("dir-list", configDirs, removeDir); }
function removeRepo(i) { configRepos.splice(i, 1); renderTagList("repo-list", configRepos, removeRepo); }

async function loadConfig() {
  try {
    currentConfig = await getJSON("/api/v1/config");
    const w = currentConfig.watchers || {};
    const s = currentConfig.synthesizer || {};
    const usage = currentConfig.usage_tracking || {};

    configDirs = (w.file_watcher && w.file_watcher.watch_directories) || [];
    configRepos = (w.git_watcher && w.git_watcher.repositories) || [];
    renderTagList("dir-list", configDirs, removeDir);
    renderTagList("repo-list", configRepos, removeRepo);

    $("input-schedule-time").value = (s.schedule && s.schedule.time) || "23:30";
    $("input-checkpoint-interval").value = (s.periodic_checkpoint && s.periodic_checkpoint.interval_hours) || 2;
    const provider = s.provider || "gemini";
    $("select-llm-provider").value = provider;
    $("input-model-name").value = (s[provider] && s[provider].model) || "gemini-3.7-flash";
    const envDefaults = { gemini: "GEMINI_API_KEY", anthropic: "ANTHROPIC_API_KEY", openai: "OPENAI_API_KEY", ollama: "" };
    $("input-llm-key-env").value = (s[provider] && s[provider].api_key_env) || envDefaults[provider] || "";
    $("input-llm-key-env").disabled = provider === "ollama";

    const exts = (w.file_watcher && w.file_watcher.extensions) || [];
    document.querySelectorAll("#ext-checkboxes input").forEach(cb => { cb.checked = exts.includes(cb.value); });

    const agent = w.agent_log_watcher || {};
    const browser = w.browser || {};
    $("toggle-claude-code").checked = agent.claude_code !== false;
    $("toggle-claude-desktop").checked = agent.claude_desktop !== false;
    $("toggle-codex").checked = agent.codex !== false;
    $("toggle-antigravity").checked = agent.antigravity !== false;
    $("toggle-gemini").checked = browser.gemini !== false;
    $("toggle-chatgpt").checked = browser.chatgpt !== false;
    $("toggle-claude-web").checked = browser.claude_web !== false;
    $("toggle-window-focus").checked = !(w.window_watcher && w.window_watcher.enabled === false);
    $("toggle-usage-tracking").checked = usage.enabled === true;
    const usageNotifications = usage.notifications || {};
    $("toggle-usage-notifications").checked = usageNotifications.enabled === true;
    $("input-usage-daily-goal").value = usage.daily_goal_minutes || 360;
    $("input-usage-milestones").value = (usage.milestones_minutes || [120, 240, 360]).join(", ");
    $("select-usage-tone").value = usageNotifications.tone || "encouraging";
    $("input-usage-quiet-start").value = usageNotifications.quiet_hours_start || "22:00";
    $("input-usage-quiet-end").value = usageNotifications.quiet_hours_end || "08:00";
    $("input-usage-cooldown").value = usageNotifications.cooldown_minutes ?? 60;
    await loadLLMStatus();
  } catch (e) { console.error("config load failed", e); }
}

async function loadLLMStatus() {
  const badge = $("llm-key-status-badge");
  badge.className = "trust noisy";
  badge.textContent = "CHECKING";
  try {
    llmStatusCache = await getJSON("/api/v1/llm/status");
    renderLLMStatus();
  } catch (e) {
    llmStatusCache = null;
    badge.className = "trust broken";
    badge.textContent = "UNAVAILABLE";
    $("llm-key-status-text").textContent = currentLang === "zh-TW"
      ? "目前無法取得 API key 偵測狀態。"
      : "API key detection status is unavailable.";
  }
}

function renderLLMStatus() {
  if (!llmStatusCache) return;
  const provider = $("select-llm-provider").value;
  const item = (llmStatusCache.providers || {})[provider] || {};
  const badge = $("llm-key-status-badge");
  const sourceLabels = currentLang === "zh-TW" ? {
    process: "目前執行程序環境",
    windows_user: "Windows 使用者環境變數",
    windows_machine: "Windows 系統環境變數",
    local_service: "本機 Ollama",
    missing: "未偵測"
  } : {
    process: "current process environment",
    windows_user: "Windows user environment",
    windows_machine: "Windows machine environment",
    local_service: "local Ollama",
    missing: "not detected"
  };
  badge.className = "trust " + (item.configured ? "ok" : "broken");
  badge.textContent = provider === "ollama" ? "LOCAL" : item.configured ? "DETECTED" : "MISSING";
  const envName = item.env_var || $("input-llm-key-env").value.trim() || "—";
  const source = sourceLabels[item.source] || item.source || sourceLabels.missing;
  $("llm-key-status-text").textContent = item.configured
    ? (currentLang === "zh-TW"
      ? `已偵測 ${envName}，來源：${source}。金鑰內容不會傳到瀏覽器。`
      : `${envName} detected from the ${source}. The secret value is not sent to the browser.`)
    : (currentLang === "zh-TW"
      ? `尚未偵測 ${envName}。請在作業系統使用者環境變數設定後按「重新檢查」。`
      : `${envName} was not detected. Set it in the OS user environment, then select Recheck.`);
}

async function saveSettings() {
  if (!currentConfig) currentConfig = {};
  const cfg = currentConfig;
  const exts = Array.from(document.querySelectorAll("#ext-checkboxes input:checked")).map(cb => cb.value);
  const provider = $("select-llm-provider").value;

  cfg.watchers = cfg.watchers || {};
  cfg.watchers.file_watcher = cfg.watchers.file_watcher || { enabled: true };
  cfg.watchers.file_watcher.watch_directories = configDirs;
  cfg.watchers.file_watcher.extensions = exts;

  cfg.watchers.git_watcher = cfg.watchers.git_watcher || { enabled: true };
  cfg.watchers.git_watcher.repositories = configRepos;

  cfg.watchers.agent_log_watcher = cfg.watchers.agent_log_watcher || { enabled: true };
  cfg.watchers.agent_log_watcher.claude_code = $("toggle-claude-code").checked;
  cfg.watchers.agent_log_watcher.claude_desktop = $("toggle-claude-desktop").checked;
  cfg.watchers.agent_log_watcher.codex = $("toggle-codex").checked;
  cfg.watchers.agent_log_watcher.antigravity = $("toggle-antigravity").checked;

  cfg.watchers.browser = cfg.watchers.browser || {};
  cfg.watchers.browser.gemini = $("toggle-gemini").checked;
  cfg.watchers.browser.chatgpt = $("toggle-chatgpt").checked;
  cfg.watchers.browser.claude_web = $("toggle-claude-web").checked;

  cfg.watchers.window_watcher = cfg.watchers.window_watcher || { enabled: true };
  cfg.watchers.window_watcher.enabled = $("toggle-window-focus").checked;

  cfg.synthesizer = cfg.synthesizer || {};
  cfg.synthesizer.schedule = cfg.synthesizer.schedule || { enabled: true };
  cfg.synthesizer.schedule.time = $("input-schedule-time").value.trim();
  cfg.synthesizer.periodic_checkpoint = cfg.synthesizer.periodic_checkpoint || { enabled: true };
  cfg.synthesizer.periodic_checkpoint.interval_hours = parseInt($("input-checkpoint-interval").value, 10) || 2;
  cfg.synthesizer.provider = provider;
  cfg.synthesizer[provider] = cfg.synthesizer[provider] || {};
  cfg.synthesizer[provider].model = $("input-model-name").value.trim();
  if (provider !== "ollama") {
    const envName = $("input-llm-key-env").value.trim();
    if (/^[A-Za-z_][A-Za-z0-9_]*$/.test(envName)) {
      cfg.synthesizer[provider].api_key_env = envName;
    }
  }

  cfg.usage_tracking = cfg.usage_tracking || {};
  cfg.usage_tracking.enabled = $("toggle-usage-tracking").checked;
  cfg.usage_tracking.daily_goal_minutes = Math.max(15, parseInt($("input-usage-daily-goal").value, 10) || 360);
  const milestones = $("input-usage-milestones").value
    .split(",")
    .map(value => parseInt(value.trim(), 10))
    .filter(value => Number.isInteger(value) && value > 0);
  cfg.usage_tracking.milestones_minutes = [...new Set(milestones)].sort((a, b) => a - b);
  cfg.usage_tracking.notifications = cfg.usage_tracking.notifications || {};
  cfg.usage_tracking.notifications.enabled = $("toggle-usage-notifications").checked;
  cfg.usage_tracking.notifications.tone = $("select-usage-tone").value;
  cfg.usage_tracking.notifications.quiet_hours_start = $("input-usage-quiet-start").value || "22:00";
  cfg.usage_tracking.notifications.quiet_hours_end = $("input-usage-quiet-end").value || "08:00";
  cfg.usage_tracking.notifications.cooldown_minutes = Math.max(0, parseInt($("input-usage-cooldown").value, 10) || 0);

  const btn = $("btn-save-settings");
  const label = btn.textContent;
  btn.disabled = true; btn.textContent = "儲存中…";
  try {
    await postJSON("/api/v1/config", cfg);
    btn.textContent = "✓ 已套用";
    refreshStatus();
    loadLLMStatus();
  } catch (e) {
    btn.textContent = "儲存失敗";
  } finally {
    setTimeout(() => { btn.disabled = false; btn.textContent = label; }, 1600);
  }
}

// ---------------------------------------------------------------- github
function initGitHubSection() {
  $("github-pill").addEventListener("click", () => {
    document.querySelectorAll(".tab").forEach(t => t.classList.remove("active"));
    document.querySelectorAll(".pane").forEach(p => p.classList.remove("active"));
    const tabBtn = document.querySelector('.tab[data-tab="tab-settings"]');
    if (tabBtn) tabBtn.classList.add("active");
    $("tab-settings").classList.add("active");
    loadConfig();
    const el = $("btn-gh-auto-connect");
    if (el) window.scrollTo({ top: el.getBoundingClientRect().top + window.scrollY - 160, behavior: "smooth" });
  });

  $("btn-gh-auto-connect").addEventListener("click", async () => {
    const btn = $("btn-gh-auto-connect");
    const label = btn.textContent;
    btn.disabled = true; btn.textContent = "偵測認證中…";
    try {
      const res = await postJSON("/api/v1/github/connect", { method: "gh_cli" });
      $("gh-sync-result").textContent = `✓ 已連線 @${res.auth.username}，已同步 ${res.sync.synced_repos_count} 個專案與 ${res.sync.synced_prs_count} 筆 PR！`;
      loadGitHubStatus();
      loadProjects(true);
    } catch (e) {
      $("gh-sync-result").textContent = `連線失敗：${e.message}`;
    } finally {
      setTimeout(() => { btn.disabled = false; btn.textContent = label; }, 1500);
    }
  });

  $("btn-gh-token-connect").addEventListener("click", async () => {
    const token = $("input-gh-token").value.trim();
    if (!token) return;
    const btn = $("btn-gh-token-connect");
    const label = btn.textContent;
    btn.disabled = true; btn.textContent = "驗證中…";
    try {
      const res = await postJSON("/api/v1/github/connect", { method: "token", token });
      $("gh-sync-result").textContent = `✓ 已連線 @${res.auth.username}，已同步 ${res.sync.synced_repos_count} 個專案與 ${res.sync.synced_prs_count} 筆 PR！`;
      $("input-gh-token").value = "";
      loadGitHubStatus();
      loadProjects(true);
    } catch (e) {
      $("gh-sync-result").textContent = `Token 驗證失敗：${e.message}`;
    } finally {
      setTimeout(() => { btn.disabled = false; btn.textContent = label; }, 1500);
    }
  });

  $("btn-gh-sync").addEventListener("click", async () => {
    const btn = $("btn-gh-sync");
    const label = btn.textContent;
    btn.disabled = true; btn.textContent = "⏳ 同步中…";
    try {
      const res = await postJSON("/api/v1/github/sync");
      $("gh-sync-result").textContent = `✓ 同步完成！已更新 ${res.synced_repos_count} 個倉庫與 ${res.synced_prs_count} 筆 PR 狀態。`;
      loadProjects(true);
    } catch (e) {
      $("gh-sync-result").textContent = `同步失敗：${e.message}`;
    } finally {
      setTimeout(() => { btn.disabled = false; btn.textContent = label; }, 1500);
    }
  });

  $("btn-gh-disconnect").addEventListener("click", async () => {
    try {
      await postJSON("/api/v1/github/disconnect");
      $("gh-sync-result").textContent = "已解除 GitHub 連線。";
      loadGitHubStatus();
      loadProjects(true);
    } catch (e) { console.error(e); }
  });
}

async function loadGitHubStatus() {
  try {
    const data = await getJSON("/api/v1/github/status");
    githubStatus = data;
    const pill = $("github-pill");
    const pillText = $("github-status-text");
    const badge = $("gh-auth-badge");
    const info = $("gh-account-info");

    if (data.connected) {
      pill.className = "pill pill-on";
      pillText.textContent = `🐙 @${data.username} (${data.public_repos + data.total_private_repos} Repos)`;
      badge.className = "trust ok";
      badge.textContent = "CONNECTED";

      const scopesStr = (data.scopes || []).join(", ") || "基本讀取";
      const limitStr = data.rate_limit ? `API 額度: ${data.rate_limit.remaining}/${data.rate_limit.limit}` : "";

      info.innerHTML = `
        <div style="display: flex; align-items: center; gap: 10px; margin-bottom: 8px;">
          <img src="${esc(data.avatar_url)}" style="width: 28px; height: 28px; border-radius: 50%; border: 1px solid var(--bd);">
          <div>
            <strong><a href="${esc(data.html_url)}" target="_blank" style="color: var(--orange); text-decoration: none;">@${esc(data.username)}</a></strong>
            <span style="color: var(--mu); font-size: 11.5px;">(${esc(data.name || "")}) · 擁有 ${data.public_repos} 公開 / ${data.total_private_repos} 私有倉庫</span>
          </div>
        </div>
        <div class="mono-mini muted" style="font-size: 10.5px;">
          權限範圍: <code>${esc(scopesStr)}</code> · ${esc(limitStr)}
        </div>`;
    } else {
      pill.className = "pill pill-off";
      pillText.textContent = "🐙 GitHub: 未連線";
      badge.className = "trust broken";
      badge.textContent = "未連線";
      info.textContent = data.message || "尚未啟用 GitHub 認證。連線後可自動讀取所有 Public / Private 倉庫、PR 進度與 CI 狀態。";
    }
  } catch (e) {
    $("github-status-text").textContent = "🐙 GitHub: 離線";
  }
}


// ---------------------------------------------------------------- summaries
function initSummariesTab() {
  const todayStr = iso(new Date());
  if ($("input-summary-start-date")) $("input-summary-start-date").value = todayStr;
  if ($("input-summary-end-date")) $("input-summary-end-date").value = todayStr;

  document.querySelectorAll(".quick-ranges .chip").forEach(chip => {
    chip.addEventListener("click", () => {
      document.querySelectorAll(".quick-ranges .chip").forEach(c => c.classList.remove("active"));
      chip.classList.add("active");
      const r = chip.dataset.range;
      const now = new Date();
      if (r === "today") {
        $("input-summary-start-date").value = iso(now);
        $("input-summary-end-date").value = iso(now);
      } else if (r === "yesterday") {
        const y = new Date(now); y.setDate(y.getDate() - 1);
        $("input-summary-start-date").value = iso(y);
        $("input-summary-end-date").value = iso(y);
      } else if (r === "this_week") {
        const mon = new Date(now); mon.setDate(now.getDate() - ((now.getDay() + 6) % 7));
        $("input-summary-start-date").value = iso(mon);
        $("input-summary-end-date").value = iso(now);
      } else if (r === "7d") {
        const past = new Date(now); past.setDate(now.getDate() - 6);
        $("input-summary-start-date").value = iso(past);
        $("input-summary-end-date").value = iso(now);
      } else if (r === "30d") {
        const past = new Date(now); past.setDate(now.getDate() - 29);
        $("input-summary-start-date").value = iso(past);
        $("input-summary-end-date").value = iso(now);
      }
    });
  });

  $("btn-generate-custom-summary").addEventListener("click", () => {
    const start = $("input-summary-start-date") ? $("input-summary-start-date").value : null;
    const end = $("input-summary-end-date") ? $("input-summary-end-date").value : null;
    generateSummary(start, end);
  });

  $("btn-copy-markdown").addEventListener("click", () => {
    if (currentSummaryMarkdown) navigator.clipboard.writeText(currentSummaryMarkdown);
  });

  document.querySelectorAll(".viewswitch .chip").forEach(btn => {
    btn.addEventListener("click", () => {
      document.querySelectorAll(".viewswitch .chip").forEach(b => b.classList.remove("active"));
      btn.classList.add("active");
      summaryView = btn.dataset.view;
      paintSummaryView();
    });
  });
}

async function loadSummaries() {
  try {
    summariesCache = await getJSON("/api/v1/summaries?limit=60");
    const box = $("summary-history-list");
    if (!summariesCache.length) {
      box.innerHTML = '<div class="placeholder">尚未產生歷史摘要。</div>';
    } else {
      box.innerHTML = summariesCache.map((s, i) => `
        <div class="sideitem ${i === 0 ? "active" : ""}" data-date="${esc(s.date_str)}">
          <div class="sideitem-title">${esc(s.date_str)}</div>
          <div class="sideitem-sub">${esc((s.llm_provider || "").toUpperCase())} · ${esc((s.created_at || "").split(" ")[1] || "")}</div>
        </div>`).join("");
      box.querySelectorAll(".sideitem").forEach(el => {
        el.addEventListener("click", () => selectSummary(el.dataset.date));
      });
      showSummary(summariesCache[0]);
    }
    paintSummaryView();
  } catch (e) {
    $("summary-history-list").innerHTML = '<div class="placeholder">無法讀取歷史報告。</div>';
  }
}

async function selectSummary(dateStr) {
  document.querySelectorAll("#summary-history-list .sideitem").forEach(el => {
    el.classList.toggle("active", el.dataset.date === dateStr);
  });
  try {
    const data = await getJSON(`/api/v1/summaries/${encodeURIComponent(dateStr)}`);
    summaryView = "day";
    document.querySelectorAll(".viewswitch .chip").forEach(b => b.classList.toggle("active", b.dataset.view === "day"));
    showSummary(data);
    paintSummaryView();
  } catch (e) { console.error(e); }
}

function showSummary(s) {
  currentSummaryMarkdown = s.raw_markdown || s.markdown || "";
  $("summary-meta").textContent = `${s.date_str} · ${(s.llm_provider || "").toUpperCase()}${s.model_name ? " / " + s.model_name : ""}`;
  const view = $("summary-view-day");
  view.innerHTML = window.marked
    ? marked.parse(currentSummaryMarkdown)
    : `<pre>${esc(currentSummaryMarkdown)}</pre>`;
}

async function generateSummary(startDate, endDate) {
  const btn = $("btn-generate-custom-summary") || $("btn-quick-summary");
  const topBtn = $("btn-quick-summary");
  const label = btn.textContent;
  btn.disabled = true; btn.textContent = "⏳ AI 分析中…";
  if (topBtn) { topBtn.disabled = true; topBtn.textContent = "⏳ 分析中…"; }
  try {
    const payload = { force_refresh: true };
    if (startDate && endDate) {
      payload.start_date = startDate;
      payload.end_date = endDate;
    } else if (startDate) {
      payload.target_date = startDate;
    } else {
      payload.target_date = iso(new Date());
    }

    const data = await postJSON("/api/v1/summaries/generate", payload);
    showSummary({ date_str: data.date_str, raw_markdown: data.markdown || data.raw_markdown, llm_provider: data.llm_provider || "", model_name: data.model_name || "" });
    loadSummaries();
    loadOpenLoops();
  } catch (e) {
    btn.textContent = "生成失敗";
  } finally {
    setTimeout(() => {
      btn.disabled = false; btn.textContent = label;
      if (topBtn) { topBtn.disabled = false; topBtn.textContent = "⚡ 生成今日摘要"; }
    }, 1400);
  }
}


// 週／月檢視：以「哪幾天有 AI 回顧報告」為軸，資料全部來自 /api/v1/summaries
function paintSummaryView() {
  const day = $("summary-view-day"), week = $("summary-view-week"), month = $("summary-view-month");
  day.hidden = summaryView !== "day";
  week.hidden = summaryView !== "week";
  month.hidden = summaryView !== "month";
  if (summaryView === "week") renderWeekView();
  if (summaryView === "month") renderMonthView();
}

const dayNames = ["日", "一", "二", "三", "四", "五", "六"];
const iso = (d) => `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}-${String(d.getDate()).padStart(2, "0")}`;

function renderWeekView() {
  const today = new Date();
  const monday = new Date(today);
  monday.setDate(today.getDate() - ((today.getDay() + 6) % 7));
  const have = new Set(summariesCache.map(s => s.date_str));

  let cells = "", covered = 0;
  for (let i = 0; i < 7; i++) {
    const d = new Date(monday); d.setDate(monday.getDate() + i);
    const key = iso(d);
    const has = have.has(key);
    if (has) covered++;
    cells += `
      <div class="weekcell ${has ? "has" : ""} ${key === iso(today) ? "today" : ""}" ${has ? `data-date="${key}"` : ""}>
        <div class="weekcell-day">${dayNames[d.getDay()]}</div>
        <div class="weekcell-date">${String(d.getDate()).padStart(2, "0")}</div>
        <div class="weekcell-state ${has ? "has" : "none"}">${has ? "有報告" : "無"}</div>
      </div>`;
  }

  const notes = summariesCache
    .filter(s => s.date_str >= iso(monday))
    .slice(0, 7)
    .map(s => `<div class="pl"><b>·</b><span><strong>${esc(s.date_str)}</strong> — ${esc(firstLine(s.raw_markdown))}</span></div>`)
    .join("") || '<div class="placeholder" style="padding:0">本週尚無報告。</div>';

  $("summary-view-week").innerHTML = `
    <h1>${iso(monday).slice(5)} – ${iso(new Date(monday.getTime() + 6 * 864e5)).slice(5)} 週檢視</h1>
    <div class="rule"></div>
    <div class="weekgrid">${cells}</div>
    <div class="rangestats">
      <div class="rangestat"><div class="mono-mini muted">REPORTS</div><div class="rangestat-value">${covered} / 7</div><div class="rangestat-sub">本週已產出</div></div>
      <div class="rangestat"><div class="mono-mini muted">OPEN LOOPS</div><div class="rangestat-value">${loopsCache.length}</div><div class="rangestat-sub">目前未結</div></div>
      <div class="rangestat"><div class="mono-mini muted">STREAMS</div><div class="rangestat-value">${projectsCache.length}</div><div class="rangestat-sub">進行中工作</div></div>
      <div class="rangestat"><div class="mono-mini muted">ACTIVE</div><div class="rangestat-value">${projectsCache.filter(p => p.status === "active").length}</div><div class="rangestat-sub">兩天內有活動</div></div>
    </div>
    <span class="mono-label">本週報告 / REPORTS</span>
    ${notes}`;

  $("summary-view-week").querySelectorAll("[data-date]").forEach(el => {
    el.addEventListener("click", () => selectSummary(el.dataset.date));
  });
}

function renderMonthView() {
  const today = new Date();
  const first = new Date(today.getFullYear(), today.getMonth(), 1);
  const days = new Date(today.getFullYear(), today.getMonth() + 1, 0).getDate();
  const lead = (first.getDay() + 6) % 7;
  const have = new Set(summariesCache.map(s => s.date_str));

  let cells = "";
  for (let i = 0; i < lead; i++) cells += '<div class="mcell blank"></div>';
  let covered = 0;
  for (let d = 1; d <= days; d++) {
    const key = iso(new Date(today.getFullYear(), today.getMonth(), d));
    const has = have.has(key);
    if (has) covered++;
    cells += `<div class="mcell ${has ? "has" : ""}" ${has ? `data-date="${key}"` : ""}>${String(d).padStart(2, "0")}${has ? '<span class="mcell-mark"></span>' : ""}</div>`;
  }

  const heads = ["一", "二", "三", "四", "五", "六", "日"]
    .map(h => `<div class="mono-mini muted" style="text-align:center">${h}</div>`).join("");

  const streams = projectsCache.length
    ? projectsCache.map(p => `
        <div class="defect" style="border-bottom:1px solid var(--bd)">
          <span>${esc(p.display_name)}</span>
          <span class="mono-mini muted">${esc(p.last_activity_at)} · 未結 ${p.open_loops_count}</span>
        </div>`).join("")
    : '<div class="placeholder">尚無進行中工作。</div>';

  $("summary-view-month").innerHTML = `
    <h1>${today.getFullYear()} 年 ${today.getMonth() + 1} 月 月檢視</h1>
    <div class="rule"></div>
    <div class="monthgrid" style="margin-bottom:6px">${heads}</div>
    <div class="monthgrid">${cells}</div>
    <div class="rangestats">
      <div class="rangestat"><div class="mono-mini muted">REPORTS</div><div class="rangestat-value">${covered} / ${days}</div><div class="rangestat-sub">本月已產出</div></div>
      <div class="rangestat"><div class="mono-mini muted">GAPS</div><div class="rangestat-value">${days - covered}</div><div class="rangestat-sub">未產出天數</div></div>
      <div class="rangestat"><div class="mono-mini muted">OPEN LOOPS</div><div class="rangestat-value">${loopsCache.length}</div><div class="rangestat-sub">目前未結</div></div>
      <div class="rangestat"><div class="mono-mini muted">STREAMS</div><div class="rangestat-value">${projectsCache.length}</div><div class="rangestat-sub">進行中工作</div></div>
    </div>
    <span class="mono-label">工作重心 / STREAMS</span>
    <div class="panel">${streams}</div>`;

  $("summary-view-month").querySelectorAll("[data-date]").forEach(el => {
    el.addEventListener("click", () => selectSummary(el.dataset.date));
  });
}

function firstLine(md) {
  if (!md) return "（無內容）";
  const line = md.split("\n").map(l => l.replace(/^[#>*\-\s]+/, "").trim()).find(l => l.length > 4);
  return (line || "").slice(0, 80);
}

// ---------------------------------------------------------------- checkpoints
function initCheckpointsTab() {
  $("btn-copy-cp").addEventListener("click", () => {
    if (currentCheckpointMarkdown) navigator.clipboard.writeText(currentCheckpointMarkdown);
  });
}

async function loadCheckpoints() {
  try {
    const list = await getJSON("/api/v1/logs/checkpoints");
    const box = $("checkpoint-history-list");
    if (!list.length) {
      box.innerHTML = '<div class="placeholder">目前尚無快照日誌。</div>';
      return;
    }
    box.innerHTML = list.map((c, i) => `
      <div class="sideitem ${i === 0 ? "active" : ""}" data-file="${esc(c.file_name)}">
        <div class="sideitem-title">${esc(c.file_name.replace(/^checkpoint_/, "").replace(/\.md$/, ""))}</div>
        <div class="sideitem-sub">${esc(c.created_at || "")} · ${(c.size_bytes / 1024).toFixed(1)} KB</div>
      </div>`).join("");
    box.querySelectorAll(".sideitem").forEach(el => {
      el.addEventListener("click", () => selectCheckpoint(el.dataset.file));
    });
    selectCheckpoint(list[0].file_name);
  } catch (e) {
    $("checkpoint-history-list").innerHTML = '<div class="placeholder">無法讀取快照日誌。</div>';
  }
}

async function selectCheckpoint(fileName) {
  document.querySelectorAll("#checkpoint-history-list .sideitem").forEach(el => {
    el.classList.toggle("active", el.dataset.file === fileName);
  });
  try {
    const data = await getJSON(`/api/v1/logs/checkpoints/${encodeURIComponent(fileName)}`);
    currentCheckpointMarkdown = data.content || "";
    $("cp-title").textContent = fileName;
    $("checkpoint-markdown-viewer").innerHTML = window.marked
      ? marked.parse(currentCheckpointMarkdown)
      : `<pre>${esc(currentCheckpointMarkdown)}</pre>`;
  } catch (e) { console.error(e); }
}

async function triggerCheckpoint() {
  const btn = $("btn-quick-checkpoint");
  const label = btn.textContent;
  btn.disabled = true; btn.textContent = "⏳ 產出中…";
  try {
    const hours = parseInt($("input-checkpoint-interval").value, 10) || 2;
    await postJSON("/api/v1/logs/checkpoints/generate", { hours });
    loadCheckpoints();
  } catch (e) {
    btn.textContent = "產出失敗";
  } finally {
    setTimeout(() => { btn.disabled = false; btn.textContent = label; }, 1400);
  }
}
