// Manus selectors；需登入後由 heartbeat/content-ready receipt 完成實站覆核。
OmniContextCapture.start({
  platform: "manus",
  projectTag: "Manus Task",
  inputSelectors: [
    "textarea[data-testid*='chat']",
    "textarea[placeholder*='message' i]",
    "div.ProseMirror[contenteditable='true']",
    "[contenteditable='true'][role='textbox']",
    "textarea"
  ],
  sendButtonSelectors: [
    "button[type='submit']",
    "button[data-testid*='send']",
    "button[aria-label*='Send']",
    "button[aria-label*='傳送']",
    ".send-btn"
  ],
  responseSelectors: [
    "[data-message-role='assistant']",
    "[data-role='assistant']",
    ".agent-message",
    ".task-result",
    ".markdown-content"
  ],
  pollMilliseconds: 2000,
  maxChecks: 90
});
