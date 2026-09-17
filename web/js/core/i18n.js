// web/js/core/i18n.js — 語言字典（`web/i18n/*.json`）的載入與套用。字典是資料，不是程式。

import { $ } from "../core/dom.js";
import { getJSON } from "../core/api.js";
import { state } from "../core/state.js";
import { paintThemeBtn } from "../core/ui.js";
import { renderContextSessions, renderHome, renderRelatedContext, renderSecretaryProposals } from "../tabs/assistant.js";
import { renderAcceptance } from "../tabs/health.js";
import { renderMemoryList } from "../tabs/memory.js";
import { renderOpenLoops, renderProjects, renderResume } from "../tabs/projects.js";
import { renderRepositorySyncStatus } from "../tabs/repos.js";
import { renderLLMStatus } from "../tabs/settings.js";
import { refreshStatus } from "../tabs/status.js";

export const LANGUAGES = ["zh-TW", "en"];

// 字典是資料，不是程式（ADR-026）：兩份 JSON 由 `web/i18n/` 提供，契約測試守著
// 「兩份 key 集合必須完全相同」——少一個 key 不會報錯，它會安靜地 fallback。
const I18N = {};

export async function loadDictionaries() {
  for (const lang of LANGUAGES) {
    I18N[lang] = await getJSON(`/static/i18n/${lang}.json`);
  }
  return I18N;
}

export function t(key, vars = {}) {
  const dict = I18N[state.currentLang] || I18N["zh-TW"];
  let str = dict[key] || (I18N["zh-TW"] && I18N["zh-TW"][key]) || key;
  for (const [k, v] of Object.entries(vars)) {
    str = str.replace(new RegExp(`\\{${k}\\}`, 'g'), v);
  }
  return str;
}

export function applyLanguage(lang) {
  state.currentLang = lang;
  localStorage.setItem("omni-lang", lang);
  document.documentElement.lang = lang === "zh-TW" ? "zh-TW" : "en";

  // 更新所有 data-i18n 節點文字
  document.querySelectorAll("[data-i18n]").forEach(el => {
    const k = el.dataset.i18n;
    if (k && I18N[state.currentLang][k]) {
      el.textContent = t(k);
    }
  });

  // 更新所有 placeholder
  document.querySelectorAll("[data-i18n-ph]").forEach(el => {
    const k = el.dataset.i18nPh;
    if (k && I18N[state.currentLang][k]) {
      el.placeholder = t(k);
    }
  });

  // 更新語言按鈕標籤
  const langBtn = $("btn-lang");
  if (langBtn) langBtn.textContent = t("lang_btn");

  paintThemeBtn();
  refreshStatus();
  renderResume();
  renderProjects();
  renderOpenLoops();
  renderContextSessions();
  renderSecretaryProposals();
  renderRepositorySyncStatus();
  if (state.relatedContextCache) renderRelatedContext(state.relatedContextCache);
  if (state.acceptanceCache) renderAcceptance();
  if (state.memoryCache) renderMemoryList();   // 記憶區清單與個人檔案列的字串也要跟著切換
  if (state.homeCache) renderHome();
  if ($("llm-key-status-badge")) renderLLMStatus();
}

export function initLanguage() {
  const langBtn = $("btn-lang");
  if (langBtn) {
    langBtn.addEventListener("click", () => {
      const nextLang = state.currentLang === "zh-TW" ? "en" : "zh-TW";
      applyLanguage(nextLang);
    });
  }
  applyLanguage(state.currentLang);
}
