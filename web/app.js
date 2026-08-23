// web/app.js — OmniContext Intel Board controller

const API = "";
const POLL_MS = 4000;

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

const $ = (id) => document.getElementById(id);
const esc = (t) => String(t == null ? "" : t)
  .replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;")
  .replace(/"/g, "&quot;").replace(/'/g, "&#039;");

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

  setInterval(() => { refreshStatus(); refreshFeed(); }, POLL_MS);
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
  $("btn-theme").textContent = document.documentElement.dataset.theme === "dark" ? "☀ 淺色" : "☾ 深色";
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
      if (id === "tab-projects") loadProjects();
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

    const pill = $("status-pill");
    pill.className = "pill " + (isMonitoring ? "pill-on" : "pill-off");
    $("status-text").textContent = isMonitoring ? "MONITORING" : "PAUSED";
    $("btn-toggle-monitor").textContent = isMonitoring ? "暫停" : "開始";

    renderStats(data.metrics);
    renderCollectors(data.watchers);
    $("last-refresh").textContent = "updated " + new Date().toLocaleTimeString();
  } catch (e) {
    $("status-text").textContent = "未連線";
    $("status-pill").className = "pill pill-off";
  }
}

function renderStats(m) {
  // 可信度依 README 已知缺陷 D1~D6 標示，避免把不可信數字當成真實紀錄
  const items = [
    { tag: "AI TURNS", value: m.ai_prompts_count, label: "Claude / Codex / Web", trust: "noisy" },
    { tag: "FILES", value: m.file_events_count, label: "論文與檔案異動", trust: "noisy" },
    { tag: "COMMITS", value: m.git_commits_count, label: "Git commits", trust: "broken" },
    { tag: "FOCUS", value: m.window_events_count, label: "視窗焦點切換", trust: "ok" },
    { tag: "STREAMS", value: projectsCache.length, label: "進行中工作", trust: "ok" }
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

function renderCollectors(w) {
  const items = [
    { name: "檔案監控", on: w.file_watcher },
    { name: "Git 掃描", on: w.git_watcher },
    { name: "視窗焦點", on: w.window_watcher },
    { name: "Agent 日誌", on: w.agent_log_watcher },
    { name: "定時排程", on: w.scheduler }
  ];
  $("watchers-grid").innerHTML = items.map(it => `
    <div class="collector">
      <div class="collector-name">${it.name}</div>
      <div class="collector-state" style="color:${it.on ? "var(--orange)" : "var(--mu)"}">
        ${it.on ? "● ENABLED" : "○ DISABLED"}
      </div>
    </div>`).join("");
}

// ---------------------------------------------------------------- live feed
async function refreshFeed() {
  try {
    const events = await getJSON(`/api/v1/events/recent?limit=60&event_type=${activeFilter}`);
    if (activeFilter === "all") recentEvents = events;
    const box = $("feed-list");
    if (!events.length) { box.innerHTML = '<div class="placeholder">目前尚無活動紀錄。</div>'; return; }
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
    $("projects-count").textContent = `ACTIVE WORKSTREAMS · ${projectsCache.length}`;
  } catch (e) {
    $("projects-list").innerHTML = '<div class="placeholder">無法讀取進行中工作。請確認 main.py 是否在執行。</div>';
  }
}

function statusLabel(p) {
  if (p.status === "active") return "活躍中";
  return `閒置 ${p.idle_days} 天`;
}

function renderResume() {
  const box = document.querySelector("#resume-card .resume-body");
  const p = projectsCache[0];
  if (!p) {
    box.innerHTML = '<div class="placeholder">尚未識別到專案活動。進行程式開發、論文寫作或在 Claude / Codex 發問後將自動建立。</div>';
    return;
  }
  box.innerHTML = `
    <div style="min-width:0">
      <div class="resume-title">${esc(p.display_name)}</div>
      <div class="resume-action">${esc(p.last_action_summary || "無紀錄")}</div>
      <div class="resume-meta">${esc(p.last_activity_at)} · ${esc(p.category || "")} · 未結 ${p.open_loops_count}</div>
    </div>
    <button class="btn btn-primary" data-resume="${esc(p.project_key)}">接續 →</button>`;
  const btn = box.querySelector("[data-resume]");
  if (btn) btn.addEventListener("click", () => expandProject(p.project_key, true));
}

function renderProjects() {
  const box = $("projects-list");
  if (!projectsCache.length) {
    box.innerHTML = '<div class="placeholder">尚未識別到專案活動。</div>';
    return;
  }
  box.innerHTML = projectsCache.map(p => {
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

    return `
      <div class="pitem" data-key="${esc(p.project_key)}">
        <div class="prow">
          <span class="pbar" style="background:${bar}"></span>
          <div style="min-width:0">
            <div class="pname" style="display:flex; align-items:center;">
              <span>${esc(p.display_name)}</span>
              ${ghBadge}
            </div>
            <div class="pmeta">${esc(p.category || "")} · ${statusLabel(p)}</div>
          </div>
          <div class="paction">${esc(p.last_action_summary || "無紀錄")}</div>
          <div class="ploops" style="color:${loopColor}">${p.open_loops_count}<span>未結</span></div>
          <div class="plast">${esc((p.last_activity_at || "").replace(/^\d{4}-/, ""))}</div>
          <div class="pchev">${open ? "▾" : "▸"}</div>
        </div>
        <div class="pdetail-slot"></div>
      </div>`;
  }).join("");

  box.querySelectorAll(".pitem").forEach(item => {
    item.querySelector(".prow").addEventListener("click", () => {
      const key = item.dataset.key;
      expandProject(expandedProject === key ? null : key);
    });
  });

  if (expandedProject) renderProjectDetail(expandedProject);
}

function expandProject(key, scroll) {
  expandedProject = key;
  renderProjects();
  if (key && scroll) {
    const el = document.querySelector(`.pitem[data-key="${CSS.escape(key)}"]`);
    if (el) window.scrollTo({ top: el.getBoundingClientRect().top + window.scrollY - 160, behavior: "smooth" });
  }
}

async function renderProjectDetail(key) {
  const item = document.querySelector(`.pitem[data-key="${CSS.escape(key)}"]`);
  if (!item) return;
  const slot = item.querySelector(".pdetail-slot");
  slot.innerHTML = '<div class="pdetail"><div class="placeholder">載入專案活動…</div></div>';

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
    : '<div class="placeholder" style="padding:0">近期尚無詳細活動紀錄。</div>';

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
    : '<div class="placeholder" style="padding:0">無未結事項。</div>';

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
          <span class="mono-label">GITHUB REPO & PULL REQUESTS</span>
          <a href="${esc(gh.html_url)}" target="_blank" class="mono-mini" style="color:var(--orange); text-decoration:none;">${esc(gh.full_name)} (${gh.is_private ? "Private" : "Public"}) ↗</a>
        </div>
        <div>${prsHtml}</div>
      </div>`;
  }

  slot.innerHTML = `
    <div class="pdetail">
      <div>
        <span class="mono-label">RECENT MODIFIED FILES (本次工作異動檔案)</span>
        ${fileListHtml}
      </div>
      <div>
        <span class="mono-label">ACTIVITY TIMELINE</span>
        ${tl}
      </div>
      <div>
        <span class="mono-label">OPEN LOOPS</span>
        ${ll}
        <div class="pdetail-actions">
          <button class="btn btn-primary btn-sm" data-cp>產出此刻快照</button>
        </div>
      </div>
      ${ghSection}
    </div>`;
  const cpBtn = slot.querySelector("[data-cp]");
  if (cpBtn) cpBtn.addEventListener("click", (ev) => { ev.stopPropagation(); triggerCheckpoint(); });
}


// ---------------------------------------------------------------- open loops rail
async function loadOpenLoops() {
  try {
    loopsCache = await getJSON("/api/v1/open-loops");
    renderOpenLoops();
  } catch (e) {
    $("open-loops-list").innerHTML = '<div class="placeholder">無法讀取未結事項。</div>';
  }
}

function renderOpenLoops() {
  $("loop-tally").textContent = String(loopsCache.length);
  const box = $("open-loops-list");
  if (!loopsCache.length) {
    box.innerHTML = '<div class="placeholder">目前沒有未結事項。AI 每日摘要會自動萃取。</div>';
    return;
  }
  box.innerHTML = loopsCache.map(l => `
    <div class="loop" data-id="${l.id}">
      <span class="loop-box"></span>
      <div style="min-width:0">
        <div class="loop-text">${esc(l.title)}</div>
        <div class="loop-src">${esc(l.project_key)} · ${esc(l.created_at || "")}</div>
      </div>
    </div>`).join("");

  box.querySelectorAll(".loop").forEach(el => {
    el.addEventListener("click", () => resolveLoop(el));
  });
}

async function resolveLoop(el) {
  const id = el.dataset.id;
  if (el.classList.contains("done")) return;
  el.classList.add("done");
  el.querySelector(".loop-box").textContent = "✓";
  try {
    await postJSON(`/api/v1/open-loops/${id}/resolve`);
    loopsCache = loopsCache.filter(l => String(l.id) !== String(id));
    $("loop-tally").textContent = String(loopsCache.length);
    setTimeout(() => { renderOpenLoops(); loadProjects(); }, 550);
  } catch (e) {
    el.classList.remove("done");
    el.querySelector(".loop-box").textContent = "";
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
  $("select-llm-provider").addEventListener("change", (e) => {
    const p = e.target.value;
    const defaults = { gemini: "gemini-3.7-flash", anthropic: "claude-3-5-sonnet-20241022", openai: "gpt-4o", ollama: "llama3.1:8b" };
    $("input-model-name").value = (currentConfig && currentConfig.synthesizer && currentConfig.synthesizer[p] && currentConfig.synthesizer[p].model) || defaults[p] || "";
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

    configDirs = (w.file_watcher && w.file_watcher.watch_directories) || [];
    configRepos = (w.git_watcher && w.git_watcher.repositories) || [];
    renderTagList("dir-list", configDirs, removeDir);
    renderTagList("repo-list", configRepos, removeRepo);

    $("input-schedule-time").value = (s.schedule && s.schedule.time) || "23:30";
    $("input-checkpoint-interval").value = (s.periodic_checkpoint && s.periodic_checkpoint.interval_hours) || 2;
    const provider = s.provider || "gemini";
    $("select-llm-provider").value = provider;
    $("input-model-name").value = (s[provider] && s[provider].model) || "gemini-3.7-flash";

    const exts = (w.file_watcher && w.file_watcher.extensions) || [];
    document.querySelectorAll("#ext-checkboxes input").forEach(cb => { cb.checked = exts.includes(cb.value); });

    const agent = w.agent_log_watcher || {};
    const browser = w.browser || {};
    $("toggle-claude-code").checked = agent.claude_code !== false;
    $("toggle-codex").checked = agent.codex !== false;
    $("toggle-antigravity").checked = agent.antigravity !== false;
    $("toggle-gemini").checked = browser.gemini !== false;
    $("toggle-chatgpt").checked = browser.chatgpt !== false;
    $("toggle-claude-web").checked = browser.claude_web !== false;
    $("toggle-manus").checked = browser.manus !== false;
    $("toggle-window-focus").checked = !(w.window_watcher && w.window_watcher.enabled === false);
  } catch (e) { console.error("config load failed", e); }
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
  cfg.watchers.agent_log_watcher.codex = $("toggle-codex").checked;
  cfg.watchers.agent_log_watcher.antigravity = $("toggle-antigravity").checked;

  cfg.watchers.browser = cfg.watchers.browser || {};
  cfg.watchers.browser.gemini = $("toggle-gemini").checked;
  cfg.watchers.browser.chatgpt = $("toggle-chatgpt").checked;
  cfg.watchers.browser.claude_web = $("toggle-claude-web").checked;
  cfg.watchers.browser.manus = $("toggle-manus").checked;

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

  const btn = $("btn-save-settings");
  const label = btn.textContent;
  btn.disabled = true; btn.textContent = "儲存中…";
  try {
    await postJSON("/api/v1/config", cfg);
    btn.textContent = "✓ 已套用";
    refreshStatus();
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
