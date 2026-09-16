"""Browser Extension 送進來的 AI 事件怎麼認身分、怎麼判回應狀態（TODO D4）。

這兩個規則以前寫在 `core/server.py` 的模組層——一個 web 層模組裡的業務判斷。
它們決定同一輪對話會不會被當成兩筆（`turn_key`）、以及回應算不算
`final_candidate`，是 ADR-001 provenance 邊界的一部分，不是路由的事。
"""

import hashlib


def browser_conversation_key(conversation_id: str | None, url: str | None) -> str:
    if conversation_id and conversation_id.strip():
        return conversation_id.strip()
    conversation_ref = (url or "unknown").strip()
    return hashlib.sha256(
        conversation_ref.encode("utf-8", errors="replace")
    ).hexdigest()[:32]


def browser_response_status(response: str | None, capture_state: str | None) -> str:
    if not (response or "").strip():
        return "missing"
    return "final_candidate" if (capture_state or "").lower() == "stable_candidate" else "partial"
