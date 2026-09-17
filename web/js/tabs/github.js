// web/js/tabs/github.js — GitHub 雲端專案與 PR 區塊。

import { getJSON, postJSON } from "../core/api.js";
import { $, esc } from "../core/dom.js";
import { t } from "../core/i18n.js";
import { API, state } from "../core/state.js";
import { loadProjects } from "../tabs/projects.js";
import { loadConfig } from "../tabs/settings.js";

export function initGitHubSection() {
  $("github-pill").addEventListener("click", () => {
    document.querySelectorAll(".tab").forEach(t => t.classList.remove("active"));
    document.querySelectorAll(".pane").forEach(p => p.classList.remove("active"));
    const tabBtn = document.querySelector('.tab[data-tab="tab-settings"]');
    if (tabBtn) tabBtn.classList.add("active");
    $("tab-settings").classList.add("active");
    loadConfig();
    const githubPanel = $("panel-github");
    if (githubPanel) githubPanel.open = true;  // 從徽章跳轉時自動展開收合的 GitHub 卡片
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

export async function loadGitHubStatus() {
  try {
    const data = await getJSON("/api/v1/github/status");
    state.githubStatus = data;
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
