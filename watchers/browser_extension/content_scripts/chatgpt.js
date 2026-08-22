// content_scripts/chatgpt.js - Injected into chatgpt.com and chat.openai.com

(function() {
  console.log("[OmniContext] ChatGPT watcher initialized.");

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
    if (!inputEl) return;

    const promptText = (inputEl.innerText || inputEl.value || "").trim();
    if (!promptText || promptText.length < 2) return;

    const now = Date.now();
    if (promptText === lastPrompt && (now - lastPromptTime) < 3000) return;

    lastPrompt = promptText;
    lastPromptTime = now;

    console.log("[OmniContext] Captured ChatGPT Prompt:", promptText.substring(0, 50));

    chrome.runtime.sendMessage({
      type: "AI_INTERACTION_CAPTURED",
      data: {
        platform: "chatgpt",
        url: window.location.href,
        prompt_text: promptText,
        response_text: null,
        project_tag: "ChatGPT Web"
      }
    });

    observeResponse(promptText);
  }

  function observeResponse(forPrompt) {
    let checkCount = 0;
    const interval = setInterval(() => {
      checkCount++;
      const assistants = document.querySelectorAll("div[data-message-author-role='assistant']");
      if (assistants.length > 0) {
        const lastResp = assistants[assistants.length - 1];
        const respText = lastResp.innerText.trim();
        
        if (respText.length > 20 && checkCount >= 5) {
          clearInterval(interval);
          chrome.runtime.sendMessage({
            type: "AI_INTERACTION_CAPTURED",
            data: {
              platform: "chatgpt",
              url: window.location.href,
              prompt_text: forPrompt,
              response_text: respText.substring(0, 2000),
              project_tag: "ChatGPT Web"
            }
          });
        }
      }

      if (checkCount > 30) {
        clearInterval(interval);
      }
    }, 1500);
  }
})();
