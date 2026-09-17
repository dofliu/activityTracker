// web/js/tabs/repos.js — 04 Git 同步中心：同步狀態、全覽與批次、repo onboarding。

import { getJSON, postJSON } from "../core/api.js";
import { $, esc } from "../core/dom.js";
import { state } from "../core/state.js";
import { registerActions } from "../core/ui.js";
import { loadProjects } from "../tabs/projects.js";

export function repoSyncLabels() {
  const zh = state.currentLang === "zh-TW";
  return zh ? {
    noRepo: "尚未在監控設定中加入 Git repository root。",
    cached: "ahead / behind 比較的是本機已保存的 remote-tracking refs；按 Fetch 才會更新遠端參照。",
    truncated: "清單已達安全上限，請縮小 repositories 設定範圍或調高 config 上限。",
    clean: "CLEAN",
    dirty: "WORKTREE CHANGED",
    untrackedOnly: "untracked only (does not block pull/push)",
    untrackedOnly: "只有 untracked（不影響 pull／push）",
    synced: "已同步",
    ahead: "待 Push",
    behind: "待 Pull",
    diverged: "已分歧",
    no_upstream: "未設定 upstream",
    detached_head: "Detached HEAD",
    upstream_unavailable: "Upstream 不可用",
    unavailable: "無法讀取",
    unknown: "未知狀態",
    fetch: "Fetch",
    pull: "Pull (FF only)",
    commit: "Commit staged",
    push: "Push",
    ovLoading: "正在讀取全部 repo 的本機 Git 狀態（不連網）…",
    ovEmpty: "沒有符合此篩選的 repo。",
    ovColumns: ["Repo", "branch → upstream", "狀態", "worktree", "上次 fetch", "動作"],
    ovFilters: { all: "全部", behind: "需 pull", ahead: "需 push", diverged: "分歧", dirty: "worktree 未提交", no_upstream: "無 upstream", synced: "已同步" },
    ovNeverFetched: "從未",
    confirmFetchAll: "對全部 repo 執行 fetch --prune？只更新遠端參照，不改任何 worktree、branch 或遠端。",
    fetchAllDone: (c) => `全部 Fetch 完成：成功 ${c.success}、失敗 ${c.failed}、跳過 ${c.skipped}`,
    batchNone: (a) => `目前沒有符合 ${a === "push" ? "Push" : "Pull (FF only)"} 前置條件的 repo。`,
    batchConfirm: (a, names, excluded, skipped = []) => `將對以下 ${names.length} 個 repo 執行 ${a === "push" ? "Push（不 force）" : "fast-forward Pull"}：\n\n${names.join("\n")}\n\n另有 ${excluded} 個 repo 因前置條件不符會被跳過${skipped.length ? `，例如：\n${skipped.join("\n")}` : "。"}\n\n執行時每個 repo 仍會重檢一次。繼續？`,
    batchDone: (a, c) => `批次 ${a === "push" ? "Push" : "Pull"} 完成：成功 ${c.success}、跳過 ${c.skipped}、失敗 ${c.failed}`,
    pushDisabled: "批次 Push 未啟用（config: repository_sync.batch.allow_push）；單一 repo 的 Push 仍可逐一執行。",
    staged: "staged",
    unstaged: "unstaged",
    untracked: "untracked",
    conflicts: "conflicts",
    confirmFetch: "只更新此 repository 的遠端參照（不改變 worktree）？",
    confirmPull: "只允許 fast-forward Pull。worktree 將前進到遠端既有 commit，繼續？",
    confirmPush: "推送此 repository 目前 branch 的既有 commits（不會 force push），繼續？",
    commitPrompt: "輸入 commit message（只會提交已 staged 的檔案）：",
    confirmCommit: "確認建立 staged-only commit？\n\n",
    working: "執行中…",
    success: "已完成",
    failed: "未執行：",
    actionHint: "每個動作都會在執行前重新檢查 branch、upstream、worktree 與分歧狀態。",
  } : {
    noRepo: "No Git repository root is configured for monitoring.",
    cached: "Ahead / behind uses locally cached remote-tracking refs. Fetch refreshes those refs.",
    truncated: "The safe list limit was reached. Narrow the configured roots or raise the config limit.",
    clean: "CLEAN",
    dirty: "WORKTREE CHANGED",
    synced: "Synced",
    ahead: "Push pending",
    behind: "Pull pending",
    diverged: "Diverged",
    no_upstream: "No upstream",
    detached_head: "Detached HEAD",
    upstream_unavailable: "Upstream unavailable",
    unavailable: "Unavailable",
    unknown: "Unknown",
    fetch: "Fetch",
    pull: "Pull (FF only)",
    commit: "Commit staged",
    push: "Push",
    ovLoading: "Reading local Git status for every repository (offline)…",
    ovEmpty: "No repository matches this filter.",
    ovColumns: ["Repo", "branch → upstream", "State", "worktree", "last fetch", "Actions"],
    ovFilters: { all: "All", behind: "needs pull", ahead: "needs push", diverged: "diverged", dirty: "dirty worktree", no_upstream: "no upstream", synced: "synced" },
    ovNeverFetched: "never",
    confirmFetchAll: "Run fetch --prune on every repository? Only remote-tracking refs change; no worktree, branch or remote is modified.",
    fetchAllDone: (c) => `Fetch all done: ${c.success} ok, ${c.failed} failed, ${c.skipped} skipped`,
    batchNone: (a) => `No repository currently meets the preconditions for ${a === "push" ? "Push" : "Pull (FF only)"}.`,
    batchConfirm: (a, names, excluded, skipped = []) => `Run ${a === "push" ? "Push (never force)" : "fast-forward Pull"} on these ${names.length} repositories:\n\n${names.join("\n")}\n\n${excluded} other repositories are excluded by preconditions${skipped.length ? `, for example:\n${skipped.join("\n")}` : "."}\n\nEach repository is rechecked before it runs. Continue?`,
    batchDone: (a, c) => `Batch ${a === "push" ? "Push" : "Pull"} done: ${c.success} ok, ${c.skipped} skipped, ${c.failed} failed`,
    pushDisabled: "Batch push is disabled (config: repository_sync.batch.allow_push); single-repo Push still works.",
    staged: "staged",
    unstaged: "unstaged",
    untracked: "untracked",
    conflicts: "conflicts",
    confirmFetch: "Refresh this repository's remote refs only (does not change the worktree)?",
    confirmPull: "Only fast-forward Pull is allowed. The worktree will advance to existing remote commits. Continue?",
    confirmPush: "Push the current branch's existing commits (never force push)?",
    commitPrompt: "Enter a commit message (only explicitly staged files will be committed):",
    confirmCommit: "Create a staged-only commit?\n\n",
    working: "Working…",
    success: "Completed",
    failed: "Not executed: ",
    actionHint: "Every action rechecks branch, upstream, worktree, and divergence before it runs.",
  };
}

export function repoSyncStateText(repo, labels) {
  const state = repo.sync_state || "unknown";
  const base = labels[state] || labels.unknown;
  if (state === "ahead" && Number.isInteger(repo.ahead)) return `${base} ↑${repo.ahead}`;
  if (state === "behind" && Number.isInteger(repo.behind)) return `${base} ↓${repo.behind}`;
  if (state === "diverged") return `${base} ↑${repo.ahead ?? "?"} ↓${repo.behind ?? "?"}`;
  return base;
}

// 這個 repo 現在有事情要做、但按鈕是灰的——把後端給的具體理由直接顯示出來。
// （使用者回報：repo 明明落後遠端卻沒得按 pull，灰按鈕沒有任何可見說明。）
export function repoBlockedReason(repo) {
  const actions = repo.actions || {};
  const state = repo.sync_state;
  const pull = actions.pull_ff_only || {};
  const push = actions.push || {};
  if (state === "behind" || state === "diverged") return pull.allowed ? "" : (pull.reason || "");
  if (state === "ahead") return push.allowed ? "" : (push.reason || "");
  if (state === "no_upstream" || state === "detached_head" || state === "upstream_unavailable") {
    return pull.reason || "";
  }
  return "";
}

export function repoSyncActionButton(repo, action, label) {
  const actionState = (repo.actions || {})[action] || {};
  const allowed = actionState.allowed === true;
  const hint = allowed ? label : (actionState.reason || "Unavailable");
  return `<button class="btn btn-ghost btn-sm repo-sync-action ${allowed ? "" : "is-disabled"}"
      data-repo-id="${esc(repo.repo_id)}" data-repo-action="${esc(action)}"
      title="${esc(hint)}" ${allowed ? "" : "disabled"}>${esc(label)}</button>`;
}

export function renderRepositorySyncStatus() {
  const list = $("repo-sync-list");
  const summary = $("repo-sync-summary");
  if (!list || !summary) return;
  const labels = repoSyncLabels();
  if (!state.repositorySyncCache.length) {
    summary.textContent = labels.noRepo;
    list.innerHTML = "";
    return;
  }

  const needsAttention = state.repositorySyncCache.filter(repo =>
    repo.sync_state !== "synced" || !repo.clean
  ).length;
  summary.innerHTML = `<strong>${state.repositorySyncCache.length}</strong> repositories · <strong>${needsAttention}</strong> ${state.currentLang === "zh-TW" ? "項需要處理" : "need attention"}<br><span>${esc(labels.cached)}</span>`;
  list.innerHTML = state.repositorySyncCache.map(repo => {
    const worktree = repo.worktree || {};
    const counts = [
      worktree.staged_files ? `${worktree.staged_files} ${labels.staged}` : "",
      worktree.unstaged_files ? `${worktree.unstaged_files} ${labels.unstaged}` : "",
      worktree.untracked_files ? `${worktree.untracked_files} ${labels.untracked}` : "",
      worktree.conflicted_files ? `${worktree.conflicted_files} ${labels.conflicts}` : "",
    ].filter(Boolean);
    const stateClass = `state-${String(repo.sync_state || "unknown").replace(/[^a-z_]/g, "")}`;
    const branch = repo.branch || "—";
    const upstream = repo.upstream || "—";
    const statusHint = repo.error || (repo.operation_in_progress ? `Git: ${repo.operation_in_progress}` : "");
    return `<article class="repo-sync-row ${stateClass}">
      <div class="repo-sync-main">
        <div class="repo-sync-name">${esc(repo.name)} <span class="repo-sync-state">${esc(repoSyncStateText(repo, labels))}</span></div>
        <div class="repo-sync-meta"><code>${esc(branch)}</code> → <code>${esc(upstream)}</code></div>
        <div class="repo-sync-path" title="${esc(repo.path || "")}">${esc(repo.path || "")}</div>
        <div class="repo-sync-worktree ${repo.tracked_clean === false ? "is-dirty" : "is-clean"}">${repo.clean ? labels.clean : `${repo.tracked_clean === false ? labels.dirty : labels.untrackedOnly}${counts.length ? ` · ${esc(counts.join(" · "))}` : ""}`}</div>
        ${statusHint ? `<div class="repo-sync-warning">${esc(statusHint)}</div>` : ""}
        ${repoBlockedReason(repo) ? `<div class="repo-sync-blocked">⛔ ${esc(repoBlockedReason(repo))}</div>` : ""}
      </div>
      <div class="repo-sync-actions">
        ${repoSyncActionButton(repo, "fetch", labels.fetch)}
        ${repoSyncActionButton(repo, "pull_ff_only", labels.pull)}
        ${repoSyncActionButton(repo, "commit_staged", labels.commit)}
        ${repoSyncActionButton(repo, "push", labels.push)}
      </div>
    </article>`;
  }).join("");
}

export async function loadRepositorySyncStatus() {
  const summary = $("repo-sync-summary");
  try {
    const data = await getJSON("/api/v1/repos/sync-status");
    state.repositorySyncCache = Array.isArray(data.repositories) ? data.repositories : [];
    renderRepositorySyncStatus();
    if (data.truncated && summary) {
      summary.insertAdjacentHTML("beforeend", `<br><span class="repo-sync-warning">${esc(repoSyncLabels().truncated)}</span>`);
    }
  } catch (e) {
    if (summary) summary.textContent = state.currentLang === "zh-TW" ? "無法讀取本機 Git 狀態。" : "Unable to read local Git status.";
  }
}

export async function runRepositorySyncAction(repoId, action) {
  const labels = repoSyncLabels();
  const result = $("repo-sync-result");
  let commitMessage = null;
  let confirmation = labels.confirmFetch;
  if (action === "pull_ff_only") confirmation = labels.confirmPull;
  if (action === "push") confirmation = labels.confirmPush;
  if (action === "commit_staged") {
    commitMessage = window.prompt(labels.commitPrompt, "");
    if (commitMessage === null || !commitMessage.trim()) return;
    confirmation = labels.confirmCommit + commitMessage.trim();
  }
  if (!window.confirm(confirmation)) return;

  if (result) result.textContent = labels.working;
  try {
    const receipt = await postJSON("/api/v1/repos/sync-action", {
      repo_id: repoId,
      action,
      confirmation: "confirmed",
      commit_message: commitMessage,
    });
    if (result) result.textContent = `${labels.success} · ${receipt.repo_name} · ${action}`;
    await loadRepositorySyncStatus();
    loadProjects(true);
  } catch (e) {
    if (result) result.textContent = `${labels.failed}${e.message}`;
    await loadRepositorySyncStatus();
  }
}

// ------------------------------------------------ ADR-011 Addendum B: overview + batch

export function repoOverviewMatches(repo, filter) {
  switch (filter) {
    case "behind": return repo.sync_state === "behind";
    case "ahead": return repo.sync_state === "ahead";
    case "diverged": return repo.sync_state === "diverged";
    case "dirty": return repo.clean === false;
    case "no_upstream": return ["no_upstream", "detached_head", "upstream_unavailable"].includes(repo.sync_state);
    case "synced": return repo.sync_state === "synced";
    default: return true;
  }
}

export function formatFetchTime(value, labels) {
  if (!value) return labels.ovNeverFetched;
  const d = new Date(value);
  if (Number.isNaN(d.getTime())) return String(value).slice(0, 16);
  return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}-${String(d.getDate()).padStart(2, "0")} ${String(d.getHours()).padStart(2, "0")}:${String(d.getMinutes()).padStart(2, "0")}`;
}

export function renderRepoOverview() {
  const table = $("repo-overview-table");
  const filters = $("repo-overview-filters");
  if (!table || !filters || state.repoOverviewCache === null) return;
  const labels = repoSyncLabels();
  const pushBtn = $("btn-repo-batch-push");
  if (pushBtn) {
    pushBtn.disabled = !state.repoOverviewBatch.push;
    pushBtn.title = state.repoOverviewBatch.push ? "" : labels.pushDisabled;
  }
  filters.hidden = false;
  filters.innerHTML = Object.entries(labels.ovFilters).map(([key, text]) => {
    const count = state.repoOverviewCache.filter(r => repoOverviewMatches(r, key)).length;
    return `<button type="button" class="repo-overview-chip ${key === state.repoOverviewFilter ? "is-active" : ""}" data-filter="${key}">${esc(text)} ${count}</button>`;
  }).join("");
  const rows = state.repoOverviewCache.filter(r => repoOverviewMatches(r, state.repoOverviewFilter));
  if (!rows.length) {
    table.innerHTML = `<div class="placeholder" style="padding:10px;">${esc(labels.ovEmpty)}</div>`;
    return;
  }
  const worktreeText = (repo) => {
    const w = repo.worktree || {};
    const parts = [
      w.staged_files ? `${w.staged_files} ${labels.staged}` : "",
      w.unstaged_files ? `${w.unstaged_files} ${labels.unstaged}` : "",
      w.untracked_files ? `${w.untracked_files} ${labels.untracked}` : "",
      w.conflicted_files ? `${w.conflicted_files} ${labels.conflicts}` : "",
    ].filter(Boolean);
    if (repo.clean) return `<span class="is-clean">${esc(labels.clean)}</span>`;
    const cls = repo.tracked_clean === false ? "is-dirty" : "is-clean";
    return `<span class="${cls}">${esc(parts.join(" · ") || labels.dirty)}</span>`;
  };
  table.innerHTML = `<table><thead><tr>${labels.ovColumns.map(c => `<th>${esc(c)}</th>`).join("")}</tr></thead><tbody>${rows.map(repo => {
    const stateClass = `state-${String(repo.sync_state || "unknown").replace(/[^a-z_]/g, "")}`;
    return `<tr class="${stateClass}">
      <td class="repo-overview-name" title="${esc(repo.path || "")}">${esc(repo.name)}</td>
      <td><code>${esc(repo.branch || "—")}</code> → <code>${esc(repo.upstream || "—")}</code></td>
      <td>${esc(repoSyncStateText(repo, labels))}${repo.error ? `<div class="repo-sync-warning">${esc(repo.error)}</div>` : ""}${repoBlockedReason(repo) ? `<div class="repo-sync-blocked">⛔ ${esc(repoBlockedReason(repo))}</div>` : ""}</td>
      <td>${worktreeText(repo)}</td>
      <td>${esc(formatFetchTime(repo.last_fetch_at, labels))}</td>
      <td class="repo-overview-actions">${repoSyncActionButton(repo, "fetch", labels.fetch)} ${repoSyncActionButton(repo, "pull_ff_only", labels.pull)} ${repoSyncActionButton(repo, "push", labels.push)}</td>
    </tr>`;
  }).join("")}</tbody></table>`;
}

export async function loadRepoOverview() {
  const table = $("repo-overview-table");
  const result = $("repo-overview-result");
  const labels = repoSyncLabels();
  if (table) table.innerHTML = `<div class="placeholder" style="padding:10px;">${esc(labels.ovLoading)}</div>`;
  try {
    const data = await getJSON("/api/v1/repos/sync-status?scope=all");
    state.repoOverviewCache = Array.isArray(data.repositories) ? data.repositories : [];
    state.repoOverviewBatch = data.batch || state.repoOverviewBatch;
    renderRepoOverview();
    if (result) {
      const s = data.summary || {};
      result.textContent = `${data.displayed_count}/${data.repository_count} repositories · ${labels.ovFilters.behind} ${s.behind || 0} · ${labels.ovFilters.ahead} ${s.ahead || 0} · ${labels.ovFilters.diverged} ${s.diverged || 0} · ${labels.ovFilters.dirty} ${s.dirty || 0}${data.truncated ? ` · ${labels.truncated}` : ""}`;
    }
  } catch (e) {
    if (table) table.innerHTML = `<div class="placeholder" style="padding:10px;">${esc(labels.failed)}${esc(e.message)}</div>`;
  }
}

export async function runRepoFetchAll() {
  const labels = repoSyncLabels();
  const result = $("repo-overview-result");
  if (!window.confirm(labels.confirmFetchAll)) return;
  if (result) result.textContent = labels.working;
  try {
    const receipt = await postJSON("/api/v1/repos/sync-fetch-all", { confirmation: "confirmed" });
    const failed = (receipt.results || []).filter(r => r.status !== "success");
    if (result) result.textContent = labels.fetchAllDone(receipt.counts || {}) + (failed.length ? ` · ${failed.map(r => `${r.repo_name}: ${r.reason || r.status}`).slice(0, 5).join(" · ")}` : "");
    await loadRepoOverview();
    loadRepositorySyncStatus();
  } catch (e) {
    if (result) result.textContent = `${labels.failed}${e.message}`;
  }
}

export async function runRepoBatch(action) {
  const labels = repoSyncLabels();
  const result = $("repo-overview-result");
  if (result) result.textContent = labels.working;
  try {
    const plan = await getJSON(`/api/v1/repos/sync-batch-plan?action=${encodeURIComponent(action)}`);
    const eligible = plan.eligible || [];
    if (!eligible.length) {
      const why = (plan.excluded || [])
        .filter(r => r.sync_state !== "synced")
        .slice(0, 3)
        .map(r => `${r.name}: ${r.reason || "—"}`);
      if (result) result.textContent = labels.batchNone(action) + (why.length ? ` · ${why.join(" · ")}` : "");
      return;
    }
    const names = eligible.map(r => `• ${r.name} (${r.branch || "—"}${action === "push" ? ` ↑${r.ahead ?? "?"}` : ` ↓${r.behind ?? "?"}`})`);
    const shown = names.length > 20 ? [...names.slice(0, 20), `… +${names.length - 20}`] : names;
    // 被排除的 repo 也要說明為什麼，否則「我的專案怎麼不在清單裡」無從查起。
    const skipped = (plan.excluded || [])
      .filter(r => r.sync_state !== "synced")
      .slice(0, 8)
      .map(r => `• ${r.name}: ${r.reason || "—"}`);
    if (!window.confirm(labels.batchConfirm(action, shown, plan.excluded_count || 0, skipped))) {
      if (result) result.textContent = "";
      return;
    }
    const receipt = await postJSON("/api/v1/repos/sync-batch", {
      action, confirmation: "confirmed", repo_ids: eligible.map(r => r.repo_id),
    });
    const problems = (receipt.results || []).filter(r => r.status !== "success");
    if (result) result.textContent = labels.batchDone(action, receipt.counts || {}) + (problems.length ? ` · ${problems.map(r => `${r.repo_name || r.repo_id}: ${r.reason || r.status}`).slice(0, 5).join(" · ")}` : "");
    await loadRepoOverview();
    loadRepositorySyncStatus();
    loadProjects(true);
  } catch (e) {
    if (result) result.textContent = `${labels.failed}${e.message}`;
  }
}

export function initRepositorySyncSection() {
  registerActions({
    "onboarding-init": (d) => onboardingInit(d.folderId, d.name),
    "onboarding-attach": (d) => onboardingAttach(d.repoId, d.name),
    "onboarding-create": (d) => onboardingCreate(d.repoId, d.name),
    "onboarding-clone": (d) => onboardingClone(d.fullName),
  });
  const refresh = $("btn-repo-sync-refresh");
  if (refresh) refresh.addEventListener("click", () => loadRepositorySyncStatus());
  const list = $("repo-sync-list");
  if (list) list.addEventListener("click", (event) => {
    const button = event.target.closest(".repo-sync-action");
    if (!button || button.disabled) return;
    runRepositorySyncAction(button.dataset.repoId, button.dataset.repoAction);
  });
  const overview = $("repo-overview-table");
  if (overview) overview.addEventListener("click", async (event) => {
    const button = event.target.closest(".repo-sync-action");
    if (!button || button.disabled) return;
    await runRepositorySyncAction(button.dataset.repoId, button.dataset.repoAction);
    loadRepoOverview();
  });
  const filters = $("repo-overview-filters");
  if (filters) filters.addEventListener("click", (event) => {
    const chip = event.target.closest(".repo-overview-chip");
    if (!chip) return;
    state.repoOverviewFilter = chip.dataset.filter || "all";
    renderRepoOverview();
  });
  const load = $("btn-repo-overview-load");
  if (load) load.addEventListener("click", loadRepoOverview);
  const fetchAll = $("btn-repo-fetch-all");
  if (fetchAll) fetchAll.addEventListener("click", runRepoFetchAll);
  const batchPull = $("btn-repo-batch-pull");
  if (batchPull) batchPull.addEventListener("click", () => runRepoBatch("pull_ff_only"));
  const batchPush = $("btn-repo-batch-push");
  if (batchPush) batchPush.addEventListener("click", () => runRepoBatch("push"));
  const scan = $("btn-onboarding-scan");
  if (scan) scan.addEventListener("click", loadOnboardingReport);
}

// ------------------------------------------------ P4.3 repo onboarding

export async function loadOnboardingReport() {
  const zh = state.currentLang === "zh-TW";
  const box = $("onboarding-report");
  if (!box) return;
  box.innerHTML = `<span class="muted small">${zh ? "掃描中…" : "Scanning…"}</span>`;
  try {
    state.onboardingReportCache = await getJSON("/api/v1/repos/onboarding-report");
    renderOnboardingReport();
  } catch (e) {
    box.innerHTML = `<span class="muted small">${esc(String(e.message || e))}</span>`;
  }
}

export function renderOnboardingReport() {
  const zh = state.currentLang === "zh-TW";
  const box = $("onboarding-report");
  const report = state.onboardingReportCache;
  if (!box || !report) return;
  const roots = report.roots || [];
  const rootOptions = roots.map(r => `<option value="${esc(r.root_id)}">${esc(r.path)}</option>`).join("");
  const ghOptions = (report.github_not_cloned || [])
    .map(g => `<option value="${esc(g.full_name)}">${esc(g.full_name)}</option>`).join("");
  const sections = [];

  const folders = report.plain_folders || [];
  sections.push(`<div class="mono-mini muted mb-6">${zh ? "① 尚未 git init 的資料夾" : "① Folders without git init"}（${folders.length}${report.plain_folders_truncated ? "+" : ""}）</div>`);
  sections.push(folders.length ? folders.map(f =>
    `<div class="tag" style="justify-content: space-between; width: 100%; margin-bottom: 4px;">
      <span>📁 ${esc(f.path)}</span>
      <button class="btn btn-ghost btn-sm" data-action="onboarding-init" data-folder-id="${esc(f.folder_id)}" data-name="${esc(f.name)}">${zh ? "git init" : "git init"}</button>
    </div>`).join("") : `<div class="muted small mb-6">${zh ? "（沒有）" : "(none)"}</div>`);

  const noRemote = report.repos_without_remote || [];
  sections.push(`<div class="mono-mini muted mb-6" style="margin-top:8px;">${zh ? "② 沒有 remote 的本機 repo" : "② Local repos without a remote"}（${noRemote.length}）</div>`);
  sections.push(noRemote.length ? noRemote.map(r =>
    `<div class="tag" style="justify-content: space-between; width: 100%; margin-bottom: 4px; flex-wrap: wrap; gap: 4px;">
      <span>📦 ${esc(r.path)}</span>
      <span>
        <select id="ob-attach-${esc(r.repo_id)}" class="mono" style="max-width: 220px;">${ghOptions}</select>
        <button class="btn btn-ghost btn-sm" data-action="onboarding-attach" data-repo-id="${esc(r.repo_id)}" data-name="${esc(r.name)}">${zh ? "連結為 origin" : "Attach as origin"}</button>
        <button class="btn btn-ghost btn-sm" data-action="onboarding-create" data-repo-id="${esc(r.repo_id)}" data-name="${esc(r.name)}">${zh ? "建立 GitHub repo(private)" : "Create GitHub repo (private)"}</button>
      </span>
    </div>`).join("") : `<div class="muted small mb-6">${zh ? "（沒有）" : "(none)"}</div>`);

  const notCloned = report.github_not_cloned || [];
  sections.push(`<div class="mono-mini muted mb-6" style="margin-top:8px;">${zh ? "③ 尚未 clone 的 GitHub repo（以 remote URL 比對）" : "③ GitHub repos not cloned (matched by remote URL)"}（${notCloned.length}）</div>`);
  sections.push(notCloned.length ? notCloned.map(g => {
    const hint = g.name_match_hint
      ? ` <span class="muted small">${zh ? "⚠ 本機有同名目錄（不自動配對）：" : "⚠ same-name local dir (not auto-paired): "}${esc(g.name_match_hint)}</span>`
      : "";
    return `<div class="tag" style="justify-content: space-between; width: 100%; margin-bottom: 4px; flex-wrap: wrap; gap: 4px;">
      <span>☁️ ${esc(g.full_name)}${g.private ? " 🔒" : ""}${hint}</span>
      <span>
        <select id="ob-clone-${esc(g.full_name)}" class="mono" style="max-width: 220px;">${rootOptions}</select>
        <button class="btn btn-ghost btn-sm" data-action="onboarding-clone" data-full-name="${esc(g.full_name)}">${zh ? "clone 到選定 root" : "Clone into root"}</button>
      </span>
    </div>`;
  }).join("") : `<div class="muted small mb-6">${zh ? "（沒有）" : "(none)"}</div>`);

  box.innerHTML = sections.join("");
}

export async function onboardingAction(payload, confirmText) {
  const zh = state.currentLang === "zh-TW";
  if (!confirm(confirmText)) return;
  const result = $("onboarding-result");
  result.textContent = zh ? "執行中…" : "Running…";
  try {
    const receipt = await postJSON("/api/v1/repos/onboarding-action", { ...payload, confirmation: "confirmed" });
    result.textContent = `✅ ${receipt.action}: ${receipt.status}${receipt.note ? " — " + receipt.note : ""}`;
    await loadOnboardingReport();
    await loadRepositorySyncStatus();
  } catch (e) {
    result.textContent = `❌ ${String(e.message || e)}`;
  }
}

export function onboardingInit(folderId, name) {
  const zh = state.currentLang === "zh-TW";
  onboardingAction(
    { action: "init_folder", folder_id: folderId },
    zh ? `對「${name}」執行 git init？只建立空的 .git，不 commit、不設 remote、不發布。` : `Run git init on "${name}"? Creates an empty .git only.`
  );
};

export function onboardingAttach(repoId, name) {
  const zh = state.currentLang === "zh-TW";
  const select = $(`ob-attach-${repoId}`);
  const fullName = select ? select.value : "";
  if (!fullName) { alert(zh ? "沒有可選的 GitHub repo；請先同步 GitHub 整合。" : "No GitHub repos to pick; sync the GitHub integration first."); return; }
  onboardingAction(
    { action: "attach_remote", repo_id: repoId, github_full_name: fullName },
    zh ? `把 ${fullName} 設為「${name}」的 origin？不會 fetch、不會 push。` : `Set ${fullName} as origin of "${name}"? No fetch, no push.`
  );
};

export function onboardingCreate(repoId, name) {
  const zh = state.currentLang === "zh-TW";
  onboardingAction(
    { action: "create_remote", repo_id: repoId, name: name, private: true },
    zh ? `在 GitHub 建立 private repo「${name}」並設為 origin？遠端為空 repo，本機不會推送任何內容；首次發布由您自行 git push -u。` : `Create private GitHub repo "${name}" and set it as origin? Nothing is pushed; you do the first push yourself.`
  );
};

export function onboardingClone(fullName) {
  const zh = state.currentLang === "zh-TW";
  const select = document.getElementById(`ob-clone-${fullName}`);
  const rootId = select ? select.value : "";
  if (!rootId) { alert(zh ? "請先在監控設定加入 Git root。" : "Add a Git root in settings first."); return; }
  const rootPath = select ? select.options[select.selectedIndex].textContent : "";
  onboardingAction(
    { action: "clone_repo", github_full_name: fullName, root_id: rootId },
    zh ? `把 ${fullName} clone 到 ${rootPath} 之下？目的地已存在時會拒絕，不覆寫任何目錄。` : `Clone ${fullName} into ${rootPath}? Refused if the destination already exists.`
  );
};

// ---------------------------------------------------------------- github
