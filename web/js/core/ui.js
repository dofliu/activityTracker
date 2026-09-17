// web/js/core/ui.js — 跨分頁的介面機制：分頁切換、主題、可收合面板、toast。

import { $ } from "../core/dom.js";
import { t } from "../core/i18n.js";
import { loadAssistantStrip, loadGreeting, loadHome, loadSecretaryProposals, loadTodayView, recordHomeLeave, syncAssistantModelControls } from "../tabs/assistant.js";
import { loadAcceptance, loadSystemHealth } from "../tabs/health.js";
import { loadRAGFolders, loadRAGProgress, loadRAGSessions } from "../tabs/knowledge.js";
import { loadMemoryPanel } from "../tabs/memory.js";
import { loadOpenLoops, loadProjects, loadRepoSnapshot, refreshFeed } from "../tabs/projects.js";
import { loadRepositorySyncStatus } from "../tabs/repos.js";
import { loadConfig } from "../tabs/settings.js";
import { loadUsagePanels, refreshStatus } from "../tabs/status.js";
import { loadCheckpoints, loadSummaries } from "../tabs/summaries.js";

export function initCollapsiblePanels() {
  document.querySelectorAll("details.panel-collapsible[id], details.sub-collapsible[id]").forEach(panel => {
    const key = `omni-panel-open:${panel.id}`;
    try {
      const saved = localStorage.getItem(key);
      if (saved === "1") panel.open = true;
      if (saved === "0") panel.open = false;
    } catch (_) { /* localStorage 不可用時維持 HTML 預設 */ }
    panel.addEventListener("toggle", () => {
      try { localStorage.setItem(key, panel.open ? "1" : "0"); } catch (_) { /* 同上 */ }
    });
  });
}

// ---------------------------------------------------------------- theme
// 外觀 = 明暗（data-theme）× 配色（data-accent）兩個獨立的軸；
// 兩者都只存在瀏覽器 localStorage，不寫入 config.yaml、不送往後端。
export const PALETTES = ["naruto", "forest", "ocean"];

export function initTheme() {
  const saved = localStorage.getItem("omni-theme");
  if (saved === "light" || saved === "dark") document.documentElement.dataset.theme = saved;
  paintThemeBtn();
  $("btn-theme").addEventListener("click", () => {
    const next = document.documentElement.dataset.theme === "dark" ? "light" : "dark";
    document.documentElement.dataset.theme = next;
    localStorage.setItem("omni-theme", next);
    paintThemeBtn();
  });

  const select = $("select-palette");
  let palette = "naruto";
  try {
    const savedPalette = localStorage.getItem("omni-palette");
    if (PALETTES.includes(savedPalette)) palette = savedPalette;
  } catch (_) { /* localStorage 不可用時維持預設配色 */ }
  applyPalette(palette);
  if (select) {
    select.value = palette;
    select.addEventListener("change", () => applyPalette(select.value));
  }
}

export function applyPalette(palette) {
  const next = PALETTES.includes(palette) ? palette : "naruto";
  // naruto 是 CSS 的預設值，不需要屬性；移除屬性可讓舊版樣式完全一致。
  if (next === "naruto") delete document.documentElement.dataset.accent;
  else document.documentElement.dataset.accent = next;
  try { localStorage.setItem("omni-palette", next); } catch (_) { /* 同上 */ }
}
export function paintThemeBtn() {
  const isDark = document.documentElement.dataset.theme === "dark";
  $("btn-theme").textContent = isDark ? t("btn_theme_light") : t("btn_theme_dark");
}

// ---------------------------------------------------------------- tabs
export function initTabs() {
  document.querySelectorAll(".tab").forEach(tab => {
    tab.addEventListener("click", () => {
      const prevTab = document.querySelector(".tab.active");
      const prev = prevTab ? prevTab.dataset.tab : null;
      document.querySelectorAll(".tab").forEach(t => t.classList.remove("active"));
      document.querySelectorAll(".pane").forEach(p => p.classList.remove("active"));
      tab.classList.add("active");
      const id = tab.dataset.tab;
      $(id).classList.add("active");
      // ADR-019 的量測指標：「你一天要離開 01 幾次」——只算這個瀏覽器的分頁切換，存 localStorage
      if (prev === "tab-assistant" && id !== "tab-assistant") recordHomeLeave();
      if (id === "tab-assistant") { loadSecretaryProposals(); loadAssistantStrip(); syncAssistantModelControls(); loadProjects(); loadTodayView(); loadMemoryPanel(); loadGreeting(); loadHome(); }
      if (id === "tab-knowledge") { loadRAGFolders(); loadRAGSessions(); loadRAGProgress(); }
      if (id === "tab-projects") { loadProjects(); loadRepoSnapshot(); }
      if (id === "tab-repos") loadRepositorySyncStatus();  // 切到分頁才掃描本機 Git，不在開頁時付這個成本
      if (id === "tab-settings") { loadConfig(); activateSettingsPane(currentSettingsPane()); }
      if (id === "tab-summaries") { loadSummaries(); loadCheckpoints(); loadUsagePanels(); loadAssistantStrip(); loadOpenLoops(); }   // 今日統計與 Focus Now 現在住這裡
    });
  });
}

// ---------------------------------------------------------------- 06 系統設定：左欄切換
export const SETTINGS_PANE_KEY = "omni-settings-pane";
export const SETTINGS_CONFIG_PANES = new Set(["secretary", "telegram", "line", "paths", "sources", "llm", "usage", "github"]);

export function currentSettingsPane() {
  let saved = null;
  try { saved = localStorage.getItem(SETTINGS_PANE_KEY); } catch (_) { /* 無 localStorage 時用預設 */ }
  return saved && document.querySelector(`.settings-pane[data-pane="${saved}"]`) ? saved : "secretary";
}

export function activateSettingsPane(key) {
  const pane = document.querySelector(`.settings-pane[data-pane="${key}"]`);
  if (!pane) return;
  document.querySelectorAll(".settings-pane").forEach(p => p.classList.toggle("active", p === pane));
  document.querySelectorAll(".settings-nav-item").forEach(b => b.classList.toggle("active", b.dataset.pane === key));
  try { localStorage.setItem(SETTINGS_PANE_KEY, key); } catch (_) { /* 同上 */ }
  // 儲存列只對設定類區塊有意義；情報流與系統健康是唯讀觀察
  const bar = document.querySelector(".settings-actionbar");
  if (bar) bar.hidden = !SETTINGS_CONFIG_PANES.has(key);
  if (key === "feed") { refreshStatus(); refreshFeed(); }
  if (key === "health") loadSystemHealth();
  if (key === "acceptance") loadAcceptance();
}

export function initSettingsNav() {
  document.querySelectorAll(".settings-nav-item").forEach(btn => {
    btn.addEventListener("click", () => activateSettingsPane(btn.dataset.pane));
  });
  activateSettingsPane(currentSettingsPane());
}

// 其他分頁若要導到系統健康／情報流，走這個入口而不是直接切分頁
export function openSettingsPane(key) {
  const tab = document.querySelector('.tab[data-tab="tab-settings"]');
  if (tab) tab.click();
  activateSettingsPane(key);
}

export function showToast(msg, duration = 3200) {
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

// ---------------------------------------------------------------- 事件委派
// D10 之前有 17 處行內 click 屬性寫在 HTML 字串裡，指向掛在 window 上的函式
// （模組化的攔路虎），而且參數是拼進 HTML 的——跳脫正確性押在每一個拼字串的人身上。
//
// 現在按鈕只寫 `data-action` ＋ `data-*` 參數，由這裡一個 document 層 listener 分派。
// 容器重繪（innerHTML 換掉）不會弄丟 listener，因為它掛在 document 上。
const ACTIONS = new Map();

export function registerActions(handlers) {
  for (const [name, fn] of Object.entries(handlers)) ACTIONS.set(name, fn);
}

export function knownActions() {
  return [...ACTIONS.keys()].sort();
}

export function initActionDelegation() {
  document.addEventListener("click", (event) => {
    const el = event.target.closest("[data-action]");
    if (!el) return;
    const handler = ACTIONS.get(el.dataset.action);
    if (!handler) return;
    event.preventDefault();
    handler(el.dataset, el, event);
  });
}
