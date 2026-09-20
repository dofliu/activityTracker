// web/js/tabs/knowledge.js — 02 知識庫：DeskRAG 對話、引用、索引與檢索 worker 管理。

import { getJSON, postJSON, request } from "../core/api.js";
import { $, esc } from "../core/dom.js";
import { t } from "../core/i18n.js";
import { API, state } from "../core/state.js";
import { registerActions } from "../core/ui.js";
import { renderAssistantChatMirror } from "../tabs/assistant.js";
import { parseMemoryCommand, rememberFromChat } from "../tabs/memory.js";
import { formatUsageDuration } from "../tabs/status.js";

// RAG 串流的介面安全網：閒置逾時上限與目前的 AbortController。
export const RAG_STREAM_IDLE_TIMEOUT_MS = 120000;

export const RAG_PROVIDER_MODELS = {
  ollama: [
    { value: "llama3.1:8b", label: "llama3.1:8b (預設)" },
    { value: "mistral:7b", label: "mistral:7b" },
    { value: "gemma4:e4b", label: "gemma4:e4b" },
    { value: "qwen3:4b", label: "qwen3:4b" }
  ],
  gemini: [
    { value: "gemini-3.7-flash", label: "gemini-3.7-flash (推薦)" },
    { value: "gemini-2.5-flash", label: "gemini-2.5-flash" },
    { value: "gemini-2.5-pro", label: "gemini-2.5-pro" }
  ],
  claude: [
    { value: "claude-3-5-sonnet-20241022", label: "claude-3-5-sonnet (推薦)" },
    { value: "claude-3-5-haiku-20241022", label: "claude-3-5-haiku" }
  ],
  openai: [
    { value: "gpt-4o", label: "gpt-4o (推薦)" },
    { value: "gpt-4o-mini", label: "gpt-4o-mini" }
  ]
};

export function updateRAGModelSelect(provider) {
  const modelSelect = $("select-rag-model") || $("input-rag-model");
  if (!modelSelect) return;
  const prov = provider || ($("select-rag-provider") ? $("select-rag-provider").value : "ollama");
  const models = RAG_PROVIDER_MODELS[prov] || RAG_PROVIDER_MODELS.ollama;
  if (modelSelect.tagName === "SELECT") {
    modelSelect.innerHTML = models.map((m, idx) => `
      <option value="${esc(m.value)}" ${idx === 0 ? "selected" : ""}>${esc(m.label || m.value)}</option>
    `).join("");
  } else {
    modelSelect.value = models[0] ? models[0].value : "llama3.1:8b";
  }
}

export function initRAGTab() {
  registerActions({
    "delete-rag-folder": (d) => deleteRAGFolder(Number(d.folderId)),
    "open-rag-file": (d) => openRAGFileInExplorer(d.path),
  });
  updateRAGModelSelect("ollama");
  // 新增目錄
  const addBtn = $("btn-rag-add-folder");
  if (addBtn) {
    addBtn.addEventListener("click", async () => {
      const pathInput = $("input-rag-folder-path");
      const nameInput = $("input-rag-folder-name");
      const path = (pathInput.value || "").trim();
      const name = (nameInput.value || "").trim();
      if (!path) {
        alert("請輸入有效的本機目錄絕對路徑！");
        return;
      }
      try {
        addBtn.disabled = true;
        addBtn.textContent = "⏳ 加入中…";
        await postJSON("/api/v1/rag/folders", {
          path,
          name: name || undefined,
          max_files: readRAGNumber("input-rag-max-files", 500, 1),
          throttle_ms: readRAGNumber("input-rag-throttle-ms", 25, 0),
        });
        pathInput.value = "";
        nameInput.value = "";
        loadRAGFolders();
        startRAGProgressPolling();
      } catch (e) {
        alert("加入目錄失敗: " + e.message);
      } finally {
        addBtn.disabled = false;
        addBtn.textContent = "+ 加入";
      }
    });
  }

  // 立即全量掃描
  const scanBtn = $("btn-rag-scan-now");
  if (scanBtn) {
    scanBtn.addEventListener("click", async () => {
      try {
        scanBtn.disabled = true;
        scanBtn.textContent = "⏳ 啟動中…";
        await postJSON("/api/v1/rag/scan", {
          max_files: readRAGNumber("input-rag-max-files", 500, 1),
          throttle_ms: readRAGNumber("input-rag-throttle-ms", 25, 0),
        });
        startRAGProgressPolling();
      } catch (e) {
        alert("啟動掃描失敗: " + e.message);
      } finally {
        setTimeout(() => { scanBtn.disabled = false; scanBtn.textContent = "⚡ 掃描索引"; }, 1500);
      }
    });
  }

  const jobAction = (buttonId, action) => {
    const button = $(buttonId);
    if (!button) return;
    button.addEventListener("click", async () => {
      const job = await getJSON("/api/v1/rag/jobs/current");
      if (!job.id) return;
      try {
        await postJSON(`/api/v1/rag/jobs/${encodeURIComponent(job.id)}/${action}`, {});
        startRAGProgressPolling();
      } catch (e) {
        alert("工作控制失敗: " + e.message);
      }
    });
  };
  jobAction("btn-rag-pause", "pause");
  jobAction("btn-rag-resume", "resume");
  jobAction("btn-rag-cancel", "cancel");

  const clearIndexBtn = $("btn-rag-clear-index");
  if (clearIndexBtn) {
    clearIndexBtn.addEventListener("click", async () => {
      if (!confirm("確定清空所有 RAG 索引嗎？只會刪除切片、向量與資料夾索引紀錄，不會刪除原始檔案或對話。")) return;
      if (prompt("請輸入 CLEAR 以確認清空所有 RAG 索引：") !== "CLEAR") return;
      try {
        await postJSON("/api/v1/rag/clear-index", { confirm: true });
        startRAGProgressPolling();
      } catch (e) {
        alert("清空索引失敗: " + e.message);
      }
    });
  }

  const verifyIndexBtn = $("btn-rag-verify-index");
  if (verifyIndexBtn) {
    verifyIndexBtn.addEventListener("click", async () => {
      try {
        await postJSON("/api/v1/rag/storage/verify", {});
        startRAGProgressPolling();
      } catch (e) {
        alert("無法啟動索引驗證: " + e.message);
      }
    });
  }

  const compactChromaBtn = $("btn-rag-compact-chroma");
  if (compactChromaBtn) {
    compactChromaBtn.addEventListener("click", async () => {
      let detail = "";
      try {
        const chroma = await getJSON("/api/v1/rag/storage/chroma");
        detail = chroma.segments_readable
          ? `\n\n目前 ${formatRAGBytes(chroma.total_bytes)}，其中可回收 ${formatRAGBytes(chroma.reclaimable_bytes)}（孤兒片段 ${chroma.orphan_dirs.length} 個、SQLite 空頁 ${formatRAGBytes(chroma.sqlite_free_bytes)}）。`
          : `\n\n讀不到 segments 表（${chroma.reason || "未知原因"}），這次不會刪任何東西。`;
      } catch (e) { detail = ""; }
      if (!confirm(`回收 Chroma 目錄空間？${detail}\n\n只刪「不被 segments 表引用」的舊片段目錄並 VACUUM chroma.sqlite3；現有索引、來源檔與對話都不動。檢索 worker 會先釋放（下次提問或預熱自動重啟）。`)) return;
      try {
        await postJSON("/api/v1/rag/storage/compact-chroma", { confirm: true });
        startRAGProgressPolling();
      } catch (e) {
        alert("無法啟動 Chroma 空間回收: " + e.message);
      }
    });
  }

  const memorySyncBtn = $("btn-rag-memory-sync");
  if (memorySyncBtn) {
    memorySyncBtn.addEventListener("click", async () => {
      if (!confirm("把小秘書記憶區筆記、每日時段摘要、Handoff、同步報告與 STATUS 草稿併入知識庫？\n只讀本機資料，在獨立 worker 執行；重跑會覆蓋同一批切片。")) return;
      try {
        await postJSON("/api/v1/rag/memory/sync", {});
        startRAGProgressPolling();
      } catch (e) {
        alert("無法啟動記憶併入: " + e.message);
      }
    });
  }

  const rebuildBM25Btn = $("btn-rag-rebuild-bm25");
  if (rebuildBM25Btn) {
    rebuildBM25Btn.addEventListener("click", async () => {
      if (!confirm("確定從既有 Chroma 向量庫重建 BM25 嗎？不會重新掃描來源檔案，但大型索引仍需要一些時間。")) return;
      try {
        await postJSON("/api/v1/rag/storage/rebuild-bm25", {});
        startRAGProgressPolling();
      } catch (e) {
        alert("無法啟動 BM25 重建: " + e.message);
      }
    });
  }

  const warmupRetrievalBtn = $("btn-rag-retrieval-warmup");
  if (warmupRetrievalBtn) {
    warmupRetrievalBtn.addEventListener("click", async () => {
      try {
        await postJSON("/api/v1/rag/retrieval/warmup", {});
        startRAGRetrievalPolling();
      } catch (e) {
        alert("無法啟動檢索 worker 預熱: " + e.message);
      }
    });
  }

  const releaseRetrievalBtn = $("btn-rag-retrieval-release");
  if (releaseRetrievalBtn) {
    releaseRetrievalBtn.addEventListener("click", async () => {
      try {
        await postJSON("/api/v1/rag/retrieval/shutdown", {});
        await refreshRAGRetrieval();
      } catch (e) {
        alert("無法釋放檢索 worker: " + e.message);
      }
    });
  }

  // 搜尋檔案
  const fileSearchInput = $("input-rag-file-search");
  if (fileSearchInput) {
    let debounceTimer = null;
    fileSearchInput.addEventListener("input", (e) => {
      clearTimeout(debounceTimer);
      debounceTimer = setTimeout(() => {
        loadRAGFiles(e.target.value.trim());
      }, 300);
    });
  }

  // 切換對話 Session
  const sessionSelect = $("select-rag-session");
  if (sessionSelect) {
    sessionSelect.addEventListener("change", (e) => {
      const sId = e.target.value;
      state.rag.sessionId = sId;
      if (sId) {
        loadRAGMessages(sId);
      } else {
        state.rag.history = [];
        renderRAGMessages();
      }
    });
  }

  // 開新對話
  const newChatBtn = $("btn-rag-new-chat");
  if (newChatBtn) {
    newChatBtn.addEventListener("click", () => {
      state.rag.sessionId = "";
      if (sessionSelect) sessionSelect.value = "";
      state.rag.history = [];
      renderRAGMessages();
      $("input-rag-prompt").focus();
    });
  }

  // 刪除對話
  const delChatBtn = $("btn-rag-del-chat");
  if (delChatBtn) {
    delChatBtn.addEventListener("click", async () => {
      if (!state.rag.sessionId) return;
      if (!confirm("確定要刪除此對話紀錄嗎？")) return;
      try {
        await request(`/api/v1/rag/chat/sessions/${encodeURIComponent(state.rag.sessionId)}`, { method: "DELETE" });
        state.rag.sessionId = "";
        state.rag.history = [];
        renderRAGMessages();
        loadRAGSessions();
      } catch (e) {
        alert("刪除失敗: " + e.message);
      }
    });
  }

  // 切換提供者自動帶出模型選單
  const providerSelect = $("select-rag-provider");
  if (providerSelect) {
    providerSelect.addEventListener("change", (e) => {
      updateRAGModelSelect(e.target.value);
    });
  }

  // 發送訊息
  const sendBtn = $("btn-rag-send");
  const promptInput = $("input-rag-prompt");
  if (sendBtn && promptInput) {
    sendBtn.addEventListener("click", sendRAGChatMessage);
    promptInput.addEventListener("keydown", (e) => {
      if ((e.ctrlKey || e.metaKey) && e.key === "Enter") {
        e.preventDefault();
        sendRAGChatMessage();
      }
    });
  }

  // 清空視窗
  const clearBtn = $("btn-rag-clear");
  if (clearBtn) {
    clearBtn.addEventListener("click", () => {
      state.rag.history = [];
      renderRAGMessages();
    });
  }
}

export async function loadRAGFolders() {
  try {
    const folders = await getJSON("/api/v1/rag/folders");
    const container = $("rag-folders-list");
    if (!container) return;
    if (!folders.length) {
      container.innerHTML = '<div class="placeholder">尚未加入任何目錄。請在上方輸入目錄路徑以建立知識庫。</div>';
      return;
    }
    container.innerHTML = folders.map(f => `
      <div class="rag-folder-card">
        <div class="rag-folder-info">
          <div class="rag-folder-name">${esc(f.name || Path_basename(f.path))} <span class="mono-mini muted">(${f.file_count || 0} 檔案)</span></div>
          <div class="rag-folder-path" title="${esc(f.path)}">${esc(f.path)}</div>
        </div>
        <button class="btn btn-ghost btn-sm" data-action="delete-rag-folder" data-folder-id="${f.id}" title="移除此資料夾的索引，保留原始檔案" style="color:var(--danger); padding:2px 6px;">移除索引</button>
      </div>
    `).join("");
  } catch (e) {
    console.error("loadRAGFolders error:", e);
  }
}

export function Path_basename(p) {
  if (!p) return "";
  const parts = p.replace(/[\\/]+$/, "").split(/[\\/]/);
  return parts[parts.length - 1] || p;
}

export async function deleteRAGFolder(id) {
  if (!confirm("確定移除這個資料夾的 RAG 索引嗎？原始檔案不會被刪除，完成後會執行 SQLite 空間回收與一致性檢查。")) return;
  try {
    await postJSON(`/api/v1/rag/folders/${id}/remove-index`, { confirm: true });
    startRAGProgressPolling();
  } catch (e) {
    alert("刪除失敗: " + e.message);
  }
}


export function readRAGNumber(id, fallback, minimum) {
  const value = Number($(id)?.value);
  return Number.isFinite(value) && value >= minimum ? Math.floor(value) : fallback;
}

export function renderBackgroundTaskPanel(data) {
  const enabled = data.enabled !== false;
  const status = data.evidence_status || "not_observed";
  const badge = $("background-tasks-evidence");
  badge.className = "trust " + (!enabled ? "broken" : status === "verified_receipts" ? "ok" : "noisy");
  badge.textContent = !enabled ? "DISABLED" : status === "verified_receipts" ? "VERIFIED" : "WAITING";
  $("background-tasks-value").textContent = formatUsageDuration(data.verified_seconds || 0);

  const completed = Number(data.completed_task_count || 0);
  const awaiting = Number(data.awaiting_final_count || 0);
  const untrusted = Number(data.untrusted_duration_count || 0);
  $("background-tasks-meta").textContent = state.ui.currentLang === "zh-TW"
    ? `已完成 ${completed} 件 · 等待 final receipt ${awaiting} 件${untrusted ? ` · 異常時長未計入 ${untrusted} 件` : ""}`
    : `${completed} completed · ${awaiting} awaiting final receipt${untrusted ? ` · ${untrusted} excluded duration` : ""}`;
  $("background-tasks-boundary").textContent = state.ui.currentLang === "zh-TW"
    ? "只納入本機來源可確認的 prompt 開始與明確 final completion；與前景使用時間完全分開，不代表生產力、一般 Terminal 或全部工作。"
    : "Only local prompt-start and explicit final-completion receipts are included. It is separate from foreground time, productivity, generic terminal time, and all work.";

  const rows = (data.recent_tasks || []).slice(0, 6);
  $("background-tasks-list").innerHTML = rows.length ? rows.map(item => {
    const completedAt = item.completed_at ? new Date(item.completed_at).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" }) : "—";
    const project = item.project_tag || (state.ui.currentLang === "zh-TW" ? "未歸戶專案" : "Unassigned project");
    return `<div class="background-task-row">
      <span class="background-task-platform">${esc(item.label)}</span>
      <span class="background-task-project" title="${esc(project)}">${esc(project)}</span>
      <span class="background-task-duration">${formatUsageDuration(item.duration_seconds)}</span>
      <span class="background-task-completed">${completedAt}</span>
    </div>`;
  }).join("") : `<div class="placeholder">${state.ui.currentLang === "zh-TW" ? "今日尚無可由開始與 final receipt 成對驗證的背景任務。" : "No background task has paired start and final receipts today."}</div>`;
}

export function renderBackgroundTaskPanelError() {
  $("background-tasks-evidence").className = "trust broken";
  $("background-tasks-evidence").textContent = "UNAVAILABLE";
  $("background-tasks-list").innerHTML = `<div class="placeholder">${state.ui.currentLang === "zh-TW" ? "無法載入背景任務收據。" : "Unable to load background task receipts."}</div>`;
}

export function formatRAGBytes(bytes) {
  if (bytes === null || bytes === undefined) return "待驗證";
  const value = Number(bytes || 0);
  if (value < 1024) return `${value} B`;
  const units = ["KB", "MB", "GB", "TB"];
  let scaled = value / 1024;
  let unit = 0;
  while (scaled >= 1024 && unit < units.length - 1) { scaled /= 1024; unit += 1; }
  return `${scaled.toFixed(scaled >= 100 ? 0 : 1)} ${units[unit]}`;
}

export function renderRAGJob(data) {
  const box = $("rag-progress-box");
  const totalChunks = $("rag-total-chunks-count");
  if (totalChunks) totalChunks.textContent = `${data.total_indexed_chunks || 0} 切片`;
  if (!data.id && !data.is_running) return false;
  if (box) box.style.display = "block";
  const isActive = Boolean(data.is_running);
  const statusMap = {
    scanning: "掃描目錄中…", indexing: "正在建立切片向量…", paused: "⏸ 已暫停",
    cancelling: "正在取消…", completed: "✅ 索引完成", completed_limited: "✅ 本次上限完成",
    cancelled: "已取消", failed: "索引失敗",
  };
  const status = data.status || "idle";
  const isCompleted = status === "completed" || status === "completed_limited";
  const percent = isCompleted ? 100 : (data.progress_percent || 0);
  if ($("rag-progress-bar")) $("rag-progress-bar").style.width = `${percent}%`;
  if ($("rag-progress-percent")) $("rag-progress-percent").textContent = `${percent}%`;
  if ($("rag-progress-status")) $("rag-progress-status").textContent = statusMap[status] || status;
  if ($("rag-progress-detail")) {
    const detail = data.message || `處理中：${data.current_file || ""} (${data.processed_files || 0}/${data.total_files || 0})`;
    $("rag-progress-detail").textContent = `${detail}${data.current_file ? ` · ${data.current_file}` : ""}`;
  }
  if ($("btn-rag-pause")) $("btn-rag-pause").disabled = !isActive || status === "paused" || status === "cancelling";
  if ($("btn-rag-resume")) $("btn-rag-resume").disabled = status !== "paused";
  if ($("btn-rag-cancel")) $("btn-rag-cancel").disabled = !isActive || status === "cancelling";
  return isActive;
}

export async function refreshRAGStorage() {
  const card = $("rag-storage-card");
  if (!card) return;
  try {
    const data = await getJSON("/api/v1/rag/storage");
    const vectorCount = data.vector_chunks === null || data.vector_chunks === undefined ? "待驗證" : Number(data.vector_chunks).toLocaleString();
    // Chroma 目錄另外算一次帳：刪掉的 collection 不會讓磁碟變小（ADR-009 Addendum C）。
    let chroma = null;
    try { chroma = await getJSON("/api/v1/rag/storage/chroma"); } catch (e) { chroma = null; }
    let chromaLine = "";
    let chromaAlert = "";
    if (chroma) {
      chromaLine = `<span>Chroma 目錄 ${formatRAGBytes(chroma.total_bytes)}</span><span>可回收 ${chroma.segments_readable ? formatRAGBytes(chroma.reclaimable_bytes) : "無法判斷"}</span>`;
      if (!chroma.segments_readable) {
        chromaAlert = `讀不到 chroma.sqlite3 的 segments 表（${chroma.reason || "未知原因"}），無法分辨哪些片段是刪掉沒回收的；不會刪任何東西。`;
      } else if (chroma.reclaimable_bytes > 200 * 1024 * 1024) {
        chromaAlert = `Chroma 目錄有 ${formatRAGBytes(chroma.reclaimable_bytes)} 是刪掉沒回收的（孤兒片段 ${chroma.orphan_dirs.length} 個 ${formatRAGBytes(chroma.orphan_bytes)}、SQLite 空頁 ${formatRAGBytes(chroma.sqlite_free_bytes)}）。按「回收 Chroma 空間」可取回。`;
      }
    }
    const consistencyAlert = data.consistency === "matched" ? "" : (data.consistency === "unverified" ? "尚未以獨立 worker 驗證 Chroma／BM25，請按「驗證索引與空間」。" : `索引計數待檢查：向量差異 ${Number(data.vector_delta || 0).toLocaleString()}。`);
    const alerts = [consistencyAlert, chromaAlert].filter(Boolean).map((text) => `<div class="rag-storage-alert">${text}</div>`).join("");
    card.innerHTML = `<div class="rag-storage-grid">
      <span>來源檔案 ${Number(data.source_files || 0).toLocaleString()}</span><span>來源大小 ${formatRAGBytes(data.source_bytes)}</span>
      <span>資料庫切片 ${Number(data.source_chunks || 0).toLocaleString()}</span><span>向量 ${vectorCount}</span>
      <span>索引空間 ${formatRAGBytes(data.index_bytes)}</span><span>SQLite ${formatRAGBytes(data.sqlite_bytes)}</span>
      ${chromaLine}
    </div>${alerts}`;
  } catch (e) {
    card.textContent = "索引儲存空間暫時無法取得。";
  }
}


export const RAG_EXTRA_BUTTON_IDS = [
  "btn-rag-scan-now", "btn-rag-add-folder", "btn-rag-verify-index", "btn-rag-rebuild-bm25",
  "btn-rag-compact-chroma", "btn-rag-memory-sync", "btn-rag-retrieval-warmup", "btn-rag-retrieval-release",
];

// TODO D1：知識庫的索引套件是選用 extra。沒裝時不藏功能，但把會失敗的按鈕灰掉並說要裝什麼。
export function applyRAGExtraAvailability(data) {
  const missing = data && data.extra_installed === false;
  for (const id of RAG_EXTRA_BUTTON_IDS) {
    const btn = $(id);
    if (!btn) continue;
    if (missing) {
      btn.disabled = true;
      btn.dataset.ragExtraDisabled = "1";
      btn.title = `需要先安裝：${data.install_hint || 'pip install "omnicontext[rag]"'}`;
    } else if (btn.dataset.ragExtraDisabled) {
      btn.disabled = false;
      delete btn.dataset.ragExtraDisabled;
      btn.removeAttribute("title");
    }
  }
  return missing;
}

export function describeRAGRetrieval(data) {
  if (data.extra_installed === false) {
    return `<div class="rag-storage-alert">知識庫的選用依賴未安裝（缺 ${esc((data.extra_missing || []).join("、"))}）。` +
      `對話仍可用，但不會帶文件脈絡；要建索引與檢索請先執行 <code>${esc(data.install_hint || 'pip install "omnicontext[rag]"')}</code> 後重啟服務。</div>`;
  }
  const mode = data.mode === "in_process" ? "in_process（在主服務內檢索）" : "常駐 worker";
  const stateMap = {
    cold: "尚未啟動（第一次提問時才載入索引）",
    starting: "啟動中…",
    loading: "已啟動，尚未預熱",
    warming: "預熱中：正在載入 BM25／Chroma／embedding…",
    ready: "就緒",
    failed: "失敗",
  };
  const lines = [`<span>模式 ${mode}</span><span>狀態 ${stateMap[data.state] || data.state}</span>`];
  if (data.warmup) {
    const w = data.warmup;
    const d = w.durations || {};
    lines.push(`<span>BM25 ${Number(w.bm25_chunks || 0).toLocaleString()} 切片</span><span>向量 ${Number(w.vector_chunks || 0).toLocaleString()}</span>`);
    lines.push(`<span>預熱耗時 ${d.total_ms !== undefined ? `${(d.total_ms / 1000).toFixed(1)} s` : "—"}</span><span>worker 記憶體 ${w.worker_rss_mb !== null && w.worker_rss_mb !== undefined ? `${w.worker_rss_mb} MB` : "—"}</span>`);
    if (w.embedding_ready === false) lines.push(`<span style="grid-column: 1 / -1;">embedding 模型未就緒：${w.embedding_error || "未知原因"}</span>`);
  }
  if (data.requests_served) {
    lines.push(`<span>已服務 ${data.requests_served} 次</span><span>最近檢索 ${data.last_retrieval_ms !== null && data.last_retrieval_ms !== undefined ? `${data.last_retrieval_ms} ms` : "—"}</span>`);
  }
  let alert = "";
  if (data.state === "failed" && data.last_error) alert = `最近錯誤：${data.last_error}`;
  else if (data.mode === "worker" && data.state === "cold" && !data.index_present) alert = "尚無索引，不需預熱。";
  else if (data.mode === "worker" && data.state === "cold" && data.restarts) alert = `worker 曾重啟 ${data.restarts} 次；${data.last_error || ""}`;
  return `<div class="rag-storage-grid">${lines.join("")}</div>${alert ? `<div class="rag-storage-alert">${alert}</div>` : ""}`;
}

export async function refreshRAGRetrieval() {
  const card = $("rag-retrieval-card");
  if (!card) return null;
  try {
    const data = await getJSON("/api/v1/rag/retrieval/status");
    card.innerHTML = describeRAGRetrieval(data);
    if (applyRAGExtraAvailability(data)) return data;
    const warmBtn = $("btn-rag-retrieval-warmup");
    const releaseBtn = $("btn-rag-retrieval-release");
    if (warmBtn) warmBtn.disabled = data.mode !== "worker" || data.state === "warming" || data.state === "ready";
    if (releaseBtn) releaseBtn.disabled = data.mode !== "worker" || !data.pid;
    return data;
  } catch (e) {
    card.textContent = "檢索 worker 狀態暫時無法取得。";
    return null;
  }
}

export function startRAGRetrievalPolling() {
  if (state.rag.retrievalTimer) clearInterval(state.rag.retrievalTimer);
  state.rag.retrievalTimer = setInterval(async () => {
    const data = await refreshRAGRetrieval();
    if (!data || !["starting", "warming", "loading"].includes(data.state)) {
      clearInterval(state.rag.retrievalTimer);
      state.rag.retrievalTimer = null;
    }
  }, 2000);
}

export async function pollRAGProgress() {
  const data = await getJSON("/api/v1/rag/progress");
  const isActive = renderRAGJob(data);
  if (!isActive) {
    if (state.rag.progressTimer) clearInterval(state.rag.progressTimer);
    state.rag.progressTimer = null;
    loadRAGFolders();
    loadRAGFiles();
    refreshRAGStorage();
  }
}

export function startRAGProgressPolling() {
  if (state.rag.progressTimer) clearInterval(state.rag.progressTimer);
  pollRAGProgress().catch(() => {});
  state.rag.progressTimer = setInterval(() => pollRAGProgress().catch(() => {}), 1000);
}

export async function loadRAGProgress() {
  try {
    const data = await getJSON("/api/v1/rag/progress");
    const active = renderRAGJob(data);
    if (active) startRAGProgressPolling();
    refreshRAGStorage();
    refreshRAGRetrieval().then((retrieval) => {
      if (retrieval && ["starting", "warming", "loading"].includes(retrieval.state)) startRAGRetrievalPolling();
    });
  } catch (e) {}
}

export async function loadRAGFiles(search = "") {
  try {
    const url = `/api/v1/rag/files?page=1&page_size=50${search ? `&search=${encodeURIComponent(search)}` : ""}`;
    const res = await getJSON(url);
    const container = $("rag-files-list");
    const countSpan = $("rag-total-files-count");
    if (countSpan) countSpan.textContent = res.total || 0;
    if (!container) return;

    if (!res.items || !res.items.length) {
      container.innerHTML = '<div class="placeholder">目前無符合條件的檔案。</div>';
      return;
    }

    container.innerHTML = res.items.map(file => `
      <div class="rag-file-item" data-action="open-rag-file" data-path="${esc(file.path)}" title="點擊在 Windows 總管開啟: ${esc(file.path)}">
        <div class="rag-file-name">📄 ${esc(file.filename)}</div>
        <div style="display:flex; gap:4px; align-items:center;">
          <span class="mono-mini muted">${file.chunk_count || 0} 切片</span>
          <span class="rag-file-badge ${file.status === "indexed" ? "indexed" : "failed"}">${file.status === "indexed" ? "OK" : "ERR"}</span>
        </div>
      </div>
    `).join("");
  } catch (e) {
    console.error("loadRAGFiles error:", e);
  }
}

export async function openRAGFileInExplorer(path) {
  try {
    await postJSON("/api/v1/rag/open-file", { path });
  } catch (e) {
    alert("無法開啟總管: " + e.message);
  }
}

export async function loadRAGSessions() {
  try {
    const sessions = await getJSON("/api/v1/rag/chat/sessions");
    state.rag.sessions = Array.isArray(sessions) ? sessions : [];
    const select = $("select-rag-session");
    if (!select) return;

    select.innerHTML = '<option value="">➕ 建立新對話</option>' + state.rag.sessions.map(s => {
      const title = (s.title || "").trim();
      const displayTitle = title && title !== "新對話" ? title : (state.ui.currentLang === "zh-TW" ? "對話紀錄" : "Chat");
      return `<option value="${esc(s.id)}" ${s.id === state.rag.sessionId ? "selected" : ""}>💬 ${esc(displayTitle)}</option>`;
    }).join("");
    if (state.rag.sessionId) {
      select.value = state.rag.sessionId;
    }
  } catch (e) {}
}

export async function loadRAGStrategies() {
  try {
    const data = await getJSON("/api/v1/rag/strategies");
    const select = $("select-rag-strategy");
    if (!select || !data.strategies) return;
    select.innerHTML = data.strategies.map(st => `
      <option value="${esc(st.name)}" ${st.name === data.default ? "selected" : ""}>${esc(st.display_name)}</option>
    `).join("");
  } catch (e) {}
}

export async function loadRAGMessages(sessionId) {
  try {
    const messages = await getJSON(`/api/v1/rag/chat/messages/${encodeURIComponent(sessionId)}`);
    state.rag.history = messages.map(m => ({
      role: m.role,
      content: m.content,
      citations: m.citations || [],
      provider: m.provider,
      model: m.model,
      time: m.created_at
    }));
    renderRAGMessages();
  } catch (e) {
    console.error("loadRAGMessages error:", e);
  }
}

export function renderRAGMessages() {
  renderAssistantChatMirror(); // 小秘書首頁同步顯示同一條對話
  const container = $("rag-chat-messages");
  if (!container) return;

  if (!state.rag.history.length) {
    container.innerHTML = `
      <div class="placeholder" style="margin: auto; text-align: center;">
        <div style="font-size: 28px; margin-bottom: 8px;">📚</div>
        <div style="font-weight: 600; margin-bottom: 4px;">DeskRAG 本地文件智慧助手</div>
        <div class="muted small">請在下方輸入問題，系統將結合本地知識庫與活動記錄進行精準問答與引文標註。</div>
      </div>
    `;
    return;
  }

  container.innerHTML = state.rag.history.map((msg, idx) => {
    const isUser = msg.role === "user";
    const renderedContent = isUser
      ? esc(msg.content).replace(/\n/g, "<br>")
      : (window.marked ? marked.parse(msg.content) : esc(msg.content).replace(/\n/g, "<br>"));

    let citationsHtml = "";
    if (!isUser && msg.citations && msg.citations.length) {
      citationsHtml = `
        <div class="rag-citations-box">
          <div class="rag-citations-title">📌 參考文檔來源 (${msg.citations.length} 個切片)：</div>
          ${msg.citations.map(c => `
            <div class="rag-citation-card">
              <div class="rag-citation-head">
                <div class="rag-citation-filename">《${esc(c.filename || c.title || "文件")}》</div>
                <div class="rag-citation-tags">
                  ${c.page ? `<span class="rag-citation-tag">第 ${c.page} 頁</span>` : ""}
                  ${c.slide ? `<span class="rag-citation-tag">第 ${c.slide} 投影片</span>` : ""}
                  ${c.sheet ? `<span class="rag-citation-tag">工作表 ${esc(c.sheet)}</span>` : ""}
                  <span class="rag-citation-tag score">相關度 ${c.score}</span>
                  <button class="rag-btn-open" data-action="open-rag-file" data-path="${esc(c.file_path)}" title="在 Windows 總管開啟並選中">📂 在總管開啟</button>
                </div>
              </div>
              <div class="rag-citation-content">${esc(c.content || "")}</div>
            </div>
          `).join("")}
        </div>
      `;
    }

    return `
      <div class="rag-msg ${isUser ? "user" : "assistant"}">
        <div class="rag-msg-bubble markdown">${renderedContent}${citationsHtml}</div>
        <div class="rag-msg-meta">
          <span>${isUser ? "YOU" : `AI (${esc(msg.provider || "ollama")} · ${esc(msg.model || "")})`}</span>
          ${msg.time ? `<span>${esc(msg.time)}</span>` : ""}
        </div>
      </div>
    `;
  }).join("");

  container.scrollTop = container.scrollHeight;
}

export async function sendRAGChatMessage(fromInput) {
  if (state.rag.streaming) return;
  // 小秘書首頁與 RAG 分頁共用同一條對話流（同 session、同歷史）。
  const promptInput = fromInput || $("input-rag-prompt");
  const prompt = (promptInput.value || "").trim();
  if (!prompt) return;

  // 「記下來：…」「偏好：…」「決定：…」直接寫進記憶區，不送 LLM（ADR-012）。
  const noteCmd = parseMemoryCommand(prompt);
  if (noteCmd) {
    promptInput.value = "";
    await rememberFromChat(noteCmd, prompt);
    return;
  }

  promptInput.value = "";
  const provider = $("select-rag-provider") ? $("select-rag-provider").value : "ollama";
  const modelSelect = $("select-rag-model") || $("input-rag-model");
  const model = modelSelect ? modelSelect.value : "llama3.1:8b";
  const strategy = $("select-rag-strategy") ? $("select-rag-strategy").value : "hybrid_rrf";
  const enableRag = $("toggle-enable-rag") ? $("toggle-enable-rag").checked : true;

  // 1. 建立或確保 Session
  if (!state.rag.sessionId) {
    try {
      const sTitle = prompt.slice(0, 24);
      const sRes = await postJSON("/api/v1/rag/chat/sessions", { title: sTitle });
      state.rag.sessionId = sRes.session_id;
      loadRAGSessions();
    } catch (e) {
      state.rag.sessionId = "session_" + Date.now();
    }
  }

  // 2. 加入 User 訊息
  const userMsg = {
    role: "user",
    content: prompt,
    provider,
    model,
    time: new Date().toLocaleTimeString()
  };
  state.rag.history.push(userMsg);

  // 儲存 User Message 到後端
  postJSON("/api/v1/rag/chat/messages", {
    session_id: state.rag.sessionId,
    role: "user",
    content: prompt,
    provider,
    model
  }).catch(() => {});

  // 3. 準備 Assistant 訊息佔位
  const assistantMsg = {
    role: "assistant",
    content: "",
    citations: [],
    provider,
    model,
    time: new Date().toLocaleTimeString()
  };
  state.rag.history.push(assistantMsg);
  renderRAGMessages();

  // 4. 開始 SSE 串流
  state.rag.streaming = true;
  const sendBtn = $("btn-rag-send");
  if (sendBtn) {
    sendBtn.disabled = true;
    sendBtn.textContent = "串流中…";
  }
  const assistantBtn = $("btn-assistant-send");
  if (assistantBtn) {
    assistantBtn.disabled = true;
    assistantBtn.textContent = state.ui.currentLang === "zh-TW" ? "回覆中…" : "Streaming…";
  }

  try {
    const apiMessages = state.rag.history.slice(0, -1).map(m => ({ role: m.role, content: m.content }));
    // 安全網：後端若完全沒回應（網路卡住、程序被砍），介面也不能永遠停在
    // 「回覆中」。閒置逾時只在「一段時間沒有任何新位元組」時才觸發，
    // 正常的長回答會不斷刷新它。
    state.rag.abort = new AbortController();
    let idleTimer = null;
    const resetIdleTimer = () => {
      if (idleTimer) clearTimeout(idleTimer);
      idleTimer = setTimeout(() => {
        state.rag.timedOut = true;
        state.rag.abort.abort();
      }, RAG_STREAM_IDLE_TIMEOUT_MS);
    };
    resetIdleTimer();
    // 串流：需要原始 Response 的 body reader，所以走 request() 而不是 postJSON()
    const response = await request("/api/v1/rag/chat", {
      signal: state.rag.abort.signal,
      method: "POST",
      body: ({
        session_id: state.rag.sessionId,
        messages: apiMessages,
        provider,
        model,
        enable_rag: enableRag,
        retrieval_strategy: strategy
      })
    });

    if (!response.ok) {
      throw new Error(`HTTP ${response.status}: ${response.statusText}`);
    }

    const reader = response.body.getReader();
    const decoder = new TextDecoder("utf-8");
    let buffer = "";

    while (true) {
      const { value, done } = await reader.read();
      if (done) break;
      resetIdleTimer();

      buffer += decoder.decode(value, { stream: true });
      const lines = buffer.split("\n\n");
      buffer = lines.pop(); // keep remainder

      for (const block of lines) {
        if (!block.trim()) continue;
        const lineParts = block.split("\n");
        let eventType = "";
        let eventData = "";

        for (const lp of lineParts) {
          if (lp.startsWith("event: ")) eventType = lp.replace("event: ", "").trim();
          if (lp.startsWith("data: ")) eventData = lp.replace("data: ", "").trim();
        }

        if (eventType === "citations" && eventData) {
          try {
            assistantMsg.citations = JSON.parse(eventData);
            renderRAGMessages();
          } catch (e) {}
        } else if (eventType === "memory" && eventData) {
          try {
            assistantMsg.memory = JSON.parse(eventData);
            renderRAGMessages();
          } catch (e) {}
        } else if (eventType === "message" && eventData) {
          try {
            const tokenObj = JSON.parse(eventData);
            assistantMsg.content += tokenObj.token || "";
            renderRAGMessages();
          } catch (e) {}
        } else if (eventType === "done") {
          if (idleTimer) clearTimeout(idleTimer);
          break;
        }
      }
    }

    // 儲存 Assistant Message 到後端
    postJSON("/api/v1/rag/chat/messages", {
      session_id: state.rag.sessionId,
      role: "assistant",
      content: assistantMsg.content,
      citations: assistantMsg.citations,
      provider,
      model
    }).then(() => loadRAGSessions()).catch(() => {});

  } catch (e) {
    const zh = state.ui.currentLang === "zh-TW";
    assistantMsg.content += state.rag.timedOut
      ? (zh
        ? `\n\n[逾時：${RAG_STREAM_IDLE_TIMEOUT_MS / 1000} 秒內沒有收到任何回應。請確認所選 provider 的 API key 與網路，或改用本機 Ollama。]`
        : `\n\n[Timed out: no response for ${RAG_STREAM_IDLE_TIMEOUT_MS / 1000}s. Check the selected provider's API key and network, or switch to local Ollama.]`)
      : `\n\n[串流發生錯誤: ${e.message}]`;
    renderRAGMessages();
  } finally {
    if (state.rag.abort) state.rag.abort = null;
    state.rag.timedOut = false;
    state.rag.streaming = false;
    if (sendBtn) {
      sendBtn.disabled = false;
      sendBtn.textContent = "發送 ⚡";
    }
    if (assistantBtn) {
      assistantBtn.disabled = false;
      assistantBtn.textContent = t("btn_assistant_send");
    }
  }
}

// ---------------------------------------------------------------- 07 System Health & Maintenance Hub
