"""Antigravity 的 transcript 格式與探索（ADR-025，TODO D9）。

Antigravity 把每段對話寫成 `**/transcript.jsonl`，兩種 step 有意義：

- `USER_INPUT`——真正的提問包在 `<USER_REQUEST>` 裡，要脫殼；`<SYSTEM_MESSAGE>` 與
  `<CONTEXT_SUMMARY>` 是工具自己注入的，整筆跳過。
- `PLANNER_RESPONSE`——模型的結論。`status == "DONE"` 是明確的終止標記。

對話 id 取自目錄結構（`<conv_id>/<something>/transcript.jsonl`），不是檔案內容。
這個平台不產生背景工作證據：來源沒有可驗證的 start／final 配對。
"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Iterator, List

from core.time_utils import get_local_now
from watchers.transcripts.base import (
    TranscriptTurn,
    build_turn_key,
    classify_response_status,
    iter_jsonl_records,
    parse_timestamp_safe,
)

PLATFORM = "antigravity"


def discover(cfg, *, full_history: bool = False, now=get_local_now) -> List[Path]:
    path_str = cfg.get("watchers.agent_log_watcher.antigravity_logs_path")
    if not path_str:
        return []

    base_path = cfg.expand_path(path_str)
    if not base_path.exists():
        return []
    return list(base_path.glob("**/transcript.jsonl"))


def parse(transcript_path: Path, *, cfg=None, now=get_local_now) -> Iterator[TranscriptTurn]:
    str_path = str(transcript_path)
    conv_id = transcript_path.parent.parent.name

    current_prompt = ""
    current_time = None
    current_position: int | None = None
    latest_real_response = ""
    latest_response_explicit_final = False

    def build(boundary_closed: bool) -> TranscriptTurn:
        return TranscriptTurn(
            platform=PLATFORM,
            conv_id=conv_id,
            prompt=current_prompt,
            response=latest_real_response if latest_real_response else None,
            url=str_path,
            timestamp=current_time,
            turn_key=build_turn_key(PLATFORM, str(transcript_path), current_position or 0),
            source_path=str(transcript_path.resolve()),
            source_position=current_position,
            response_status=classify_response_status(
                latest_real_response,
                explicit_final=latest_response_explicit_final,
                boundary_closed=boundary_closed,
            ),
        )

    for line_number, item in iter_jsonl_records(transcript_path):
        step_type = item.get("type")
        ts = parse_timestamp_safe(item.get("created_at") or item.get("timestamp"))

        if step_type == "USER_INPUT":
            raw_prompt = item.get("content", "")
            clean_prompt = raw_prompt.strip()
            if clean_prompt.startswith("<USER_REQUEST>"):
                clean_prompt = clean_prompt.replace("<USER_REQUEST>", "").replace("</USER_REQUEST>", "").strip()

            # 過濾系統內部注入訊息與 Checkpoint Summary
            if "<SYSTEM_MESSAGE>" in clean_prompt or "<CONTEXT_SUMMARY>" in clean_prompt:
                continue

            if len(clean_prompt) >= 2:
                # 遇到新提問：先送出上一輪提問與其最終真實回答
                if current_prompt and current_time:
                    yield build(boundary_closed=True)
                current_prompt = clean_prompt
                current_time = ts or datetime.fromtimestamp(transcript_path.stat().st_mtime)
                current_position = line_number
                latest_real_response = ""
                latest_response_explicit_final = False

        elif step_type == "PLANNER_RESPONSE":
            model_content = (item.get("content") or "").strip()
            # 排除純空字串或工具調用字串，只保留實質結論
            if model_content and len(model_content) >= 5 and not model_content.startswith("<") and not model_content.startswith("["):
                latest_real_response = model_content
                latest_response_explicit_final = item.get("status") == "DONE"

    # 最後一輪
    if current_prompt and current_time:
        yield build(boundary_closed=False)
