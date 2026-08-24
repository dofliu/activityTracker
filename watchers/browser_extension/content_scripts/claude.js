// Claude.ai selectors；需登入後由 heartbeat/content-ready receipt 完成實站覆核。
OmniContextCapture.start({
  platform: "claude",
  projectTag: "Claude Web",
  inputSelectors: [
    "div.ProseMirror[contenteditable='true']",
    "[data-testid='chat-input'][contenteditable='true']",
    "fieldset div[contenteditable='true']",
    "textarea[placeholder*='Reply']",
    "textarea[placeholder*='Message']"
  ],
  sendButtonSelectors: [
    "button[aria-label*='Send Message']",
    "button[aria-label*='Send message']",
    "button[aria-label*='Send']",
    "button[aria-label*='傳送']",
    "fieldset button[type='submit']"
  ],
  responseSelectors: [
    "[data-is-streaming] .font-claude-response-body",
    ".font-claude-message",
    "[data-testid*='assistant'] .standard-markdown",
    ".standard-markdown"
  ],
  pollMilliseconds: 1500,
  maxChecks: 120
});
