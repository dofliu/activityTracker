// popup.js

const HEALTH_URL = "http://127.0.0.1:8765/api/v1/health";
const STATUS_URL = "http://127.0.0.1:8765/api/v1/extension/status";
const MONITOR_URL = "http://127.0.0.1:8765/extension-monitor";

async function checkHealth() {
  const container = document.getElementById("status-container");
  const dot = document.getElementById("status-dot");
  const text = document.getElementById("status-text");
  const pairingContainer = document.getElementById("pairing-container");
  const pairingDot = document.getElementById("pairing-dot");
  const pairingText = document.getElementById("pairing-text");
  const heartbeatText = document.getElementById("heartbeat-text");
  const captureText = document.getElementById("capture-text");

  text.innerText = "連線測試中...";
  try {
    const healthResponse = await fetch(HEALTH_URL);
    const data = await healthResponse.json();
    if (!healthResponse.ok || data.status !== "ok") throw new Error("Invalid health response");
    container.className = "status-badge status-online";
    dot.className = "dot dot-green";
    text.innerText = "本地服務連線正常";
  } catch (err) {
    container.className = "status-badge status-offline";
    dot.className = "dot dot-red";
    text.innerText = "本地服務未啟動 (Port 8765)";
    pairingContainer.className = "status-badge status-offline";
    pairingDot.className = "dot dot-red";
    pairingText.innerText = "服務離線，無法驗證配對";
    heartbeatText.innerText = "Heartbeat：本機服務離線";
    captureText.innerText = "Capture：無法回報";
    return;
  }

  const token = document.getElementById("ingest-token").value.trim();
  if (!token) {
    pairingContainer.className = "status-badge status-offline";
    pairingDot.className = "dot dot-red";
    pairingText.innerText = "尚未設定 ingest token";
    heartbeatText.innerText = "Heartbeat：等待 token";
    return;
  }

  try {
    const statusResponse = await fetch(STATUS_URL, {
      headers: { "X-OmniContext-Ingest-Token": token }
    });
    if (!statusResponse.ok) throw new Error(`HTTP ${statusResponse.status}`);
    const status = await statusResponse.json();
    pairingContainer.className = "status-badge status-online";
    pairingDot.className = "dot dot-green";
    pairingText.innerText = status.extension.pairing_verified
      ? "Extension token 配對成功"
      : "Extension token 尚未驗證";
    const heartbeatAt = status.extension.last_heartbeat_at || "—";
    heartbeatText.innerText = status.extension.heartbeat_verified
      ? `Heartbeat：已驗證 · ${heartbeatAt}`
      : `Heartbeat：尚無近期 receipt · ${heartbeatAt}`;
    const captureStatus = status.extension.last_capture_status || "none";
    const errorCode = status.extension.last_error_code
      ? ` · ${status.extension.last_error_code}`
      : "";
    captureText.innerText = `Capture：${captureStatus}${errorCode} · queue ${status.extension.offline_queue_size || 0}`;
    (status.platforms || []).forEach(item => {
      const el = document.querySelector(`[data-platform="${item.key}"]`);
      if (!el) return;
      const state = !item.enabled
        ? "關閉"
        : item.observation_status === "observed"
          ? `已觀察 ${item.events_today}`
          : item.content_script_seen
            ? "Content ready"
            : "等待資料";
      el.innerText = `${item.label} · ${state}`;
    });
  } catch (err) {
    pairingContainer.className = "status-badge status-offline";
    pairingDot.className = "dot dot-red";
    pairingText.innerText = "Token 不一致或尚未配對";
    heartbeatText.innerText = "Heartbeat：驗證失敗";
  }
}

function requestHeartbeat(callback) {
  chrome.runtime.sendMessage({ type: "OMNICONTEXT_HEARTBEAT_NOW" }, () => {
    // 讀取 lastError 可避免 Extension reload 時產生未處理的 console error。
    void chrome.runtime.lastError;
    if (callback) callback();
  });
}

function loadToken() {
  chrome.storage.local.get(["omnicontext_ingest_token"], (res) => {
    document.getElementById("ingest-token").value = res.omnicontext_ingest_token || "";
    requestHeartbeat(checkHealth);
  });
}

function saveToken() {
  const token = document.getElementById("ingest-token").value.trim();
  chrome.storage.local.set({ omnicontext_ingest_token: token }, () => {
    document.getElementById("btn-save-token").innerText = "已儲存";
    setTimeout(() => {
      document.getElementById("btn-save-token").innerText = "儲存 Token";
    }, 1200);
    requestHeartbeat(checkHealth);
  });
}

document.getElementById("btn-check").addEventListener("click", checkHealth);
document.getElementById("btn-save-token").addEventListener("click", saveToken);
document.getElementById("btn-open-monitor").addEventListener("click", () => {
  chrome.tabs.create({ url: MONITOR_URL });
});
document.addEventListener("DOMContentLoaded", loadToken);
