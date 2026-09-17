// web/js/tabs/assistant.js — 01 小秘書：脈絡記憶、提案、秘書桌面、問候卡、今日視圖。

import { getJSON, postJSON, request } from "../core/api.js";
import { $, esc } from "../core/dom.js";
import { t } from "../core/i18n.js";
import { state } from "../core/state.js";
import { initCollapsiblePanels, registerActions, showToast } from "../core/ui.js";
import { sendRAGChatMessage, updateRAGModelSelect } from "../tabs/knowledge.js";
import { memoryKindLabel } from "../tabs/memory.js";
import { loadProjects, renderFocusCarousel, renderProjects } from "../tabs/projects.js";

export async function loadContextSessions() {
  const box = $("context-sessions-list");
  try {
    state.contextSessionsCache = await getJSON("/api/v1/context/sessions");
    renderContextSessions();
  } catch (e) {
    if (box) box.innerHTML = `<div class="placeholder">${state.currentLang === "zh-TW" ? "近期工作階段暫時無法讀取。" : "Recent work sessions are temporarily unavailable."}</div>`;
  }
}

export function formatContextTime(value) {
  const parsed = new Date(value);
  if (Number.isNaN(parsed.getTime())) return "—";
  return parsed.toLocaleString(state.currentLang === "zh-TW" ? "zh-TW" : "en", {
    month: "2-digit", day: "2-digit", hour: "2-digit", minute: "2-digit"
  });
}

export function renderContextSessions() {
  const box = $("context-sessions-list");
  const badge = $("context-sessions-badge");
  if (!box || !badge) return;
  if (!state.contextSessionsCache) {
    badge.textContent = "LOADING";
    return;
  }
  const sessions = state.contextSessionsCache.sessions || [];
  badge.textContent = `${sessions.length} SESSIONS`;
  badge.className = `trust ${sessions.length ? "ok" : "noisy"}`;
  if (!sessions.length) {
    box.innerHTML = `<div class="placeholder">${state.currentLang === "zh-TW" ? "近 72 小時沒有可歸戶的 AI、Git 或檔案事件。" : "No canonical AI, Git, or file events in the last 72 hours."}</div>`;
    return;
  }
  box.innerHTML = sessions.map(session => {
    const counts = session.event_counts || {};
    const chips = [
      counts.ai_turn ? `AI ${counts.ai_turn}` : "",
      counts.git_commit ? `GIT ${counts.git_commit}` : "",
      counts.file_activity ? `FILE ${counts.file_activity}` : "",
      `${session.events_observed || 0} EVENTS`
    ].filter(Boolean).map(label => `<span class="context-memory-chip">${esc(label)}</span>`).join("");
    return `
      <article class="context-session" data-session-id="${esc(session.session_id)}">
        <div class="context-session-top">
          <span class="context-session-project">${esc(session.project_key)}</span>
          <span class="context-session-time">${esc(formatContextTime(session.ended_at))}</span>
        </div>
        <div class="context-session-headline">${esc(session.headline || session.narrative || "—")}</div>
        <div class="context-session-meta">${chips}</div>
      </article>`;
  }).join("");
}

export async function searchRelatedContext() {
  const input = $("input-related-question");
  const button = $("btn-related-search");
  const box = $("related-memory-results");
  const question = (input.value || "").trim();
  if (question.length < 2) {
    showToast(state.currentLang === "zh-TW" ? "請先輸入至少兩個字。" : "Enter at least two characters.");
    return;
  }
  button.disabled = true;
  button.textContent = state.currentLang === "zh-TW" ? "查詢中…" : "Searching…";
  box.innerHTML = `<div class="placeholder">${state.currentLang === "zh-TW" ? "正在查詢本機 semantic index…" : "Searching the local semantic index…"}</div>`;
  try {
    state.relatedContextCache = await postJSON("/api/v1/context/related", {
      question,
      top_k: 8
    });
    renderRelatedContext(state.relatedContextCache);
  } catch (e) {
    state.relatedContextCache = null;
    box.innerHTML = `<div class="placeholder">${state.currentLang === "zh-TW" ? "本機 Ollama 或 semantic index 目前不可用；查詢內容未保存。" : "Local Ollama or the semantic index is unavailable; the query was not stored."}</div>`;
  } finally {
    button.disabled = false;
    button.textContent = t("btn_related_search");
  }
}

export function renderRelatedContext(data) {
  const box = $("related-memory-results");
  if (!box || !data) return;
  const matches = data.matches || [];
  const advisory = state.currentLang === "zh-TW"
    ? (matches.length ? "找到語意相近的歷史紀錄；請檢視來源後再決定是否可沿用。" : "沒有超過門檻的相似紀錄，但不代表歷史中一定沒有相關工作。")
    : (matches.length ? "Semantically related history found. Review each source before reuse." : "No match crossed the threshold; related history may still exist outside current coverage.");
  const rows = matches.map(item => `
    <div class="related-memory-match">
      <div class="related-memory-match-title">${esc(item.title || item.source_ref)}</div>
      <div class="related-memory-match-meta">${esc(item.source_ref)} · SCORE ${Number(item.score || 0).toFixed(3)} · ${esc(item.trust_status || "observed")}${item.project_key ? ` · ${esc(item.project_key)}` : ""}</div>
    </div>`).join("");
  box.innerHTML = `<div class="related-memory-advisory">${esc(advisory)}</div>${rows || ""}`;
}

// ---------------------------------------------------------------- P5-1 proposal-only secretary
export async function loadSecretaryProposals() {
  const box = $("secretary-proposals-list");
  try {
    state.secretaryProposalsCache = await getJSON("/api/v1/secretary/proposals?limit=6");
    renderSecretaryProposals();
    if (state.projectsCache.length) renderProjects();  // 專案卡的 💡 建議 chip 依提案快取更新
  } catch (e) {
    if (box) box.innerHTML = `<div class="placeholder">${state.currentLang === "zh-TW" ? "建議暫時無法讀取。" : "Suggestions are temporarily unavailable."}</div>`;
  }
}

export function renderSecretaryProposals() {
  const box = $("secretary-proposals-list");
  const badge = $("secretary-proposals-badge");
  if (!box || !badge) return;
  if (!state.secretaryProposalsCache) return;
  renderFocusCarousel();
  // 小秘書首頁的 advisor 徽章：LLM 註解啟用且成功時顯示 provider，否則 RULES
  const advisorBadge = $("assistant-advisor-badge");
  if (advisorBadge) {
    const adv = state.secretaryProposalsCache.advisor || null;
    if (adv && (adv.status === "annotated" || adv.status === "cached")) {
      advisorBadge.textContent = `LLM · ${String(adv.provider || "").toUpperCase()}`;
      advisorBadge.className = "trust ok";
    } else {
      advisorBadge.textContent = "RULES";
      advisorBadge.className = "trust noisy";
    }
  }
  const proposals = state.secretaryProposalsCache.proposals || [];
  badge.textContent = `${proposals.length} ${state.currentLang === "zh-TW" ? "項" : "ITEMS"}`;
  badge.className = `trust ${proposals.length ? "noisy" : "ok"}`;
  // 太舊而沒列入的 PR／issue 要說出來，不能悄悄消失（ADR-007 Addendum 2026-09-08）
  const staleNote = $("secretary-stale-note");
  if (staleNote) {
    const stale = (state.secretaryProposalsCache.inputs || {}).github_stale_excluded || null;
    if (stale && stale.total > 0) {
      staleNote.textContent = state.currentLang === "zh-TW"
        ? `另有 ${stale.total} 件超過 ${stale.threshold_days} 天沒更新的 PR／issue 不列入考量（PR ${stale.prs}、issue ${stale.issues}）；要看的話調 proactive_secretary.github_stale_after_days。`
        : `${stale.total} PR/issue item(s) idle for more than ${stale.threshold_days} days are left out (${stale.prs} PR, ${stale.issues} issue); adjust proactive_secretary.github_stale_after_days to include them.`;
      staleNote.title = (stale.subjects || []).map(x => `${x.subject_ref} · ${Math.round(x.age_days)}d`).join("\n");
      staleNote.hidden = false;
    } else {
      staleNote.hidden = true;
    }
  }
  if (!proposals.length) {
    box.innerHTML = `<div class="placeholder">${state.currentLang === "zh-TW" ? "目前沒有超過規則門檻的建議；不代表所有工作都已完成。" : "No suggestion crossed the current rule threshold; this does not prove all work is complete."}</div>`;
    return;
  }
  const zh = state.currentLang === "zh-TW";
  // P5-R1 advisory：LLM 只能註解，不能增刪或執行；fallback 時完全不顯示
  const advisor = state.secretaryProposalsCache.advisor || null;
  const advisorActive = advisor && (advisor.status === "annotated" || advisor.status === "cached");
  const advisorBanner = advisorActive
    ? `<div class="advisor-summary">
         <span class="advisor-tag">🧠 ${zh ? "LLM 參考註解" : "LLM ADVISORY"} · ${esc(advisor.provider || "")}${advisor.model ? ` / ${esc(advisor.model)}` : ""}</span>
         ${advisor.summary ? `<div class="advisor-text">${esc(advisor.summary)}</div>` : ""}
         <span class="advisor-boundary">${zh ? "僅供參考；不會執行任何動作，也不保存" : "Advisory only; nothing is executed or persisted"}</span>
       </div>`
    : "";
  box.innerHTML = advisorBanner + proposals.map(item => {
    const refs = (item.evidence_refs || []).map(ref => `<span class="proposal-ref">${esc(ref)}</span>`).join("");
    const priority = String(item.priority || "medium").toUpperCase();
    // detail 是 PR/issue 標題；title 只有 repo#number，兩者都要顯示才看得懂是什麼事
    const detail = item.detail ? `<div class="proposal-detail">${esc(item.detail)}</div>` : "";
    const llmHint = item.llm_priority_hint && item.llm_priority_hint !== item.priority
      ? ` <span class="advisor-hint">${zh ? "LLM 建議優先序" : "LLM hint"}: ${esc(String(item.llm_priority_hint).toUpperCase())}</span>`
      : "";
    const llmNote = item.llm_note
      ? `<div class="proposal-llm-note">🧠 ${esc(item.llm_note)}${llmHint}</div>`
      : "";
    // P5-R2/R3：executor 啟用且此 proposal 有白名單動作時才出現批准按鈕；
    // 動作內容由 server 端 template 決定，前端只傳 proposal_id（可選 template_id）。
    // L2 動作需二次確認（server 回 428 + 一次性確認碼）。
    const actions = item.execution_available
      ? (item.actions && item.actions.length ? item.actions : (item.action ? [item.action] : []))
      : [];
    const execTag = actions.length
      ? actions.map(act => {
          const tier = esc(String(act.risk_level || "").split("_")[0] || "L?");
          const confirmMark = act.requires_confirmation ? "🛡️ " : "⚡ ";
          return `<button class="btn btn-ghost btn-sm proposal-exec-btn" data-action="execute-proposal" data-proposal-id="${esc(item.proposal_id)}" data-template-id="${esc(act.template_id)}">${confirmMark}${zh ? "批准執行" : "Approve"}（${tier}）</button>`;
        }).join("")
      : `<span>${zh ? "不執行" : "NOT EXECUTABLE"}</span>`;
    const actionLabel = actions.length
      ? actions.map(act =>
          `<div class="proposal-exec-label">${zh ? "可代辦" : "Available action"}：${esc(act.label || act.template_id)}${act.requires_confirmation ? (zh ? "（L2 需輸入確認碼）" : " (L2 requires confirm code)") : ""}</div>`
        ).join("")
      : "";
    const link = item.url
      ? `<a class="proposal-link" href="${esc(item.url)}" target="_blank" rel="noopener">${zh ? "在 GitHub 開啟 →" : "Open on GitHub →"}</a>`
      : "";
    // 更主動：停滯／未收尾事項若尚無 L2 起草動作，直接告訴使用者開啟 L2 就能請小秘書先起草計畫
    const executorInfo = state.secretaryProposalsCache.executor || {};
    const stalledType = item.proposal_type === "stalled_open_loop" || item.proposal_type === "unfinished_recent";
    const hasDraft = actions.some(act => act.template_id === "agent_draft_plan");
    const l2Hint = stalledType && !hasDraft
      ? `<div class="proposal-l2-hint">🛡️ ${executorInfo.enabled && !executorInfo.l2_available
          ? (zh ? "開啟 L2（設定 → 小秘書執行器）後，小秘書可先為這件事起草重啟計畫，批准＋確認碼才執行。" : "Enable L2 (Settings → Executor) and the secretary can draft a restart plan for this; runs only after approval + confirm code.")
          : (zh ? "開啟執行器與 L2 後，小秘書可先為這件事起草重啟計畫（需批准＋確認碼）。" : "With the executor and L2 enabled, the secretary can draft a restart plan (approval + confirm code).")}</div>`
      : "";
    // 被每專案上限折疊掉的數量：讓使用者知道那裡還有多少事，而不是以為只有這些
    const pending = item.same_project_pending
      ? `<span class="proposal-pending">${zh ? `此專案另有 ${item.same_project_pending} 項` : `+${item.same_project_pending} more here`}</span>`
      : "";
    // ADR-022：會議候選待辦——你點了才成為未結事項，秘書自己不會。
    const followups = Array.isArray(item.meeting_followups) ? item.meeting_followups : [];
    const followupBlock = followups.length
      ? `<div class="proposal-followups">
           <div class="proposal-followups-hint">${zh ? "候選待辦（點「加入」才會成為未結事項）" : "Candidate follow-ups (only become open loops when you add them)"}</div>
           ${followups.map(f => `
             <div class="proposal-followup-row">
               <span class="proposal-followup-text">${esc(f.text)}</span>
               <button class="btn btn-ghost btn-sm" data-action="resolve-followup" data-note-id="${Number(item.meeting_note_id)}" data-index="${Number(f.index)}" data-resolution="accept" data-project-key="${esc(String(item.project_key || ""))}">${zh ? "加入未結事項" : "Add open loop"}</button>
               <button class="btn btn-ghost btn-sm" data-action="resolve-followup" data-note-id="${Number(item.meeting_note_id)}" data-index="${Number(f.index)}" data-resolution="ignore" data-project-key="">${zh ? "忽略" : "Ignore"}</button>
             </div>`).join("")}
         </div>`
      : "";
    const snoozeAttrs = `data-proposal-type="${esc(String(item.proposal_type))}" data-project-key="${esc(String(item.project_key))}" data-subject-ref="${esc(String(item.subject_ref || ""))}"`;
    return `
      <article class="proposal-card">
        <div class="proposal-card-top">
          <span class="proposal-project">${esc(item.project_key || "OmniContext")}</span>
          <span class="trust ${item.priority === "high" ? "broken" : "noisy"}">${esc(priority)}</span>
        </div>
        <div class="proposal-title">${esc(item.title)}</div>
        ${detail}
        <div class="proposal-reason">${esc(item.reason)}</div>
        ${item.why_now ? `<div class="proposal-why"><span>${esc(t("why_now_label"))}：</span>${esc(item.why_now)}</div>` : ""}
        ${item.memory_note ? `<div class="proposal-memory"><span>🧠 ${esc(t("memory_note_label"))}：</span>${esc(item.memory_note)}</div>` : ""}
        <div class="proposal-action"><span>${zh ? "建議" : "Suggested"}</span>${esc(item.suggested_action)}</div>
        ${llmNote}
        ${actionLabel}
        ${followupBlock}
        ${l2Hint}
        <div class="proposal-meta">
          <span>${esc(item.risk_level || "L0_READ_ONLY")}</span>
          ${execTag}
          ${pending}
          ${link}
          <button class="btn btn-ghost btn-sm" data-action="snooze-proposal" ${snoozeAttrs} data-days="7">${zh ? "7 天內不再提醒" : "Snooze 7d"}</button>
          <button class="btn btn-ghost btn-sm" data-action="snooze-proposal" ${snoozeAttrs} data-days="">${zh ? "不再提醒" : "Dismiss"}</button>
        </div>
        <div class="proposal-refs">${refs}</div>
      </article>`;
  }).join("");
}

// ADR-022：候選待辦→未結事項。這是唯一會寫 open_loops 的路徑；忽略只改觀察正文。
export async function resolveMeetingFollowup(noteId, index, action, projectKey) {
  const zh = state.currentLang === "zh-TW";
  try {
    const res = await request("/api/v1/secretary/meetings/followups", {
      method: "POST",
      body: { note_id: noteId, index, action, project_key: projectKey || null },
    });
    const data = await res.json().catch(() => ({}));
    if (!res.ok) throw new Error(data.detail || res.statusText);
    if (action === "accept" && data.open_loop_id) {
      alert(zh ? `已加入未結事項：${data.text}` : `Added as an open loop: ${data.text}`);
    }
    await loadSecretaryProposals();
    if (typeof loadProjects === "function") loadProjects();
  } catch (e) {
    alert((zh ? "處理候選待辦失敗: " : "Could not update the follow-up: ") + e.message);
  }
};

// 回饋迴路：使用者說「這個先不用提醒」。沒有這個，分流清單永遠不會變準。
export async function snoozeProposal(proposalType, projectKey, subjectRef, days) {
  const zh = state.currentLang === "zh-TW";
  const permanent = days === null;
  const label = permanent
    ? (zh ? "確定不再提醒這一項？" : "Dismiss this suggestion permanently?")
    : (zh ? `${days} 天內不再提醒這一項？` : `Snooze this suggestion for ${days} days?`);
  if (!confirm(label)) return;
  try {
    const res = await request("/api/v1/secretary/proposals/snooze", {
      method: "POST",
      body: ({
        proposal_type: proposalType,
        project_key: projectKey,
        subject_ref: subjectRef,
        days: permanent ? null : days,
        dismissed: permanent,
      }),
    });
    if (!res.ok) throw new Error(`HTTP ${res.status}`);
    await loadSecretaryProposals();
  } catch (err) {
    alert((zh ? "設定失敗：" : "Failed: ") + err.message);
  }
};

// P5-R2：批准執行白名單動作。前端只送 proposal_id + execution token；
// 執行什麼由 server 端 template 決定，token 只留在 sessionStorage（關分頁即失效）。
export async function executeProposal(proposalId, templateId = null, confirmCode = null) {
  const zh = state.currentLang === "zh-TW";
  const item = ((state.secretaryProposalsCache || {}).proposals || [])
    .find(p => p.proposal_id === proposalId);
  const acts = item ? (item.actions && item.actions.length ? item.actions : (item.action ? [item.action] : [])) : [];
  const act = templateId ? acts.find(a => a.template_id === templateId) : acts[0];
  const label = act ? (act.label || act.template_id) : proposalId;
  if (!confirmCode && !confirm((zh ? "批准執行：" : "Approve action: ") + label + (zh ? "？" : "?"))) return;

  let token = sessionStorage.getItem("omni_execution_token") || "";
  if (!token) {
    token = prompt(zh
      ? "輸入 execution token（在終端機執行 `omnicontext init --show-token` 取得）："
      : "Enter execution token (shown by `omnicontext init --show-token`):") || "";
    token = token.trim();
    if (!token) return;
    sessionStorage.setItem("omni_execution_token", token);
  }

  try {
    const body = {};
    if (templateId) body.template_id = templateId;
    if (confirmCode) body.confirm_code = confirmCode;
    const res = await request(`/api/v1/secretary/proposals/${encodeURIComponent(proposalId)}/execute`, {
      method: "POST",
      headers: { "x-omnicontext-execution-token": token },
      body,
    });
    const data = await res.json().catch(() => ({}));
    if (res.status === 401) {
      sessionStorage.removeItem("omni_execution_token");
      alert(data.detail || (zh ? "execution token 無效，請重試。" : "Invalid execution token."));
      return;
    }
    if (res.status === 428 && data.confirm) {
      // P5-R3 L2 二次確認：顯示 server 的一次性確認碼，要求使用者親手回填。
      const code = data.confirm.confirm_code || "";
      const typed = prompt(zh
        ? `此為 L2 動作（${data.confirm.label || ""}）。\n確認碼：${code}\n請輸入上方 6 碼以確認執行（${data.confirm.expires_in_seconds || 300} 秒內有效）：`
        : `L2 action (${data.confirm.label || ""}).\nConfirm code: ${code}\nType the 6-digit code to proceed:`) || "";
      if (!typed.trim()) return;
      await window.executeProposal(proposalId, data.confirm.template_id, typed.trim());
      return;
    }
    if (!res.ok) {
      alert((zh ? "未執行：" : "Rejected: ") + (data.detail || `HTTP ${res.status}`));
      return;
    }
    const receipt = data.receipt || {};
    let message = (zh ? "執行狀態：" : "Execution status: ") + (receipt.status || "?");
    if (data.result && data.result.handoff_markdown) {
      try {
        await navigator.clipboard.writeText(data.result.handoff_markdown);
        message += zh ? "\nContext Handoff 已複製到剪貼簿。" : "\nContext Handoff copied to clipboard.";
      } catch (e) {
        message += zh ? "\n（Handoff 產生成功，請由回應複製）" : "\n(Handoff generated.)";
      }
    }
    if (data.result && data.result.plan_markdown) {
      try {
        await navigator.clipboard.writeText(data.result.plan_markdown);
        message += zh ? "\n行動計畫已複製到剪貼簿。" : "\nDraft plan copied to clipboard.";
      } catch (e) { /* 剪貼簿被拒不影響結果 */ }
      if (data.result.output_path) {
        message += zh ? `\n完整輸出：${data.result.output_path}` : `\nFull output: ${data.result.output_path}`;
      }
    }
    if (data.result && typeof data.result.files_changed === "number" && data.result.changed_files) {
      const changed = data.result.changed_files;
      message += zh
        ? `\nAgent 修改了 ${data.result.files_changed} 個檔案（未 commit）`
        : `\nAgent modified ${data.result.files_changed} file(s) (not committed)`;
      if (changed.length) message += "\n- " + changed.slice(0, 8).join("\n- ");
      message += zh
        ? "\n請用 git diff 檢視；git checkout . 可整批還原。"
        : "\nReview with git diff; revert everything with git checkout .";
    }
    if (receipt.error_code) message += `\n(${receipt.error_code})`;
    alert(message);
    await loadSecretaryProposals();
  } catch (err) {
    alert((zh ? "執行失敗：" : "Execution failed: ") + err.message);
  }
};

// ---------------------------------------------------------------- 01 小秘書首頁（assistant home）
// 對話與 RAG 分頁共用同一條 session／歷史；首頁只是入口與精簡鏡像。
export function syncAssistantModelControls() {
  const providerSrc = $("select-rag-provider");
  const providerDst = $("select-assistant-provider");
  if (providerSrc && providerDst) {
    providerDst.innerHTML = providerSrc.innerHTML;
    providerDst.value = providerSrc.value;
  }
  const modelSrc = $("select-rag-model");
  const modelDst = $("select-assistant-model");
  if (modelSrc && modelDst) {
    modelDst.innerHTML = modelSrc.innerHTML;
    if (modelSrc.value) modelDst.value = modelSrc.value;
  }
}

export function sendAssistantMessage() {
  const input = $("input-assistant-prompt");
  if (!input) return;
  // 送出前把首頁的 provider/model 同步回 RAG 分頁控制項，走同一條發送流程。
  const providerDst = $("select-assistant-provider");
  const modelDst = $("select-assistant-model");
  const providerSrc = $("select-rag-provider");
  const modelSrc = $("select-rag-model");
  if (providerDst && providerSrc && providerDst.value) providerSrc.value = providerDst.value;
  if (modelDst && modelSrc && modelDst.value) modelSrc.value = modelDst.value;
  sendRAGChatMessage(input);
}

export function initAssistantHome() {
  registerActions({
    "execute-proposal": (d) => executeProposal(d.proposalId, d.templateId || null),
    "resolve-followup": (d) => resolveMeetingFollowup(Number(d.noteId), Number(d.index), d.resolution, d.projectKey),
    "snooze-proposal": (d) => snoozeProposal(d.proposalType, d.projectKey, d.subjectRef, d.days === "" ? null : Number(d.days)),
  });
  syncAssistantModelControls();
  const providerDst = $("select-assistant-provider");
  if (providerDst) {
    providerDst.addEventListener("change", () => {
      const src = $("select-rag-provider");
      if (src) src.value = providerDst.value;
      updateRAGModelSelect(providerDst.value);
      syncAssistantModelControls();
    });
  }
  const modelDst = $("select-assistant-model");
  if (modelDst) {
    modelDst.addEventListener("change", () => {
      const src = $("select-rag-model");
      if (src) src.value = modelDst.value;
    });
  }
  const sendBtn = $("btn-assistant-send");
  const input = $("input-assistant-prompt");
  if (sendBtn) sendBtn.addEventListener("click", sendAssistantMessage);
  if (input) {
    input.addEventListener("keydown", event => {
      if (event.key === "Enter") sendAssistantMessage();
    });
  }
}

export function renderAssistantChatMirror() {
  const box = $("assistant-chat-messages");
  if (!box) return;
  const zh = state.currentLang === "zh-TW";
  if (!state.ragChatHistory.length) {
    box.innerHTML = `<div class="placeholder">${esc(t("assistant_chat_empty"))}</div>`;
    return;
  }
  const recent = state.ragChatHistory.slice(-12);
  box.innerHTML = recent.map(msg => {
    const isUser = msg.role === "user";
    const cites = !isUser && Array.isArray(msg.citations) && msg.citations.length
      ? `<div class="assistant-cite-chip">📎 ${msg.citations.length} ${zh ? "則引用 · 詳見知識庫分頁" : "citations · see RAG tab"}</div>`
      : "";
    const mem = !isUser && msg.memory && msg.memory.included
      ? `<span class="assistant-memory-chip" title="${esc((msg.memory.sections || []).join(", "))}">🧠 ${zh ? `參考記憶區 ${msg.memory.notes_used || 0} 筆` : `memory: ${msg.memory.notes_used || 0} notes`}${msg.memory.truncated ? (zh ? "（已截斷）" : " (truncated)") : ""}</span>`
      : "";
    return `
      <div class="assistant-msg ${isUser ? "user" : "bot"}">
        <span class="assistant-msg-role">${isUser ? (zh ? "您" : "YOU") : "🤖"}</span>
        <div class="assistant-msg-body">${esc(msg.content || "…")}${mem}${cites}</div>
      </div>`;
  }).join("");
  box.scrollTop = box.scrollHeight;
}

export function assistantChip(label, value, tone) {
  return `
    <div class="assistant-chip">
      <span class="mono-mini muted">${esc(label)}</span>
      <span class="assistant-chip-value ${tone || ""}">${esc(value)}</span>
    </div>`;
}

export async function loadAssistantStrip() {
  const box = $("assistant-strip");
  if (!box) return;
  const zh = state.currentLang === "zh-TW";
  const [usage, background] = await Promise.all([
    getJSON("/api/v1/usage/today").catch(() => null),
    getJSON("/api/v1/background-tasks/today").catch(() => null),
  ]);
  const chips = [];
  if (usage && usage.goal) {
    chips.push(assistantChip(
      zh ? "AI 協作前景" : "AI FOREGROUND",
      `${usage.goal.foreground_minutes ?? 0} min`,
      ""
    ));
    chips.push(assistantChip(
      "COVERAGE",
      String(usage.coverage_status || "?").toUpperCase(),
      usage.coverage_status === "observed" ? "ok" : ""
    ));
  }
  if (background) {
    chips.push(assistantChip(
      zh ? "背景任務" : "BG TASKS",
      `${background.verified_minutes ?? 0} min · ${background.completed_task_count ?? 0} ${zh ? "件" : "done"}`,
      ""
    ));
  }
  const proposals = state.secretaryProposalsCache && Array.isArray(state.secretaryProposalsCache.proposals)
    ? state.secretaryProposalsCache.proposals.length
    : null;
  if (proposals !== null) {
    chips.push(assistantChip(zh ? "待判斷建議" : "SUGGESTIONS", String(proposals), proposals ? "" : "ok"));
  }
  box.innerHTML = chips.join("");
}


export async function loadTodayView() {
  const pack = $("today-pack");
  const presetBtn = $("btn-create-presets");
  try {
    state.todayViewCache = await getJSON("/api/v1/secretary/today");
  } catch (e) {
    state.todayViewCache = null;
    if (pack) pack.hidden = true;
    return;
  }
  const zh = state.currentLang === "zh-TW";
  const sched = state.todayViewCache.schedules || {};
  const calBox = $("today-calendar");
  if (calBox) {
    const cal = state.todayViewCache.calendar || {};
    if (cal.enabled && cal.line) {
      calBox.textContent = `📅 ${cal.line}`;
      calBox.title = cal.claim_boundary || "";
      calBox.hidden = false;
    } else {
      calBox.hidden = true;
    }
  }
  if (pack) {
    if (state.todayViewCache.pack_line) {
      const when = String((state.todayViewCache.pack || {}).finished_at || "").slice(5, 16).replace("T", " ");
      pack.innerHTML = `<strong>${esc(state.todayViewCache.pack_line)}</strong> · ${esc(when)}`;
      pack.hidden = false;
    } else if (sched.scheduled_tasks_enabled && !sched.all_present) {
      pack.textContent = zh
        ? "尚未建立每日排程；按「📦 建立每日排程」讓小秘書每天 07:30 產同步報告與 Handoff。"
        : "No daily schedules yet. Click “📦 Create daily schedules” for a 07:30 morning pack.";
      pack.hidden = false;
    } else if (!sched.executor_enabled || !sched.scheduled_tasks_enabled) {
      // 排程跟著執行器開關（D6）；只有設定檔還留著已淘汰的 scheduled_tasks.enabled: false 時才另有說法。
      const legacy = sched.scheduled_tasks_legacy_opt_out === true;
      pack.textContent = zh
        ? (legacy
          ? "設定檔仍留著已淘汰的 executor.scheduled_tasks.enabled: false，排程因此關著；刪掉那一行就會跟著執行器開關。"
          : "小秘書排程未啟用（設定 → 小秘書執行器 → 啟用執行器）；啟用後可一鍵建立每日早晨包。")
        : (legacy
          ? "Your config still sets the retired executor.scheduled_tasks.enabled: false, so schedules stay off; delete that line to follow the executor switch."
          : "Scheduled tasks are off (Settings → Executor); enable the executor to create the daily morning pack.");
      pack.hidden = false;
    } else {
      pack.hidden = true;
    }
  }
  if (presetBtn) {
    if (sched.all_present) {
      presetBtn.textContent = zh ? "✅ 每日排程已建立" : "✅ Daily schedules ready";
      presetBtn.disabled = true;
    } else {
      presetBtn.textContent = t("btn_create_presets");
      presetBtn.disabled = false;
    }
  }
}

export async function createSchedulePresets() {
  const zh = state.currentLang === "zh-TW";
  if (!confirm(zh
    ? "建立兩個每日排程？\n• 07:30 早晨包：Repo 同步報告＋STATUS 草稿＋活躍專案 Handoff\n• 21:30 晚間：今天有活動的專案各產一份 Handoff\n全部是 L0 唯讀動作，不會 fetch、不改任何 repo。"
    : "Create two daily schedules?\n• 07:30 morning pack (repo sync report, STATUS draft, active-project handoffs)\n• 21:30 evening handoffs for today's active projects\nAll L0 read-only.")) return;
  let token = sessionStorage.getItem("omni_execution_token") || "";
  if (!token) {
    token = prompt(zh
      ? "輸入 execution token（在終端機執行 `omnicontext init --show-token` 取得）："
      : "Enter execution token (shown by `omnicontext init --show-token`):") || "";
    token = token.trim();
    if (!token) return;
    sessionStorage.setItem("omni_execution_token", token);
  }
  try {
    const res = await request("/api/v1/secretary/scheduled-tasks/presets", {
      method: "POST", headers: { "x-omnicontext-execution-token": token },
    });
    const data = await res.json().catch(() => ({}));
    if (res.status === 401) { sessionStorage.removeItem("omni_execution_token"); alert(data.detail || (zh ? "execution token 無效。" : "Invalid execution token.")); return; }
    if (!res.ok) { alert((zh ? "未建立：" : "Not created: ") + (data.detail || `HTTP ${res.status}`)); return; }
    showToast(zh ? `已建立 ${(data.created || []).length} 個排程（${(data.already_present || []).length} 個原本就有）` : `${(data.created || []).length} schedules created (${(data.already_present || []).length} already existed)`);
    loadTodayView();
  } catch (e) {
    alert((zh ? "建立失敗：" : "Failed: ") + e.message);
  }
}

// ---------------------------------------------------------------- 小秘書問候卡（今天／近 2 小時做了什麼＋一句鼓勵）

export async function loadGreeting() {
  const textBox = $("greeting-text");
  if (!textBox) return;
  try {
    state.greetingCache = await getJSON(`/api/v1/secretary/greeting?window=${encodeURIComponent(state.greetingWindow)}`);
  } catch (e) {
    textBox.innerHTML = `<span class="placeholder">${state.currentLang === "zh-TW" ? "問候卡暫時讀不到。" : "Greeting unavailable."}</span>`;
    return;
  }
  renderGreeting();
}

export function renderGreeting() {
  const g = state.greetingCache;
  const textBox = $("greeting-text");
  const statsBox = $("greeting-stats");
  const source = $("greeting-source");
  const boundary = $("greeting-boundary");
  if (!g || !textBox) return;
  const zh = state.currentLang === "zh-TW";
  const stats = g.stats || {};
  if (g.source === "llm" && g.text) {
    textBox.innerHTML = `<p class="greeting-line">${esc(g.text)}</p>`;
  } else {
    const items = (g.achievements || []).map(a => `<li>${esc(a)}</li>`).join("");
    textBox.innerHTML = `
      <p class="greeting-line greeting-headline">${esc(g.headline)}</p>
      <p class="greeting-line">${esc(g.lead)}</p>
      ${items ? `<ul class="greeting-list">${items}</ul>` : ""}
      ${g.recent_summary ? `<p class="greeting-line muted">${zh ? "剛剛在做：" : "Just now: "}${esc(g.recent_summary)}</p>` : ""}
      ${g.schedule_line ? `<p class="greeting-line greeting-schedule">📅 ${esc(g.schedule_line)}</p>` : ""}
      <p class="greeting-line greeting-encourage">${esc(g.encouragement)}</p>`;
  }
  if (source) {
    source.textContent = g.source === "llm" ? `LLM · ${String(g.llm_provider || "").toUpperCase()}` : "RULES";
    source.className = "trust ok";
    source.title = zh ? `鼓勵語池：${g.encouragement_pool || ""}` : `pool: ${g.encouragement_pool || ""}`;
    source.hidden = g.source !== "llm";   // 問候併進桌面後，規則版不重複掛第二顆 RULES；LLM 潤飾時才標示來源
  }
  if (boundary) boundary.title = g.claim_boundary || "";
  if (statsBox) {
    const chips = [];
    const add = (label, value, title) => { if (value) chips.push(`<span class="pchip" title="${esc(title || "")}">${esc(label)} ${esc(String(value))}</span>`); };
    add(zh ? "專案" : "projects", stats.projects_touched, g.evidence && g.evidence.projects);
    add("commit", stats.commits, g.evidence && g.evidence.commits);
    add("PR", stats.prs_touched, g.evidence && g.evidence.prs);
    add(zh ? "AI 對話" : "AI turns", stats.ai_turns, g.evidence && g.evidence.ai_turns);
    add(zh ? "文件" : "docs", stats.files_writing, g.evidence && g.evidence.files);
    add(zh ? "收掉" : "resolved", stats.loops_resolved, g.evidence && g.evidence.loops_resolved);
    add(zh ? "會議" : "meetings", stats.meetings, g.evidence && g.evidence.meetings);
    if (stats.foreground_minutes >= 15) add(zh ? "專注" : "focus", `${Math.round(stats.foreground_minutes)} min`, g.evidence && g.evidence.foreground_minutes);
    statsBox.innerHTML = chips.join("");
    statsBox.hidden = chips.length === 0;
  }
  if (boundary) boundary.textContent = g.claim_boundary || "";
  document.querySelectorAll(".greeting-tools .chip").forEach(c => c.classList.toggle("active", c.dataset.window === state.greetingWindow));
}

export function initGreetingCard() {
  document.querySelectorAll(".greeting-tools .chip").forEach(chip => {
    chip.addEventListener("click", () => { state.greetingWindow = chip.dataset.window || "today"; loadGreeting(); });
  });
  const refresh = $("btn-greeting-refresh");
  if (refresh) refresh.addEventListener("click", loadGreeting);
}

// ---------------------------------------------------------------- ADR-019 秘書桌面：01 是首頁
// 卡片由秘書決定該顯示什麼（焦點一張、記得一則、上次做到哪）；完整清單降為可展開的詳情，
// 其他分頁是更深的詳情。規則在後端（/api/v1/secretary/home），這裡只呈現＋接到對話框。
export const HOME_LEAVES_KEY = "omni-home-leaves";

export function localDateKey(d = new Date()) {
  return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}-${String(d.getDate()).padStart(2, "0")}`;
}
export function readHomeLeaves() {
  try {
    const raw = JSON.parse(localStorage.getItem(HOME_LEAVES_KEY) || "{}");
    return raw && typeof raw === "object" ? raw : {};
  } catch (_) { return {}; }
}
export function recordHomeLeave() {
  const data = readHomeLeaves();
  const key = localDateKey();
  data[key] = (Number(data[key]) || 0) + 1;
  Object.keys(data).sort().slice(0, -14).forEach(k => delete data[k]);   // 只留 14 天
  try { localStorage.setItem(HOME_LEAVES_KEY, JSON.stringify(data)); } catch (_) { /* 無 localStorage 就不量 */ }
  renderHomeLeaves();
}
export function homeLeaveCounts() {
  const data = readHomeLeaves();
  const y = new Date(); y.setDate(y.getDate() - 1);
  return { today: Number(data[localDateKey()]) || 0, yesterday: Number(data[localDateKey(y)]) || 0 };
}
export function renderHomeLeaves() {
  const el = $("home-leaves");
  if (el) el.textContent = t("home_leaves", homeLeaveCounts());
}

export async function loadHome() {
  const slots = $("home-desk-slots");
  if (!slots) return;
  try {
    state.homeCache = await getJSON("/api/v1/secretary/home");
  } catch (e) {
    state.homeCache = null;
    slots.innerHTML = `<div class="placeholder">${esc(t("home_unavailable"))}</div>`;
    return;
  }
  renderHome();
}

export function homeActionButtons(item) {
  const zh = state.currentLang === "zh-TW";
  const actions = item.execution_available
    ? (item.actions && item.actions.length ? item.actions : (item.action ? [item.action] : []))
    : [];
  return actions.map(act => {
    const tier = esc(String(act.risk_level || "").split("_")[0] || "L?");
    return `<button class="btn btn-ghost btn-sm" data-action="execute-proposal" data-proposal-id="${esc(item.proposal_id)}" data-template-id="${esc(act.template_id)}">${act.requires_confirmation ? "🛡️" : "⚡"} ${zh ? "批准執行" : "Approve"}（${tier}）</button>`;
  }).join("");
}

export function renderHome() {
  const h = state.homeCache;
  const slots = $("home-desk-slots");
  if (!h || !slots) return;
  const zh = state.currentLang === "zh-TW";
  const errors = Object.entries(h.sections || {}).filter(([, v]) => String(v).startsWith("error"));
  const badge = $("home-desk-badge");
  if (badge) {
    badge.textContent = errors.length ? "PARTIAL" : "RULES";
    badge.className = `trust ${errors.length ? "broken" : "ok"}`;
    badge.title = errors.length ? errors.map(([k, v]) => `${k}: ${v}`).join("\n") : (h.claim_boundary || "");
  }
  const prof = $("home-desk-profile");
  if (prof) { prof.textContent = h.profile_line || ""; prof.title = h.profile_line || ""; prof.hidden = !h.profile_line; }
  const cal = $("home-desk-calendar");
  const calendar = h.calendar || {};
  if (cal) {
    if (calendar.enabled && calendar.line) { cal.textContent = `📅 ${calendar.line}`; cal.title = calendar.claim_boundary || ""; cal.hidden = false; }
    else cal.hidden = true;
  }
  // ADR-022：會議中／訊號不一致時如實說明差異（不讀會議內容、不錄音）
  const meetingBox = $("home-desk-meeting");
  if (meetingBox) {
    const meeting = h.meeting || {};
    if (meeting.line) {
      meetingBox.textContent = meeting.line;
      meetingBox.title = meeting.claim_boundary || "";
      meetingBox.hidden = false;
    } else {
      meetingBox.hidden = true;
    }
  }

  // 焦點：提案引擎排序後的第一張
  const focus = (h.focus || {}).proposal || null;
  const remaining = (h.focus || {}).remaining || 0;
  let focusHtml;
  if (focus) {
    const link = focus.url ? `<a class="btn btn-ghost btn-sm" href="${esc(focus.url)}" target="_blank" rel="noopener">GitHub →</a>` : "";
    const ask = t("home_ask_focus_prompt", { title: focus.title || "", project: focus.project_key || "OmniContext", why: focus.why_now || focus.reason || "" });
    focusHtml = `
      <div class="home-slot home-slot-focus">
        <div class="home-slot-label">${esc(t("home_focus_label"))}<span class="trust ${focus.priority === "high" ? "broken" : "noisy"}">${esc(String(focus.priority || "medium").toUpperCase())}</span></div>
        <div class="home-slot-project">${esc(focus.project_key || "OmniContext")} · ${esc(focus.proposal_type || "")}</div>
        <div class="home-slot-title">${esc(focus.title || "")}</div>
        ${focus.detail ? `<div class="home-slot-body">${esc(focus.detail)}</div>` : ""}
        ${focus.why_now ? `<div class="home-slot-why"><strong>${esc(t("home_why_now"))}：</strong>${esc(focus.why_now)}</div>` : ""}
        ${focus.reason ? `<div class="home-slot-why">${esc(focus.reason)}</div>` : ""}
        <div class="home-slot-actions">
          ${homeActionButtons(focus)}${link}
          <button class="btn btn-ghost btn-sm" data-home-ask="${esc(ask)}">${esc(t("home_ask"))}</button>
          <button class="btn btn-ghost btn-sm" data-home-detail="proposals">${esc(remaining ? t("home_more_proposals", { n: remaining }) : t("home_detail_proposals"))} →</button>
        </div>
      </div>`;
  } else {
    focusHtml = `<div class="home-slot home-slot-focus is-empty"><div class="home-slot-label">${esc(t("home_focus_label"))}</div><div class="home-slot-body">${esc(t("home_no_focus"))}</div></div>`;
  }

  // 記得：依固定順序挑的一則
  const pick = h.memory_pick || {};
  const note = pick.note || null;
  let memHtml;
  if (note) {
    const body = String(note.body || "");
    const shortBody = body.length > 220 ? body.slice(0, 220) + "…" : body;
    const ask = t("home_ask_memory_prompt", { title: note.title || shortBody.slice(0, 60) });
    memHtml = `
      <div class="home-slot home-slot-memory">
        <div class="home-slot-label">${esc(t("home_memory_label"))}<span class="trust noisy">${esc(memoryKindLabel(note.kind))}</span></div>
        <div class="home-slot-project">${note.project_key ? esc(note.project_key) + " · " : ""}${esc(String(note.created_at || "").slice(0, 16).replace("T", " "))}</div>
        ${note.title ? `<div class="home-slot-title">${esc(note.title)}</div>` : ""}
        <div class="home-slot-body">${esc(shortBody)}</div>
        <div class="home-slot-why"><strong>${esc(t("home_why_this"))}：</strong>${esc(pick.why_this || "")}</div>
        <div class="home-slot-actions">
          <button class="btn btn-ghost btn-sm" data-home-ask="${esc(ask)}">${esc(t("home_ask"))}</button>
          <button class="btn btn-ghost btn-sm" data-home-detail="memory">${esc(t("home_detail_memory"))} →</button>
        </div>
      </div>`;
  } else {
    memHtml = `<div class="home-slot home-slot-memory is-empty"><div class="home-slot-label">${esc(t("home_memory_label"))}</div><div class="home-slot-body">${esc(zh ? (pick.hint || t("home_no_memory")) : t("home_no_memory"))}</div></div>`;
  }
  slots.innerHTML = focusHtml + memHtml;

  // 詳情：面板展開或跳分頁，並顯示「一天離開首頁幾次」
  const d = h.details || {};
  const det = $("home-desk-details");
  if (det) {
    const chip = (key, label, count) => `<button class="home-detail-chip" data-home-detail="${key}">${esc(label)}${count !== undefined && count !== null ? `<b>${esc(String(count))}</b>` : ""}</button>`;
    det.innerHTML = `<span class="mono-label">${esc(t("home_details_label"))}</span>`
      + chip("proposals", t("home_detail_proposals"), d.proposals)
      + chip("memory", t("home_detail_memory"), d.notes)
      + chip("projects", t("home_detail_projects"), d.active_projects)
      + chip("repos", t("home_detail_repos"))
      + chip("knowledge", t("home_detail_knowledge"))
      + chip("summaries", t("home_detail_summaries"))
      + `<span class="home-leaves" id="home-leaves"></span>`;
    renderHomeLeaves();
  }
  const boundary = $("home-desk-boundary");
  if (boundary) { boundary.textContent = h.claim_boundary || ""; boundary.title = h.claim_boundary || ""; }
}

export function askSecretaryAbout(text) {
  const input = $("input-assistant-prompt");
  if (!input) return;
  input.value = String(text || "");
  input.focus();
  input.scrollIntoView({ behavior: "smooth", block: "center" });
}

export function openDetailsPanel(id) {
  const panel = $(id);
  if (!panel) return;
  panel.open = true;   // toggle 事件會把狀態記進 localStorage（initCollapsiblePanels）
  panel.scrollIntoView({ behavior: "smooth", block: "start" });
}

export function homeDetailAction(kind) {
  const tabs = { projects: "tab-projects", repos: "tab-repos", knowledge: "tab-knowledge", summaries: "tab-summaries" };
  if (kind === "proposals") { openDetailsPanel("today-panel"); return; }
  if (kind === "memory") { openDetailsPanel("memory-panel"); return; }
  const btn = tabs[kind] ? document.querySelector(`.tab[data-tab="${tabs[kind]}"]`) : null;
  if (btn) btn.click();
}

export function initHomeDesk() {
  const desk = $("home-desk");
  if (!desk) return;
  desk.addEventListener("click", (ev) => {
    const ask = ev.target.closest("[data-home-ask]");
    if (ask) { askSecretaryAbout(ask.dataset.homeAsk); return; }
    const det = ev.target.closest("[data-home-detail]");
    if (det) homeDetailAction(det.dataset.homeDetail);
  });
  const refresh = $("btn-home-refresh");
  if (refresh) refresh.addEventListener("click", () => { loadHome(); loadGreeting(); });   // 問候併進桌面，共用一顆
  // 詳情面板的 summary 裡有按鈕：按按鈕不該同時開合面板
  document.querySelectorAll("#today-panel summary button, #memory-panel summary button").forEach(btn => {
    btn.addEventListener("click", (ev) => { ev.preventDefault(); ev.stopPropagation(); });
  });
}

// ---------------------------------------------------------------- ADR-012 小秘書記憶區
