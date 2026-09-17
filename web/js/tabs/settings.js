// web/js/tabs/settings.js — 06 系統設定：設定表單、排程任務、Telegram 與 LINE。

import { getJSON, postJSON, request } from "../core/api.js";
import { $, esc } from "../core/dom.js";
import { API, state } from "../core/state.js";
import { registerActions } from "../core/ui.js";
import { refreshStatus } from "../tabs/status.js";

export function initSettingsForm() {
  registerActions({
    "run-scheduled-task": (d) => runScheduledTaskNow(Number(d.taskId)),
    "toggle-scheduled-task": (d) => toggleScheduledTask(Number(d.taskId), d.enabled === "true"),
    "delete-scheduled-task": (d) => deleteScheduledTask(Number(d.taskId)),
  });
  $("btn-add-dir").addEventListener("click", () => {
    const input = $("input-new-dir");
    const v = input.value.trim();
    if (v && !state.configDirs.includes(v)) { state.configDirs.push(v); renderTagList("dir-list", state.configDirs, removeDir); input.value = ""; }
  });
  $("btn-browse-dir").addEventListener("click", async () => {
    try {
      const res = await postJSON("/api/v1/utils/browse-folder");
      if (res && res.status === "success" && res.path) {
        $("input-new-dir").value = res.path;
        if (!state.configDirs.includes(res.path)) {
          state.configDirs.push(res.path);
          renderTagList("dir-list", state.configDirs, removeDir);
        }
      }
    } catch (e) { console.error("Browse dir error", e); }
  });

  $("btn-add-calendar-path").addEventListener("click", () => {
    const input = $("input-new-calendar-path");
    const v = input.value.trim();
    if (v && !state.configCalendarPaths.includes(v)) { state.configCalendarPaths.push(v); renderTagList("calendar-path-list", state.configCalendarPaths, removeCalendarPath); input.value = ""; }
  });
  $("btn-browse-calendar").addEventListener("click", async () => {
    try {
      const res = await postJSON("/api/v1/utils/browse-folder");
      if (res && res.status === "success" && res.path) {
        $("input-new-calendar-path").value = res.path;
        if (!state.configCalendarPaths.includes(res.path)) {
          state.configCalendarPaths.push(res.path);
          renderTagList("calendar-path-list", state.configCalendarPaths, removeCalendarPath);
        }
      }
    } catch (e) { console.error("Browse calendar error", e); }
  });

  $("btn-add-repo").addEventListener("click", () => {
    const input = $("input-new-repo");
    const v = input.value.trim();
    if (v && !state.configRepos.includes(v)) { state.configRepos.push(v); renderTagList("repo-list", state.configRepos, removeRepo); input.value = ""; }
  });
  $("btn-browse-repo").addEventListener("click", async () => {
    try {
      const res = await postJSON("/api/v1/utils/browse-folder");
      if (res && res.status === "success" && res.path) {
        $("input-new-repo").value = res.path;
        if (!state.configRepos.includes(res.path)) {
          state.configRepos.push(res.path);
          renderTagList("repo-list", state.configRepos, removeRepo);
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
    $("input-model-name").value = (state.currentConfig && state.currentConfig.synthesizer && state.currentConfig.synthesizer[p] && state.currentConfig.synthesizer[p].model) || defaults[p] || "";
    $("input-llm-key-env").value = (state.currentConfig && state.currentConfig.synthesizer && state.currentConfig.synthesizer[p] && state.currentConfig.synthesizer[p].api_key_env) || envDefaults[p] || "";
    $("input-llm-key-env").disabled = p === "ollama";
    renderLLMStatus();
  });

  // P5-R5 自訂排程任務：template／schedule kind 切換對應欄位，新增走 token API。
  $("select-sched-template").addEventListener("change", () => {
    $("sched-project-field").hidden = $("select-sched-template").value !== "generate_handoff";
  });
  $("select-sched-kind").addEventListener("change", () => {
    const kind = $("select-sched-kind").value;
    $("sched-weekday-field").hidden = kind !== "weekly";
    $("sched-day-field").hidden = kind !== "monthly";
  });
  $("btn-add-sched-task").addEventListener("click", addScheduledTask);

  // Telegram 設定流程（P5-R4b 前置）：偵測 chat id → 即時測試 → 通過才儲存。
  $("btn-tg-detect").addEventListener("click", tgDetectChat);
  $("btn-tg-test").addEventListener("click", tgTest);
  $("btn-tg-connect").addEventListener("click", tgConnect);
  $("btn-tg-disconnect").addEventListener("click", tgDisconnect);
  // P5-R4b inline 批准：解鎖／上鎖批准通道（解鎖需 execution token）。
  $("btn-tg-arm").addEventListener("click", tgArm);
  $("btn-tg-arm-code").addEventListener("click", tgIssueArmCode);
  $("btn-line-test").addEventListener("click", () => lineTest(false));
  $("btn-line-connect").addEventListener("click", () => lineTest(true));
  $("btn-line-disconnect").addEventListener("click", lineDisconnect);
  $("btn-tg-disarm").addEventListener("click", tgDisarm);
}

export function renderTagList(id, list, onRemove) {
  const box = $(id);
  if (!list.length) { box.innerHTML = '<div class="muted small">尚未設定</div>'; return; }
  box.innerHTML = list.map((v, i) => `<div class="tag"><span>${esc(v)}</span><span class="tag-x" data-i="${i}">✕</span></div>`).join("");
  box.querySelectorAll(".tag-x").forEach(x => x.addEventListener("click", () => onRemove(Number(x.dataset.i))));
}
export function removeDir(i) { state.configDirs.splice(i, 1); renderTagList("dir-list", state.configDirs, removeDir); }
export function removeRepo(i) { state.configRepos.splice(i, 1); renderTagList("repo-list", state.configRepos, removeRepo); }
export function removeCalendarPath(i) { state.configCalendarPaths.splice(i, 1); renderTagList("calendar-path-list", state.configCalendarPaths, removeCalendarPath); }

export async function loadConfig() {
  try {
    state.currentConfig = await getJSON("/api/v1/config");
    const w = state.currentConfig.watchers || {};
    const s = state.currentConfig.synthesizer || {};
    const usage = state.currentConfig.usage_tracking || {};

    state.configDirs = (w.file_watcher && w.file_watcher.watch_directories) || [];
    state.configRepos = (w.git_watcher && w.git_watcher.repositories) || [];
    renderTagList("dir-list", state.configDirs, removeDir);
    renderTagList("repo-list", state.configRepos, removeRepo);

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
    const calendar = w.calendar_watcher || {};
    $("toggle-calendar").checked = calendar.enabled !== false;
    state.configCalendarPaths = Array.isArray(calendar.paths) ? calendar.paths.slice() : [];
    renderTagList("calendar-path-list", state.configCalendarPaths, removeCalendarPath);
    $("input-calendar-horizon").value = Number(calendar.horizon_days || 30);
    $("toggle-calendar-titles").checked = calendar.store_titles !== false;
    const executor = (state.currentConfig.proactive_secretary || {}).executor || {};
    $("toggle-executor-enabled").checked = executor.enabled === true;
    $("input-greeting-name").value = ((state.currentConfig.proactive_secretary || {}).greeting || {}).display_name || "";
    $("toggle-executor-l2").checked = !!(executor.l2 && executor.l2.enabled === true);
    $("toggle-executor-l2-write").checked = !!(executor.l2 && executor.l2.allow_write === true);
    $("select-agent-cli").value = (executor.agent_cli && executor.agent_cli.binary) === "codex" ? "codex" : "claude";
    $("toggle-tg-approvals").checked = !!(executor.telegram_approvals && executor.telegram_approvals.enabled === true);
    const tgChat = ((state.currentConfig.notifiers || {}).telegram || {}).chat || {};
    $("toggle-tg-chat").checked = tgChat.enabled === true;
    loadScheduledTasks();
    loadTelegramStatus();
    loadTelegramApprovalsStatus();
    loadLineStatus();
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

export async function loadLLMStatus() {
  const badge = $("llm-key-status-badge");
  badge.className = "trust noisy";
  badge.textContent = "CHECKING";
  try {
    state.llmStatusCache = await getJSON("/api/v1/llm/status");
    renderLLMStatus();
  } catch (e) {
    state.llmStatusCache = null;
    badge.className = "trust broken";
    badge.textContent = "UNAVAILABLE";
    $("llm-key-status-text").textContent = state.currentLang === "zh-TW"
      ? "目前無法取得 API key 偵測狀態。"
      : "API key detection status is unavailable.";
  }
}

export function renderLLMStatus() {
  if (!state.llmStatusCache) return;
  const provider = $("select-llm-provider").value;
  const item = (state.llmStatusCache.providers || {})[provider] || {};
  const badge = $("llm-key-status-badge");
  const sourceLabels = state.currentLang === "zh-TW" ? {
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
    ? (state.currentLang === "zh-TW"
      ? `已偵測 ${envName}，來源：${source}。金鑰內容不會傳到瀏覽器。`
      : `${envName} detected from the ${source}. The secret value is not sent to the browser.`)
    : (state.currentLang === "zh-TW"
      ? `尚未偵測 ${envName}。請在作業系統使用者環境變數設定後按「重新檢查」。`
      : `${envName} was not detected. Set it in the OS user environment, then select Recheck.`);
}

export async function saveSettings() {
  if (!state.currentConfig) state.currentConfig = {};
  const cfg = state.currentConfig;
  const exts = Array.from(document.querySelectorAll("#ext-checkboxes input:checked")).map(cb => cb.value);
  const provider = $("select-llm-provider").value;

  cfg.watchers = cfg.watchers || {};
  cfg.watchers.file_watcher = cfg.watchers.file_watcher || { enabled: true };
  cfg.watchers.file_watcher.watch_directories = state.configDirs;
  cfg.watchers.file_watcher.extensions = exts;

  cfg.watchers.git_watcher = cfg.watchers.git_watcher || { enabled: true };
  cfg.watchers.git_watcher.repositories = state.configRepos;

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

  cfg.watchers.calendar_watcher = cfg.watchers.calendar_watcher || { enabled: true, scan_interval_seconds: 900 };
  cfg.watchers.calendar_watcher.enabled = $("toggle-calendar").checked;
  cfg.watchers.calendar_watcher.paths = state.configCalendarPaths;
  cfg.watchers.calendar_watcher.horizon_days = Math.max(1, Math.min(366, Number($("input-calendar-horizon").value) || 30));
  cfg.watchers.calendar_watcher.store_titles = $("toggle-calendar-titles").checked;

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

  // P5-R2/R3 執行器：開關與 CLI 選擇；換 CLI 時同步重設對應的預設 args，
  // 使用者自訂的 args 只要不換 binary 就原樣保留。
  cfg.proactive_secretary = cfg.proactive_secretary || {};
  cfg.proactive_secretary.executor = cfg.proactive_secretary.executor || {};
  const executorCfg = cfg.proactive_secretary.executor;
  executorCfg.enabled = $("toggle-executor-enabled").checked;
  cfg.proactive_secretary.greeting = cfg.proactive_secretary.greeting || {};
  cfg.proactive_secretary.greeting.display_name = ($("input-greeting-name").value || "").trim().slice(0, 40);
  executorCfg.l2 = executorCfg.l2 || {};
  executorCfg.l2.enabled = $("toggle-executor-l2").checked;
  executorCfg.l2.allow_write = $("toggle-executor-l2-write").checked;
  executorCfg.agent_cli = executorCfg.agent_cli || {};
  const cliChoice = $("select-agent-cli").value === "codex" ? "codex" : "claude";
  if (executorCfg.agent_cli.binary !== cliChoice) {
    executorCfg.agent_cli.binary = cliChoice;
    executorCfg.agent_cli.args = cliChoice === "codex" ? ["exec", "{prompt}"] : ["-p", "{prompt}"];
  }
  executorCfg.telegram_approvals = executorCfg.telegram_approvals || {};
  executorCfg.telegram_approvals.enabled = $("toggle-tg-approvals").checked;
  cfg.notifiers = cfg.notifiers || {};
  cfg.notifiers.telegram = cfg.notifiers.telegram || {};
  cfg.notifiers.telegram.chat = cfg.notifiers.telegram.chat || {};
  cfg.notifiers.telegram.chat.enabled = $("toggle-tg-chat").checked;

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

// ------------------------------------------------------ P5-R5 scheduled tasks

export function requireExecutionToken() {
  const zh = state.currentLang === "zh-TW";
  let token = sessionStorage.getItem("omni_execution_token") || "";
  if (!token) {
    token = (prompt(zh
      ? "輸入 execution token（在終端機執行 `omnicontext init --show-token` 取得）："
      : "Enter execution token (shown by `omnicontext init --show-token`):") || "").trim();
    if (!token) return null;
    sessionStorage.setItem("omni_execution_token", token);
  }
  return token;
}

export async function schedRequest(url, method, body) {
  const zh = state.currentLang === "zh-TW";
  const token = requireExecutionToken();
  if (!token) return null;
  const res = await request(url, {
    method,
    headers: { "x-omnicontext-execution-token": token },
    body,
  });
  const data = await res.json().catch(() => ({}));
  if (res.status === 401) {
    sessionStorage.removeItem("omni_execution_token");
    alert(zh ? "execution token 無效，請重試。" : "Invalid execution token.");
    return null;
  }
  if (!res.ok) {
    alert((zh ? "操作被拒絕：" : "Rejected: ") + (data.detail || res.status));
    return null;
  }
  return data;
}

export function schedScheduleLabel(task) {
  const zh = state.currentLang === "zh-TW";
  const weekdays = zh
    ? ["週一", "週二", "週三", "週四", "週五", "週六", "週日"]
    : ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"];
  if (task.schedule_kind === "weekly") {
    return (zh ? "每週" : "Weekly ") + (weekdays[task.weekday] || "?") + " " + task.run_time;
  }
  if (task.schedule_kind === "monthly") {
    return zh ? `每月 ${task.day_of_month} 日 ${task.run_time}` : `Monthly day ${task.day_of_month} ${task.run_time}`;
  }
  return (zh ? "每日 " : "Daily ") + task.run_time;
}

export async function loadScheduledTasks() {
  const box = $("sched-task-list");
  if (!box) return;
  try {
    state.scheduledTasksCache = await getJSON("/api/v1/secretary/scheduled-tasks");
    renderScheduledTasks();
    const select = $("select-sched-template");
    const templates = state.scheduledTasksCache.templates || [];
    select.innerHTML = templates
      .map(item => `<option value="${esc(item.template_id)}">${esc(item.label)}</option>`)
      .join("");
    $("sched-project-field").hidden = select.value !== "generate_handoff";
  } catch (e) {
    box.innerHTML = `<span class="muted small">${esc(String(e.message || e))}</span>`;
  }
}

export function renderScheduledTasks() {
  const zh = state.currentLang === "zh-TW";
  const box = $("sched-task-list");
  if (!box || !state.scheduledTasksCache) return;
  const tasks = state.scheduledTasksCache.tasks || [];
  if (!tasks.length) {
    box.innerHTML = `<span class="muted small">${zh ? "尚未建立任何排程任務。" : "No scheduled tasks yet."}</span>`;
    return;
  }
  box.innerHTML = tasks.map(task => {
    const params = task.params && task.params.project_key ? ` · ${esc(task.params.project_key)}` : "";
    const last = task.last_run_at
      ? `${zh ? "上次" : "last"} ${esc(task.last_run_at.replace("T", " "))} → ${esc(task.last_status || "?")}`
      : (zh ? "尚未執行" : "not run yet");
    const stateLabel = task.enabled ? (zh ? "停用" : "Disable") : (zh ? "啟用" : "Enable");
    const registered = task.template_registered
      ? ""
      : ` <span class="trust broken">${zh ? "TEMPLATE 已下架" : "TEMPLATE UNREGISTERED"}</span>`;
    return `<div class="tag" style="justify-content: space-between; width: 100%; margin-bottom: 4px;">
      <span>${task.enabled ? "🟢" : "⚪"} <b>${esc(task.template_label || task.template_id)}</b>${params}
        · ${esc(schedScheduleLabel(task))} · <span class="muted small">${last}</span>${registered}</span>
      <span>
        <button class="btn btn-ghost btn-sm" data-action="run-scheduled-task" data-task-id="${task.id}">${zh ? "立即執行" : "Run now"}</button>
        <button class="btn btn-ghost btn-sm" data-action="toggle-scheduled-task" data-task-id="${task.id}" data-enabled="${task.enabled ? "false" : "true"}">${stateLabel}</button>
        <button class="btn btn-ghost btn-sm" data-action="delete-scheduled-task" data-task-id="${task.id}">✕</button>
      </span>
    </div>`;
  }).join("");
}

export async function runScheduledTaskNow(taskId) {
  const zh = state.currentLang === "zh-TW";
  const data = await schedRequest(`/api/v1/secretary/scheduled-tasks/${taskId}/run`, "POST");
  if (data) {
    alert((zh ? "已執行：" : "Executed: ") + (data.status || "?")
      + (data.result && data.result.output_path ? `\n${data.result.output_path}` : ""));
    loadScheduledTasks();
  }
};

export async function toggleScheduledTask(taskId, enabled) {
  const data = await schedRequest(`/api/v1/secretary/scheduled-tasks/${taskId}`, "PATCH", { enabled });
  if (data) loadScheduledTasks();
};

export async function deleteScheduledTask(taskId) {
  const zh = state.currentLang === "zh-TW";
  if (!confirm(zh ? "刪除此排程任務？" : "Delete this scheduled task?")) return;
  const data = await schedRequest(`/api/v1/secretary/scheduled-tasks/${taskId}`, "DELETE");
  if (data) loadScheduledTasks();
};

export async function addScheduledTask() {
  const kind = $("select-sched-kind").value;
  const payload = {
    template_id: $("select-sched-template").value,
    params: {},
    schedule_kind: kind,
    run_time: $("input-sched-time").value || "08:30",
  };
  if (payload.template_id === "generate_handoff") {
    payload.params.project_key = $("input-sched-project").value.trim();
  }
  if (kind === "weekly") payload.weekday = parseInt($("select-sched-weekday").value, 10);
  if (kind === "monthly") payload.day_of_month = parseInt($("input-sched-day").value, 10) || 1;
  const data = await schedRequest("/api/v1/secretary/scheduled-tasks", "POST", payload);
  if (data) loadScheduledTasks();
}

// ------------------------------------------------------ telegram setup flow

export async function loadTelegramStatus() {
  const badge = $("tg-status-badge");
  if (!badge) return;
  const zh = state.currentLang === "zh-TW";
  try {
    state.telegramStatusCache = await getJSON("/api/v1/telegram/status");
    const st = state.telegramStatusCache;
    const sourceLabel = (source) => ({
      env: zh ? "環境變數" : "env var",
      config: "config.yaml",
      provided: zh ? "本次輸入" : "just entered",
      missing: zh ? "未設定" : "not set",
    })[source] || source;
    if (st.enabled && st.token_configured && st.chat_id_configured) {
      badge.className = "trust ok";
      badge.textContent = zh ? "已啟用" : "ENABLED";
    } else if (st.token_configured) {
      badge.className = "trust noisy";
      badge.textContent = zh ? "已設定未啟用" : "CONFIGURED";
    } else {
      badge.className = "trust broken";
      badge.textContent = zh ? "未設定" : "NOT SET";
    }
    $("input-tg-token").placeholder = st.token_configured
      ? (zh ? `已設定（${sourceLabel(st.token_source)}）；留空沿用` : `configured (${sourceLabel(st.token_source)}); leave blank to keep`)
      : "123456789:AA...";
    $("input-tg-chat").placeholder = st.chat_id_configured
      ? (zh ? `已設定（${sourceLabel(st.chat_id_source)}）；留空沿用` : `configured (${sourceLabel(st.chat_id_source)}); leave blank to keep`)
      : "—";
    $("input-tg-morning").value = st.morning_briefing_time || "09:00";
    $("input-tg-evening").value = st.evening_summary_time || "23:30";
    const setup = $("panel-tg-setup");
    if (setup) {
      let preference = null;
      try { preference = localStorage.getItem("omni-panel-open:panel-tg-setup"); } catch (_) { /* 保持預設 */ }
      if (preference === null) {
        // 已連線且啟用 → 連線設定收合，讓批准區塊成為卡片主體
        setup.open = !(st.enabled && st.token_configured && st.chat_id_configured);
      }
    }
  } catch (e) {
    badge.className = "trust broken";
    badge.textContent = "UNAVAILABLE";
  }
}

export function tgRenderResult(receipt) {
  const zh = state.currentLang === "zh-TW";
  const box = $("tg-test-result");
  if (!box) return;
  if (!receipt) { box.textContent = ""; return; }
  if (receipt.ok) {
    const parts = [
      (zh ? "✅ token 有效，bot：@" : "✅ token valid, bot: @") + (receipt.bot_username || "?"),
    ];
    if (receipt.message_sent === true) {
      parts.push(zh ? "測試訊息已送達" : "test message delivered");
    }
    if (receipt.saved === true) {
      parts.push(zh ? "已儲存並啟用（排程已重載）" : "saved & enabled (scheduler reloaded)");
    }
    if (receipt.hint) parts.push(receipt.hint);
    box.textContent = parts.join(" · ");
    box.style.color = "var(--ok, #4caf50)";
  } else {
    box.textContent = `❌ ${receipt.error_code || "error"}：${receipt.hint || ""}`;
    box.style.color = "var(--danger, #e57373)";
  }
}

export function tgPayload() {
  const token = $("input-tg-token").value.trim();
  const chat = $("input-tg-chat").value.trim();
  return {
    bot_token: token || null,
    chat_id: chat || null,
  };
}

export async function tgDetectChat() {
  const zh = state.currentLang === "zh-TW";
  const box = $("tg-chat-candidates");
  box.hidden = false;
  box.innerHTML = `<span class="muted small">${zh ? "偵測中…" : "Detecting…"}</span>`;
  try {
    const res = await postJSON("/api/v1/telegram/detect-chat-id", { bot_token: tgPayload().bot_token });
    if (!res.ok) { tgRenderResult(res); box.hidden = true; return; }
    const candidates = res.candidates || [];
    if (!candidates.length) {
      box.innerHTML = `<span class="muted small">${esc(res.hint || (zh ? "沒有偵測到對話" : "No chats detected"))}</span>`;
      return;
    }
    box.innerHTML = candidates.map(c =>
      `<button class="btn btn-ghost btn-sm tg-chat-pick" data-chat="${esc(c.chat_id)}">💬 ${esc(c.display_name)}（${esc(c.chat_type)} · ${esc(c.chat_id)}）</button>`
    ).join(" ");
    box.querySelectorAll(".tg-chat-pick").forEach(btn => btn.addEventListener("click", () => {
      $("input-tg-chat").value = btn.dataset.chat;
      box.hidden = true;
    }));
  } catch (e) {
    box.innerHTML = `<span class="muted small">${esc(String(e.message || e))}</span>`;
  }
}

export async function tgTest() {
  const zh = state.currentLang === "zh-TW";
  $("tg-test-result").textContent = zh ? "測試中…" : "Testing…";
  try {
    tgRenderResult(await postJSON("/api/v1/telegram/test", tgPayload()));
  } catch (e) {
    tgRenderResult({ ok: false, error_code: "request_failed", hint: String(e.message || e) });
  }
}

export async function tgConnect() {
  const zh = state.currentLang === "zh-TW";
  $("tg-test-result").textContent = zh ? "驗證並儲存中…" : "Validating & saving…";
  try {
    const payload = tgPayload();
    payload.enabled = true;
    payload.morning_briefing_time = $("input-tg-morning").value || "09:00";
    payload.evening_summary_time = $("input-tg-evening").value || "23:30";
    const receipt = await postJSON("/api/v1/telegram/connect", payload);
    tgRenderResult(receipt);
    if (receipt.saved) {
      $("input-tg-token").value = "";
      $("input-tg-chat").value = "";
      loadTelegramStatus();
    }
  } catch (e) {
    tgRenderResult({ ok: false, error_code: "request_failed", hint: String(e.message || e) });
  }
}

export async function loadTelegramApprovalsStatus() {
  const box = $("tg-approvals-status");
  if (!box) return;
  const zh = state.currentLang === "zh-TW";
  try {
    const st = await getJSON("/api/v1/telegram/approvals/status");
    const parts = [];
    if (!st.enabled) {
      parts.push(zh ? "批准通道：未啟用（勾選上方選項並儲存設定）" : "Approvals: disabled (tick the option above and save settings)");
    } else if (st.armed) {
      parts.push((zh ? "🔓 已解鎖至 " : "🔓 unlocked until ") + String(st.armed_until || "").replace("T", " "));
    } else {
      parts.push(zh ? "🔒 已上鎖（按「解鎖遠端批准」啟用）" : "🔒 locked (press Unlock to enable)");
    }
    parts.push((zh ? "輪詢器：" : "poller: ") + (st.poller_running ? (zh ? "運行中" : "running") : (zh ? "未運行（啟用後重載設定）" : "not running")));
    if (st.arm_code && st.arm_code.pending) {
      parts.push((zh ? "解鎖碼有效至 " : "unlock code until ") + String(st.arm_code.expires_at || "").slice(11, 16));
    }
    try {
      const chat = await getJSON("/api/v1/telegram/chat/status");
      parts.push((zh ? "對話：" : "chat: ") + (chat.enabled ? (zh ? "開" : "on") : (zh ? "關" : "off")));
      if (chat.enabled && chat.remote_arm_enabled) parts.push(zh ? "允許 /arm" : "/arm allowed");
    } catch (e) {}
    box.textContent = parts.join(" · ");
  } catch (e) {
    box.textContent = "";
  }
}

export async function tgIssueArmCode() {
  const zh = state.currentLang === "zh-TW";
  const box = $("tg-arm-code-box");
  const data = await schedRequest("/api/v1/telegram/approvals/arm-code", "POST");
  if (!data) return;
  if (box) {
    box.innerHTML = `<span class="arm-code">${esc(data.code)}</span>` +
      `<span class="muted small">${zh
        ? `${Math.round((data.ttl_seconds || 300) / 60)} 分鐘內有效、只能用一次。在手機傳「/arm ${esc(data.code)}」解鎖。`
        : `Valid for ${Math.round((data.ttl_seconds || 300) / 60)} min, single use. Send “/arm ${esc(data.code)}” from your phone.`}</span>`;
    box.hidden = false;
    setTimeout(() => { box.hidden = true; box.innerHTML = ""; }, (data.ttl_seconds || 300) * 1000);
  }
  loadTelegramApprovalsStatus();
}

// ---------------------------------------------------------------- ADR-014 LINE（只能推播）
export async function loadLineStatus() {
  const badge = $("line-status-badge");
  if (!badge) return;
  const zh = state.currentLang === "zh-TW";
  try {
    const st = await getJSON("/api/v1/line/status");
    if (st.enabled && st.token_configured && st.to_configured) {
      badge.textContent = zh ? "已啟用（推播）" : "ENABLED (PUSH)";
      badge.className = "trust ok";
    } else if (st.token_configured) {
      badge.textContent = zh ? "待設定收件 ID" : "NEEDS USER ID";
      badge.className = "trust noisy";
    } else {
      badge.textContent = zh ? "未設定" : "NOT SET";
      badge.className = "trust broken";
    }
    if (st.token_source === "env" && $("input-line-token")) {
      $("input-line-token").placeholder = zh ? "（已由環境變數提供）" : "(provided by env var)";
    }
  } catch (e) {
    badge.textContent = zh ? "讀不到" : "UNKNOWN";
    badge.className = "trust broken";
  }
}

export function lineRenderResult(receipt) {
  const box = $("line-test-result");
  if (!box) return;
  const zh = state.currentLang === "zh-TW";
  if (receipt.ok) {
    const name = receipt.bot_display_name || receipt.bot_basic_id || "";
    box.textContent = (receipt.message_sent
      ? (zh ? `✅ 連線成功，已發出測試訊息 ${name}` : `✅ Connected, test message sent ${name}`)
      : (zh ? `✅ token 有效 ${name}｜${receipt.hint || ""}` : `✅ Token valid ${name} | ${receipt.hint || ""}`))
      + (receipt.saved ? (zh ? "｜已儲存並啟用" : " | saved and enabled") : "");
  } else {
    box.textContent = `❌ ${receipt.error_code || "failed"}：${receipt.hint || ""}`;
  }
}

export async function lineTest(save) {
  const zh = state.currentLang === "zh-TW";
  const box = $("line-test-result");
  if (box) box.textContent = zh ? "測試中…" : "Testing…";
  const body = {
    access_token: ($("input-line-token").value || "").trim() || null,
    to: ($("input-line-to").value || "").trim() || null,
  };
  try {
    const receipt = await postJSON(save ? "/api/v1/line/connect" : "/api/v1/line/test", body);
    lineRenderResult(receipt);
    if (receipt.saved) {
      $("input-line-token").value = "";
      loadLineStatus();
      loadNotificationChannels();
    }
  } catch (e) {
    lineRenderResult({ ok: false, error_code: "request_failed", hint: String(e.message || e) });
  }
}

export async function lineDisconnect() {
  const zh = state.currentLang === "zh-TW";
  if (!confirm(zh ? "停用 LINE 推播並清除本機儲存的 token 與收件 ID？" : "Disable LINE push and clear the stored token and recipient id?")) return;
  try {
    const receipt = await postJSON("/api/v1/line/disconnect", {});
    lineRenderResult({ ok: true, hint: receipt.hint });
    loadLineStatus();
    loadNotificationChannels();
  } catch (e) {
    lineRenderResult({ ok: false, error_code: "request_failed", hint: String(e.message || e) });
  }
}

export async function loadNotificationChannels() {
  const box = $("tg-approvals-status");
  if (!box) return;
  try {
    await getJSON("/api/v1/notifications/channels");
  } catch (e) {}
}

export async function tgArm() {
  const data = await schedRequest("/api/v1/telegram/approvals/arm", "POST");
  if (data) loadTelegramApprovalsStatus();
}

export async function tgDisarm() {
  try {
    await postJSON("/api/v1/telegram/approvals/disarm");
    loadTelegramApprovalsStatus();
  } catch (e) { /* 狀態列會反映實況 */ }
}

export async function tgDisconnect() {
  const zh = state.currentLang === "zh-TW";
  if (!confirm(zh ? "停用 Telegram 推播並清除本機保存的 token／chat id？" : "Disable Telegram push and clear the locally stored token / chat id?")) return;
  try {
    tgRenderResult(await postJSON("/api/v1/telegram/disconnect"));
    loadTelegramStatus();
  } catch (e) {
    tgRenderResult({ ok: false, error_code: "request_failed", hint: String(e.message || e) });
  }
}

// ------------------------------------------------------ local repository sync
