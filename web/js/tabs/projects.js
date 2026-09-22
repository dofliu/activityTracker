// web/js/tabs/projects.js — 03 進行中工作：情報流、專案卡、快速動作、未結事項與 Focus Now。

import { getJSON, postJSON } from "../core/api.js";
import { $, esc } from "../core/dom.js";
import { t } from "../core/i18n.js";
import { state } from "../core/state.js";
import { showToast } from "../core/ui.js";
import { formatContextTime, loadSecretaryProposals } from "../tabs/assistant.js";
import { triggerCheckpoint } from "../tabs/summaries.js";

export async function refreshFeed() {
  try {
    const events = await getJSON(`/api/v1/events/recent?limit=60&event_type=${state.feed.activeFilter}`);
    if (state.feed.activeFilter === "all") state.feed.recentEvents = events;
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
export async function loadProjects(force) {
  try {
    state.projects.cache = await getJSON("/api/v1/projects/active");
    if (force || !state.feed.recentEvents.length) {
      try { state.feed.recentEvents = await getJSON("/api/v1/events/recent?limit=200&event_type=all"); } catch (e) {}
    }
    renderResume();
    renderProjects();
    $("projects-count").textContent = `${t("active_workstreams")} · ${state.projects.cache.length}`;
  } catch (e) {
    $("projects-list").innerHTML = `<div class="placeholder">${t("ph_loading_projects")}</div>`;
  }
}

export function statusLabel(p) {
  if (p.status === "active") return t("status_active");
  return t("status_idle", { days: p.idle_days });
}

// ---------------------------------------------------------------- Quick Actions & Toast
export async function runOpenAction(path, action = "explorer", url = null) {
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

export function renderActionGroup(p) {
  if (!p) return "";
  const ghUrl = p.github_url || (p.github && p.github.html_url);
  const path = p.local_path;
  const vsCodeUri = path ? "vscode://file/" + encodeURI(path.replace(/\\/g, "/")) : "";
  const folderUri = path ? "openfolder:///" + encodeURI(path.replace(/\\/g, "/")) : "";

  const folderBtn = path
    ? `<a class="action-btn" href="${esc(folderUri)}" data-act="folder" data-path="${esc(path)}" title="${state.ui.currentLang === 'zh-TW' ? '開啟本機資料夾 (' + esc(path) + ')' : 'Open Folder (' + esc(path) + ')'}">📁</a>`
    : `<button class="action-btn disabled" title="${state.ui.currentLang === 'zh-TW' ? '尚未定位到本機路徑' : 'No local path'}">📁</button>`;

  const vsCodeBtn = path
    ? `<a class="action-btn" href="${esc(vsCodeUri)}" title="${state.ui.currentLang === 'zh-TW' ? '在 VS Code 中開啟專案' : 'Open in VS Code'}">💻</a>`
    : `<button class="action-btn disabled" title="${state.ui.currentLang === 'zh-TW' ? '尚未定位到本機路徑' : 'No local path'}">💻</button>`;

  const ghBtn = ghUrl
    ? `<a class="action-btn" href="${esc(ghUrl)}" target="_blank" rel="noopener noreferrer" title="${state.ui.currentLang === 'zh-TW' ? '前往 GitHub 專案頁面' : 'Open on GitHub'}">🐙</a>`
    : `<button class="action-btn disabled" title="${state.ui.currentLang === 'zh-TW' ? '未綁定 GitHub 倉庫' : 'No GitHub repo'}">🐙</button>`;

  return `
    <div class="action-group" data-stop-propagation>
      ${folderBtn}
      ${vsCodeBtn}
      ${ghBtn}
    </div>`;
}

export function attachActionGroupListeners(parentEl) {
  // 動作列整塊吃掉點擊，不讓它冒泡到「展開這張專案卡」（D10 之前是字串裡的 onclick）
  parentEl.querySelectorAll("[data-stop-propagation]").forEach(group => {
    group.addEventListener("click", (ev) => ev.stopPropagation());
  });
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

export async function copyProjectHandoff(projectKey, displayName) {
  try {
    showToast(state.ui.currentLang === "zh-TW" ? "⏳ 正在提煉專案接續記憶..." : "⏳ Building context handoff...");
    const res = await getJSON(`/api/v1/projects/${encodeURIComponent(projectKey)}/handoff?turns=5`);
    if (res && res.markdown) {
      await navigator.clipboard.writeText(res.markdown);
      const name = displayName || res.display_name || projectKey;
      showToast(state.ui.currentLang === "zh-TW" ? `⚡ 已複製 [${name}] 接續 Prompt！可直接貼入任何 AI 開工` : `⚡ Copied [${name}] handoff prompt to clipboard!`);
    } else {
      showToast("⚠️ 無法生成接續 Prompt");
    }
  } catch (e) {
    showToast("⚠️ 複製失敗: " + e.message);
  }
}

export function renderResume() {
  // 「上次做到哪」現在住在 01 今日行動清單最上方（02 只留專案卡）。
  const box = $("today-resume");
  if (!box) return;
  const p = state.projects.cache[0];
  if (!p) {
    box.innerHTML = `<div class="placeholder">${t("ph_no_projects")}</div>`;
    return;
  }
  box.innerHTML = `
    <div style="min-width:0">
      <div class="today-resume-label">${esc(t("today_resume_label"))}</div>
      <div class="today-resume-title">${esc(p.display_name)}</div>
      <div class="today-resume-action">${esc(p.last_action_summary || "無紀錄")}</div>
      <div class="today-resume-meta">${esc(p.last_activity_at)} · ${esc(p.category || "")} · ${t("open_loop_count")} ${p.open_loops_count}</div>
    </div>
    <div class="today-resume-buttons">
      ${renderActionGroup(p)}
      <button class="btn" data-copy-handoff="${esc(p.project_key)}" data-name="${esc(p.display_name)}" style="background:var(--s2); border:1px solid var(--bd); color:var(--tx); font-weight:600; font-size:12px; padding:6px 12px; cursor:pointer;" title="${state.ui.currentLang === 'zh-TW' ? '一鍵複製結構化接續 Prompt 貼入 AI 開工' : 'Copy structured handoff prompt for AI'}">${t("btn_copy_handoff")}</button>
      <button class="btn btn-primary" data-resume="${esc(p.project_key)}">${state.ui.currentLang === "zh-TW" ? "接續 →" : "Resume →"}</button>
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
  if (btn) btn.addEventListener("click", () => focusProject(p.project_key));
}

// ---------------------------------------------------------------- 01 今天：早晨包摘要與預設排程

export function normalizePathKey(value) {
  return String(value || "").replace(/\\/g, "/").replace(/\/+$/, "").toLowerCase();
}

export async function loadRepoSnapshot() {
  try {
    state.projects.repoSnapshot = await getJSON("/api/v1/repos/sync-snapshot");
  } catch (e) {
    state.projects.repoSnapshot = null;
  }
  if (state.projects.cache.length) renderProjects();
}

export function repoSnapshotFor(project) {
  if (!state.projects.repoSnapshot || !state.projects.repoSnapshot.available) return null;
  const repos = state.projects.repoSnapshot.repositories || [];
  const pathKey = normalizePathKey(project.local_path);
  if (pathKey) {
    const byPath = repos.find(r => normalizePathKey(r.path) === pathKey);
    if (byPath) return byPath;
  }
  const byName = repos.filter(r => r.name === project.display_name || r.name === project.project_key);
  return byName.length === 1 ? byName[0] : null;  // 同名多個 clone 屬歧義，不亂配
}

export function projectChips(p) {
  const zh = state.ui.currentLang === "zh-TW";
  const chips = [];
  const repo = repoSnapshotFor(p);
  if (repo) {
    const map = {
      behind: [`↓${repo.behind ?? "?"} ${zh ? "待 pull" : "pull"}`, "pchip-behind"],
      ahead: [`↑${repo.ahead ?? "?"} ${zh ? "待 push" : "push"}`, "pchip-ahead"],
      diverged: [`↑${repo.ahead ?? "?"} ↓${repo.behind ?? "?"} ${zh ? "分歧" : "diverged"}`, "pchip-diverged"],
      synced: [zh ? "已同步" : "synced", "pchip-synced"],
    };
    const entry = map[repo.sync_state];
    if (entry) {
      const dirty = repo.clean === false ? (zh ? " · 未提交" : " · dirty") : "";
      const title = (zh ? "來自最近一次同步報告快照（cached remote-tracking ref）" : "From the latest sync report snapshot (cached remote-tracking ref)")
        + (repo.last_fetch_at ? ` · fetch ${String(repo.last_fetch_at).slice(0, 16)}` : "");
      chips.push(`<span class="pchip ${entry[1]}" title="${esc(title)}">${esc(entry[0])}${dirty}</span>`);
    }
  }
  const proposals = ((state.secretary.proposals || {}).proposals || []).filter(
    item => item.project_key === p.project_key || item.project_key === p.display_name
  );
  if (proposals.length) {
    chips.push(`<span class="pchip pchip-proposals" title="${esc(proposals.map(i => i.title).join(" / "))}">💡 ${proposals.length} ${zh ? "建議" : (proposals.length > 1 ? "suggestions" : "suggestion")}</span>`);
  }
  return chips.join("");
}


export function renderProjects() {
  const box = $("projects-list");
  if (!state.projects.cache.length) {
    box.innerHTML = `<div class="placeholder">${t("ph_no_projects")}</div>`;
    return;
  }

  const activeProjects = state.projects.cache.filter(p => (p.idle_days == null || p.idle_days <= 60));
  const idleProjects = state.projects.cache.filter(p => (p.idle_days != null && p.idle_days > 60));

  // 決定渲染之專案清單
  let listToRender = state.projects.cache;
  if (!state.projects.showAll) {
    if (state.projects.expandedKey && !activeProjects.some(p => p.project_key === state.projects.expandedKey)) {
      const exp = idleProjects.find(p => p.project_key === state.projects.expandedKey);
      listToRender = exp ? [...activeProjects, exp] : activeProjects;
    } else {
      listToRender = activeProjects;
    }
  }

  const pCountEl = $("projects-count");
  if (pCountEl) {
    pCountEl.textContent = `${t("active_workstreams")} · ${state.projects.showAll ? state.projects.cache.length : activeProjects.length + ' / ' + state.projects.cache.length}`;
  }

  let projectsHtml = listToRender.map(p => {
    const bar = p.status === "active" ? "var(--orange)"
      : (p.open_loops_count > 0 ? "var(--warn)" : "var(--bd)");
    const loopColor = p.open_loops_count >= 3 ? "var(--orange)"
      : (p.open_loops_count > 0 ? "var(--tx)" : "var(--mu)");
    const open = state.projects.expandedKey === p.project_key;

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
            <div class="pmeta">${esc(p.category || "")} · ${statusLabel(p)}${projectChips(p)}</div>
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
          ${state.projects.showAll
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
      state.projects.showAll = !state.projects.showAll;
      renderProjects();
    });
  }

  box.querySelectorAll(".pitem").forEach(item => {
    item.querySelector(".prow").addEventListener("click", () => {
      const key = item.dataset.key;
      expandProject(state.projects.expandedKey === key ? null : key);
    });
  });

  if (state.projects.expandedKey) renderProjectDetail(state.projects.expandedKey);
}

export function expandProject(key, scroll) {
  if (key) {
    // 若要展開的專案屬於 60 天以上閒置專案，自動切換至顯示全部
    const isIdle = state.projects.cache.some(p => p.project_key === key && p.idle_days > 60);
    if (isIdle) state.projects.showAll = true;
  }
  state.projects.expandedKey = key;
  renderProjects();
  if (key && scroll) {
    setTimeout(() => {
      const el = document.querySelector(`.pitem[data-key="${CSS.escape(key)}"]`);
      if (el) window.scrollTo({ top: el.getBoundingClientRect().top + window.scrollY - 160, behavior: "smooth" });
    }, 50);
  }
}

export async function renderProjectDetail(key) {
  const item = document.querySelector(`.pitem[data-key="${CSS.escape(key)}"]`);
  if (!item) return;
  const slot = item.querySelector(".pdetail-slot");
  slot.innerHTML = `<div class="pdetail"><div class="placeholder">${t("ph_loading_projects")}</div></div>`;

  const proj = state.projects.cache.find(p => p.project_key === key);
  let events = [];
  try {
    events = await getJSON(`/api/v1/events/recent?limit=30&project=${encodeURIComponent(key)}`);
  } catch (e) {
    events = state.feed.recentEvents.filter(e => (e.project || "") === key || (proj && (e.project || "") === proj.display_name));
  }

  let loops = [];
  try { loops = await getJSON(`/api/v1/open-loops?project=${encodeURIComponent(key)}`); } catch (e) {}

  // 近期工作階段（原本是 02 的獨立面板；現在只在該專案展開時顯示，減少重複）
  let sessions = [];
  try {
    const payload = await getJSON(`/api/v1/context/sessions?project=${encodeURIComponent(key)}&limit=5`);
    sessions = payload.sessions || [];
  } catch (e) {}
  const sessionsHtml = sessions.length
    ? sessions.map(session => {
        const counts = session.event_counts || {};
        const chips = [
          counts.ai_turn ? `AI ${counts.ai_turn}` : "",
          counts.git_commit ? `GIT ${counts.git_commit}` : "",
          counts.file_activity ? `FILE ${counts.file_activity}` : "",
        ].filter(Boolean).map(label => `<span class="context-memory-chip">${esc(label)}</span>`).join("");
        return `<article class="context-session">
          <div class="context-session-top"><span class="context-session-time">${esc(formatContextTime(session.started_at || session.ended_at))} → ${esc(formatContextTime(session.ended_at))}</span></div>
          <div class="context-session-headline">${esc(session.headline || session.narrative || "—")}</div>
          <div class="context-session-meta">${chips}</div>
        </article>`;
      }).join("")
    : `<div class="placeholder" style="padding:0">${state.ui.currentLang === "zh-TW" ? "近 72 小時沒有可歸戶的工作階段。" : "No canonical work session in the last 72 hours."}</div>`;

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
          <button class="btn" data-detail-handoff="${esc(proj.project_key)}" data-name="${esc(proj.display_name)}" style="background:var(--s2); border:1px solid var(--bd); color:var(--tx); font-weight:600; font-size:11.5px; padding:4px 10px; cursor:pointer;" title="${state.ui.currentLang === 'zh-TW' ? '一鍵複製結構化接續 Prompt 貼入 AI 開工' : 'Copy structured handoff prompt for AI'}">${t("btn_copy_handoff")}</button>
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
      <div class="pdetail-sessions">
        <span class="mono-label">${t("sec_sessions")}</span>
        ${sessionsHtml}
        <div class="context-memory-boundary">${state.ui.currentLang === "zh-TW" ? "依專案與事件間隔推定，不代表實際工時。" : "Inferred from event gaps; not actual working time."}</div>
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


// ---------------------------------------------------------------- focus carousel (observed open loops + proposal-only suggestions)
export async function loadOpenLoops() {
  try {
    state.projects.loops = await getJSON("/api/v1/open-loops");
  } catch (e) {
    state.projects.loops = [];
  }
  renderOpenLoops();
}

export function focusDateMs(value) {
  const parsed = Date.parse(String(value || "").replace(" ", "T"));
  return Number.isFinite(parsed) ? parsed : 0;
}

export function buildFocusCarouselItems() {
  const now = Date.now();
  const candidates = [];

  for (const loop of state.projects.loops) {
    const lastSeen = focusDateMs(loop.last_seen_at || loop.created_at);
    const ageDays = lastSeen ? Math.max(0, (now - lastSeen) / 86400000) : 30;
    // Open Loop 沒有人工 priority 欄位，因此只用可檢查的來源信心與時間排序，
    // 不把這個畫面偽裝成 AI 已判定的事實重要度。
    const score = 0.42 + Math.min(0.16, ageDays / 180) + Math.min(0.10, Number(loop.confidence || 0) * 0.10);
    candidates.push({
      key: `open-loop:${loop.id}`,
      kind: "open_loop",
      priority: "observed",
      score,
      project_key: loop.project_key,
      title: loop.title,
      detail: loop.created_at || "",
      updated_at: lastSeen,
      loop_id: loop.id,
    });
  }

  for (const proposal of state.secretary.proposals?.proposals || []) {
    candidates.push({
      key: `proposal:${proposal.proposal_id}`,
      kind: "proposal",
      priority: proposal.priority || "medium",
      score: Number(proposal.score || 0),
      project_key: proposal.project_key || "OmniContext",
      title: proposal.detail || proposal.title,
      detail: proposal.suggested_action || proposal.reason || "",
      updated_at: 0,
      proposal_type: proposal.proposal_type,
      subject_ref: proposal.subject_ref || "",
    });
  }

  candidates.sort((left, right) => (
    right.score - left.score || right.updated_at - left.updated_at || left.key.localeCompare(right.key)
  ));

  // 同一專案最多兩張，避免單一大量待辦或 repository 佔滿整個焦點輪播。
  const projectCounts = new Map();
  const selected = [];
  for (const item of candidates) {
    const count = projectCounts.get(item.project_key) || 0;
    if (count >= 2) continue;
    projectCounts.set(item.project_key, count + 1);
    selected.push(item);
    if (selected.length === 5) break;
  }
  return selected;
}

export function renderOpenLoops() {
  renderFocusCarousel();
}

export function renderFocusCarousel() {
  const box = $("open-loops-list");
  const tally = $("loop-tally");
  if (!box || !tally) return;

  state.focus.items = buildFocusCarouselItems();
  tally.textContent = `${state.focus.items.length}/5 · ${state.projects.loops.length}`;
  if (!state.focus.items.length) {
    box.innerHTML = `<div class="placeholder">${t("ph_no_loops")}</div>`;
    return;
  }
  state.focus.index = Math.min(state.focus.index, state.focus.items.length - 1);
  const item = state.focus.items[state.focus.index];
  const observed = item.kind === "open_loop";
  const source = observed ? t("focus_observed") : t("focus_proposal");
  const priority = observed ? source : String(item.priority || "medium").toUpperCase();
  const action = observed
    ? `<button class="focus-action focus-resolve-btn" data-resolve="${item.loop_id}">${t("focus_resolve")}</button>`
    : `<button class="focus-action focus-secondary-action" data-focus-snooze="1">${t("focus_snooze")}</button>`;
  const dots = state.focus.items.map((candidate, index) => `
    <button class="focus-dot ${index === state.focus.index ? "active" : ""}" data-focus-index="${index}" aria-label="${esc(t("focus_count", {current: index + 1, total: state.focus.items.length, open: state.projects.loops.length}))}" aria-current="${index === state.focus.index ? "true" : "false"}"></button>`
  ).join("");

  box.innerHTML = `
    <article class="focus-card ${observed ? "focus-observed" : "focus-proposal"}" data-id="${observed ? item.loop_id : ""}">
      <div class="focus-card-top">
        <span class="focus-source">${esc(source)}</span>
        <span class="focus-priority ${esc(String(item.priority || "medium").toLowerCase())}">${esc(priority)}</span>
      </div>
      <div class="focus-project">${esc(item.project_key)}</div>
      <h2 class="focus-title" title="${esc(item.title)}">${esc(item.title)}</h2>
      <div class="focus-detail">${esc(item.detail || (state.ui.currentLang === "zh-TW" ? "可回到專案查看來源與下一步。" : "Open the project to review its source and next step."))}</div>
      <div class="focus-card-bottom">
        <div class="focus-controls">
          <button class="focus-nav" data-focus-prev aria-label="${esc(t("focus_previous"))}">←</button>
          <div class="focus-dots">${dots}</div>
          <button class="focus-nav" data-focus-next aria-label="${esc(t("focus_next"))}">→</button>
          <button class="focus-nav focus-toggle" data-focus-toggle aria-label="${esc(state.focus.userPaused ? t("focus_play") : t("focus_pause"))}" aria-pressed="${state.focus.userPaused}">${state.focus.userPaused ? "▶" : "Ⅱ"}</button>
        </div>
        <div class="focus-actions">
          <button class="focus-action focus-primary-action" data-focus-project="${esc(item.project_key)}">${t("focus_view_project")}</button>
          ${action}
        </div>
        <div class="focus-boundary">${t("focus_boundary")} · ${t("focus_count", {current: state.focus.index + 1, total: state.focus.items.length, open: state.projects.loops.length})}</div>
      </div>
    </article>`;
}

export function focusProject(projectKey) {
  if (!projectKey) return;
  const tabBtn = document.querySelector('.tabs button[data-tab="tab-projects"]');
  if (tabBtn) tabBtn.click();
  const isIdle = state.projects.cache.some(project => project.project_key === projectKey && project.idle_days > 60);
  if (isIdle) state.projects.showAll = true;
  expandProject(projectKey, true);
  showToast(state.ui.currentLang === "zh-TW" ? `🎯 已定位並展開專案: ${projectKey}` : `🎯 Focused project: ${projectKey}`);
}

export function initFocusCarousel() {
  const box = $("open-loops-list");
  if (!box) return;

  box.addEventListener("mouseenter", () => { state.focus.pointerPaused = true; });
  box.addEventListener("mouseleave", () => { state.focus.pointerPaused = false; });
  box.addEventListener("focusin", () => { state.focus.pointerPaused = true; });
  box.addEventListener("focusout", () => { state.focus.pointerPaused = false; });
  box.addEventListener("click", event => {
    const target = event.target.closest("button");
    if (!target) return;
    if (target.dataset.focusIndex !== undefined) {
      state.focus.index = Number(target.dataset.focusIndex);
      renderFocusCarousel();
    } else if (target.hasAttribute("data-focus-prev")) {
      state.focus.index = (state.focus.index - 1 + state.focus.items.length) % state.focus.items.length;
      renderFocusCarousel();
    } else if (target.hasAttribute("data-focus-next")) {
      state.focus.index = (state.focus.index + 1) % state.focus.items.length;
      renderFocusCarousel();
    } else if (target.hasAttribute("data-focus-toggle")) {
      state.focus.userPaused = !state.focus.userPaused;
      renderFocusCarousel();
    } else if (target.dataset.focusProject) {
      focusProject(target.dataset.focusProject);
    } else if (target.dataset.resolve) {
      resolveLoop(target.closest(".focus-card"));
    } else if (target.dataset.focusSnooze) {
      const item = state.focus.items[state.focus.index];
      if (item) window.snoozeProposal(item.proposal_type, item.project_key, item.subject_ref, 7);
    }
  });

  document.addEventListener("visibilitychange", () => {
    if (!document.hidden) renderFocusCarousel();
  });
  if (!window.matchMedia("(prefers-reduced-motion: reduce)").matches) {
    state.focus.timer = window.setInterval(() => {
      if (document.hidden || state.focus.userPaused || state.focus.pointerPaused || state.focus.items.length < 2) return;
      state.focus.index = (state.focus.index + 1) % state.focus.items.length;
      renderFocusCarousel();
    }, 9000);
  }
}

export async function resolveLoop(el) {
  const id = el?.dataset.id;
  if (!id || el.classList.contains("done")) return;
  el.classList.add("done");
  const resBtn = el.querySelector("[data-resolve]");
  if (resBtn) {
    resBtn.style.background = "var(--ok)";
    resBtn.style.borderColor = "var(--ok)";
    resBtn.style.color = "#fff";
  }
  try {
    await postJSON(`/api/v1/open-loops/${id}/resolve`);
    state.projects.loops = state.projects.loops.filter(loop => String(loop.id) !== String(id));
    showToast(state.ui.currentLang === "zh-TW" ? "⚡ 未結事項已標記為已結案！" : "⚡ Marked open loop as resolved!");
    setTimeout(() => { renderOpenLoops(); loadSecretaryProposals(); loadProjects(); }, 550);
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
