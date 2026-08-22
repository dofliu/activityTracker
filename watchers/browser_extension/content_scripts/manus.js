// content_scripts/manus.js - Injected into manus.im

(function() {
  console.log("[OmniContext] Manus watcher initialized.");

  let lastPrompt = "";
  let lastPromptTime = 0;

  document.addEventListener("keydown", (e) => {
    if (e.key === "Enter" && !e.shiftKey) {
      captureCurrentInput();
    }
  }, true);

  document.addEventListener("click", (e) => {
    const target = e.target;
    if (target.closest("button[type='submit'], .send-btn, button[aria-label*='Send']")) {
      captureCurrentInput();
    }
  }, true);

  function captureCurrentInput() {
    const inputEl = document.querySelector("textarea, input[type='text'], div[contenteditable='true']");
    if (!inputEl) return;

    const promptText = (inputEl.innerText || inputEl.value || "").trim();
    if (!promptText || promptText.length < 2) return;

    const now = Date.now();
    if (promptText === lastPrompt && (now - lastPromptTime) < 3000) return;

    lastPrompt = promptText;
    lastPromptTime = now;

    console.log("[OmniContext] Captured Manus Prompt:", promptText.substring(0, 50));

    chrome.runtime.sendMessage({
      type: "AI_INTERACTION_CAPTURED",
      data: {
        platform: "manus",
        url: window.location.href,
        prompt_text: promptText,
        response_text: null,
        project_tag: "Manus Task"
      }
    });

    observeResponse(promptText);
  }

  function observeResponse(forPrompt) {
    let checkCount = 0;
    const interval = setInterval(() => {
      checkCount++;
      const results = document.querySelectorAll(".task-result, .agent-message, .markdown-content");
      if (results.length > 0) {
        const lastResp = results[results.length - 1];
        const respText = lastResp.innerText.trim();
        
        if (respText.length > 20 && checkCount >= 6) {
          clearInterval(interval);
          chrome.runtime.sendMessage({
            type: "AI_INTERACTION_CAPTURED",
            data: {
              platform: "manus",
              url: window.location.href,
              prompt_text: forPrompt,
              response_text: respText.substring(0, 2000),
              project_tag: "Manus Task"
            }
          });
        }
      }

      if (checkCount > 30) {
        clearInterval(interval);
      }
    }, 2000);
  }
})();
