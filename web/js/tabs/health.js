// web/js/tabs/health.js — 06 系統健康與驗收中心。

import { getJSON, postJSON } from "../core/api.js";
import { $, esc } from "../core/dom.js";
import { t } from "../core/i18n.js";
import { state } from "../core/state.js";
import { Path_basename } from "../tabs/knowledge.js";
import { statusLabel } from "../tabs/projects.js";
import { refreshStatus } from "../tabs/status.js";

export function initSystemHealthTab() {
  const refreshBtn = $("btn-refresh-system-health");
  if (refreshBtn) refreshBtn.addEventListener("click", loadSystemHealth);

  const healBtn = $("btn-trigger-heal-ui");
  if (healBtn) healBtn.addEventListener("click", triggerSystemHeal);

  const walBtn = $("btn-trigger-wal-ui");
  if (walBtn) walBtn.addEventListener("click", triggerSystemWalCheckpoint);

  const maintainBtn = $("btn-trigger-maintain-ui");
  if (maintainBtn) maintainBtn.addEventListener("click", triggerSystemMaintenance);

  const clearBtn = $("btn-clear-system-console");
  if (clearBtn) clearBtn.addEventListener("click", () => {
    const con = $("system-action-console");
    if (con) con.textContent = "// 終端輸出已清空。";
  });
}

export function appendSystemConsole(title, data) {
  const con = $("system-action-console");
  if (!con) return;
  const time = new Date().toLocaleTimeString();
  const formatted = typeof data === "string" ? data : JSON.stringify(data, null, 2);
  const block = `\n[${time}] >>> ${title}\n${formatted}\n`;
  if (con.textContent.startsWith("//")) {
    con.textContent = block.trim();
  } else {
    con.textContent = (con.textContent + "\n" + block).trim();
  }
  if (con.parentElement) con.parentElement.scrollTop = con.parentElement.scrollHeight;
}

// ---------------------------------------------------------------- 驗收中心
// 只呈現後端查到的收據；狀態字彙與 core/acceptance.py 一一對應，前端不自己判斷通過與否。
export const ACCEPTANCE_STATUS_META = {
  passed:         { icon: "✅", trust: "ok" },
  attested:       { icon: "🖊", trust: "ok" },
  partial:        { icon: "🟡", trust: "noisy" },
  pending:        { icon: "⬜", trust: "" },
  needs_human:    { icon: "👤", trust: "noisy" },
  not_configured: { icon: "➖", trust: "" },
  runtime_only:   { icon: "🌐", trust: "" }
};

export function acceptanceStatusLabel(status) {
  return t("acceptance_st_" + status) || status;
}

export async function loadAcceptance() {
  try {
    state.health.acceptance = await getJSON("/api/v1/acceptance/checklist");
    renderAcceptance();
  } catch (e) {
    console.error("loadAcceptance error:", e);
    const box = $("acceptance-items");
    if (box) box.innerHTML = `<div class="placeholder">${state.ui.currentLang === "zh-TW" ? "無法讀取驗收現況。" : "Acceptance status is unavailable."}</div>`;
  }
}

export function renderAcceptance() {
  const data = state.health.acceptance;
  if (!data) return;
  const zh = state.ui.currentLang === "zh-TW";
  const summary = data.summary || {};
  const settled = (summary.passed || 0) + (summary.attested || 0);

  const badge = $("acceptance-summary-badge");
  if (badge) {
    const blocking = (summary.blocking_release || []).length;
    badge.className = "trust " + (blocking ? "broken" : settled === summary.total ? "ok" : "noisy");
    badge.textContent = `${settled}/${summary.total || 0}`;
  }
  const stamp = $("acceptance-generated-at");
  if (stamp) stamp.textContent = data.generated_at ? `${data.generated_at} · ${data.mode}` : "";

  const gatesBox = $("acceptance-gates");
  if (gatesBox) {
    const blocking = summary.blocking_release || [];
    const banner = blocking.length
      ? `<div class="acceptance-banner broken">🔴 ${esc(blocking.join("、"))} ${esc(t("acceptance_blocking"))}</div>`
      : "";
    const gates = (data.release_gates || []).map(gate => {
      const meta = ACCEPTANCE_STATUS_META[gate.status] || { icon: "•", trust: "" };
      const pendingItems = gate.outstanding || [];
      const shown = pendingItems.slice(0, 4).join("、") + (pendingItems.length > 4 ? ` … (${pendingItems.length})` : "");
      return `<div class="acceptance-gate">
        <div class="acceptance-gate-head">
          <span class="acceptance-icon">${meta.icon}</span>
          <span class="mono-mini muted">${esc(gate.id)}</span>
          <span class="acceptance-gate-text">${esc(gate.text)}</span>
        </div>
        ${pendingItems.length ? `<div class="acceptance-gate-pending mono-mini muted">${esc(t("acceptance_outstanding"))}：${esc(shown)}</div>` : ""}
      </div>`;
    }).join("");
    gatesBox.innerHTML = banner + `<div class="acceptance-gates-title mono-mini muted">${esc(t("acceptance_gates_title"))}</div>` + (gates || `<div class="placeholder">${zh ? "只查了部分項目，gate 需要完整清單。" : "Partial run — gates need the full checklist."}</div>`);
  }

  const box = $("acceptance-items");
  if (!box) return;
  box.innerHTML = (data.items || []).map(item => {
    const meta = ACCEPTANCE_STATUS_META[item.status] || { icon: "•", trust: "" };
    const flag = item.blocks_release && item.status !== "passed" && item.status !== "attested"
      ? `<span class="trust broken">P0</span>` : "";
    const attestable = item.status === "needs_human" || item.status === "attested";
    const attested = item.status === "attested";
    const button = attestable
      ? `<button class="btn btn-ghost btn-sm acceptance-confirm" data-item="${esc(item.id)}" data-confirmed="${attested ? "1" : "0"}">${esc(t(attested ? "acceptance_unconfirm_btn" : "acceptance_confirm_btn"))}</button>`
      : "";
    const attestation = item.attestation
      ? `<div class="acceptance-attestation mono-mini">🖊 ${esc(t("acceptance_attested_by_you"))} ${esc(item.attestation.confirmed_at)}${item.attestation.note ? " · " + esc(item.attestation.note) : ""}</div>`
      : "";
    return `<div class="acceptance-row">
      <div class="acceptance-row-head">
        <span class="acceptance-icon">${meta.icon}</span>
        <span class="mono-mini muted">${esc(item.id)}</span>
        <span class="acceptance-title">${esc(item.title)}</span>
        <span class="trust ${meta.trust}">${esc(acceptanceStatusLabel(item.status))}</span>
        ${flag}
        <span class="head-right">${button}</span>
      </div>
      <div class="acceptance-detail">${esc(item.detail)}</div>
      <div class="acceptance-meta mono-mini muted">${esc(t("acceptance_how"))}：${esc(item.how)}</div>
      <div class="acceptance-meta mono-mini muted">${esc(t("acceptance_criterion"))}：${esc(item.criterion)}</div>
      ${attestation}
      <details class="acceptance-evidence">
        <summary class="mono-mini muted">${esc(t("acceptance_evidence"))}</summary>
        <pre>${esc(JSON.stringify(item.evidence || {}, null, 2))}</pre>
      </details>
    </div>`;
  }).join("") || `<div class="placeholder">${zh ? "沒有項目。" : "No items."}</div>`;

  box.querySelectorAll(".acceptance-confirm").forEach(btn => {
    btn.addEventListener("click", () => confirmAcceptanceItem(btn.dataset.item, btn.dataset.confirmed !== "1"));
  });
}

export async function confirmAcceptanceItem(itemId, confirmed) {
  if (confirmed && !confirm(t("acceptance_confirm_ask"))) return;
  try {
    await postJSON("/api/v1/acceptance/confirm", { item_id: itemId, confirmed, note: "" });
    loadAcceptance();
  } catch (e) {
    alert(e.message);
  }
}

export async function loadSystemHealth() {
  try {
    const data = await getJSON("/api/v1/system/health");
    renderSystemOverview(data);
    renderCollectorDiagnosticsMatrix(data);
    loadLatestMaintenanceReceipt();
  } catch (e) {
    console.error("loadSystemHealth error:", e);
  }
}

export function renderSystemOverview(data) {
  const stateBadge = $("system-overall-badge");
  const stateVal = $("sys-stat-state");
  const dbSizeVal = $("sys-stat-dbsize");
  const walVal = $("sys-stat-wal");
  const projectsVal = $("sys-stat-projects");
  const dbPathVal = $("sys-stat-dbpath");
  const healedCountVal = $("sys-stat-healed-count");
  const lastHealVal = $("sys-stat-last-heal");

  const status = data.status || "healthy";
  const degradedList = data.degraded_collectors || [];

  if (stateBadge) {
    stateBadge.className = "trust " + (status === "healthy" ? "ok" : status === "degraded" ? "noisy" : "broken");
    stateBadge.textContent = status.toUpperCase();
  }
  if (stateVal) {
    stateVal.className = "system-stat-val " + (status === "healthy" ? "text-success" : status === "degraded" ? "text-warning" : "text-danger");
    stateVal.textContent = status === "healthy" ? "HEALTHY (正常)" : status === "degraded" ? `DEGRADED (${degradedList.length} 異常)` : "STOPPED (已暫停)";
  }

  if (data.database) {
    const sizeMb = (Number(data.database.size_bytes || 0) / (1024 * 1024)).toFixed(2);
    const walKb = (Number(data.database.wal_size_bytes || 0) / 1024).toFixed(1);
    if (dbSizeVal) dbSizeVal.textContent = `${sizeMb} MB`;
    if (walVal) walVal.textContent = `WAL: ${walKb} KB`;
    if (projectsVal) projectsVal.textContent = `${data.database.active_projects_count || 0} 專案`;
    if (dbPathVal) {
      dbPathVal.textContent = Path_basename(data.database.path || "omni_context.db");
      dbPathVal.title = data.database.path || "";
    }
  }

  if (data.self_healing) {
    const totalHealed = data.self_healing.total_healing_events_count || 0;
    if (healedCountVal) healedCountVal.textContent = `${totalHealed} 次自動修復`;
    const lastEvent = data.self_healing.last_healing_event;
    if (lastHealVal) {
      lastHealVal.textContent = lastEvent ? `最近修復: ${new Date(lastEvent.timestamp).toLocaleTimeString()}` : "常駐守護中";
    }
  }
}

export function renderCollectorDiagnosticsMatrix(data) {
  const container = $("collectors-diagnostics-grid");
  if (!container) return;

  const watchers = data.watchers || {};
  const health = data.collector_health || {};
  const runtime = data.collector_runtime || {};
  const diag = data.collector_diagnostics || {};

  const collectors = [
    {
      key: "file_watcher",
      title: "📁 檔案變更監控 (File Watcher)",
      on: watchers.file_watcher,
      h: health.file_watcher || "healthy",
      r: runtime.file_watcher || "stopped",
      d: diag.file_watcher || {},
    },
    {
      key: "git_watcher",
      title: "🐙 Git 倉庫掃描 (Git Scanner)",
      on: watchers.git_watcher,
      h: health.git_watcher || "healthy",
      r: runtime.git_watcher || "stopped",
      d: diag.git_watcher || {},
    },
    {
      key: "window_watcher",
      title: "🪟 前景視窗焦點 (Window Focus)",
      on: watchers.window_watcher,
      h: health.window_watcher || "healthy",
      r: runtime.window_watcher || "stopped",
      d: diag.window_watcher || {},
    },
    {
      key: "agent_log_watcher",
      title: "🤖 AI 日誌採集 (Agent Log Watcher)",
      on: watchers.agent_log_watcher,
      h: health.agent_log_watcher || "healthy",
      r: runtime.agent_log_watcher || "stopped",
      d: diag.agent_log_watcher || {},
    },
    {
      key: "scheduler",
      title: "⏰ 背景合成與排程 (Scheduler)",
      on: watchers.scheduler,
      h: health.scheduler || "healthy",
      r: runtime.scheduler || "stopped",
      d: diag.scheduler || {},
    },
  ];

  const colorMap = {
    healthy: "var(--success, #22c55e)",
    idle: "var(--warn, #eab308)",
    degraded: "var(--danger, #ef4444)",
    stopped: "var(--danger, #ef4444)",
    stale: "var(--danger, #ef4444)",
    disabled: "var(--mu, #888)",
  };

  const labelMap = {
    healthy: "運作中 (Healthy)",
    idle: "待命中 (Idle)",
    degraded: "部分降級 (Degraded)",
    stopped: "已停止 (Stopped)",
    stale: "無有效資料 (Stale)",
    disabled: "已停用 (Disabled)",
  };

  container.innerHTML = collectors.map(c => {
    const isDegraded = c.h === "degraded";
    const isStopped = !c.on || c.r === "stopped";
    const dotColor = colorMap[c.h] || "var(--mu)";
    const statusLabel = labelMap[c.h] || (c.on ? "Active" : "Off");

    let metricHtml = "";
    let isolatedAlert = "";

    if (c.key === "file_watcher") {
      const scheduled = (c.d.monitored_directories || []).length;
      const failed = (c.d.failed_directories || []).length;
      const restarts = (c.d.healing_history || []).length;
      metricHtml = `
        <div class="collector-diag-metric-row"><span>已排程監控目錄</span><span>${scheduled} 個</span></div>
        <div class="collector-diag-metric-row"><span>異常失效目錄</span><span>${failed} 個</span></div>
        <div class="collector-diag-metric-row"><span>自動重啟修復次數</span><span>${restarts} 次</span></div>
      `;
      if (failed > 0) {
        isolatedAlert = `<div class="collector-diag-isolated-box">⚠️ 失敗目錄: ${esc((c.d.failed_directories || []).join(", "))}</div>`;
      }
    } else if (c.key === "git_watcher") {
      const scanned = c.d.scanned_repositories_count || 0;
      const degraded = (c.d.degraded_repositories || []).length;
      const restarts = (c.d.healing_history || []).length;
      metricHtml = `
        <div class="collector-diag-metric-row"><span>已掃描正常倉庫</span><span>${scanned} 個</span></div>
        <div class="collector-diag-metric-row"><span>局部隔離損壞倉庫</span><span>${degraded} 個</span></div>
        <div class="collector-diag-metric-row"><span>自動修復線程次數</span><span>${restarts} 次</span></div>
      `;
      if (degraded > 0) {
        isolatedAlert = `<div class="collector-diag-isolated-box">⚠️ 隔離倉庫: ${esc((c.d.degraded_repositories || []).join(", "))}</div>`;
      }
    } else if (c.key === "window_watcher") {
      const probeState = c.d.state || "normal";
      const app = c.d.current_app || "None";
      const unavail = c.d.unavailable_seconds || 0;
      metricHtml = `
        <div class="collector-diag-metric-row"><span>前景探測狀態</span><span>${esc(probeState)}</span></div>
        <div class="collector-diag-metric-row"><span>當前焦點應用</span><span>${esc(app)}</span></div>
        <div class="collector-diag-metric-row"><span>不可用持續秒數</span><span>${unavail}s</span></div>
      `;
    } else if (c.key === "agent_log_watcher") {
      const restarts = (c.d.healing_history || []).length;
      const errCode = c.d.last_error_code || "None";
      const drift = c.d.drift || {};
      const drifted = (drift.platforms || []);
      metricHtml = `
        <div class="collector-diag-metric-row"><span>多 AI 來源隔離解析</span><span>Claude / Codex / Antigravity</span></div>
        <div class="collector-diag-metric-row"><span>最後錯誤代碼</span><span>${esc(errCode)}</span></div>
        <div class="collector-diag-metric-row"><span>自動修復線程次數</span><span>${restarts} 次</span></div>
        <div class="collector-diag-metric-row"><span>格式漂移偵測（${Number(drift.window_days || 0)} 天）</span><span>${drifted.length ? `⚠️ ${drifted.length} 個平台` : "正常"}</span></div>
      `;
      if (drifted.length) {
        // 解析沒報錯、檔案也在更新，卻一筆事件都沒有——這是靜默失效，不是「沒在用」。
        isolatedAlert = `<div class="collector-diag-isolated-box">⚠️ 疑似 transcript 格式漂移：${esc(drifted.map(p => p.platform).join(", "))}（檔案有更新，視窗內零事件）</div>`;
      }
    } else if (c.key === "scheduler") {
      const restarts = (c.d.healing_history || []).length;
      metricHtml = `
        <div class="collector-diag-metric-row"><span>每小時 WAL Checkpoint</span><span>已註冊 (TRUNCATE)</span></div>
        <div class="collector-diag-metric-row"><span>每日 03:30 深夜維護</span><span>已註冊 (7天輪替/90天修剪)</span></div>
        <div class="collector-diag-metric-row"><span>排程自動修復次數</span><span>${restarts} 次</span></div>
      `;
    }

    return `
      <div class="collector-diag-card ${isDegraded ? "degraded" : isStopped ? "stopped" : ""}">
        <div class="collector-diag-head">
          <span class="collector-diag-title">
            <span class="pill-dot" style="background:${dotColor}"></span>
            ${c.title}
          </span>
          <span class="trust ${c.h === "healthy" ? "ok" : c.h === "degraded" ? "noisy" : "broken"}">${statusLabel}</span>
        </div>
        <div class="collector-diag-metrics">
          ${metricHtml}
        </div>
        ${isolatedAlert}
      </div>
    `;
  }).join("");
}

export async function loadLatestMaintenanceReceipt() {
  const container = $("maintenance-receipt-container");
  const badge = $("maintenance-integrity-badge");
  if (!container) return;

  try {
    const res = await getJSON("/api/v1/system/maintenance/receipt");
    if (!res.has_receipt || !res.receipt) {
      container.innerHTML = '<div class="placeholder">尚未產生維護收據。可點擊上方「執行資料庫完整維護」立即建立。</div>';
      if (badge) { badge.className = "trust noisy"; badge.textContent = "NO RECEIPT"; }
      return;
    }

    const r = res.receipt;
    const isOk = r.status === "passed" && r.integrity === "ok";
    if (badge) {
      badge.className = "trust " + (isOk ? "ok" : "broken");
      badge.textContent = isOk ? "INTEGRITY OK" : "WARNING";
    }

    const pruneCount = r.pruning ? (r.pruning.total_pruned || 0) : 0;
    const backupPath = r.backup ? r.backup.backup_path || "無" : "線上備份建立成功";
    const timeStr = r.created_at ? new Date(r.created_at).toLocaleString() : "未知";

    container.innerHTML = `
      <div class="receipt-grid">
        <div class="receipt-item">
          <div class="receipt-item-title">LAST MAINTENANCE TIME</div>
          <div class="receipt-item-val">${esc(timeStr)}</div>
        </div>
        <div class="receipt-item">
          <div class="receipt-item-title">SQLITE INTEGRITY CHECK</div>
          <div class="receipt-item-val text-success">${esc(r.integrity || "ok")}</div>
        </div>
        <div class="receipt-item">
          <div class="receipt-item-title">HISTORICAL EVENTS PRUNED</div>
          <div class="receipt-item-val">${pruneCount} 筆記錄</div>
        </div>
        <div class="receipt-item">
          <div class="receipt-item-title">BACKUP VAULT ROTATION</div>
          <div class="receipt-item-val" title="${esc(backupPath)}">保留最新 7 份</div>
        </div>
      </div>
    `;
  } catch (e) {
    container.innerHTML = `<div class="placeholder">載入維護收據失敗: ${esc(e.message)}</div>`;
  }
}

export async function triggerSystemHeal() {
  const btn = $("btn-trigger-heal-ui");
  const orig = btn ? btn.innerHTML : "";
  if (btn) { btn.disabled = true; btn.innerHTML = "⏳ 正在巡檢修復中…"; }
  appendSystemConsole("觸發全域採集器自我修復巡檢 (POST /api/v1/system/heal)...", "正在檢測並修復掛掉的 Observer 與工作線程...");
  try {
    const res = await postJSON("/api/v1/system/heal");
    appendSystemConsole("✅ 自我修復巡檢完成收據", res);
    loadSystemHealth();
    refreshStatus();
  } catch (e) {
    appendSystemConsole("❌ 自我修復巡檢失敗", e.message);
  } finally {
    if (btn) {
      btn.disabled = false;
      btn.innerHTML = orig;
    }
  }
}

export async function triggerSystemWalCheckpoint() {
  const btn = $("btn-trigger-wal-ui");
  const orig = btn ? btn.innerHTML : "";
  if (btn) { btn.disabled = true; btn.innerHTML = "⏳ 截斷 WAL 中…"; }
  appendSystemConsole("執行 SQLite WAL Checkpoint (POST /api/v1/system/wal-checkpoint)...", { mode: "TRUNCATE" });
  try {
    const res = await postJSON("/api/v1/system/wal-checkpoint", { mode: "TRUNCATE" });
    appendSystemConsole("✅ WAL Checkpoint 執行結果", res);
    loadSystemHealth();
  } catch (e) {
    appendSystemConsole("❌ WAL Checkpoint 失敗", e.message);
  } finally {
    if (btn) {
      btn.disabled = false;
      btn.innerHTML = orig;
    }
  }
}

export async function triggerSystemMaintenance() {
  const btn = $("btn-trigger-maintain-ui");
  const orig = btn ? btn.innerHTML : "";
  if (btn) { btn.disabled = true; btn.innerHTML = "⏳ 正在執行維護…"; }
  appendSystemConsole("執行資料庫完整健康維護 (POST /api/v1/system/maintenance)...", {
    max_backups: 7,
    retention_days: 90,
    dry_run: false
  });
  try {
    const res = await postJSON("/api/v1/system/maintenance", {
      max_backups: 7,
      retention_days: 90,
      dry_run: false
    });
    appendSystemConsole("✅ 資料庫完整維護收據 (Latest Maintenance Receipt)", res);
    loadSystemHealth();
  } catch (e) {
    appendSystemConsole("❌ 資料庫維護失敗", e.message);
  } finally {
    if (btn) {
      btn.disabled = false;
      btn.innerHTML = orig;
    }
  }
}
