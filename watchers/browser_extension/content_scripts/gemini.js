// content_scripts/gemini.js - Injected into gemini.google.com

(function() {
  console.log("[OmniContext] Gemini watcher initialized.");

  let lastPrompt = "";
  let lastPromptTime = 0;

  // 監聽鍵盤 Enter 事件 (在輸入框中按 Enter)
  document.addEventListener("keydown", (e) => {
    if (e.key === "Enter" && !e.shiftKey) {
      captureCurrentInput();
    }
  }, true);

  // 監聽點擊發送按鈕
  document.addEventListener("click", (e) => {
    const target = e.target;
    if (target.closest("button[aria-label*='Send'], button[aria-label*='傳送'], .send-button")) {
      captureCurrentInput();
    }
  }, true);

  function captureCurrentInput() {
    // 尋找 Gemini 輸入框
    const inputEl = document.querySelector("div.ql-editor, div[contenteditable='true'], textarea");
    if (!inputEl) return;

    const promptText = (inputEl.innerText || inputEl.value || "").trim();
    if (!promptText || promptText.length < 2) return;

    const now = Date.now();
    if (promptText === lastPrompt && (now - lastPromptTime) < 3000) return;

    lastPrompt = promptText;
    lastPromptTime = now;

    console.log("[OmniContext] Captured Gemini Prompt:", promptText.substring(0, 50));

    // 發送給 background worker
    chrome.runtime.sendMessage({
      type: "AI_INTERACTION_CAPTURED",
      data: {
        platform: "gemini",
        url: window.location.href,
        prompt_text: promptText,
        response_text: null,
        project_tag: "Gemini Web"
      }
    });

    // 啟動 Response 觀察者以擷取接下來的回應
    observeResponse(promptText);
  }

  function observeResponse(forPrompt) {
    let checkCount = 0;
    const interval = setInterval(() => {
      checkCount++;
      // 尋找最新的模型回應容器
      const responses = document.querySelectorAll("message-content, .model-response-text, .response-container-content");
      if (responses.length > 0) {
        const lastResp = responses[responses.length - 1];
        const respText = lastResp.innerText.trim();
        
        // 若生成長度大於 20 字且已停止快速增長
        if (respText.length > 20 && checkCount >= 5) {
          clearInterval(interval);
          chrome.runtime.sendMessage({
            type: "AI_INTERACTION_CAPTURED",
            data: {
              platform: "gemini",
              url: window.location.href,
              prompt_text: forPrompt,
              response_text: respText.substring(0, 2000), // 取前 2000 字精華
              project_tag: "Gemini Web"
            }
          });
        }
      }

      if (checkCount > 30) {
        clearInterval(interval); // 超時停止
      }
    }, 1500);
  }
})();
