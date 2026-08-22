// background.js - OmniContext Background Service Worker (MV3)

const SERVER_URL = "http://127.0.0.1:8765/api/v1/events/ai";

// 簡單 Hash 函式
function simpleHash(str) {
  let hash = 0;
  for (let i = 0; i < str.length; i++) {
    const char = str.charCodeAt(i);
    hash = (hash << 5) - hash + char;
    hash |= 0;
  }
  return hash.toString(36);
}

// 處理訊息
chrome.runtime.onMessage.addListener((message, sender, sendResponse) => {
  if (message.type === "AI_INTERACTION_CAPTURED") {
    const payload = message.data;
    const hasResp = Boolean(payload.response_text && payload.response_text.trim().length > 0);
    const pHash = simpleHash(payload.prompt_text.trim());
    const dedupKey = `${payload.platform}:${pHash}:${hasResp ? 'resp' : 'req'}`;

    // 使用 chrome.storage.session 防止 Service Worker 休眠遺失或重複
    chrome.storage.session.get([dedupKey], (res) => {
      if (res[dedupKey]) {
        sendResponse({ status: "skipped_duplicate" });
        return;
      }

      // 標記已發送
      const setObj = {};
      setObj[dedupKey] = Date.now();
      chrome.storage.session.set(setObj);

      // 發送給本地 OmniContext 伺服器
      sendEventToServer(payload)
        .then(data => {
          console.log("[OmniContext] Successfully synced AI event:", data);
          sendResponse({ status: "success", data });
        })
        .catch(err => {
          console.warn("[OmniContext] Server unreachable, queuing locally:", err);
          queueOfflineEvent(payload);
          sendResponse({ status: "queued_offline", error: err.message });
        });
    });

    return true; // 保持異步通道
  }
});

async function sendEventToServer(payload) {
  const response = await fetch(SERVER_URL, {
    method: "POST",
    headers: {
      "Content-Type": "application/json"
    },
    body: JSON.stringify(payload)
  });
  if (!response.ok) {
    throw new Error(`HTTP ${response.status}`);
  }
  return await response.json();
}

// 離線暫存佇列
function queueOfflineEvent(payload) {
  chrome.storage.local.get(["offline_ai_events"], (res) => {
    const queue = res.offline_ai_events || [];
    queue.push({ payload, time: Date.now() });
    // 最多保留 100 筆
    chrome.storage.local.set({ offline_ai_events: queue.slice(-100) });
  });
}

// 定期檢查並重送離線佇列
setInterval(() => {
  chrome.storage.local.get(["offline_ai_events"], (res) => {
    const queue = res.offline_ai_events || [];
    if (queue.length === 0) return;

    console.log(`[OmniContext] Attempting to flush ${queue.length} offline events...`);
    const remaining = [];

    Promise.all(queue.map(item => 
      sendEventToServer(item.payload)
        .catch(err => {
          remaining.push(item);
        })
    )).then(() => {
      chrome.storage.local.set({ offline_ai_events: remaining });
    });
  });
}, 30000);
