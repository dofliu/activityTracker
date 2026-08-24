// content_scripts/claude.js - Injected into claude.ai

(function() {
  console.log("[OmniContext] Claude watcher initialized.");
  chrome.runtime.sendMessage({ type: "OMNICONTEXT_CONTENT_READY", platform: "claude" });

  let lastPrompt = "";
  let lastPromptTime = 0;

  document.addEventListener("keydown", (e) => {
    if (e.key === "Enter" && !e.shiftKey) {
      captureCurrentInput();
    }
  }, true);

  document.addEventListener("click", (e) => {
    const target = e.target;
    if (target.closest("button[aria-label*='Send Message'], button[aria-label*='Send'], fieldset button")) {
      captureCurrentInput();
    }
  }, true);

  function captureCurrentInput() {
    const inputEl = document.querySelector("div.ProseMirror, div[contenteditable='true'], textarea");
    if (!inputEl) {
      chrome.runtime.sendMessage({
        type: "OMNICONTEXT_CAPTURE_DIAGNOSTIC",
        platform: "claude",
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

    console.log("[OmniContext] Claude prompt detected.");

    chrome.runtime.sendMessage({
      type: "AI_INTERACTION_CAPTURED",
      data: {
        platform: "claude",
        url: window.location.href,
        conversation_id: window.location.pathname,
        prompt_text: promptText,
        response_text: null,
        project_tag: "Claude Web"
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
      const responses = document.querySelectorAll(".font-claude-message, .grid-cols-1 .standard-markdown");
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
              platform: "claude",
              url: window.location.href,
              conversation_id: window.location.pathname,
              prompt_text: forPrompt,
              response_text: respText,
              metadata: { capture_state: "stable_candidate" },
              project_tag: "Claude Web"
            }
          });
        }
      }

      if (checkCount > 120) {
        if (lastText.length > 20) {
          chrome.runtime.sendMessage({
            type: "AI_INTERACTION_CAPTURED",
            data: {
              platform: "claude",
              url: window.location.href,
              conversation_id: window.location.pathname,
              prompt_text: forPrompt,
              response_text: lastText,
              metadata: { capture_state: "partial_timeout" },
              project_tag: "Claude Web"
            }
          });
        } else {
          chrome.runtime.sendMessage({
            type: "OMNICONTEXT_CAPTURE_DIAGNOSTIC",
            platform: "claude",
            status: "error",
            error_code: "response_selector_not_found"
          });
        }
        clearInterval(interval);
      }
    }, 1500);
  }
})();
