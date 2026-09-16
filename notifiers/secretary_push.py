"""把一份推播內容送到所有指定的通道（ADR-014）——**扇出只有這一份**。

組裝一次（`notifiers.messages`）、渲染多次（`notifiers.channels`）。每個通道
各自 try/except，一個失敗不影響另一個；回傳的 receipt 逐通道記錄結果，供
排程 log 與 API 回查。

2026-09-16（TODO D5）之前，Windows 桌面通知走的是另一條路：`desktop_notifier`
自己組內容、自己送、自己吞例外，於是「一個通道失敗不影響其他通道」「receipt 逐
通道誠實」這兩個契約對桌面完全不適用，內容也和這裡的版本各自漂移。現在桌面是
第三個 `ChannelAdapter`，四個入口（晨報／晚間交接／每日日報／停滯提醒）＋里程碑
一律經過 :func:`push_message`——要送哪些通道由呼叫端給，怎麼送由 adapter 決定。
"""

from __future__ import annotations

import logging
from datetime import datetime
from typing import Any, Callable, Sequence

from core.config import get_config
from notifiers.channels import ChannelAdapter, desktop_channels, enabled_push_channels
from notifiers.messages import (
    Message,
    build_daily_summary,
    build_evening_handoff,
    build_morning_briefing,
    build_stagnation_alert,
    build_usage_milestone,
)

logger = logging.getLogger("OmniContext.SecretaryPush")


def push_message(
    message: Message | None,
    *,
    kind: str,
    cfg: Any | None = None,
    channels: Sequence[ChannelAdapter] | None = None,
) -> dict[str, Any]:
    """送出一則訊息；``message`` 為 None（沒有內容可推）時如實回報而不送。"""
    cfg = cfg or get_config()
    if message is None:
        return {"kind": kind, "sent": 0, "skipped": "no_content", "results": []}
    adapters = list(channels) if channels is not None else enabled_push_channels(cfg)
    if not adapters:
        return {"kind": kind, "sent": 0, "skipped": "no_channel_configured", "results": []}

    results: list[dict[str, Any]] = []
    for adapter in adapters:
        try:
            results.append(adapter.send(message))
        except Exception as exc:  # noqa: BLE001 — 單一通道失敗不影響其他通道
            logger.error("Push to %s crashed: %s", adapter.name, type(exc).__name__)
            results.append({"channel": adapter.name, "sent": False, "error": type(exc).__name__})
    sent = sum(1 for item in results if item.get("sent"))
    receipt = {"kind": kind, "sent": sent, "attempted": len(results), "results": results}
    logger.info(
        "Push %s: %s/%s channels ok (%s)",
        kind, sent, len(results), ", ".join(f"{r['channel']}={r.get('sent')}" for r in results),
    )
    return receipt


def _push(kind: str, builder: Callable[[], Message | None], cfg, channels) -> dict[str, Any]:
    try:
        message = builder()
    except Exception as exc:  # noqa: BLE001 — 組裝失敗如實回報，不推半成品
        logger.error("Building %s failed: %s", kind, exc, exc_info=True)
        return {"kind": kind, "sent": 0, "skipped": f"build_error:{type(exc).__name__}", "results": []}
    return push_message(message, kind=kind, cfg=cfg, channels=channels)


def push_morning_briefing(
    *,
    cfg: Any | None = None,
    now: datetime | None = None,
    channels: Sequence[ChannelAdapter] | None = None,
) -> dict[str, Any]:
    return _push("morning_briefing", lambda: build_morning_briefing(now=now, cfg=cfg), cfg, channels)


def push_evening_handoff(
    *,
    cfg: Any | None = None,
    now: datetime | None = None,
    channels: Sequence[ChannelAdapter] | None = None,
) -> dict[str, Any]:
    return _push("evening_handoff", lambda: build_evening_handoff(now=now), cfg, channels)


def push_daily_summary(
    date_str: str,
    *,
    cfg: Any | None = None,
    channels: Sequence[ChannelAdapter] | None = None,
) -> dict[str, Any]:
    return _push("daily_summary", lambda: build_daily_summary(date_str), cfg, channels)


def push_stagnation_alert(
    *,
    cfg: Any | None = None,
    channels: Sequence[ChannelAdapter] | None = None,
    min_idle_days: int | None = None,
) -> dict[str, Any]:
    """停滯提醒；``min_idle_days`` 為 None 時用 :func:`build_stagnation_alert` 的門檻。"""
    builder = (
        (lambda: build_stagnation_alert(min_idle_days=int(min_idle_days)))
        if min_idle_days is not None
        else (lambda: build_stagnation_alert())
    )
    return _push("stagnation_alert", builder, cfg, channels)


def push_usage_milestone(
    summary: Any,
    milestone_minutes: int,
    message: str,
    *,
    cfg: Any | None = None,
    channels: Sequence[ChannelAdapter] | None = None,
) -> dict[str, Any]:
    """每日介面使用里程碑。

    預設只送桌面：里程碑收據（`MilestoneNotificationReceipt.channel`）記的就是
    ``desktop``，預設多送一個通道會讓收據說不出話來。要送別的通道就明講 ``channels``。
    """
    if channels is None:
        channels = desktop_channels(cfg)
    return _push(
        "usage_milestone",
        lambda: build_usage_milestone(summary, milestone_minutes, message),
        cfg,
        channels,
    )


def push_enabled(cfg: Any | None = None) -> bool:
    """有任何**遠端**通道設定完成即為 True（排程據此決定是否組裝內容）。

    桌面通道不算在內：它的排程另有開關與時間（`notifiers.desktop.*`），
    見 `notifiers.channels` 的模組 docstring。
    """
    return bool(enabled_push_channels(cfg or get_config()))
