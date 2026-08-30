"""P2.7 live acceptance：驗收本機 agent 背景任務的成對 receipt。

在真正跑過 Claude Code / Claude Desktop local-agent / Codex 任務的機器上，
對運行中的 OmniContext 服務執行：

    python scripts/background_task_live_acceptance.py --platforms claude_code,codex

腳本只讀取非敏感的 ``/api/v1/background-tasks/today`` summary（計數與秒數），
不接觸 prompt / response 內容。每個要求的平台當日必須至少有 1 筆
``completed``（成對 start + final）receipt 才算 PASS；receipt JSON 預設
寫入 OS temp，並輸出可貼回 STATUS.yaml 的建議段落。
"""

from __future__ import annotations

import argparse
import json
import sys
import tempfile
import urllib.error
import urllib.request
from datetime import datetime
from pathlib import Path
from typing import Any

DEFAULT_BASE_URL = "http://127.0.0.1:8765"
DEFAULT_PLATFORMS = ("claude_code", "claude_desktop", "codex")
RECEIPT_FILENAME = "background-task-live-acceptance.json"


def fetch_summary(base_url: str, date: str | None = None, timeout: int = 10) -> dict[str, Any]:
    url = f"{base_url.rstrip('/')}/api/v1/background-tasks/today"
    if date:
        url += f"?date={date}"
    with urllib.request.urlopen(url, timeout=timeout) as response:  # loopback service
        return json.loads(response.read().decode("utf-8"))


def evaluate_background_receipts(
    summary: dict[str, Any],
    required_platforms: list[str],
) -> dict[str, Any]:
    """純函式：由 summary 判定每個平台是否已有可驗證的 completed receipt。"""
    interfaces = {
        str(item.get("platform")): item for item in summary.get("interfaces", [])
    }
    platforms: list[dict[str, Any]] = []
    for key in required_platforms:
        item = interfaces.get(key, {})
        completed = int(item.get("completed_tasks") or 0)
        seconds = float(item.get("verified_seconds") or 0.0)
        platforms.append(
            {
                "platform": key,
                "completed_tasks_today": completed,
                "verified_seconds_today": round(seconds, 3),
                "passed": completed > 0 and seconds > 0,
            }
        )
    all_passed = bool(platforms) and all(item["passed"] for item in platforms)
    return {
        "status": "passed" if all_passed else "failed",
        "date": summary.get("date"),
        "required_platforms": list(required_platforms),
        "platforms": platforms,
        "verified_union_seconds_all_platforms": summary.get("verified_seconds"),
        "completed_task_count_all_platforms": summary.get("completed_task_count"),
        "awaiting_final_count": summary.get("awaiting_final_count"),
        "untrusted_duration_count": summary.get("untrusted_duration_count"),
        "claim_boundary": summary.get("claim_boundary"),
    }


def render_status_snippet(receipt: dict[str, Any]) -> str:
    lines = [
        "  background_task_receipts:",
        f"    captured_at: \"{receipt.get('captured_at', '')}\"",
        "    local_api: passed",
    ]
    for item in receipt.get("platforms", []):
        key = item["platform"]
        lines.append(
            f"    {key}_completed_receipts_today: {item['completed_tasks_today']}"
        )
        lines.append(
            f"    {key}_verified_seconds_today: {item['verified_seconds_today']}"
        )
    lines.append(
        "    claim_boundary: \"僅代表本機成對 prompt start 與 explicit final completion receipt；"
        "不代表前景使用、生產力或全天 coverage。\""
    )
    return "\n".join(lines)


def _parse_platforms(raw: str) -> list[str]:
    values = [item.strip().lower() for item in raw.split(",") if item.strip()]
    return list(dict.fromkeys(values)) or list(DEFAULT_PLATFORMS)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="P2.7 background task live-receipt acceptance"
    )
    parser.add_argument("--base-url", default=DEFAULT_BASE_URL)
    parser.add_argument(
        "--platforms",
        default=",".join(DEFAULT_PLATFORMS),
        help="逗號分隔：claude_code,claude_desktop,codex（可只驗收其中幾個）",
    )
    parser.add_argument("--date", default=None, help="YYYY-MM-DD；預設今天")
    parser.add_argument("--output-dir", default=None, help="receipt 輸出目錄；預設 OS temp")
    args = parser.parse_args(argv)

    try:
        summary = fetch_summary(args.base_url, args.date)
    except (urllib.error.URLError, OSError, ValueError) as exc:
        print(
            json.dumps(
                {
                    "status": "service_unreachable",
                    "base_url": args.base_url,
                    "error": str(exc),
                    "hint": "先在本機啟動服務：python main.py（或 omnicontext）",
                },
                ensure_ascii=False,
                indent=2,
            )
        )
        return 2

    receipt = evaluate_background_receipts(summary, _parse_platforms(args.platforms))
    receipt["captured_at"] = datetime.now().astimezone().isoformat(timespec="seconds")
    receipt["source"] = f"{args.base_url.rstrip('/')}/api/v1/background-tasks/today"

    output_dir = Path(
        args.output_dir or tempfile.mkdtemp(prefix="omnicontext-acceptance-")
    )
    output_dir.mkdir(parents=True, exist_ok=True)
    receipt_path = output_dir / RECEIPT_FILENAME
    receipt_path.write_text(
        json.dumps(receipt, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    receipt["receipt_path"] = str(receipt_path)

    print(json.dumps(receipt, ensure_ascii=False, indent=2))
    if receipt["status"] == "passed":
        print("\n# 可貼回 STATUS.yaml evidence_snapshot 的建議段落：")
        print(render_status_snippet(receipt))
    else:
        print("\n# 尚未通過。常見原因與對策：")
        print(
            f"#  1) 本腳本以「當日」為界（本次檢查 {receipt.get('date')}）。"
            "剛過午夜時今天可能還沒有任何完成任務；"
            "可用 --date YYYY-MM-DD 檢查前一天的 receipt。"
        )
        print(
            "#  2) claude_code 只讀「本機」Claude Code CLI 的 transcript"
            "（雲端／網頁版 session 不會寫入本機 ~/.claude/projects）；"
            "claude_desktop 需要 Cowork／local-agent 任務。"
            "在本機實際跑一個任務並等它完整結束。"
        )
        awaiting = int(receipt.get("awaiting_final_count") or 0)
        if awaiting:
            print(
                f"#  3) 偵測到 {awaiting} 筆已開始、尚未看到 final 的任務——"
                "等它們真正完成後會轉為 completed，再重跑本腳本。"
            )
        else:
            print(
                "#  3) 任務完成後 agent watcher 約每 60 秒增量掃描一次，"
                "稍候 1–2 分鐘再重跑。"
            )
    return 0 if receipt["status"] == "passed" else 1


if __name__ == "__main__":
    sys.exit(main())
