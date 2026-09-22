// web/js/tabs/memory.js — 01 記憶區（ADR-012）：筆記、偏好、決定、觀察與個人檔案。

import { getJSON, postJSON, sendJSON } from "../core/api.js";
import { $, esc } from "../core/dom.js";
import { t } from "../core/i18n.js";
import { API, state } from "../core/state.js";
import { showToast } from "../core/ui.js";
import { loadSecretaryProposals } from "../tabs/assistant.js";
import { renderRAGMessages } from "../tabs/knowledge.js";

export const MEMORY_COMMANDS = [
  { kind: "user_note", re: /^\s*(?:記下來|記住|筆記|\/note|remember)\s*[:：]?\s*([\s\S]+)$/ },
  { kind: "preference", re: /^\s*(?:偏好|\/pref(?:erence)?)\s*[:：]?\s*([\s\S]+)$/ },
  { kind: "decision", re: /^\s*(?:決定|\/decision|decide)\s*[:：]?\s*([\s\S]+)$/ },
];

// 與後端 core/secretary_memory.parse_note_command 同一套前綴；只在前端先判斷要不要送 LLM。
export function parseMemoryCommand(text) {
  for (const cmd of MEMORY_COMMANDS) {
    const m = cmd.re.exec(text || "");
    if (!m) continue;
    let body = m[1].trim();
    let projectKey = null;
    const at = /^@([\w.\-]+)\s*[:：]?\s*([\s\S]*)$/.exec(body);
    const bracket = /^\[([^\]]{1,120})\]\s*([\s\S]*)$/.exec(body);
    if (at) { projectKey = at[1]; body = at[2].trim(); }
    else if (bracket) { projectKey = bracket[1].trim(); body = bracket[2].trim(); }
    if (!body) return null;
    return { kind: cmd.kind, body, project_key: projectKey };
  }
  return null;
}

export function memoryKindLabel(kind) {
  const zh = state.ui.currentLang === "zh-TW";
  return ({
    user_note: zh ? "筆記" : "note",
    preference: zh ? "偏好" : "preference",
    decision: zh ? "決定" : "decision",
    observation: zh ? "觀察" : "observation",
  })[kind] || kind;
}

export async function rememberFromChat(cmd, rawPrompt) {
  const zh = state.ui.currentLang === "zh-TW";
  state.rag.history.push({ role: "user", content: rawPrompt, time: new Date().toLocaleTimeString() });
  let reply;
  try {
    const note = await postJSON("/api/v1/secretary/memory", { ...cmd, source: "chat" });
    reply = zh
      ? `🧠 已記下（${memoryKindLabel(note.kind)}${note.project_key ? ` · ${note.project_key}` : ""}）：${note.body}\n之後回答與提案都會參考；可在下方記憶區刪除。`
      : `🧠 Remembered (${memoryKindLabel(note.kind)}${note.project_key ? ` · ${note.project_key}` : ""}): ${note.body}\nFuture answers and proposals will use it; delete it in the memory panel below.`;
    loadMemoryPanel();
  } catch (e) {
    reply = (zh ? "沒有記下：" : "Not saved: ") + e.message;
  }
  state.rag.history.push({ role: "assistant", content: reply, citations: [], time: new Date().toLocaleTimeString() });
  renderRAGMessages();
}

export async function loadMemoryPanel() {
  const list = $("memory-list");
  const badge = $("memory-badge");
  if (!list) return;
  try {
    state.memory.notes = await getJSON("/api/v1/secretary/memory?limit=60");
    try { state.memory.profile = await getJSON("/api/v1/secretary/profile"); } catch (_) { state.memory.profile = null; }
  } catch (e) {
    state.memory.notes = null;
    list.innerHTML = `<div class="placeholder">${state.ui.currentLang === "zh-TW" ? "記憶區暫時讀不到。" : "Memory unavailable."}</div>`;
    if (badge) { badge.textContent = "—"; badge.className = "trust noisy"; }
    return;
  }
  renderMemoryList();
}

export function renderMemoryList() {
  const list = $("memory-list");
  const badge = $("memory-badge");
  const clearBtn = $("btn-memory-clear-obs");
  if (!list || !state.memory.notes) return;
  const zh = state.ui.currentLang === "zh-TW";
  const notes = state.memory.notes.notes || [];
  const counts = state.memory.notes.counts || {};
  if (badge) {
    badge.textContent = `${state.memory.notes.total || 0} ${zh ? "筆" : "NOTES"}`;
    badge.className = `trust ${state.memory.notes.total ? "ok" : "noisy"}`;
  }
  if (clearBtn) clearBtn.disabled = !(counts.observation > 0);
  // ADR-018：偏好筆記裡宣告的「本期優先」「語氣」——只是偏好的一種讀法，不是另一套資料。
  const prof = state.memory.profile;
  const profileStrip = prof && prof.declared
    ? `<div class="memory-profile" title="${esc(t("memory_profile_hint"))}">
         ${(prof.priorities || []).length ? `<span class="mono-mini muted">${esc(t("memory_profile_priorities"))}</span> ${prof.priorities.map(p => `<span class="pchip">${esc(p)}</span>`).join(" ")}` : ""}
         ${prof.tone_declared ? `<span class="mono-mini muted">${esc(t("memory_profile_tone"))}</span> <span class="trust ok">${esc(zh ? prof.tone_label : prof.tone)}</span>` : ""}
       </div>`
    : `<div class="memory-profile is-empty mono-mini muted" title="${esc(t("memory_profile_hint"))}">${esc(t("memory_profile_none"))}</div>`;
  if (!notes.length) {
    list.innerHTML = profileStrip + `<div class="placeholder">${zh
      ? "還沒有任何記憶。上方輸入或在對話框打「記下來：…」；早晨包跑過後秘書也會留下觀察。"
      : "Nothing remembered yet. Use the form above or type “remember: …” in the chat; the morning pack also leaves observations."}</div>`;
    return;
  }
  list.innerHTML = profileStrip + notes.map(n => {
    const when = String(n.created_at || "").slice(0, 16).replace("T", " ");
    const proj = n.project_key ? `<span class="pchip">${esc(n.project_key)}</span>` : "";
    const title = n.title ? `<strong>${esc(n.title)}</strong> · ` : "";
    const src = n.kind === "observation" ? ` · ${esc(n.source || "")}` : "";
    return `
      <div class="memory-item" data-note-id="${n.id}">
        <span class="memory-kind ${esc(n.kind)}">${esc(memoryKindLabel(n.kind))}</span>
        <div class="memory-item-body">${title}${esc(n.body)}${proj}<div class="memory-item-meta">${esc(when)}${src}</div></div>
        <button class="btn btn-ghost btn-sm btn-delete" data-delete-note="${n.id}" title="${zh ? "刪除這筆" : "Delete"}">✕</button>
      </div>`;
  }).join("");
  list.querySelectorAll("[data-delete-note]").forEach(btn => {
    btn.addEventListener("click", () => deleteMemoryNote(Number(btn.getAttribute("data-delete-note"))));
  });
}

export async function deleteMemoryNote(noteId) {
  const zh = state.ui.currentLang === "zh-TW";
  try {
    await sendJSON(`/api/v1/secretary/memory/${noteId}`, "DELETE");
    showToast(zh ? "已刪除" : "Deleted");
    loadMemoryPanel();
    loadSecretaryProposals();
  } catch (e) {
    alert((zh ? "刪除失敗：" : "Delete failed: ") + e.message);
  }
}

export async function addMemoryNote() {
  const zh = state.ui.currentLang === "zh-TW";
  const bodyInput = $("input-memory-body");
  const projectInput = $("input-memory-project");
  const kindSelect = $("select-memory-kind");
  const body = (bodyInput && bodyInput.value || "").trim();
  if (!body) return;
  try {
    await postJSON("/api/v1/secretary/memory", {
      kind: kindSelect ? kindSelect.value : "user_note",
      body,
      project_key: (projectInput && projectInput.value || "").trim() || null,
      source: "web",
    });
    if (bodyInput) bodyInput.value = "";
    showToast(zh ? "已記下" : "Remembered");
    loadMemoryPanel();
    loadSecretaryProposals();
  } catch (e) {
    alert((zh ? "沒有記下：" : "Not saved: ") + e.message);
  }
}

export async function clearMemoryObservations() {
  const zh = state.ui.currentLang === "zh-TW";
  if (!confirm(zh ? "刪除秘書自己寫的所有觀察？您的筆記、偏好與決定不受影響。" : "Delete every secretary observation? Your notes, preferences and decisions are untouched.")) return;
  try {
    const data = await sendJSON("/api/v1/secretary/memory?kind=observation", "DELETE");
    showToast(zh ? `已刪除 ${data.deleted || 0} 則觀察` : `${data.deleted || 0} observations deleted`);
    loadMemoryPanel();
  } catch (e) {
    alert((zh ? "清除失敗：" : "Clear failed: ") + e.message);
  }
}

export async function toggleMemoryContext() {
  const box = $("memory-context-box");
  if (!box) return;
  if (!box.hidden) { box.hidden = true; return; }
  const zh = state.ui.currentLang === "zh-TW";
  box.textContent = zh ? "整理中…" : "Loading…";
  box.hidden = false;
  try {
    const data = await getJSON("/api/v1/secretary/memory/context");
    const r = data.receipt || {};
    const head = r.included
      ? (zh ? `（${r.chars} 字 · ${r.notes_used} 筆記憶${r.truncated ? " · 已截斷" : ""}）\n` : `(${r.chars} chars · ${r.notes_used} notes${r.truncated ? " · truncated" : ""})\n`)
      : (zh ? `（目前沒有可注入的脈絡：${r.reason || "empty"}）` : `(nothing to inject: ${r.reason || "empty"})`);
    box.textContent = head + (data.text || "");
  } catch (e) {
    box.textContent = (zh ? "讀不到：" : "Unavailable: ") + e.message;
  }
}

export function initMemoryPanel() {
  const addBtn = $("btn-memory-add");
  const bodyInput = $("input-memory-body");
  const clearBtn = $("btn-memory-clear-obs");
  const ctxBtn = $("btn-memory-context");
  if (addBtn) addBtn.addEventListener("click", addMemoryNote);
  if (bodyInput) bodyInput.addEventListener("keydown", (ev) => { if (ev.key === "Enter") { ev.preventDefault(); addMemoryNote(); } });
  if (clearBtn) clearBtn.addEventListener("click", (ev) => { ev.preventDefault(); ev.stopPropagation(); clearMemoryObservations(); });
  if (ctxBtn) ctxBtn.addEventListener("click", (ev) => { ev.preventDefault(); ev.stopPropagation(); toggleMemoryContext(); });
}

// ---------------------------------------------------------------- 02 專案卡：git 狀態 chip（來自 L0 同步報告快照）
