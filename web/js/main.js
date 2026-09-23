// web/js/main.js — 進入點（ADR-026，TODO D10）
//
// D10 之前這段是 app.js 第 803–853 行的 DOMContentLoaded。內容一字不動，只多了一件事：
// **先等字典載進來**再初始化，因為字典現在是 `web/i18n/*.json` 而不是程式裡的物件。

import { initLanguage, loadDictionaries } from "./core/i18n.js";
import { POLL_MS } from "./core/state.js";
import { initActionDelegation, initCollapsiblePanels, initSettingsNav, initTabs, initTheme, loadDemoBanner } from "./core/ui.js";
import { initAssistantHome, initGreetingCard, initHomeDesk, loadAssistantStrip, loadGreeting, loadHome, loadSecretaryProposals, loadTodayView } from "./tabs/assistant.js";
import { initGitHubSection, loadGitHubStatus } from "./tabs/github.js";
import { initSystemHealthTab, loadSystemHealth } from "./tabs/health.js";
import { initRAGTab, loadRAGFolders, loadRAGSessions, loadRAGStrategies } from "./tabs/knowledge.js";
import { initMemoryPanel, loadMemoryPanel } from "./tabs/memory.js";
import { initFocusCarousel, loadOpenLoops, loadProjects, loadRepoSnapshot, refreshFeed } from "./tabs/projects.js";
import { initRepositorySyncSection } from "./tabs/repos.js";
import { initSettingsForm, loadConfig } from "./tabs/settings.js";
import { initControls, loadUsagePanels, refreshStatus } from "./tabs/status.js";
import { initCheckpointsTab, initSummariesTab, loadCheckpoints, loadSummaries } from "./tabs/summaries.js";

document.addEventListener("DOMContentLoaded", async () => {
  await loadDictionaries();
  initActionDelegation();

  initLanguage();
  initTheme();
  initTabs();
  initSettingsNav();
  initControls();
  initSettingsForm();
  initGitHubSection();
  initRepositorySyncSection();
  initSummariesTab();
  initCheckpointsTab();
  initRAGTab();
  initSystemHealthTab();
  initFocusCarousel();
  initAssistantHome();

  initCollapsiblePanels();

  loadDemoBanner();
  refreshStatus();
  refreshFeed();
  loadProjects();
  loadOpenLoops();
  loadConfig();
  loadGitHubStatus();
  loadSummaries();
  loadCheckpoints();
  loadUsagePanels();
  loadSecretaryProposals();
  loadAssistantStrip();
  loadTodayView();
  loadMemoryPanel();
  initMemoryPanel();
  loadRepoSnapshot();
  loadRAGFolders();
  loadRAGSessions();
  loadRAGStrategies();
  loadSystemHealth();

  setInterval(() => { refreshStatus(); refreshFeed(); }, POLL_MS);
  setInterval(loadUsagePanels, 30000);
  setInterval(loadAssistantStrip, 30000);
  // 問候卡：小秘書每 10 分鐘自動重新整理（統計只讀本機，成本很低）
  loadGreeting();
  initGreetingCard();
  setInterval(loadGreeting, 10 * 60 * 1000);
  // ADR-019 秘書桌面：與問候卡同節奏重新挑
  loadHome();
  initHomeDesk();
  setInterval(loadHome, 10 * 60 * 1000);
});
