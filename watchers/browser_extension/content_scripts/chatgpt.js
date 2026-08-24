// ChatGPT selectors；實站驗證日期 2026-08-25（chatgpt.com，繁中 anonymous session）。
OmniContextCapture.start({
  platform: "chatgpt",
  projectTag: "ChatGPT Web",
  inputSelectors: [
    "#prompt-textarea[contenteditable='true']",
    "#prompt-textarea",
    "textarea[data-testid='prompt-textarea']"
  ],
  sendButtonSelectors: [
    "button[data-testid='send-button']",
    "button[aria-label*='Send prompt']",
    "button[aria-label*='傳送提示詞']",
    "button[aria-label*='傳送']"
  ],
  responseSelectors: [
    "[data-message-author-role='assistant']",
    "[data-testid^='conversation-turn-'] [data-message-author-role='assistant']"
  ],
  pollMilliseconds: 1500,
  maxChecks: 120
});
