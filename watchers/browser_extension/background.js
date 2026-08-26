// background.js - OmniContext Background Service Worker (MV3)

const EVENT_URL = "http://127.0.0.1:8765/api/v1/events/ai";
const HEARTBEAT_URL = "http://127.0.0.1:8765/api/v1/extension/heartbeat";
const DIAGNOSTIC_KEY = "omnicontext_extension_diagnostics";
const INSTANCE_KEY = "omnicontext_extension_instance_id";
const HEARTBEAT_ALARM = "omnicontext-heartbeat";
const OFFLINE_FLUSH_ALARM = "omnicontext-offline-flush";
const SUPPORTED_PLATFORMS = new Set(["chatgpt", "gemini", "claude", "manus"]);

// 只用於本次 Extension session 的去重鍵，不作密碼學用途。
function simpleHash(str) {
  let hash = 0;
  for (let i = 0; i < str.length; i++) {
    const char = str.charCodeAt(i);
    hash = (hash << 5) - hash + char;
    hash |= 0;
  }
  return hash.toString(36);
}

function safePlatform(value) {
  const platform = String(value || "").trim().toLowerCase();
  return SUPPORTED_PLATFORMS.has(platform) ? platform : null;
}

function safeErrorCode(error) {
  const message = String(error?.message || error || "unknown_error").toLowerCase();
  const httpMatch = message.match(/http\s+(\d{3})/);
  if (httpMatch) return `http_${httpMatch[1]}`;
  if (message.includes("token")) return "token_not_configured";
  if (message.includes("fetch") || message.includes("network")) return "network_unreachable";
  return "extension_error";
}

async function updateDiagnostics(patch) {
  const stored = await chrome.storage.local.get([DIAGNOSTIC_KEY]);
  const current = stored[DIAGNOSTIC_KEY] || {};
  const next = { ...current, ...patch };
  await chrome.storage.local.set({ [DIAGNOSTIC_KEY]: next });
  return next;
}

async function markContentReady(platform) {
  const safe = safePlatform(platform);
  if (!safe) return;
  const stored = await chrome.storage.local.get([DIAGNOSTIC_KEY]);
  const current = stored[DIAGNOSTIC_KEY] || {};
  const ready = { ...(current.content_script_last_seen || {}) };
  ready[safe] = Date.now();
  await updateDiagnostics({
    content_script_last_seen: ready,
    last_capture_status: current.last_capture_status || "content_ready"
  });
}

async function getOrCreateInstanceId() {
  const stored = await chrome.storage.local.get([INSTANCE_KEY]);
  if (stored[INSTANCE_KEY]) return stored[INSTANCE_KEY];
  const instanceId = crypto.randomUUID();
  await chrome.storage.local.set({ [INSTANCE_KEY]: instanceId });
  return instanceId;
}

async function buildHeartbeatPayload() {
  const stored = await chrome.storage.local.get([DIAGNOSTIC_KEY, "offline_ai_events"]);
  const diagnostics = stored[DIAGNOSTIC_KEY] || {};
  const cutoff = Date.now() - 24 * 60 * 60 * 1000;
  const readyPlatforms = Object.entries(diagnostics.content_script_last_seen || {})
    .filter(([platform, seenAt]) => safePlatform(platform) && Number(seenAt) >= cutoff)
    .map(([platform]) => platform)
    .sort();
  const readyPlatformReceipts = readyPlatforms.map(platform => ({
    platform,
    seen_at: new Date(Number(diagnostics.content_script_last_seen[platform])).toISOString()
  }));
  return {
    instance_id: await getOrCreateInstanceId(),
    extension_version: chrome.runtime.getManifest().version,
    ready_platforms: readyPlatforms,
    ready_platform_receipts: readyPlatformReceipts,
    last_capture_status: diagnostics.last_capture_status || "none",
    last_capture_at: diagnostics.last_capture_at || null,
    last_error_code: diagnostics.last_error_code || null,
    offline_queue_size: (stored.offline_ai_events || []).length
  };
}

async function sendHeartbeat() {
  const stored = await chrome.storage.local.get(["omnicontext_ingest_token"]);
  const ingestToken = (stored.omnicontext_ingest_token || "").trim();
  if (!ingestToken) {
    await updateDiagnostics({ last_heartbeat_status: "token_not_configured" });
    return { status: "token_not_configured" };
  }

  try {
    const response = await fetch(HEARTBEAT_URL, {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
        "X-OmniContext-Ingest-Token": ingestToken
      },
      body: JSON.stringify(await buildHeartbeatPayload())
    });
    if (!response.ok) throw new Error(`HTTP ${response.status}`);
    const receipt = await response.json();
    await updateDiagnostics({
      last_heartbeat_status: "accepted",
      last_heartbeat_at: receipt.server_received_at || new Date().toISOString(),
      last_heartbeat_error_code: null
    });
    return receipt;
  } catch (error) {
    const errorCode = safeErrorCode(error);
    await updateDiagnostics({
      last_heartbeat_status: "error",
      last_heartbeat_error_code: errorCode
    });
    return { status: "error", error_code: errorCode };
  }
}

async function sendEventToServer(payload) {
  const stored = await chrome.storage.local.get(["omnicontext_ingest_token"]);
  const ingestToken = (stored.omnicontext_ingest_token || "").trim();
  if (!ingestToken) throw new Error("ingest token not configured");
  const response = await fetch(EVENT_URL, {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
      "X-OmniContext-Ingest-Token": ingestToken
    },
    body: JSON.stringify(payload)
  });
  if (!response.ok) throw new Error(`HTTP ${response.status}`);
  return await response.json();
}

async function queueOfflineEvent(payload) {
  const stored = await chrome.storage.local.get(["offline_ai_events"]);
  const queue = stored.offline_ai_events || [];
  queue.push({ payload, time: Date.now() });
  await chrome.storage.local.set({ offline_ai_events: queue.slice(-100) });
}

async function handleCapturedInteraction(message, sender) {
  const payload = message.data || {};
  const promptText = String(payload.prompt_text || "").trim();
  const platform = safePlatform(payload.platform);
  if (!platform || promptText.length < 2) {
    await updateDiagnostics({
      last_capture_status: "error",
      last_error_code: "invalid_capture_payload",
      last_capture_at: new Date().toISOString()
    });
    await sendHeartbeat();
    return { status: "invalid_payload" };
  }

  const hasResponse = Boolean(payload.response_text && payload.response_text.trim().length > 0);
  const promptHash = simpleHash(promptText);
  const responseHash = hasResponse ? simpleHash(payload.response_text.trim()) : "request";
  const conversationRef = payload.conversation_id || payload.url || sender.tab?.url || "unknown";
  const bucket = Math.floor(Date.now() / 600000);
  const dedupKey = `${platform}:${simpleHash(conversationRef)}:${promptHash}:${responseHash}:${bucket}`;
  const duplicate = await chrome.storage.session.get([dedupKey]);
  if (duplicate[dedupKey]) {
    await updateDiagnostics({
      last_capture_status: "skipped_duplicate",
      last_capture_at: new Date().toISOString(),
      last_error_code: null
    });
    await sendHeartbeat();
    return { status: "skipped_duplicate" };
  }

  await chrome.storage.session.set({ [dedupKey]: Date.now() });
  await updateDiagnostics({
    last_capture_status: "attempting",
    last_capture_at: new Date().toISOString(),
    last_error_code: null
  });

  try {
    const data = await sendEventToServer(payload);
    await updateDiagnostics({ last_capture_status: "accepted", last_error_code: null });
    await sendHeartbeat();
    return { status: "success", data };
  } catch (error) {
    await queueOfflineEvent(payload);
    await updateDiagnostics({
      last_capture_status: "queued_offline",
      last_error_code: safeErrorCode(error)
    });
    await sendHeartbeat();
    return { status: "queued_offline", error_code: safeErrorCode(error) };
  }
}

async function flushOfflineQueue() {
  const stored = await chrome.storage.local.get(["offline_ai_events"]);
  const queue = stored.offline_ai_events || [];
  if (!queue.length) return sendHeartbeat();

  const remaining = [];
  for (const item of queue) {
    try {
      await sendEventToServer(item.payload);
    } catch (error) {
      remaining.push(item);
    }
  }
  await chrome.storage.local.set({ offline_ai_events: remaining });
  await updateDiagnostics({
    last_capture_status: remaining.length ? "queued_offline" : "accepted",
    last_error_code: remaining.length ? "offline_flush_incomplete" : null
  });
  return sendHeartbeat();
}

chrome.runtime.onMessage.addListener((message, sender, sendResponse) => {
  if (message.type === "AI_INTERACTION_CAPTURED") {
    handleCapturedInteraction(message, sender).then(sendResponse);
    return true;
  }
  if (message.type === "OMNICONTEXT_CONTENT_READY") {
    markContentReady(message.platform)
      .then(sendHeartbeat)
      .then(sendResponse);
    return true;
  }
  if (message.type === "OMNICONTEXT_CAPTURE_DIAGNOSTIC") {
    const platform = safePlatform(message.platform);
    const errorCode = String(message.error_code || "").toLowerCase();
    markContentReady(platform)
      .then(() => updateDiagnostics({
        last_capture_status: message.status || "error",
        last_capture_at: new Date().toISOString(),
        last_error_code: /^[a-z0-9_.-]{1,80}$/.test(errorCode) ? errorCode : "content_script_error"
      }))
      .then(sendHeartbeat)
      .then(sendResponse);
    return true;
  }
  if (message.type === "OMNICONTEXT_HEARTBEAT_NOW") {
    sendHeartbeat().then(sendResponse);
    return true;
  }
  return false;
});

function initializeAlarms() {
  // MV3 Service Worker 可能休眠，使用 alarms 才能可靠恢復 heartbeat 與離線重送。
  chrome.alarms.create(HEARTBEAT_ALARM, { periodInMinutes: 1 });
  chrome.alarms.create(OFFLINE_FLUSH_ALARM, { periodInMinutes: 1 });
}

chrome.alarms.onAlarm.addListener((alarm) => {
  if (alarm.name === HEARTBEAT_ALARM) void sendHeartbeat();
  if (alarm.name === OFFLINE_FLUSH_ALARM) void flushOfflineQueue();
});
chrome.runtime.onInstalled.addListener(() => {
  initializeAlarms();
  void sendHeartbeat();
});
chrome.runtime.onStartup.addListener(() => {
  initializeAlarms();
  void sendHeartbeat();
});

initializeAlarms();
void sendHeartbeat();
