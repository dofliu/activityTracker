// content_scripts/chatgpt.js - Injected into chatgpt.com and chat.openai.com

(function() {
  console.log("[OmniContext] ChatGPT watcher initialized.");
  chrome.runtime.sendMessage({ type: "OMNICONTEXT_CONTENT_READY", platform: "chatgpt" });

  let lastPrompt = "";
  let lastPromptTime = 0;

  document.addEventListener("keydown", (e) => {
    if (e.key === "Enter" && !e.shiftKey) {
      captureCurrentInput();
    }
  }, true);

  document.addEventListener("click", (e) => {
    const target = e.target;
    if (target.closest("button[data-testid='send-button'], button[aria-label*='Send prompt']")) {
      captureCurrentInput();
    }
  }, true);

  function captureCurrentInput() {
    const inputEl = document.querySelector("#prompt-textarea, textarea[placeholder*='Message']");
    if (!inputEl) {
      chrome.runtime.sendMessage({
        type: "OMNICONTEXT_CAPTURE_DIAGNOSTIC",
        platform: "chatgpt",
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

    console.log("[OmniContext] ChatGPT prompt detected.");

    chrome.runtime.sendMessage({
      type: "AI_INTERACTION_CAPTURED",
      data: {
        platform: "chatgpt",
        url: window.location.href,
        conversation_id: window.location.pathname,
        prompt_text: promptText,
        response_text: null,
        project_tag: "ChatGPT Web"
      }
    });

    observeResponse(promptText);
  }

  function observeResponse(forPrompt) {
    let checkCount = 0;
    let lastText = "";
    let stableCount = 0;
    const interval = setInterval(() => {
      checkCount++;
      const assistants = document.querySelectorAll("div[data-message-author-role='assistant']");
      if (assistants.length > 0) {
        const lastResp = assistants[assistants.length - 1];
        const respText = lastResp.innerText.trim();
        
        stableCount = respText === lastText && respText.length > 20 ? stableCount + 1 : 0;
        lastText = respText;
        if (stableCount >= 2) {
          clearInterval(interval);
          chrome.runtime.sendMessage({
            type: "AI_INTERACTION_CAPTURED",
            data: {
              platform: "chatgpt",
              url: window.location.href,
              conversation_id: window.location.pathname,
              prompt_text: forPrompt,
              response_text: respText,
              metadata: { capture_state: "stable_candidate" },
              project_tag: "ChatGPT Web"
            }
          });
        }
      }

      if (checkCount > 120) {
        if (lastText.length > 20) {
          chrome.runtime.sendMessage({
            type: "AI_INTERACTION_CAPTURED",
            data: {
              platform: "chatgpt",
              url: window.location.href,
              conversation_id: window.location.pathname,
              prompt_text: forPrompt,
              response_text: lastText,
              metadata: { capture_state: "partial_timeout" },
              project_tag: "ChatGPT Web"
            }
          });
        } else {
          chrome.runtime.sendMessage({
            type: "OMNICONTEXT_CAPTURE_DIAGNOSTIC",
            platform: "chatgpt",
            status: "error",
            error_code: "response_selector_not_found"
          });
        }
        clearInterval(interval);
      }
    }, 1500);
  }
})();
