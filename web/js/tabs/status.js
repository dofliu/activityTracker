// web/js/tabs/status.js — 頂部狀態列、使用量面板與採集器卡片。

import { getJSON, postJSON } from "../core/api.js";
import { $, esc } from "../core/dom.js";
import { t } from "../core/i18n.js";
import { state } from "../core/state.js";
import { createSchedulePresets, loadSecretaryProposals, searchRelatedContext } from "../tabs/assistant.js";
import { loadAcceptance } from "../tabs/health.js";
import { renderBackgroundTaskPanel, renderBackgroundTaskPanelError } from "../tabs/knowledge.js";
import { loadProjects, refreshFeed } from "../tabs/projects.js";
import { generateSummary, triggerCheckpoint } from "../tabs/summaries.js";

export function initControls() {
  $("btn-toggle-monitor").addEventListener("click", async () => {
    try {
      await postJSON(state.isMonitoring ? "/api/v1/control/stop" : "/api/v1/control/start");
      refreshStatus();
    } catch (e) { console.error(e); }
  });

  $("btn-quick-checkpoint").addEventListener("click", triggerCheckpoint);
  $("btn-trigger-cp-now").addEventListener("click", triggerCheckpoint);
  $("btn-quick-summary").addEventListener("click", () => generateSummary(null));
  $("btn-refresh-projects").addEventListener("click", () => loadProjects(true));
  $("btn-refresh-usage").addEventListener("click", loadUsagePanels);
  const acceptanceBtn = $("btn-refresh-acceptance");
  if (acceptanceBtn) acceptanceBtn.addEventListener("click", loadAcceptance);
  const presetBtn = $("btn-create-presets");
  if (presetBtn) presetBtn.addEventListener("click", createSchedulePresets);
  $("btn-refresh-proposals").addEventListener("click", loadSecretaryProposals);
  $("btn-related-search").addEventListener("click", searchRelatedContext);
  $("input-related-question").addEventListener("keydown", event => {
    if (event.key === "Enter") searchRelatedContext();
  });

  document.querySelectorAll(".filters .chip").forEach(btn => {
    btn.addEventListener("click", () => {
      document.querySelectorAll(".filters .chip").forEach(b => b.classList.remove("active"));
      btn.classList.add("active");
      state.activeFilter = btn.dataset.filter;
      refreshFeed();
    });
  });
}

export async function refreshStatus() {
  try {
    const data = await getJSON("/api/v1/control/status");
    state.isMonitoring = data.is_running;
    const monitoringState = data.monitoring_state || (state.isMonitoring ? "healthy" : "stopped");

    const pill = $("status-pill");
    pill.className = "pill " + (monitoringState === "degraded" ? "pill-warn" : state.isMonitoring ? "pill-on" : "pill-off");
    $("status-text").textContent = monitoringState === "degraded"
      ? t("status_degraded")
      : state.isMonitoring ? t("status_monitoring") : t("status_paused");
    $("btn-toggle-monitor").textContent = state.isMonitoring ? t("btn_pause_monitor") : t("btn_start_monitor");

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

export function renderRuntimeTrust(monitoringState, degradedCollectors) {
  const badge = $("data-trust-runtime-badge");
  if (!badge) return;
  const degraded = monitoringState === "degraded";
  const healthy = monitoringState === "healthy";
  badge.className = `mono-mini runtime-trust-badge ${healthy ? "runtime-ok" : degraded ? "runtime-degraded" : "runtime-stopped"}`;
  if (healthy) {
    badge.textContent = "8/8 CONTRACT · RUNTIME OK ▾";
  } else if (degraded) {
    badge.textContent = `8/8 CONTRACT · ${degradedCollectors.length} DEGRADED ▾`;
  } else {
    badge.textContent = `8/8 CONTRACT · ${monitoringState === "stopped" ? "STOPPED" : "DISCONNECTED"} ▾`;
  }
}

export function formatUsageDuration(seconds) {
  const totalMinutes = Math.max(0, Math.round(Number(seconds || 0) / 60));
  const hours = Math.floor(totalMinutes / 60);
  const minutes = totalMinutes % 60;
  if (!hours) return `${minutes}m`;
  return minutes ? `${hours}h ${minutes}m` : `${hours}h`;
}

export async function loadUsagePanels() {
  const [usageResult, extensionResult, captureResult, backgroundTaskResult] = await Promise.allSettled([
    getJSON("/api/v1/usage/today"),
    getJSON("/api/v1/extension/status"),
    getJSON("/api/v1/capture/status"),
    getJSON("/api/v1/background-tasks/today")
  ]);
  if (usageResult.status === "fulfilled") renderUsagePanel(usageResult.value);
  else renderUsagePanelError();
  if (captureResult.status === "fulfilled") renderCaptureCoverage(
    captureResult.value,
    extensionResult.status === "fulfilled" ? extensionResult.value : null
  );
  else renderCaptureCoverageError();
  if (backgroundTaskResult.status === "fulfilled") renderBackgroundTaskPanel(backgroundTaskResult.value);
  else renderBackgroundTaskPanelError();
}

export function renderUsagePanel(data) {
  const goal = data.goal || {};
  const coverage = data.coverage_status || "unavailable";
  const coverageBadge = $("usage-coverage");
  coverageBadge.className = "trust " + (coverage === "complete" ? "ok" : coverage === "partial" ? "noisy" : "broken");
  coverageBadge.textContent = coverage.toUpperCase();

  $("usage-goal-value").textContent = formatUsageDuration(goal.foreground_seconds || 0);
  const progress = Number(goal.progress_percent || 0);
  const goalText = state.currentLang === "zh-TW"
    ? `${goal.label || "AI 協作"}：${Number(goal.foreground_minutes || 0).toFixed(1)} / ${goal.daily_goal_minutes || 0} 分鐘（${progress.toFixed(1)}%）`
    : `${goal.label || "AI collaboration"}: ${Number(goal.foreground_minutes || 0).toFixed(1)} / ${goal.daily_goal_minutes || 0} min (${progress.toFixed(1)}%)`;
  $("usage-goal-progress").textContent = goalText;
  $("usage-progress-bar").style.width = `${Math.min(100, Math.max(0, progress))}%`;
  const dataUpdated = data.data_updated_at ? new Date(data.data_updated_at).toLocaleString() : "—";
  $("usage-boundary").textContent = state.currentLang === "zh-TW"
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
    </div>`).join("") : `<div class="placeholder">${state.currentLang === "zh-TW" ? "今日尚無已觀察到的介面使用資料。" : "No observed interface usage today."}</div>`;
}

export function renderUsagePanelError() {
  $("usage-coverage").className = "trust broken";
  $("usage-coverage").textContent = "UNAVAILABLE";
  $("usage-interface-list").innerHTML = `<div class="placeholder">${state.currentLang === "zh-TW" ? "無法載入使用時間。" : "Unable to load usage data."}</div>`;
}

// 參數原本叫 `state`，把 `core/state.js` 匯入的共享 `state` 遮蔽掉了：`state.currentLang`
// 讀的是一個字串（"observed" 之類）的 `.currentLang`，永遠 undefined，所以中文介面一直拿到
// 英文標籤。同一個檔案裡的 `renderCaptureCoverage` 用的就是共享的 `state.currentLang`，可見
// 這裡是不小心遮到，不是刻意。
export function captureStateLabel(captureState) {
  const labels = state.currentLang === "zh-TW" ? {
    observed: "已觀察", waiting: "等待資料", available_waiting: "可讀取／待掃描",
    cache_detected_unparsed: "快取存在／未解析", unsupported: "目前不支援", not_applicable: "不適用"
  } : {
    observed: "OBSERVED", waiting: "WAITING", available_waiting: "READY TO SCAN",
    cache_detected_unparsed: "CACHE / UNPARSED", unsupported: "UNSUPPORTED", not_applicable: "N/A"
  };
  return labels[captureState] || String(captureState || "UNKNOWN").toUpperCase();
}

export function renderCaptureCoverage(data, extensionData) {
  const platforms = data.platforms || [];
  const observed = platforms.reduce((total, item) => total + [item.desktop_focus, item.web_capture, item.transcript_capture].filter(channel => channel.state === "observed").length, 0);
  const badge = $("capture-coverage-badge");
  badge.className = "trust " + (observed > 0 ? "ok" : "noisy");
  badge.textContent = `${observed} OBSERVED`;
  const extension = (extensionData && extensionData.extension) || null;
  $("capture-extension-summary").textContent = extension
    ? (state.currentLang === "zh-TW"
      ? `Browser Extension：今日 ${Number(extension.events_today || 0)} events · ${extension.heartbeat_verified ? "近期已連線" : "尚無近期 heartbeat"}`
      : `Browser Extension: ${Number(extension.events_today || 0)} events today · ${extension.heartbeat_verified ? "recent heartbeat" : "no recent heartbeat"}`)
    : (state.currentLang === "zh-TW" ? "Browser Extension 狀態目前無法取得。" : "Browser Extension status is unavailable.");
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
  $("capture-coverage-boundary").textContent = state.currentLang === "zh-TW"
    ? "FOCUS 只記前景時間；WEB 為 Extension turns／responses；LOG 為本機 transcript turns／responses。三者不能互相替代。"
    : "FOCUS is foreground time; WEB is Extension turns/responses; LOG is local transcript turns/responses. These signals are independent.";
}

export function renderCaptureCoverageError() {
  $("capture-coverage-badge").className = "trust broken";
  $("capture-coverage-badge").textContent = "UNAVAILABLE";
  $("capture-extension-summary").textContent = state.currentLang === "zh-TW" ? "採集狀態暫時無法取得。" : "Capture status is unavailable.";
  $("capture-coverage-list").innerHTML = `<div class="placeholder">${state.currentLang === "zh-TW" ? "無法取得採集 coverage。" : "Capture coverage is unavailable."}</div>`;
}

export function renderStats(m) {
  const items = [
    { tag: "AI TURNS", value: m.ai_prompts_count, label: "Claude / Codex / Web", trust: "ok" },
    { tag: "FILES", value: m.file_events_count, label: state.currentLang === "zh-TW" ? "論文與檔案異動" : "Paper & File Events", trust: "ok" },
    { tag: "COMMITS", value: m.git_commits_count, label: state.currentLang === "zh-TW" ? "Git commits 提交" : "Git Commits", trust: "ok" },
    { tag: "FOCUS", value: m.window_events_count, label: state.currentLang === "zh-TW" ? "視窗焦點切換" : "Window Focus", trust: "ok" },
    { tag: "STREAMS", value: state.projectsCache.length, label: state.currentLang === "zh-TW" ? "進行中工作" : "Active Workstreams", trust: "ok" }
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

export function renderCollectors(w, lastEvents = {}, health = {}, diagnostics = {}) {
  const items = [
    { key: "file_watcher", name: t("collector_file"), on: w.file_watcher, last: lastEvents.file_watcher, h: health.file_watcher || "stale", d: diagnostics.file_watcher },
    { key: "git_watcher", name: t("collector_git"), on: w.git_watcher, last: lastEvents.git_watcher, h: health.git_watcher || "stale", d: diagnostics.git_watcher },
    { key: "window_watcher", name: t("collector_window"), on: w.window_watcher, last: lastEvents.window_watcher, h: health.window_watcher || "stale", d: diagnostics.window_watcher },
    { key: "agent_log_watcher", name: t("collector_agent"), on: w.agent_log_watcher, last: lastEvents.agent_log_watcher, h: health.agent_log_watcher || "stale", d: diagnostics.agent_log_watcher },
    { key: "calendar_watcher", name: t("collector_calendar"), on: w.calendar_watcher, last: lastEvents.calendar_watcher, h: health.calendar_watcher || "disabled", d: diagnostics.calendar_watcher },
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
    healthy: state.currentLang === "zh-TW" ? "運作中" : "Active",
    idle: state.currentLang === "zh-TW" ? "待命中" : "Idle",
    degraded: state.currentLang === "zh-TW" ? "部分採集異常" : "Degraded",
    stopped: state.currentLang === "zh-TW" ? "已停止" : "Stopped",
    stale: state.currentLang === "zh-TW" ? "無有效資料" : "Stale",
    disabled: state.currentLang === "zh-TW" ? "已停用" : "Disabled"
  };

  $("watchers-grid").innerHTML = items.map(it => {
    let lastTimeStr = "";
    if (it.last) {
      const timePart = it.last.includes(" ") ? it.last.split(" ")[1] : it.last;
      lastTimeStr = `<span class="mono-mini" style="font-size:10px; opacity:0.85; display:block; margin-top:2px; color:var(--text-dim);">${state.currentLang === "zh-TW" ? "最後寫入" : "Last"}: ${timePart}</span>`;
    } else if (it.on && it.key !== "scheduler") {
      lastTimeStr = `<span class="mono-mini" style="font-size:10px; opacity:0.85; display:block; margin-top:2px; color:var(--danger, #ef4444); font-weight:600;">${state.currentLang === "zh-TW" ? "尚無紀錄 (待排查)" : "No data (Pending)"}</span>`;
    }
    const dotColor = colorMap[it.h] || "var(--mu)";
    const statusText = labelMap[it.h] || (it.on ? t("collector_enabled") : t("collector_disabled"));
    let diagnosticText = "";
    if (it.key === "window_watcher" && it.d && ["unavailable", "error"].includes(it.d.state)) {
      diagnosticText = state.currentLang === "zh-TW"
        ? `前景 probe 不可用 · ${Number(it.d.unavailable_seconds || 0)}s`
        : `Foreground probe unavailable · ${Number(it.d.unavailable_seconds || 0)}s`;
    }
    if (it.key === "calendar_watcher" && it.d) {
      if (it.d.state === "unconfigured") {
        diagnosticText = state.currentLang === "zh-TW" ? "尚未設定 .ics 路徑（系統設定 → 採集來源）" : "No .ics paths configured (Settings → Sources)";
      } else if (Number(it.d.degraded_sources_count || 0) > 0) {
        const names = (it.d.degraded_sources || []).map(s => s.source_name).join(", ");
        diagnosticText = `${state.currentLang === "zh-TW" ? "來源錯誤" : "Source error"}: ${names}`;
      } else if (it.d.scan_count) {
        diagnosticText = state.currentLang === "zh-TW"
          ? `${it.d.last_scan_files} 個檔 · 視野內 ${it.d.last_scan_instances} 筆`
          : `${it.d.last_scan_files} file(s) · ${it.d.last_scan_instances} in horizon`;
      }
    }
    if (it.key === "agent_log_watcher" && it.d && it.d.sources) {
      const failed = Object.entries(it.d.sources).filter(([, value]) => value.state === "error").map(([key]) => key);
      if (failed.length) diagnosticText = `${state.currentLang === "zh-TW" ? "來源錯誤" : "Source error"}: ${failed.join(", ")}`;
      // 漂移（檔案在動、事件是零）比單一來源錯誤更該被看見：解析沒報錯，但什麼都沒採到。
      const drifted = ((it.d.drift || {}).platforms || []).map(p => p.platform);
      if (drifted.length) {
        const days = Number((it.d.drift || {}).window_days || 0);
        diagnosticText = state.currentLang === "zh-TW"
          ? `⚠️ 疑似格式漂移：${drifted.join(", ")}（檔案有更新，${days} 天零事件）`
          : `⚠️ Possible format drift: ${drifted.join(", ")} (files updated, zero events in ${days}d)`;
      }
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
