"""多通道推播與 LINE 的 contract tests（ADR-014）。

- 訊息內容與呈現分離：同一份 Message 渲染成純文字（LINE）或 HTML（Telegram）。
- 組裝在任一子步驟失敗時只省略該段，不讓整則推播消失；沒有內容就不推。
- adapter 能力如實宣告：LINE 不能接收、不能按鈕、不能刪訊息、不支援富文字。
- 扇出：一個通道失敗不影響另一個，receipt 逐通道誠實。
- LINE 設定：憑證來源優先序、失敗分類、驗證通過才寫 config；token 只走
  Authorization header，絕不進 URL、log 或任何 receipt。
全部以 fake transport 執行，不需真實 bot、網路或資料庫。
"""

from __future__ import annotations

import pytest

from notifiers import line_setup
from notifiers.channels import (
    LineChannel,
    TelegramChannel,
    channels_status,
    enabled_push_channels,
    line_channel,
    telegram_channel,
)
from notifiers.messages import (
    Message,
    Section,
    build_daily_summary,
    build_evening_handoff,
    build_morning_briefing,
    build_stagnation_alert,
    render_plain,
    render_telegram_html,
)
from notifiers.secretary_push import push_message, push_morning_briefing

LINE_TOKEN = "line-channel-access-token-for-tests"
LINE_TO = "U0123456789abcdef0123456789abcdef"
TG_TOKEN = "123456789:AAFakeTelegramToken"
TG_CHAT = "987654321"


class DictConfig:
    def __init__(self, data):
        self.data = data

    def get(self, key_path, default=None):
        value = self.data
        for key in key_path.split("."):
            if not isinstance(value, dict) or key not in value:
                return default
            value = value[key]
        return value


def _cfg(telegram=False, line=False):
    return DictConfig({
        "notifiers": {
            "telegram": {"enabled": telegram, "bot_token": TG_TOKEN, "chat_id": TG_CHAT},
            "line": {"enabled": line, "access_token": LINE_TOKEN, "to": LINE_TO},
        }
    })


class TelegramTransport:
    """Telegram transport 簽章：(url, payload, timeout)。"""

    def __init__(self, status=200, body=None):
        self.calls = []
        self.status = status
        self.body = body if body is not None else {"ok": True, "result": {}}

    def __call__(self, url, payload, timeout):
        self.calls.append((url, payload))
        return self.status, self.body

    def texts(self):
        return [payload.get("text", "") for _, payload in self.calls]


class LineTransport:
    """LINE transport 簽章：(url, payload, headers, timeout)。"""

    def __init__(self, status=200, body=None):
        self.calls = []
        self.status = status
        self.body = body if body is not None else {}

    def __call__(self, url, payload, headers, timeout):
        self.calls.append({"url": url, "payload": payload, "headers": headers})
        return self.status, self.body

    def texts(self):
        return [
            message["text"]
            for call in self.calls
            for message in (call["payload"].get("messages") or [])
        ]


@pytest.fixture(autouse=True)
def _no_env(monkeypatch):
    for name in ("LINE_CHANNEL_ACCESS_TOKEN", "LINE_TO_ID", "TELEGRAM_BOT_TOKEN", "TELEGRAM_CHAT_ID"):
        monkeypatch.delenv(name, raising=False)


SAMPLE = Message(
    title="標題",
    sections=(Section(heading="分節：", lines=("• 一", "• 二")), Section(lines=("尾段",))),
    footer="邊界說明",
)


# ---- 內容與呈現分離 ----


def test_plain_render_has_no_markup():
    text = render_plain(SAMPLE)
    assert "<" not in text and ">" not in text
    assert text.splitlines() == ["標題", "", "分節：", "• 一", "• 二", "", "尾段", "", "邊界說明"]


def test_telegram_render_bolds_and_escapes():
    html = render_telegram_html(SAMPLE)
    assert html.startswith("<b>標題</b>") and "<b>分節：</b>" in html and "<i>邊界說明</i>" in html
    # 內容一律 escape，避免使用者資料破壞 HTML 解析
    risky = Message(title="a <b>x", sections=(Section(lines=("2 < 3 & 4 > 1",)),))
    rendered = render_telegram_html(risky)
    assert "&lt;b&gt;" in rendered and "2 &lt; 3 &amp; 4 &gt; 1" in rendered


# ---- 組裝 ----


def _projects(**overrides):
    base = {
        "project_key": "alpha", "display_name": "Alpha", "status": "active",
        "last_action_summary": "修 CI", "last_activity_at": "2026-09-03T08:00:00",
        "idle_days": 0, "category": "dev",
    }
    base.update(overrides)
    return base


def _greeting(*, window="today", observed=True, source="rules"):
    """build_greeting 的替身：只要形狀對，晨報就該把它擺在最前面。"""
    lead = {"today": "今天才開工約 2 小時，你已經：", "yesterday": "昨天你："}[window]
    if not observed:
        return {"window": window, "headline": "Dof，早安。", "lead": f"{'今天' if window == 'today' else '昨天'}還沒偵測到活動。",
                "achievements": [], "recent_summary": None, "encouragement": "慢慢開始也很好。", "source": "rules",
                "text": "Dof，早安。 今天還沒偵測到活動。 慢慢開始也很好。", "stats": {"observed_anything": False}}
    return {"window": window, "headline": "Dof，早安。", "lead": lead,
            "achievements": ["開了 1 個 PR、合併了 1 個 PR", "3 個 commit 落在 2 個 repo，＋30 行"],
            "recent_summary": None, "encouragement": "節奏很好；記得中間站起來走一走。", "source": source,
            "text": "Dof，早安。（LLM 潤飾版）今天開工兩小時就開了 1 個 PR。", "stats": {"observed_anything": True}}


def _stub_greeting(monkeypatch, responses):
    calls = []

    def fake(*, window="today", **kw):
        calls.append(window)
        return responses[window]

    monkeypatch.setattr("core.secretary_greeting.build_greeting", fake)
    return calls


def test_morning_briefing_opens_with_the_secretary_greeting(monkeypatch):
    monkeypatch.setattr("core.secretary_packs.latest_pack_summary", lambda **kw: None)
    monkeypatch.setattr("core.proactive_secretary.briefing_proposals", lambda limit=2: {"proposals": [], "total": 0})
    calls = _stub_greeting(monkeypatch, {"today": _greeting()})
    message = build_morning_briefing(projects=[_projects()], open_loops=[], cfg=_cfg())
    first = message.sections[0]
    assert first.heading is None
    assert first.lines == (
        "Dof，早安。", "今天才開工約 2 小時，你已經：",
        "• 開了 1 個 PR、合併了 1 個 PR", "• 3 個 commit 落在 2 個 repo，＋30 行",
        "節奏很好；記得中間站起來走一走。",
    )
    text = render_plain(message)
    assert text.index("Dof，早安。") < text.index("今日重點活躍專案")
    # 今天有活動就不必去看昨天
    assert calls == ["today"]
    # Telegram 版一樣是同一份內容，只是 escape 過
    assert "Dof，早安。" in render_telegram_html(message)


def test_morning_briefing_says_yesterday_when_today_is_still_empty(monkeypatch):
    monkeypatch.setattr("core.secretary_packs.latest_pack_summary", lambda **kw: None)
    monkeypatch.setattr("core.proactive_secretary.briefing_proposals", lambda limit=2: {"proposals": []})
    calls = _stub_greeting(monkeypatch, {
        "today": _greeting(observed=False),
        "yesterday": _greeting(window="yesterday"),
    })
    text = render_plain(build_morning_briefing(projects=[], open_loops=[], cfg=_cfg()))
    assert "昨天你：" in text and "今天還沒偵測到活動" not in text
    assert calls == ["today", "yesterday"]

    # 昨天也沒有 → 誠實留今天那句，不硬湊
    _stub_greeting(monkeypatch, {"today": _greeting(observed=False), "yesterday": _greeting(window="yesterday", observed=False)})
    text = render_plain(build_morning_briefing(projects=[], open_loops=[], cfg=_cfg()))
    assert "今天還沒偵測到活動" in text and "昨天" not in text


def test_morning_briefing_uses_llm_text_verbatim_and_can_be_switched_off(monkeypatch):
    monkeypatch.setattr("core.secretary_packs.latest_pack_summary", lambda **kw: None)
    monkeypatch.setattr("core.proactive_secretary.briefing_proposals", lambda limit=2: {"proposals": []})
    _stub_greeting(monkeypatch, {"today": _greeting(source="llm")})
    message = build_morning_briefing(projects=[], open_loops=[], cfg=_cfg())
    assert message.sections[0].lines == ("Dof，早安。（LLM 潤飾版）今天開工兩小時就開了 1 個 PR。",)

    off = DictConfig({"proactive_secretary": {"greeting": {"in_morning_briefing": False}}})
    text = render_plain(build_morning_briefing(projects=[], open_loops=[], cfg=off))
    assert "Dof，早安" not in text and "晨間簡報" in text

    def boom(**kw):
        raise RuntimeError("db locked")

    monkeypatch.setattr("core.secretary_greeting.build_greeting", boom)
    text = render_plain(build_morning_briefing(projects=[], open_loops=[], cfg=_cfg()))
    assert "Dof，早安" not in text and "晨間簡報" in text and "尚無高頻專案" in text


def test_morning_briefing_includes_projects_loops_and_degrades_gracefully(monkeypatch):
    _stub_greeting(monkeypatch, {"today": _greeting()})
    monkeypatch.setattr("core.secretary_packs.latest_pack_summary", lambda **kw: {"needs_pull": 2})
    monkeypatch.setattr("core.secretary_packs.pack_summary_line", lambda summary: "早晨包：repo 需 pull 2")
    monkeypatch.setattr(
        "core.proactive_secretary.briefing_proposals",
        lambda limit=2: {"proposals": [{"project_key": "alpha", "title": "PR 等 review", "why_now": "只差一個 merge"}], "total": 3},
    )
    message = build_morning_briefing(
        projects=[_projects()], open_loops=[{"project_key": "alpha", "title": "收尾 ADR"}]
    )
    text = render_plain(message)
    assert "Alpha：修 CI" in text and "收尾 ADR" in text
    assert "早晨包：repo 需 pull 2" in text
    assert "PR 等 review" in text and "只差一個 merge" in text and "共 3 項" in text


def test_morning_briefing_survives_secretary_and_pack_failure(monkeypatch):
    def boom(*args, **kwargs):
        raise RuntimeError("offline")

    monkeypatch.setattr("core.secretary_packs.latest_pack_summary", boom)
    monkeypatch.setattr("core.proactive_secretary.briefing_proposals", boom)
    monkeypatch.setattr("core.secretary_greeting.build_greeting", boom)
    text = render_plain(build_morning_briefing(projects=[], open_loops=[]))
    assert "晨間簡報" in text and "尚無高頻專案" in text and "無待辦未結事項" in text
    # 秘書層與早晨包都掛掉時，那兩段完全不出現（也不留談建議的 footer）
    assert "待判斷建議" not in text and "早晨包" not in text and "建議" not in text


def test_evening_handoff_only_counts_today(monkeypatch):
    monkeypatch.setattr("core.secretary_packs.latest_pack_summary", lambda **kw: None)
    from datetime import datetime

    now = datetime(2026, 9, 3, 22, 0)
    message = build_evening_handoff(
        now=now,
        projects=[_projects(), _projects(display_name="Beta", last_activity_at="2026-08-01T10:00:00")],
        open_loops=[],
    )
    text = render_plain(message)
    assert "今日推進 1 個專案" in text and "Alpha" in text and "Beta" not in text
    assert "唯讀盤點" in text


def test_daily_summary_and_stagnation_return_none_without_content():
    message = build_daily_summary("2026-09-03", raw_markdown="# 日報\n內容")
    assert message is not None and "日報" in render_plain(message)
    long_message = build_daily_summary("2026-09-03", raw_markdown="字" * 5000, max_chars=100)
    assert "已截斷" in render_plain(long_message)
    assert build_stagnation_alert(projects=[_projects()]) is None
    stagnant = build_stagnation_alert(projects=[_projects(status="idle", idle_days=9)])
    assert stagnant is not None and "閒置 9 天" in render_plain(stagnant)


# ---- adapter 能力與傳送 ----


def test_capabilities_are_declared_honestly():
    assert TelegramChannel("t", "c").capabilities() == {
        "channel": "telegram", "receive": True, "buttons": True, "delete_message": True, "rich_text": "html",
    }
    assert LineChannel("t", "u").capabilities() == {
        "channel": "line", "receive": False, "buttons": False, "delete_message": False, "rich_text": "plain",
    }


def test_telegram_adapter_sends_html_and_chunks_long_text():
    transport = TelegramTransport()
    channel = TelegramChannel(TG_TOKEN, TG_CHAT, transport=transport)
    receipt = channel.send(SAMPLE)
    assert receipt == {"channel": "telegram", "sent": True, "parts_sent": 1}
    assert transport.calls[0][1]["parse_mode"] == "HTML" and "<b>標題</b>" in transport.texts()[0]

    transport = TelegramTransport()
    channel = TelegramChannel(TG_TOKEN, TG_CHAT, transport=transport)
    receipt = channel.send_text("字" * 8000)
    assert receipt["sent"] is True and receipt["parts_sent"] == 3
    assert sum(len(text) for text in transport.texts()) == 8000


def test_telegram_falls_back_to_plain_text_when_html_is_rejected():
    class PickyTransport(TelegramTransport):
        def __call__(self, url, payload, timeout):
            self.calls.append((url, payload))
            if payload.get("parse_mode") == "HTML":
                return 400, {"ok": False, "description": "Bad Request: can't parse entities"}
            return 200, {"ok": True, "result": {}}

    transport = PickyTransport()
    channel = TelegramChannel(TG_TOKEN, TG_CHAT, transport=transport)
    receipt = channel.send(SAMPLE)
    assert receipt["sent"] is True
    assert [call[1].get("parse_mode") for call in transport.calls] == ["HTML", None]


def test_line_adapter_sends_plain_text_with_bearer_header_and_batches():
    transport = LineTransport()
    channel = LineChannel(LINE_TOKEN, LINE_TO, transport=transport)
    receipt = channel.send(SAMPLE)
    assert receipt == {"channel": "line", "sent": True, "parts_sent": 1}
    call = transport.calls[0]
    assert call["url"].endswith("/message/push") and LINE_TOKEN not in call["url"]
    assert call["headers"]["Authorization"] == f"Bearer {LINE_TOKEN}"
    assert call["payload"]["to"] == LINE_TO
    assert "<b>" not in transport.texts()[0] and "標題" in transport.texts()[0]

    # 超長內容：每則 ≤ 4800 字、每次 push ≤ 5 則
    transport = LineTransport()
    channel = LineChannel(LINE_TOKEN, LINE_TO, transport=transport)
    receipt = channel.send_text("字" * 30000)
    assert receipt["sent"] is True and receipt["parts_sent"] == 7
    assert all(len(call["payload"]["messages"]) <= 5 for call in transport.calls)
    assert all(len(text) <= 4800 for text in transport.texts())


def test_line_reports_quota_limit_distinctly():
    channel = LineChannel(LINE_TOKEN, LINE_TO, transport=LineTransport(status=429, body={"message": "monthly limit"}))
    receipt = channel.send(SAMPLE)
    assert receipt == {"channel": "line", "sent": False, "error": "quota_or_rate_limited", "parts_sent": 0}


def test_adapter_network_failure_is_reported_not_raised():
    def broken(*args, **kwargs):
        raise ConnectionError("dns")

    assert LineChannel(LINE_TOKEN, LINE_TO, transport=broken).send(SAMPLE)["error"] == "ConnectionError"
    assert TelegramChannel(TG_TOKEN, TG_CHAT, transport=broken).send(SAMPLE)["error"] == "ConnectionError"


# ---- 通道解析與扇出 ----


def test_enabled_channels_follow_switches():
    assert enabled_push_channels(_cfg()) == []
    assert [c.name for c in enabled_push_channels(_cfg(telegram=True))] == ["telegram"]
    assert [c.name for c in enabled_push_channels(_cfg(line=True))] == ["line"]
    assert [c.name for c in enabled_push_channels(_cfg(telegram=True, line=True))] == ["telegram", "line"]
    # 開關開了但憑證不全 → 不算就緒
    partial = DictConfig({"notifiers": {"line": {"enabled": True, "access_token": LINE_TOKEN, "to": ""}}})
    assert line_channel(partial) is None
    assert telegram_channel(DictConfig({"notifiers": {"telegram": {"enabled": True, "bot_token": "", "chat_id": TG_CHAT}}})) is None


def test_one_channel_failure_does_not_block_the_other():
    ok = LineChannel(LINE_TOKEN, LINE_TO, transport=LineTransport())

    class Exploding(TelegramChannel):
        def send(self, message):
            raise RuntimeError("boom")

    receipt = push_message(SAMPLE, kind="test", cfg=_cfg(), channels=[Exploding(TG_TOKEN, TG_CHAT), ok])
    assert receipt["sent"] == 1 and receipt["attempted"] == 2
    assert {r["channel"]: r.get("sent") for r in receipt["results"]} == {"telegram": False, "line": True}
    assert receipt["results"][0]["error"] == "RuntimeError"


def test_push_skips_honestly_without_channels_or_content():
    assert push_message(SAMPLE, kind="test", cfg=_cfg(), channels=[])["skipped"] == "no_channel_configured"
    assert push_message(None, kind="test", cfg=_cfg(), channels=[])["skipped"] == "no_content"


def test_push_reports_build_errors_without_sending(monkeypatch):
    def boom(**kwargs):
        raise ValueError("bad state")

    monkeypatch.setattr("notifiers.secretary_push.build_morning_briefing", boom)
    channel = LineChannel(LINE_TOKEN, LINE_TO, transport=LineTransport())
    receipt = push_morning_briefing(cfg=_cfg(line=True), channels=[channel])
    assert receipt["skipped"] == "build_error:ValueError" and receipt["sent"] == 0


# ---- 狀態與 secret 邊界 ----


def test_channels_status_declares_line_as_push_only_and_hides_secrets():
    status = channels_status(_cfg(telegram=True, line=True))
    assert status["push_ready"] == ["telegram", "line"]
    line = status["channels"]["line"]
    assert line["receive"] is False and line["push_only"] is True and line["rich_text"] == "plain"
    assert "webhook" in line["push_only_reason"]
    assert status["channels"]["telegram"]["receive"] is True
    blob = str(status)
    assert LINE_TOKEN not in blob and LINE_TO not in blob and TG_TOKEN not in blob


def test_line_status_and_credential_precedence(monkeypatch):
    cfg = _cfg(line=True)
    assert line_setup.line_status(cfg)["token_source"] == "config"
    monkeypatch.setenv("LINE_CHANNEL_ACCESS_TOKEN", "from-env")
    status = line_setup.line_status(cfg)
    assert status["token_source"] == "env" and status["token_configured"] is True
    assert status["push_only"] is True and "從不回傳" not in str(status)
    assert "from-env" not in str(status)


# ---- LINE 設定流程 ----


def test_line_connection_test_verifies_then_sends():
    transport = LineTransport(body={"basicId": "@omni", "displayName": "OmniContext"})
    receipt = line_setup.test_line_connection(
        access_token=LINE_TOKEN, to=LINE_TO, cfg=_cfg(line=True), transport=transport
    )
    assert receipt["ok"] is True and receipt["message_sent"] is True
    assert receipt["bot_basic_id"] == "@omni" and receipt["push_only"] is True
    assert [call["url"].rsplit("/v2/bot/", 1)[1] for call in transport.calls] == ["info", "message/push"]
    assert LINE_TOKEN not in str(receipt) and LINE_TO not in str(receipt)


@pytest.mark.parametrize(
    "status, expected",
    [(401, "invalid_token"), (403, "invalid_token"), (400, "invalid_request"), (429, "rate_limited"), (500, "line_api_error")],
)
def test_line_failures_are_classified(status, expected):
    receipt = line_setup.test_line_connection(
        access_token=LINE_TOKEN, to=LINE_TO, cfg=_cfg(line=True),
        transport=LineTransport(status=status, body={"message": "nope"}),
    )
    assert receipt["ok"] is False and receipt["error_code"] == expected and receipt["step"] == "info"


def test_line_missing_token_and_missing_recipient_are_distinct():
    empty = DictConfig({"notifiers": {"line": {}}})
    assert line_setup.test_line_connection(cfg=empty)["error_code"] == "token_missing"
    no_to = DictConfig({"notifiers": {"line": {"access_token": LINE_TOKEN}}})
    receipt = line_setup.test_line_connection(
        cfg=no_to, transport=LineTransport(body={"basicId": "@omni"})
    )
    assert receipt["ok"] is True and receipt["message_sent"] is None and "userId" in receipt["hint"]


def test_line_network_error_is_reported_not_raised():
    def broken(*args, **kwargs):
        raise TimeoutError("slow")

    receipt = line_setup.test_line_connection(access_token=LINE_TOKEN, cfg=_cfg(line=True), transport=broken)
    assert receipt["error_code"] == "network_unreachable" and receipt["step"] == "info"


def test_save_line_settings_is_fail_closed():
    written = []  # 只記錄 config_writer 被呼叫幾次
    cfg = DictConfig({"notifiers": {"line": {"enabled": False, "access_token": "", "to": ""}}})
    receipt = line_setup.save_line_settings(
        access_token=LINE_TOKEN, to=LINE_TO, cfg=cfg,
        transport=LineTransport(status=401, body={}),
        config_writer=lambda c: written.append(c),
    )
    assert receipt["saved"] is False and written == []
    assert cfg.data["notifiers"]["line"]["enabled"] is False
    assert cfg.data["notifiers"]["line"]["access_token"] == ""

    receipt = line_setup.save_line_settings(
        access_token=LINE_TOKEN, to=LINE_TO, cfg=cfg,
        transport=LineTransport(body={"basicId": "@omni"}),
        config_writer=lambda c: written.append(c),
    )
    assert receipt["saved"] is True and len(written) == 1
    assert cfg.data["notifiers"]["line"] == {"enabled": True, "access_token": LINE_TOKEN, "to": LINE_TO}


def test_disconnect_line_clears_secrets():
    cfg = DictConfig({"notifiers": {"line": {"enabled": True, "access_token": LINE_TOKEN, "to": LINE_TO}}})
    receipt = line_setup.disconnect_line(cfg=cfg, config_writer=lambda c: None)
    assert receipt["disconnected"] is True
    assert cfg.data["notifiers"]["line"] == {"enabled": False, "access_token": "", "to": ""}


def test_line_api_never_puts_the_token_in_the_url():
    transport = LineTransport(body={"basicId": "@omni"})
    line_setup._call_api(LINE_TOKEN, "info", transport=transport)
    assert LINE_TOKEN not in transport.calls[0]["url"]
    assert transport.calls[0]["headers"]["Authorization"].endswith(LINE_TOKEN)
