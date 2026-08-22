// background.js - OmniContext Background Service Worker

const SERVER_URL = "http://127.0.0.1:8765/api/v1/events/ai";
const sentPromptHashes = new Set();

chrome.runtime.onMessage.addListener((message, sender, sendResponse) => {
  if (message.type === "AI_INTERACTION_CAPTURED") {
    const payload = message.data;
    
    // 簡單 Hash 防重覆發送
    const hash = `${payload.platform}:${payload.prompt_text.trim()}`;
    if (sentPromptHashes.has(hash)) {
      sendResponse({ status: "skipped_duplicate" });
      return true;
    }
    sentPromptHashes.add(hash);

    // 發送至本地 OmniContext 伺服器
    fetch(SERVER_URL, {
      method: "POST",
      headers: {
        "Content-Type": "application/json"
      },
      body: JSON.stringify(payload)
    })
    .then(res => res.json())
    .then(data => {
      console.log("[OmniContext] Successfully synced AI event:", data);
      sendResponse({ status: "success", data });
    })
    .catch(err => {
      console.warn("[OmniContext] Failed to send event to local server (is server running?):", err);
      sendResponse({ status: "error", error: err.message });
    });

    return true; // Keep message channel open for async response
  }
});
