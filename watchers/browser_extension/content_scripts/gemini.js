// content_scripts/gemini.js - Injected into gemini.google.com

(function() {
  console.log("[OmniContext] Gemini watcher initialized.");
  chrome.runtime.sendMessage({ type: "OMNICONTEXT_CONTENT_READY", platform: "gemini" });

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
    if (!inputEl) {
      chrome.runtime.sendMessage({
        type: "OMNICONTEXT_CAPTURE_DIAGNOSTIC",
        platform: "gemini",
        status: "error",
        error_code: "input_selector_not_found"
      });
      return;
    }

    const promptText = (inputEl.innerText || inputEl.value || "").trim();
    if (!promptText || promptText.length < 2) return;

    const now = Date.now();
    if (promptText === lastPrompt && (now - lastPromptTime) < 3000) return;

    lastPrompt = promptText;
    lastPromptTime = now;

    console.log("[OmniContext] Gemini prompt detected.");

    // 發送給 background worker
    chrome.runtime.sendMessage({
      type: "AI_INTERACTION_CAPTURED",
      data: {
        platform: "gemini",
        url: window.location.href,
        conversation_id: window.location.pathname,
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
    let lastText = "";
    let stableCount = 0;
    const interval = setInterval(() => {
      checkCount++;
      // 尋找最新的模型回應容器
      const responses = document.querySelectorAll("message-content, .model-response-text, .response-container-content");
      if (responses.length > 0) {
        const lastResp = responses[responses.length - 1];
        const respText = lastResp.innerText.trim();
        
        stableCount = respText === lastText && respText.length > 20 ? stableCount + 1 : 0;
        lastText = respText;
        if (stableCount >= 2) {
          clearInterval(interval);
          chrome.runtime.sendMessage({
            type: "AI_INTERACTION_CAPTURED",
            data: {
              platform: "gemini",
              url: window.location.href,
              conversation_id: window.location.pathname,
              prompt_text: forPrompt,
              response_text: respText,
              metadata: { capture_state: "stable_candidate" },
              project_tag: "Gemini Web"
            }
          });
        }
      }

      if (checkCount > 120) {
        if (lastText.length > 20) {
          chrome.runtime.sendMessage({
            type: "AI_INTERACTION_CAPTURED",
            data: {
              platform: "gemini",
              url: window.location.href,
              conversation_id: window.location.pathname,
              prompt_text: forPrompt,
              response_text: lastText,
              metadata: { capture_state: "partial_timeout" },
              project_tag: "Gemini Web"
            }
          });
        } else {
          chrome.runtime.sendMessage({
            type: "OMNICONTEXT_CAPTURE_DIAGNOSTIC",
            platform: "gemini",
            status: "error",
            error_code: "response_selector_not_found"
          });
        }
        clearInterval(interval);
      }
    }, 1500);
  }
})();
