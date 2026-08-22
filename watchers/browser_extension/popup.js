// popup.js

const HEALTH_URL = "http://127.0.0.1:8765/api/v1/health";

function checkHealth() {
  const container = document.getElementById("status-container");
  const dot = document.getElementById("status-dot");
  const text = document.getElementById("status-text");

  text.innerText = "連線測試中...";

  fetch(HEALTH_URL)
    .then(res => res.json())
    .then(data => {
      if (data.status === "ok") {
        container.className = "status-badge status-online";
        dot.className = "dot dot-green";
        text.innerText = "本地服務連線正常";
      } else {
        throw new Error("Invalid response");
      }
    })
    .catch(err => {
      container.className = "status-badge status-offline";
      dot.className = "dot dot-red";
      text.innerText = "本地服務未啟動 (Port 8765)";
    });
}

document.getElementById("btn-check").addEventListener("click", checkHealth);
document.addEventListener("DOMContentLoaded", checkHealth);
