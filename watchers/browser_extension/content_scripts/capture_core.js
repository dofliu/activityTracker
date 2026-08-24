// 共用 capture core：各站只宣告 selectors，生命週期與可信度邊界集中管理。
(function() {
  "use strict";

  function extractText(element) {
    return String(element?.innerText || element?.value || element?.textContent || "").trim();
  }

  function firstMatch(selectors) {
    for (const selector of selectors) {
      const element = document.querySelector(selector);
      if (element) return element;
    }
    return null;
  }

  function allMatches(selectors) {
    const seen = new Set();
    const elements = [];
    for (const selector of selectors) {
      for (const element of document.querySelectorAll(selector)) {
        if (!seen.has(element)) {
          seen.add(element);
          elements.push(element);
        }
      }
    }
    return elements;
  }

  function latestResponse(selectors) {
    const responses = allMatches(selectors);
    const element = responses.length ? responses[responses.length - 1] : null;
    return { count: responses.length, text: extractText(element) };
  }

  function sendDiagnostic(platform, errorCode) {
    chrome.runtime.sendMessage({
      type: "OMNICONTEXT_CAPTURE_DIAGNOSTIC",
      platform,
      status: "error",
      error_code: errorCode
    });
  }

  function start(config) {
    const platform = config.platform;
    let lastPrompt = "";
    let lastPromptTime = 0;

    function inputElement() {
      return firstMatch(config.inputSelectors);
    }

    function targetIsComposer(target) {
      const input = inputElement();
      return Boolean(input && target && (target === input || input.contains(target)));
    }

    function observeResponse(forPrompt, baseline) {
      let checks = 0;
      let lastText = "";
      let stableCount = 0;
      const interval = setInterval(() => {
        checks += 1;
        const current = latestResponse(config.responseSelectors);
        const isNew = current.count > baseline.count || (
          current.text && current.text !== baseline.text
        );
        if (isNew && current.text) {
          stableCount = current.text === lastText && current.text.length > 20
            ? stableCount + 1
            : 0;
          lastText = current.text;
          if (stableCount >= 2) {
            clearInterval(interval);
            chrome.runtime.sendMessage({
              type: "AI_INTERACTION_CAPTURED",
              data: {
                platform,
                url: window.location.href,
                conversation_id: window.location.pathname,
                prompt_text: forPrompt,
                response_text: current.text,
                metadata: { capture_state: "stable_candidate" },
                project_tag: config.projectTag
              }
            });
          }
        }

        if (checks > config.maxChecks) {
          clearInterval(interval);
          if (lastText.length > 20) {
            chrome.runtime.sendMessage({
              type: "AI_INTERACTION_CAPTURED",
              data: {
                platform,
                url: window.location.href,
                conversation_id: window.location.pathname,
                prompt_text: forPrompt,
                response_text: lastText,
                metadata: { capture_state: "partial_timeout" },
                project_tag: config.projectTag
              }
            });
          } else {
            sendDiagnostic(platform, "new_response_selector_not_found");
          }
        }
      }, config.pollMilliseconds);
    }

    function captureCurrentInput() {
      const input = inputElement();
      if (!input) {
        sendDiagnostic(platform, "input_selector_not_found");
        return;
      }
      const promptText = extractText(input);
      if (promptText.length < 2) return;

      const now = Date.now();
      if (promptText === lastPrompt && now - lastPromptTime < 3000) return;
      lastPrompt = promptText;
      lastPromptTime = now;
      const baseline = latestResponse(config.responseSelectors);

      chrome.runtime.sendMessage({
        type: "AI_INTERACTION_CAPTURED",
        data: {
          platform,
          url: window.location.href,
          conversation_id: window.location.pathname,
          prompt_text: promptText,
          response_text: null,
          metadata: { capture_state: "prompt_detected" },
          project_tag: config.projectTag
        }
      });
      observeResponse(promptText, baseline);
    }

    document.addEventListener("keydown", (event) => {
      if (event.key === "Enter" && !event.shiftKey && !event.isComposing && targetIsComposer(event.target)) {
        captureCurrentInput();
      }
    }, true);

    document.addEventListener("submit", (event) => {
      const input = inputElement();
      if (input && event.target?.contains(input)) captureCurrentInput();
    }, true);

    document.addEventListener("click", (event) => {
      const button = event.target?.closest?.("button");
      if (!button) return;
      const input = inputElement();
      const composerForm = button.closest("form");
      const selectorMatch = config.sendButtonSelectors.some((selector) => button.matches(selector));
      if (selectorMatch || (input && composerForm?.contains(input))) captureCurrentInput();
    }, true);

    chrome.runtime.sendMessage({ type: "OMNICONTEXT_CONTENT_READY", platform });
  }

  globalThis.OmniContextCapture = { start };
})();
