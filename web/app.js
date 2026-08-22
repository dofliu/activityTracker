// web/app.js - OmniContext Web Dashboard Controller

const API_BASE = "";

let currentConfig = null;
let activeFilter = "all";
let currentSummaryMarkdown = "";
let currentCheckpointMarkdown = "";
let isMonitoring = false;

document.addEventListener("DOMContentLoaded", () => {
  initTabs();
  initControlButtons();
  initSettingsForm();
  initSummariesTab();
  initCheckpointsTab();

  // 初始載入與定時輪詢
  loadActiveProjects();
  refreshStatusAndMetrics();
  refreshLiveFeed();
  loadConfigIntoSettings();
  loadSummariesList();
  loadCheckpointsList();

  setInterval(() => {
    refreshStatusAndMetrics();
    refreshLiveFeed();
  }, 4000);
});

// =====================================================================
// 1. 分頁切換 (Tabs Navigation)
// =====================================================================
function initTabs() {
  document.querySelectorAll(".nav-tab").forEach(tab => {
    tab.addEventListener("click", () => {
      document.querySelectorAll(".nav-tab").forEach(t => t.classList.remove("active"));
      document.querySelectorAll(".tab-pane").forEach(p => p.classList.remove("active"));

      tab.classList.add("active");
      const targetId = tab.getAttribute("data-tab");
      document.getElementById(targetId).classList.add("active");

      if (targetId === "tab-projects") loadActiveProjects();
      if (targetId === "tab-summaries") loadSummariesList();
      if (targetId === "tab-checkpoints") loadCheckpointsList();
      if (targetId === "tab-settings") loadConfigIntoSettings();
    });
  });

  document.getElementById("btn-refresh-projects").addEventListener("click", loadActiveProjects);
}

// =====================================================================
// 2. 進行中專案 (Active Projects - P1 核心首頁)
// =====================================================================
async function loadActiveProjects() {
  const container = document.getElementById("projects-grid");
  try {
    const res = await fetch(`${API_BASE}/api/v1/projects/active`);
    if (!res.ok) return;
    const projects = await res.json();

    if (projects.length === 0) {
      container.innerHTML = '<div class="loading-placeholder">尚未識別到專案活動。進行程式開發、論文寫作或在 Claude/Codex 發問後將自動建立。</div>';
      return;
    }

    container.innerHTML = projects.map(p => {
      let statusClass = "proj-status-active";
      let statusText = "🟢 活躍中";
      if (p.status === "idle") {
        statusClass = "proj-status-idle";
        statusText = `🟡 閒置 ${p.idle_days} 天`;
      } else if (p.status === "stale") {
        statusClass = "proj-status-stale";
        statusText = `⚪ 閒置 ${p.idle_days} 天`;
      }

      let catIcon = "💻";
      if (p.category.includes("Research") || p.category.includes("Paper")) catIcon = "📄";
      if (p.category.includes("AI")) catIcon = "🤖";

      return `
        <div class="project-card">
          <div>
            <div class="project-card-header">
              <div>
                <div class="project-title">${catIcon} ${escapeHtml(p.display_name)}</div>
                <div class="project-category">${escapeHtml(p.category)}</div>
              </div>
              <span class="project-status-badge ${statusClass}">${statusText}</span>
            </div>

            <div class="project-action-box">
              <strong>最新動態：</strong> ${escapeHtml(p.last_action_summary || '無紀錄')}
            </div>
          </div>

          <div class="project-meta-row">
            <span>⏱️ 最後活動: ${p.last_activity_at}</span>
            <span>📌 未結事項: <strong>${p.open_loops_count}</strong></span>
          </div>
        </div>
      `;
    }).join("");

    document.getElementById("stat-active-projects").innerText = projects.length;
  } catch (err) {
    console.error("Failed to load active projects:", err);
  }
}

// =====================================================================
// 3. 系統狀態與控制按鈕
// =====================================================================
function initControlButtons() {
  const btnToggle = document.getElementById("btn-toggle-monitor");
  btnToggle.addEventListener("click", () => {
    if (isMonitoring) {
      stopMonitoring();
    } else {
      startMonitoring();
    }
  });

  document.getElementById("btn-quick-checkpoint").addEventListener("click", triggerQuickCheckpoint);
  document.getElementById("btn-quick-summary").addEventListener("click", () => triggerGenerateSummary());
  document.getElementById("btn-trigger-cp-now").addEventListener("click", triggerQuickCheckpoint);

  document.querySelectorAll(".filter-btn").forEach(btn => {
    btn.addEventListener("click", () => {
      document.querySelectorAll(".filter-btn").forEach(b => b.classList.remove("active"));
      btn.classList.add("active");
      activeFilter = btn.getAttribute("data-filter");
      refreshLiveFeed();
    });
  });
}

async function refreshStatusAndMetrics() {
  try {
    const res = await fetch(`${API_BASE}/api/v1/control/status`);
    if (!res.ok) return;
    const data = await res.json();

    isMonitoring = data.is_running;
    updateStatusBadge(data.is_running);

    document.getElementById("stat-ai-prompts").innerText = data.metrics.ai_prompts_count;
    document.getElementById("stat-file-events").innerText = data.metrics.file_events_count;
    document.getElementById("stat-git-commits").innerText = data.metrics.git_commits_count;
    document.getElementById("stat-window-events").innerText = data.metrics.window_events_count;

    renderWatchersGrid(data.watchers);
    document.getElementById("last-refresh-time").innerText = `更新於: ${new Date().toLocaleTimeString()}`;
  } catch (err) {
    console.warn("Failed to fetch status:", err);
  }
}

function updateStatusBadge(running) {
  const badge = document.getElementById("system-status-badge");
  const text = document.getElementById("status-text");
  const toggleBtn = document.getElementById("btn-toggle-monitor");
  const toggleIcon = document.getElementById("toggle-icon");
  const toggleText = document.getElementById("toggle-text");

  if (running) {
    badge.className = "status-badge status-running";
    text.innerText = "🟢 監控中 (Active)";
    toggleBtn.className = "btn btn-danger";
    toggleIcon.innerText = "⏸";
    toggleText.innerText = "停止監控";
  } else {
    badge.className = "status-badge status-stopped";
    text.innerText = "🔴 已暫停 (Paused)";
    toggleBtn.className = "btn btn-primary";
    toggleIcon.innerText = "▶";
    toggleText.innerText = "開始監控";
  }
}

function renderWatchersGrid(watchers) {
  const grid = document.getElementById("watchers-grid");
  const items = [
    { name: "論文與檔案監控", enabled: watchers.file_watcher, icon: "📁" },
    { name: "Git 代碼倉庫監控", enabled: watchers.git_watcher, icon: "💻" },
    { name: "視窗焦點時間統計", enabled: watchers.window_watcher, icon: "⏳" },
    { name: "本機 Agent 日誌解析", enabled: watchers.agent_log_watcher, icon: "🤖" },
    { name: "每日定時彙整排程", enabled: watchers.scheduler, icon: "⏱️" }
  ];

  grid.innerHTML = items.map(it => `
    <div class="watcher-badge-item">
      <span>${it.icon} ${it.name}</span>
      <span style="color: ${it.enabled ? '#34d399' : '#94a3b8'}; font-weight: bold;">
        ${it.enabled ? '● 啟用' : '○ 停用'}
      </span>
    </div>
  `).join("");
}

async function startMonitoring() {
  try {
    const res = await fetch(`${API_BASE}/api/v1/control/start`, { method: "POST" });
    const data = await res.json();
    alert(data.message || "已啟動監控");
    refreshStatusAndMetrics();
  } catch (err) {
    alert("啟動失敗: " + err.message);
  }
}

async function stopMonitoring() {
  try {
    const res = await fetch(`${API_BASE}/api/v1/control/stop`, { method: "POST" });
    const data = await res.json();
    alert(data.message || "已停止監控");
    refreshStatusAndMetrics();
  } catch (err) {
    alert("停止失敗: " + err.message);
  }
}

// =====================================================================
// 4. 即時活動流 (Live Feed)
// =====================================================================
async function refreshLiveFeed() {
  try {
    const res = await fetch(`${API_BASE}/api/v1/events/recent?limit=40&event_type=${activeFilter}`);
    if (!res.ok) return;
    const events = await res.json();
    const container = document.getElementById("feed-list");

    if (events.length === 0) {
      container.innerHTML = '<div class="loading-placeholder">目前尚無活動紀錄。</div>';
      return;
    }

    container.innerHTML = events.map(e => {
      let badgeClass = "badge-ai";
      if (e.type === "file") badgeClass = "badge-file";
      if (e.type === "git") badgeClass = "badge-git";
      if (e.type === "window") badgeClass = "badge-window";

      return `
        <div class="feed-item">
          <div class="feed-item-left">
            <div>
              <span class="feed-badge ${badgeClass}">${e.badge}</span>
              <span class="feed-title">${escapeHtml(e.title)}</span>
            </div>
            <div class="feed-detail">${escapeHtml(e.detail || '')}</div>
            ${e.response ? `<div class="feed-detail" style="color:#38bdf8;">${escapeHtml(e.response.substring(0, 160))}...</div>` : ''}
          </div>
          <div class="feed-time">${e.timestamp.split(" ")[1]}</div>
        </div>
      `;
    }).join("");
  } catch (err) {
    console.warn("Failed to refresh feed:", err);
  }
}

// =====================================================================
// 5. 監控配置管理 (Settings)
// =====================================================================
let configDirs = [];
let configRepos = [];

function initSettingsForm() {
  document.getElementById("btn-add-dir").addEventListener("click", () => {
    const input = document.getElementById("input-new-dir");
    const val = input.value.trim();
    if (val && !configDirs.includes(val)) {
      configDirs.push(val);
      renderDirList();
      input.value = "";
    }
  });

  document.getElementById("btn-add-repo").addEventListener("click", () => {
    const input = document.getElementById("input-new-repo");
    const val = input.value.trim();
    if (val && !configRepos.includes(val)) {
      configRepos.push(val);
      renderRepoList();
      input.value = "";
    }
  });

  document.getElementById("btn-save-settings").addEventListener("click", saveSettings);
}

async function loadConfigIntoSettings() {
  try {
    const res = await fetch(`${API_BASE}/api/v1/config`);
    if (!res.ok) return;
    currentConfig = await res.json();

    configDirs = currentConfig.watchers?.file_watcher?.watch_directories || [];
    renderDirList();

    configRepos = currentConfig.watchers?.git_watcher?.repositories || [];
    renderRepoList();

    document.getElementById("input-schedule-time").value = currentConfig.synthesizer?.schedule?.time || "23:30";
    document.getElementById("input-checkpoint-interval").value = currentConfig.synthesizer?.periodic_checkpoint?.interval_hours || 2;
    
    const provider = currentConfig.synthesizer?.provider || "gemini";
    document.getElementById("select-llm-provider").value = provider;
    document.getElementById("input-model-name").value = currentConfig.synthesizer?.[provider]?.model || "gemini-2.5-flash";

    const exts = currentConfig.watchers?.file_watcher?.extensions || [];
    document.querySelectorAll("#ext-checkboxes input").forEach(cb => {
      cb.checked = exts.includes(cb.value);
    });

    // 載入各開關
    document.getElementById("toggle-claude-code").checked = currentConfig.watchers?.agent_log_watcher?.claude_code !== false;
    document.getElementById("toggle-codex").checked = currentConfig.watchers?.agent_log_watcher?.codex !== false;
    document.getElementById("toggle-antigravity").checked = currentConfig.watchers?.agent_log_watcher?.antigravity !== false;
    document.getElementById("toggle-gemini").checked = currentConfig.watchers?.browser?.gemini !== false;
    document.getElementById("toggle-chatgpt").checked = currentConfig.watchers?.browser?.chatgpt !== false;
    document.getElementById("toggle-claude-web").checked = currentConfig.watchers?.browser?.claude_web !== false;
    document.getElementById("toggle-manus").checked = currentConfig.watchers?.browser?.manus !== false;
    document.getElementById("toggle-window-focus").checked = currentConfig.watchers?.window_watcher?.enabled !== false;
  } catch (err) {
    console.error("Failed to load config:", err);
  }
}

function renderDirList() {
  const container = document.getElementById("dir-list");
  if (configDirs.length === 0) {
    container.innerHTML = '<div class="text-muted text-sm">尚未設定監控資料夾</div>';
    return;
  }
  container.innerHTML = configDirs.map((dir, idx) => `
    <div class="tag-item">
      <span>${escapeHtml(dir)}</span>
      <span class="tag-delete" onclick="removeDir(${idx})">✕</span>
    </div>
  `).join("");
}

function renderRepoList() {
  const container = document.getElementById("repo-list");
  if (configRepos.length === 0) {
    container.innerHTML = '<div class="text-muted text-sm">尚未設定 Git 專案根目錄</div>';
    return;
  }
  container.innerHTML = configRepos.map((repo, idx) => `
    <div class="tag-item">
      <span>${escapeHtml(repo)}</span>
      <span class="tag-delete" onclick="removeRepo(${idx})">✕</span>
    </div>
  `).join("");
}

window.removeDir = function(idx) {
  configDirs.splice(idx, 1);
  renderDirList();
};

window.removeRepo = function(idx) {
  configRepos.splice(idx, 1);
  renderRepoList();
};

async function saveSettings() {
  if (!currentConfig) currentConfig = {};

  const checkedExts = Array.from(document.querySelectorAll("#ext-checkboxes input:checked")).map(cb => cb.value);
  const provider = document.getElementById("select-llm-provider").value;
  const modelName = document.getElementById("input-model-name").value.trim();

  if (!currentConfig.watchers) currentConfig.watchers = {};
  if (!currentConfig.watchers.file_watcher) currentConfig.watchers.file_watcher = { enabled: true };
  currentConfig.watchers.file_watcher.watch_directories = configDirs;
  currentConfig.watchers.file_watcher.extensions = checkedExts;

  if (!currentConfig.watchers.git_watcher) currentConfig.watchers.git_watcher = { enabled: true };
  currentConfig.watchers.git_watcher.repositories = configRepos;

  if (!currentConfig.watchers.agent_log_watcher) currentConfig.watchers.agent_log_watcher = { enabled: true };
  currentConfig.watchers.agent_log_watcher.claude_code = document.getElementById("toggle-claude-code").checked;
  currentConfig.watchers.agent_log_watcher.codex = document.getElementById("toggle-codex").checked;
  currentConfig.watchers.agent_log_watcher.antigravity = document.getElementById("toggle-antigravity").checked;

  if (!currentConfig.watchers.browser) currentConfig.watchers.browser = {};
  currentConfig.watchers.browser.gemini = document.getElementById("toggle-gemini").checked;
  currentConfig.watchers.browser.chatgpt = document.getElementById("toggle-chatgpt").checked;
  currentConfig.watchers.browser.claude_web = document.getElementById("toggle-claude-web").checked;
  currentConfig.watchers.browser.manus = document.getElementById("toggle-manus").checked;

  if (!currentConfig.watchers.window_watcher) currentConfig.watchers.window_watcher = { enabled: true };
  currentConfig.watchers.window_watcher.enabled = document.getElementById("toggle-window-focus").checked;

  if (!currentConfig.synthesizer) currentConfig.synthesizer = {};
  if (!currentConfig.synthesizer.schedule) currentConfig.synthesizer.schedule = { enabled: true };
  currentConfig.synthesizer.schedule.time = document.getElementById("input-schedule-time").value.trim();

  if (!currentConfig.synthesizer.periodic_checkpoint) currentConfig.synthesizer.periodic_checkpoint = { enabled: true };
  currentConfig.synthesizer.periodic_checkpoint.interval_hours = parseInt(document.getElementById("input-checkpoint-interval").value) || 2;

  currentConfig.synthesizer.provider = provider;
  if (!currentConfig.synthesizer[provider]) currentConfig.synthesizer[provider] = {};
  currentConfig.synthesizer[provider].model = modelName;

  try {
    const res = await fetch(`${API_BASE}/api/v1/config`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(currentConfig)
    });
    const data = await res.json();
    alert("✅ " + data.message);
    refreshStatusAndMetrics();
  } catch (err) {
    alert("儲存失敗: " + err.message);
  }
}

// =====================================================================
// 6. AI 每日摘要 (Summaries Tab)
// =====================================================================
function initSummariesTab() {
  document.getElementById("input-summary-date").value = new Date().toISOString().split("T")[0];

  document.getElementById("btn-generate-custom-summary").addEventListener("click", () => {
    const targetDate = document.getElementById("input-summary-date").value;
    triggerGenerateSummary(targetDate, true);
  });

  document.getElementById("btn-copy-markdown").addEventListener("click", () => {
    if (!currentSummaryMarkdown) return;
    navigator.clipboard.writeText(currentSummaryMarkdown);
    alert("📋 Markdown 內容已複製至剪貼簿！");
  });
}

async function loadSummariesList() {
  try {
    const res = await fetch(`${API_BASE}/api/v1/summaries`);
    if (!res.ok) return;
    const list = await res.json();
    const container = document.getElementById("summary-history-list");

    if (list.length === 0) {
      container.innerHTML = '<div class="loading-placeholder">尚未產生歷史摘要。點擊上方按鈕立即生成。</div>';
      return;
    }

    container.innerHTML = list.map((item, idx) => `
      <div class="history-item ${idx === 0 ? 'active' : ''}" onclick="selectSummaryDate('${item.date_str}')">
        <div class="history-item-title">📅 ${item.date_str}</div>
        <div class="history-item-sub">${item.llm_provider.toUpperCase()} (${item.model_name}) • ${item.created_at || ''}</div>
      </div>
    `).join("");

    if (list.length > 0) {
      renderMarkdownSummary(list[0].date_str, list[0].raw_markdown);
    }
  } catch (err) {
    console.error("Failed to load summaries list:", err);
  }
}

window.selectSummaryDate = async function(dateStr) {
  try {
    const res = await fetch(`${API_BASE}/api/v1/summaries/${dateStr}`);
    if (!res.ok) return;
    const data = await res.json();
    renderMarkdownSummary(data.date_str, data.raw_markdown);

    document.querySelectorAll(".history-item").forEach(el => {
      if (el.innerText.includes(dateStr)) el.classList.add("active");
      else el.classList.remove("active");
    });
  } catch (err) {
    console.error("Failed to load summary for date:", err);
  }
};

function renderMarkdownSummary(title, markdown) {
  currentSummaryMarkdown = markdown;
  document.getElementById("current-summary-title").innerText = `📄 每日回顧報告 (${title})`;
  const viewer = document.getElementById("summary-markdown-viewer");
  if (window.marked) {
    viewer.innerHTML = marked.parse(markdown);
  } else {
    viewer.innerText = markdown;
  }
}

async function triggerGenerateSummary(targetDate = null, force = true) {
  const btn = document.getElementById("btn-quick-summary");
  const originalText = btn.innerText;
  btn.innerText = "⏳ AI 分析中...";
  btn.disabled = true;

  try {
    const res = await fetch(`${API_BASE}/api/v1/summaries/generate`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        target_date: targetDate,
        force_refresh: force
      })
    });
    const data = await res.json();
    alert("🎉 每日 AI 回顧摘要已產出！");
    loadSummariesList();
    renderMarkdownSummary(data.date_str, data.markdown);
  } catch (err) {
    alert("生成摘要失敗: " + err.message);
  } finally {
    btn.innerText = originalText;
    btn.disabled = false;
  }
}

// =====================================================================
// 7. 週期性快照日誌 (Checkpoints Tab)
// =====================================================================
function initCheckpointsTab() {
  document.getElementById("btn-copy-cp").addEventListener("click", () => {
    if (!currentCheckpointMarkdown) return;
    navigator.clipboard.writeText(currentCheckpointMarkdown);
    alert("📋 Checkpoint Log 已複製至剪貼簿！");
  });
}

async function loadCheckpointsList() {
  try {
    const res = await fetch(`${API_BASE}/api/v1/logs/checkpoints`);
    if (!res.ok) return;
    const list = await res.json();
    const container = document.getElementById("checkpoint-history-list");

    if (list.length === 0) {
      container.innerHTML = '<div class="loading-placeholder">目前尚無快照日誌。點擊「產出快照」立即建立。</div>';
      return;
    }

    container.innerHTML = list.map((item, idx) => `
      <div class="history-item ${idx === 0 ? 'active' : ''}" onclick="selectCheckpointFile('${item.file_name}')">
        <div class="history-item-title">⏱️ ${item.file_name}</div>
        <div class="history-item-sub">建立時間: ${item.created_at} (${(item.size_bytes / 1024).toFixed(1)} KB)</div>
      </div>
    `).join("");

    if (list.length > 0) {
      selectCheckpointFile(list[0].file_name);
    }
  } catch (err) {
    console.error("Failed to load checkpoints:", err);
  }
}

window.selectCheckpointFile = async function(fileName) {
  try {
    const res = await fetch(`${API_BASE}/api/v1/logs/checkpoints/${fileName}`);
    if (!res.ok) return;
    const data = await res.json();
    currentCheckpointMarkdown = data.content;

    document.getElementById("current-cp-title").innerText = `⏱️ 活動快照內容 (${fileName})`;
    const viewer = document.getElementById("checkpoint-markdown-viewer");
    if (window.marked) {
      viewer.innerHTML = marked.parse(data.content);
    } else {
      viewer.innerText = data.content;
    }
  } catch (err) {
    console.error("Failed to read checkpoint content:", err);
  }
};

async function triggerQuickCheckpoint() {
  try {
    const res = await fetch(`${API_BASE}/api/v1/logs/checkpoints/generate`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ hours: 2 })
    });
    const data = await res.json();
    alert(`✅ 已產出活動快照日誌: ${data.file_name}`);
    loadCheckpointsList();
  } catch (err) {
    alert("產出快照失敗: " + err.message);
  }
}

function escapeHtml(text) {
  if (!text) return "";
  return String(text)
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;")
    .replace(/'/g, "&#039;");
}
