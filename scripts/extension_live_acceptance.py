"""Extension live PASS receipt 驅動器：引導取得本輪已驗證的擷取收據。

在已登入 ChatGPT / Claude.ai / Gemini 的 Chrome（已載入 OmniContext
Extension 並完成 token 配對）的機器上，對運行中的服務執行：

    python scripts/extension_live_acceptance.py --platforms chatgpt,claude

腳本以 ``/api/v1/extension/verification`` 建立 baseline，之後輪詢並把
harness 回報的 ``next_actions`` 翻成可以照做的步驟；直到本輪同時取得
新 heartbeat、content-ready、event 與非空 response 才記 PASS。
receipt 只含計數、時間戳與平台鍵，不含 token、URL、prompt 或 response。
"""

from __future__ import annotations

import argparse
import json
import sys
import tempfile
import time
import urllib.error
import urllib.request
from datetime import datetime
from pathlib import Path
from typing import Any

DEFAULT_BASE_URL = "http://127.0.0.1:8765"
DEFAULT_PLATFORMS = ("chatgpt", "claude")
RECEIPT_FILENAME = "extension-live-acceptance.json"

_ACTION_HINTS = {
    "configure_extension_token": (
        "執行 `python main.py init --show-token`，把 token 貼進 Extension popup 並儲存"
    ),
    "open_extension_popup_or_reload": (
        "點開 Extension 圖示（或在 chrome://extensions 按 Reload），讓新的 heartbeat 送出"
    ),
}

_PLATFORM_ACTION_HINTS = {
    "enable_platform": "在 Extension popup 勾選啟用「{key}」平台",
    "reload_target_tab": "重新整理已登入的 {key} 分頁，讓 content script 重新註冊",
    "send_new_prompt": "在 {key} 送出一則新的提問（歷史事件不算本輪）",
    "wait_for_assistant_response": "等待 {key} 的 AI 回覆完整結束",
}


def describe_next_actions(actions: list[str]) -> list[str]:
    """純函式：把 harness 的 next_actions 代碼翻成可執行步驟。"""
    steps: list[str] = []
    for action in actions:
        if action in _ACTION_HINTS:
            steps.append(_ACTION_HINTS[action])
            continue
        name, _, key = action.partition(":")
        template = _PLATFORM_ACTION_HINTS.get(name)
        steps.append(template.format(key=key or "?") if template else action)
    return steps


def summarize_verification(payload: dict[str, Any]) -> dict[str, Any]:
    """純函式：壓縮 verification 回應為 receipt 需要的非敏感欄位。"""
    checks = payload.get("checks", {})
    platforms = [
        {
            "key": item.get("key"),
            "enabled": bool(item.get("enabled")),
            "content_ready_after_start": bool(item.get("content_ready_after_start")),
            "event_after_start": bool(item.get("event_after_start")),
            "event_delta": int(item.get("event_delta") or 0),
            "response_after_start": bool(item.get("response_after_start")),
            "response_delta": int(item.get("response_delta") or 0),
            "passed": bool(item.get("passed")),
        }
        for item in checks.get("platforms", [])
    ]
    return {
        "verification_id": payload.get("verification_id"),
        "status": payload.get("status"),
        "started_at": payload.get("started_at"),
        "deadline_at": payload.get("deadline_at"),
        "required_platforms": payload.get("required_platforms", []),
        "token_configured": bool(checks.get("token_configured")),
        "heartbeat_after_start": bool(checks.get("heartbeat_after_start")),
        "last_heartbeat_at": checks.get("last_heartbeat_at"),
        "platforms": platforms,
        "next_actions": payload.get("next_actions", []),
        "privacy_boundary": payload.get("privacy_boundary"),
        "claim_boundary": payload.get("claim_boundary"),
    }


def _request_json(
    url: str,
    *,
    payload: dict[str, Any] | None = None,
    timeout: int = 10,
) -> dict[str, Any]:
    data = None
    headers = {"Accept": "application/json"}
    if payload is not None:
        data = json.dumps(payload).encode("utf-8")
        headers["Content-Type"] = "application/json"
    request = urllib.request.Request(url, data=data, headers=headers)
    with urllib.request.urlopen(request, timeout=timeout) as response:  # loopback
        return json.loads(response.read().decode("utf-8"))


def _parse_platforms(raw: str) -> list[str]:
    values = [item.strip().lower() for item in raw.split(",") if item.strip()]
    return list(dict.fromkeys(values)) or list(DEFAULT_PLATFORMS)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Extension live PASS receipt acceptance driver"
    )
    parser.add_argument("--base-url", default=DEFAULT_BASE_URL)
    parser.add_argument(
        "--platforms",
        default=",".join(DEFAULT_PLATFORMS),
        help="逗號分隔：chatgpt,claude,gemini",
    )
    parser.add_argument("--timeout", type=int, default=600, help="verification 秒數上限（60–1800）")
    parser.add_argument("--poll-seconds", type=int, default=5)
    parser.add_argument("--output-dir", default=None, help="receipt 輸出目錄；預設 OS temp")
    args = parser.parse_args(argv)
    base_url = args.base_url.rstrip("/")

    try:
        _request_json(f"{base_url}/api/v1/health")
    except (urllib.error.URLError, OSError, ValueError) as exc:
        print(
            json.dumps(
                {
                    "status": "service_unreachable",
                    "base_url": base_url,
                    "error": str(exc),
                    "hint": "先在本機啟動服務：python main.py（或 omnicontext）",
                },
                ensure_ascii=False,
                indent=2,
            )
        )
        return 2

    try:
        run = _request_json(
            f"{base_url}/api/v1/extension/verification",
            payload={
                "platforms": _parse_platforms(args.platforms),
                "timeout_seconds": args.timeout,
            },
        )
    except urllib.error.HTTPError as exc:
        print(
            json.dumps(
                {"status": "start_rejected", "http_status": exc.code, "error": str(exc)},
                ensure_ascii=False,
                indent=2,
            )
        )
        return 2

    verification_id = run["verification_id"]
    print(f"verification_id: {verification_id}")
    print(f"deadline: {run.get('deadline_at')}（逾時前完成下列步驟）")

    poll_seconds = max(2, int(args.poll_seconds))
    last_actions: list[str] | None = None
    payload = run
    while payload.get("status") == "running":
        actions = list(payload.get("next_actions", []))
        if actions != last_actions:
            last_actions = actions
            print("\n下一步：")
            for step in describe_next_actions(actions):
                print(f"  - {step}")
        time.sleep(poll_seconds)
        try:
            payload = _request_json(
                f"{base_url}/api/v1/extension/verification/{verification_id}"
            )
        except (urllib.error.URLError, OSError, ValueError) as exc:
            print(f"輪詢失敗（服務可能已停止）：{exc}")
            return 2

    receipt = summarize_verification(payload)
    receipt["captured_at"] = datetime.now().astimezone().isoformat(timespec="seconds")
    receipt["source"] = f"{base_url}/api/v1/extension/verification/{verification_id}"

    output_dir = Path(
        args.output_dir or tempfile.mkdtemp(prefix="omnicontext-acceptance-")
    )
    output_dir.mkdir(parents=True, exist_ok=True)
    receipt_path = output_dir / RECEIPT_FILENAME
    receipt_path.write_text(
        json.dumps(receipt, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    receipt["receipt_path"] = str(receipt_path)

    print()
    print(json.dumps(receipt, ensure_ascii=False, indent=2))
    if receipt["status"] == "passed":
        print(
            "\n# PASS：可把本 receipt 記入 STATUS.yaml 的 "
            "extension_live_verification_harness（real_pass_receipt: obtained）。"
        )
        return 0
    print("\n# 未通過：依上方最後的『下一步』完成操作後重跑本腳本。")
    return 1


if __name__ == "__main__":
    sys.exit(main())
