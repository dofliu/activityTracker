"""Telegram 介面化設定流程（P5-R4b 前置）的 contract tests。

核心契約：secret（bot token／chat id 值）永不出現在任何 API 回應；
即時連線測試誠實回報（invalid_token / chat_not_found / network_unreachable）；
驗證全部通過才寫 config；環境變數來源的 secret 絕不複製進 config 檔。
所有測試以注入的 fake transport 執行，不需真實網路或真實 token。
"""

import json

from fastapi.testclient import TestClient

from core.security import REDACTED, merge_redacted_config, redact_config
from core.server import app
from notifiers.telegram_setup import (
    TEST_MESSAGE_TEXT,
    detect_telegram_chat_id,
    disconnect_telegram,
    save_telegram_settings,
    telegram_status,
)
# 以別名匯入，避免函式名以 test_ 開頭被 pytest 誤收集為測試
from notifiers.telegram_setup import test_telegram_connection as run_connection_test

FAKE_TOKEN = "123456789:AAFakeTokenForContractTestsOnly"
FAKE_CHAT_ID = "987654321"


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


def _cfg(bot_token=None, chat_id=None, enabled=False):
    telegram = {"enabled": enabled}
    if bot_token is not None:
        telegram["bot_token"] = bot_token
    if chat_id is not None:
        telegram["chat_id"] = chat_id
    return DictConfig({"notifiers": {"telegram": telegram}})


def _ok_transport(calls=None):
    """getMe / sendMessage / getUpdates 全部成功的 fake Bot API。"""

    def transport(url, payload, timeout):
        if calls is not None:
            calls.append((url, payload))
        if url.endswith("/getMe"):
            return 200, {"ok": True, "result": {"username": "omni_bot", "first_name": "Omni"}}
        if url.endswith("/sendMessage"):
            return 200, {"ok": True, "result": {"message_id": 1}}
        if url.endswith("/getUpdates"):
            return 200, {
                "ok": True,
                "result": [
                    {"message": {"chat": {"id": 987654321, "type": "private", "first_name": "Do", "last_name": "Liu"}}},
                    {"message": {"chat": {"id": 987654321, "type": "private", "first_name": "Do"}}},
                    {"edited_message": {"chat": {"id": -100777, "type": "group", "title": "Lab Group"}}},
                ],
            }
        return 404, {}

    return transport


def _assert_no_secrets(payload):
    text = json.dumps(payload, ensure_ascii=False)
    assert FAKE_TOKEN not in text
    assert FAKE_TOKEN.split(":", 1)[1] not in text


# ---- status ----


def test_status_reports_sources_without_secret_values(monkeypatch):
    monkeypatch.delenv("TELEGRAM_BOT_TOKEN", raising=False)
    monkeypatch.delenv("TELEGRAM_CHAT_ID", raising=False)
    status = telegram_status(cfg=_cfg(bot_token=FAKE_TOKEN))
    assert status["token_configured"] is True
    assert status["token_source"] == "config"
    assert status["chat_id_configured"] is False
    assert status["chat_id_source"] == "missing"
    _assert_no_secrets(status)

    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", FAKE_TOKEN)
    status = telegram_status(cfg=_cfg())
    assert status["token_source"] == "env"  # 環境變數優先
    _assert_no_secrets(status)


# ---- 即時連線測試 ----


def test_connection_test_validates_token_and_sends_one_test_message(monkeypatch):
    monkeypatch.delenv("TELEGRAM_BOT_TOKEN", raising=False)
    monkeypatch.delenv("TELEGRAM_CHAT_ID", raising=False)
    calls = []
    receipt = run_connection_test(
        bot_token=FAKE_TOKEN,
        chat_id=FAKE_CHAT_ID,
        cfg=_cfg(),
        transport=_ok_transport(calls),
    )
    assert receipt["ok"] is True
    assert receipt["bot_username"] == "omni_bot"
    assert receipt["message_sent"] is True
    assert receipt["token_source"] == "provided"
    _assert_no_secrets(receipt)
    # 恰好兩個呼叫：getMe 驗 token → sendMessage 測試訊息（固定內容）
    assert [url.rsplit("/", 1)[1] for url, _ in calls] == ["getMe", "sendMessage"]
    assert calls[1][1]["text"] == TEST_MESSAGE_TEXT


def test_connection_test_without_chat_id_returns_hint_not_failure(monkeypatch):
    monkeypatch.delenv("TELEGRAM_CHAT_ID", raising=False)
    receipt = run_connection_test(
        bot_token=FAKE_TOKEN, cfg=_cfg(), transport=_ok_transport()
    )
    assert receipt["ok"] is True
    assert receipt["message_sent"] is None
    assert "chat id" in receipt["hint"].lower() or "CHAT ID" in receipt["hint"]


def test_invalid_token_maps_to_stable_error_code():
    def transport(url, payload, timeout):
        return 401, {"ok": False, "description": "Unauthorized"}

    receipt = run_connection_test(bot_token=FAKE_TOKEN, cfg=_cfg(), transport=transport)
    assert receipt["ok"] is False
    assert receipt["error_code"] == "invalid_token"
    assert "BotFather" in receipt["hint"]
    _assert_no_secrets(receipt)


def test_chat_not_found_maps_to_actionable_hint():
    def transport(url, payload, timeout):
        if url.endswith("/getMe"):
            return 200, {"ok": True, "result": {"username": "omni_bot"}}
        return 400, {"ok": False, "description": "Bad Request: chat not found"}

    receipt = run_connection_test(
        bot_token=FAKE_TOKEN, chat_id="42", cfg=_cfg(), transport=transport
    )
    assert receipt["ok"] is False
    assert receipt["error_code"] == "chat_not_found"
    assert "/start" in receipt["hint"]


def test_network_failure_is_reported_honestly():
    def transport(url, payload, timeout):
        raise ConnectionError("boom")

    receipt = run_connection_test(bot_token=FAKE_TOKEN, cfg=_cfg(), transport=transport)
    assert receipt["ok"] is False
    assert receipt["error_code"] == "network_unreachable"


def test_missing_token_fails_closed_with_guidance(monkeypatch):
    monkeypatch.delenv("TELEGRAM_BOT_TOKEN", raising=False)
    receipt = run_connection_test(cfg=_cfg(), transport=_ok_transport())
    assert receipt["ok"] is False
    assert receipt["error_code"] == "token_missing"


# ---- chat id 偵測 ----


def test_detect_chat_id_parses_and_dedupes_candidates():
    result = detect_telegram_chat_id(
        bot_token=FAKE_TOKEN, cfg=_cfg(), transport=_ok_transport()
    )
    assert result["ok"] is True
    by_id = {item["chat_id"]: item for item in result["candidates"]}
    assert set(by_id) == {"987654321", "-100777"}
    assert by_id["987654321"]["display_name"] == "Do Liu"
    assert by_id["-100777"]["chat_type"] == "group"
    _assert_no_secrets(result)


def test_detect_chat_id_empty_updates_returns_start_hint():
    def transport(url, payload, timeout):
        return 200, {"ok": True, "result": []}

    result = detect_telegram_chat_id(bot_token=FAKE_TOKEN, cfg=_cfg(), transport=transport)
    assert result["ok"] is True
    assert result["candidates"] == []
    assert "/start" in result["hint"]


# ---- connect（驗證通過才儲存） ----


def test_connect_saves_only_after_full_validation(monkeypatch):
    monkeypatch.delenv("TELEGRAM_BOT_TOKEN", raising=False)
    monkeypatch.delenv("TELEGRAM_CHAT_ID", raising=False)
    written = []

    # 失敗案例：sendMessage 被拒 → config 完全不動
    def failing_transport(url, payload, timeout):
        if url.endswith("/getMe"):
            return 200, {"ok": True, "result": {"username": "omni_bot"}}
        return 400, {"ok": False, "description": "Bad Request: chat not found"}

    cfg = _cfg()
    receipt = save_telegram_settings(
        bot_token=FAKE_TOKEN,
        chat_id="42",
        cfg=cfg,
        transport=failing_transport,
        config_writer=lambda c: written.append("write"),
    )
    assert receipt["saved"] is False
    assert written == []
    assert cfg.data["notifiers"]["telegram"].get("bot_token") is None
    assert cfg.data["notifiers"]["telegram"]["enabled"] is False

    # 成功案例：getMe＋測試訊息通過 → 寫入 config 並啟用
    cfg = _cfg()
    receipt = save_telegram_settings(
        bot_token=FAKE_TOKEN,
        chat_id=FAKE_CHAT_ID,
        morning_briefing_time="08:15",
        evening_summary_time="bad-value",
        cfg=cfg,
        transport=_ok_transport(),
        config_writer=lambda c: written.append("write"),
    )
    assert receipt["saved"] is True
    assert written == ["write"]
    telegram = cfg.data["notifiers"]["telegram"]
    assert telegram["enabled"] is True
    assert telegram["bot_token"] == FAKE_TOKEN
    assert telegram["chat_id"] == FAKE_CHAT_ID
    assert telegram["morning_briefing_time"] == "08:15"
    assert telegram["evening_summary_time"] == "23:30"  # 非法時間回退預設
    _assert_no_secrets(receipt)


def test_connect_never_copies_env_secrets_into_config(monkeypatch):
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", FAKE_TOKEN)
    monkeypatch.delenv("TELEGRAM_CHAT_ID", raising=False)
    cfg = _cfg()
    receipt = save_telegram_settings(
        chat_id=FAKE_CHAT_ID,  # token 由環境變數提供、chat 由 UI 提供
        cfg=cfg,
        transport=_ok_transport(),
        config_writer=lambda c: None,
    )
    assert receipt["saved"] is True
    assert receipt["token_source"] == "env"
    telegram = cfg.data["notifiers"]["telegram"]
    assert telegram.get("bot_token") is None  # env 值不落檔
    assert telegram["chat_id"] == FAKE_CHAT_ID


def test_connect_requires_chat_id_to_enable(monkeypatch):
    monkeypatch.delenv("TELEGRAM_CHAT_ID", raising=False)
    receipt = save_telegram_settings(
        bot_token=FAKE_TOKEN,
        cfg=_cfg(),
        transport=_ok_transport(),
        config_writer=lambda c: None,
    )
    assert receipt["saved"] is False
    assert receipt["error_code"] == "chat_id_missing"


def test_disconnect_clears_config_secrets_only():
    cfg = _cfg(bot_token=FAKE_TOKEN, chat_id=FAKE_CHAT_ID, enabled=True)
    receipt = disconnect_telegram(cfg=cfg, config_writer=lambda c: None)
    assert receipt["ok"] is True
    telegram = cfg.data["notifiers"]["telegram"]
    assert telegram["enabled"] is False
    assert telegram["bot_token"] == ""
    assert telegram["chat_id"] == ""


# ---- config API 邊界（UI 依賴的遮蔽機制） ----


def test_config_redaction_covers_telegram_secrets_roundtrip():
    original = {"notifiers": {"telegram": {"enabled": True, "bot_token": FAKE_TOKEN, "chat_id": FAKE_CHAT_ID, "bot_token_env": "TELEGRAM_BOT_TOKEN"}}}
    public = redact_config(original)
    telegram = public["notifiers"]["telegram"]
    assert telegram["bot_token"] == REDACTED
    assert telegram["chat_id"] == REDACTED
    assert telegram["bot_token_env"] == "TELEGRAM_BOT_TOKEN"  # env 名稱可顯示
    # UI 把遮蔽值原樣送回儲存 → 保留原 secret，不會被星號覆寫
    merged = merge_redacted_config(original, public)
    assert merged["notifiers"]["telegram"]["bot_token"] == FAKE_TOKEN
    assert merged["notifiers"]["telegram"]["chat_id"] == FAKE_CHAT_ID


def test_status_endpoint_returns_no_secret_values():
    client = TestClient(app)
    response = client.get(
        "/api/v1/telegram/status", headers={"Origin": "http://127.0.0.1:8765"}
    )
    assert response.status_code == 200
    body = response.json()
    assert body["secret_boundary"] == "status_only_no_secret_values"
    for key in body:
        assert "token" not in key or key.endswith(("_configured", "_source"))
