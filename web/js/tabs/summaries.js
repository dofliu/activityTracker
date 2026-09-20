// web/js/tabs/summaries.js — 05 摘要與統計：日／週／月報與活動快照。

import { getJSON, postJSON } from "../core/api.js";
import { $, esc } from "../core/dom.js";
import { state } from "../core/state.js";
import { loadOpenLoops } from "../tabs/projects.js";

export function initSummariesTab() {
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
    if (state.summaries.dayMarkdown) navigator.clipboard.writeText(state.summaries.dayMarkdown);
  });

  document.querySelectorAll(".viewswitch .chip").forEach(btn => {
    btn.addEventListener("click", () => {
      document.querySelectorAll(".viewswitch .chip").forEach(b => b.classList.remove("active"));
      btn.classList.add("active");
      state.summaries.view = btn.dataset.view;
      paintSummaryView();
    });
  });
}

export async function loadSummaries() {
  try {
    state.summaries.cache = await getJSON("/api/v1/summaries?limit=60");
    const box = $("summary-history-list");
    if (!state.summaries.cache.length) {
      box.innerHTML = '<div class="placeholder">尚未產生歷史摘要。</div>';
    } else {
      box.innerHTML = state.summaries.cache.map((s, i) => `
        <div class="sideitem ${i === 0 ? "active" : ""}" data-date="${esc(s.date_str)}">
          <div class="sideitem-title">${esc(s.date_str)}</div>
          <div class="sideitem-sub">${esc((s.llm_provider || "").toUpperCase())} · ${esc((s.created_at || "").split(" ")[1] || "")}</div>
        </div>`).join("");
      box.querySelectorAll(".sideitem").forEach(el => {
        el.addEventListener("click", () => selectSummary(el.dataset.date));
      });
      showSummary(state.summaries.cache[0]);
    }
    paintSummaryView();
  } catch (e) {
    $("summary-history-list").innerHTML = '<div class="placeholder">無法讀取歷史報告。</div>';
  }
}

export async function selectSummary(dateStr) {
  document.querySelectorAll("#summary-history-list .sideitem").forEach(el => {
    el.classList.toggle("active", el.dataset.date === dateStr);
  });
  try {
    const data = await getJSON(`/api/v1/summaries/${encodeURIComponent(dateStr)}`);
    state.summaries.view = "day";
    document.querySelectorAll(".viewswitch .chip").forEach(b => b.classList.toggle("active", b.dataset.view === "day"));
    showSummary(data);
    paintSummaryView();
  } catch (e) { console.error(e); }
}

export function showSummary(s) {
  state.summaries.dayMarkdown = s.raw_markdown || s.markdown || "";
  $("summary-meta").textContent = `${s.date_str} · ${(s.llm_provider || "").toUpperCase()}${s.model_name ? " / " + s.model_name : ""}`;
  const view = $("summary-view-day");
  view.innerHTML = window.marked
    ? marked.parse(state.summaries.dayMarkdown)
    : `<pre>${esc(state.summaries.dayMarkdown)}</pre>`;
}

export async function generateSummary(startDate, endDate) {
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
export function paintSummaryView() {
  const day = $("summary-view-day"), week = $("summary-view-week"), month = $("summary-view-month");
  day.hidden = state.summaries.view !== "day";
  week.hidden = state.summaries.view !== "week";
  month.hidden = state.summaries.view !== "month";
  if (state.summaries.view === "week") renderWeekView();
  if (state.summaries.view === "month") renderMonthView();
}

export const dayNames = ["日", "一", "二", "三", "四", "五", "六"];
export const iso = (d) => `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}-${String(d.getDate()).padStart(2, "0")}`;

export function renderWeekView() {
  const today = new Date();
  const monday = new Date(today);
  monday.setDate(today.getDate() - ((today.getDay() + 6) % 7));
  const have = new Set(state.summaries.cache.map(s => s.date_str));

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

  const notes = state.summaries.cache
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
      <div class="rangestat"><div class="mono-mini muted">OPEN LOOPS</div><div class="rangestat-value">${state.projects.loops.length}</div><div class="rangestat-sub">目前未結</div></div>
      <div class="rangestat"><div class="mono-mini muted">STREAMS</div><div class="rangestat-value">${state.projects.cache.length}</div><div class="rangestat-sub">進行中工作</div></div>
      <div class="rangestat"><div class="mono-mini muted">ACTIVE</div><div class="rangestat-value">${state.projects.cache.filter(p => p.status === "active").length}</div><div class="rangestat-sub">兩天內有活動</div></div>
    </div>
    <span class="mono-label">本週報告 / REPORTS</span>
    ${notes}`;

  $("summary-view-week").querySelectorAll("[data-date]").forEach(el => {
    el.addEventListener("click", () => selectSummary(el.dataset.date));
  });
}

export function renderMonthView() {
  const today = new Date();
  const first = new Date(today.getFullYear(), today.getMonth(), 1);
  const days = new Date(today.getFullYear(), today.getMonth() + 1, 0).getDate();
  const lead = (first.getDay() + 6) % 7;
  const have = new Set(state.summaries.cache.map(s => s.date_str));

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

  const streams = state.projects.cache.length
    ? state.projects.cache.map(p => `
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
      <div class="rangestat"><div class="mono-mini muted">OPEN LOOPS</div><div class="rangestat-value">${state.projects.loops.length}</div><div class="rangestat-sub">目前未結</div></div>
      <div class="rangestat"><div class="mono-mini muted">STREAMS</div><div class="rangestat-value">${state.projects.cache.length}</div><div class="rangestat-sub">進行中工作</div></div>
    </div>
    <span class="mono-label">工作重心 / STREAMS</span>
    <div class="panel">${streams}</div>`;

  $("summary-view-month").querySelectorAll("[data-date]").forEach(el => {
    el.addEventListener("click", () => selectSummary(el.dataset.date));
  });
}

export function firstLine(md) {
  if (!md) return "（無內容）";
  const line = md.split("\n").map(l => l.replace(/^[#>*\-\s]+/, "").trim()).find(l => l.length > 4);
  return (line || "").slice(0, 80);
}

// ---------------------------------------------------------------- checkpoints
export function initCheckpointsTab() {
  $("btn-copy-cp").addEventListener("click", () => {
    if (state.summaries.checkpointMarkdown) navigator.clipboard.writeText(state.summaries.checkpointMarkdown);
  });
}

export async function loadCheckpoints() {
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

export async function selectCheckpoint(fileName) {
  document.querySelectorAll("#checkpoint-history-list .sideitem").forEach(el => {
    el.classList.toggle("active", el.dataset.file === fileName);
  });
  try {
    const data = await getJSON(`/api/v1/logs/checkpoints/${encodeURIComponent(fileName)}`);
    state.summaries.checkpointMarkdown = data.content || "";
    $("cp-title").textContent = fileName;
    $("checkpoint-markdown-viewer").innerHTML = window.marked
      ? marked.parse(state.summaries.checkpointMarkdown)
      : `<pre>${esc(state.summaries.checkpointMarkdown)}</pre>`;
  } catch (e) { console.error(e); }
}

export async function triggerCheckpoint() {
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

// ---------------------------------------------------------------- DeskRAG Knowledge & Chat
