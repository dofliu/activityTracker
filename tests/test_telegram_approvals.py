"""P5-R4b Telegram inline 批准的 contract tests（ADR-008 階段 4）。

核心契約：批准通道需先以 execution token 解鎖（in-memory、有 TTL、重啟
即失效）；只處理綁定 chat 的回呼；只批 L0/L1（L2 立即作廢 confirm code
並導回儀表板）；重複 callback 去重；所有回覆誠實且不含 secret。
全部以 fake transport／fake executor 執行，不需真實 bot 或網路。
"""

import json
from datetime import datetime, timedelta

import pytest
from fastapi.testclient import TestClient

from core.agent_executor import ExecutionRejected, _PENDING_L2_CONFIRMS
from core.server import app
from notifiers import telegram_approvals as approvals
from notifiers.telegram_approvals import (
    arm_approvals,
    approvals_status,
    build_proposals_push,
    disarm_approvals,
    handle_telegram_update,
    parse_callback_data,
    telegram_approvals_enabled,
)

FAKE_TOKEN = "123456789:AAFakeTokenForApprovalTests"
FAKE_CHAT = "987654321"
NOW = datetime(2026, 8, 31, 9, 0, 0)


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


def _cfg(enabled=True, executor=True, token=FAKE_TOKEN, chat=FAKE_CHAT, ttl_hours=24):
    return DictConfig(
        {
            "proactive_secretary": {
                "executor": {
                    "enabled": executor,
                    "telegram_approvals": {
                        "enabled": enabled,
                        "arm_ttl_hours": ttl_hours,
                        "max_actions_per_push": 4,
                    },
                }
            },
            "notifiers": {
                "telegram": {"enabled": True, "bot_token": token, "chat_id": chat}
            },
        }
    )


@pytest.fixture(autouse=True)
def _clean_state(monkeypatch):
    monkeypatch.delenv("TELEGRAM_BOT_TOKEN", raising=False)
    monkeypatch.delenv("TELEGRAM_CHAT_ID", raising=False)
    approvals._reset_state_for_tests()
    _PENDING_L2_CONFIRMS.clear()
    yield
    approvals._reset_state_for_tests()
    _PENDING_L2_CONFIRMS.clear()


class RecordingTransport:
    def __init__(self):
        self.calls = []

    def __call__(self, url, payload, timeout):
        self.calls.append((url.rsplit("/", 1)[1], payload))
        return 200, {"ok": True, "result": {}}

    def methods(self):
        return [method for method, _ in self.calls]


def _callback_update(
    data="ap:abc123:generate_handoff", chat_id=FAKE_CHAT, callback_id="cb-1"
):
    return {
        "update_id": 1,
        "callback_query": {
            "id": callback_id,
            "data": data,
            "message": {"chat": {"id": chat_id}},
        },
    }


# ---- 開關與 arm 邊界 ----


def test_switch_layering_defaults_off():
    assert telegram_approvals_enabled(_cfg(enabled=False)) is False
    assert telegram_approvals_enabled(_cfg(executor=False)) is False
    assert telegram_approvals_enabled(_cfg()) is True


def test_arm_requires_enabled_then_ttl_and_disarm():
    with pytest.raises(ExecutionRejected) as excinfo:
        arm_approvals(cfg=_cfg(enabled=False), now=NOW)
    assert excinfo.value.error_code == "telegram_approvals_disabled"
    assert approvals._is_armed(NOW) is False

    receipt = arm_approvals(cfg=_cfg(ttl_hours=2), now=NOW)
    assert receipt["armed"] is True
    assert receipt["ttl_hours"] == 2
    assert approvals._is_armed(NOW + timedelta(hours=1, minutes=59)) is True
    assert approvals._is_armed(NOW + timedelta(hours=2, minutes=1)) is False  # TTL 過期

    disarm_approvals()
    assert approvals._is_armed(NOW) is False

    status = approvals_status(cfg=_cfg(), now=NOW)
    assert status["armed"] is False
    assert status["enabled"] is True


# ---- update 處理 ----


def test_foreign_chat_updates_ignored_silently():
    transport = RecordingTransport()
    execute_calls = []
    for update in (
        _callback_update(chat_id="666"),
        {"update_id": 2, "message": {"chat": {"id": "666"}, "text": "/proposals"}},
    ):
        receipt = handle_telegram_update(
            update,
            cfg=_cfg(),
            now=NOW,
            transport=transport,
            execute=lambda *a, **k: execute_calls.append((a, k)),
        )
        assert receipt["handled"] == "ignored_foreign_chat"
    # 完全靜默：不回覆、不執行、不外洩存在性資訊
    assert transport.calls == []
    assert execute_calls == []
    assert approvals_status(cfg=_cfg(), now=NOW)["ignored_foreign_updates"] == 2


def test_unarmed_callback_refused_without_execution():
    transport = RecordingTransport()
    execute_calls = []
    receipt = handle_telegram_update(
        _callback_update(),
        cfg=_cfg(),
        now=NOW,
        transport=transport,
        execute=lambda *a, **k: execute_calls.append((a, k)),
    )
    assert receipt["handled"] == "refused_not_armed"
    assert execute_calls == []
    assert transport.methods() == ["answerCallbackQuery"]
    assert "未解鎖" in transport.calls[0][1]["text"]


def test_armed_callback_executes_l1_and_reports():
    arm_approvals(cfg=_cfg(), now=NOW)
    transport = RecordingTransport()
    execute_calls = []

    def fake_execute(proposal_id, **kwargs):
        execute_calls.append((proposal_id, kwargs))
        return {
            "receipt": {
                "id": 7,
                "template_id": "generate_handoff",
                "status": "succeeded",
            }
        }

    receipt = handle_telegram_update(
        _callback_update(data="ap:abc123:generate_handoff"),
        cfg=_cfg(),
        now=NOW,
        transport=transport,
        execute=fake_execute,
    )
    assert receipt["handled"] == "executed"
    assert receipt["receipt_id"] == 7
    assert execute_calls == [
        ("abc123", {"approved_via": "telegram_inline", "template_id": "generate_handoff"})
    ]
    assert transport.methods() == ["answerCallbackQuery", "sendMessage"]
    assert "telegram_inline" in transport.calls[1][1]["text"]
    assert FAKE_TOKEN not in json.dumps(receipt)


def test_duplicate_callback_id_processed_once():
    arm_approvals(cfg=_cfg(), now=NOW)
    transport = RecordingTransport()
    execute_calls = []

    def fake_execute(proposal_id, **kwargs):
        execute_calls.append(proposal_id)
        return {"receipt": {"id": 1, "template_id": "t", "status": "succeeded"}}

    update = _callback_update(callback_id="same-id")
    first = handle_telegram_update(
        update, cfg=_cfg(), now=NOW, transport=transport, execute=fake_execute
    )
    second = handle_telegram_update(
        update, cfg=_cfg(), now=NOW, transport=transport, execute=fake_execute
    )
    assert first["handled"] == "executed"
    assert second["handled"] == "duplicate_callback"
    assert len(execute_calls) == 1


def test_malformed_callback_data_answered_invalid():
    arm_approvals(cfg=_cfg(), now=NOW)
    transport = RecordingTransport()
    execute_calls = []
    for bad in ("rm -rf /", "ap:only-one-part", "xx:pid:tid", ""):
        receipt = handle_telegram_update(
            _callback_update(data=bad, callback_id=f"cb-{bad!r}"),
            cfg=_cfg(),
            now=NOW,
            transport=transport,
            execute=lambda *a, **k: execute_calls.append((a, k)),
        )
        assert receipt["handled"] == "invalid_callback_data"
    assert execute_calls == []


def test_l2_confirmation_required_discards_pending_code():
    arm_approvals(cfg=_cfg(), now=NOW)
    transport = RecordingTransport()
    # 模擬 execute_proposal 對 L2 首呼叫簽發 confirm code 的狀態
    _PENDING_L2_CONFIRMS["abc123"] = {"code_hash": "x", "expires_at": NOW, "template_id": "agent_draft_plan"}

    receipt = handle_telegram_update(
        _callback_update(data="ap:abc123:agent_draft_plan"),
        cfg=_cfg(),
        now=NOW,
        transport=transport,
        execute=lambda *a, **k: {"status": "confirmation_required"},
    )
    assert receipt["handled"] == "l2_refused_confirm_discarded"
    assert "abc123" not in _PENDING_L2_CONFIRMS  # 剛簽發的碼立即作廢
    assert transport.methods() == ["answerCallbackQuery"]
    assert "L2" in transport.calls[0][1]["text"]


def test_execution_rejected_answered_honestly():
    arm_approvals(cfg=_cfg(), now=NOW)
    transport = RecordingTransport()

    def fake_execute(*args, **kwargs):
        raise ExecutionRejected(
            "proposal_not_found_or_expired", "expired", http_status=404
        )

    receipt = handle_telegram_update(
        _callback_update(),
        cfg=_cfg(),
        now=NOW,
        transport=transport,
        execute=fake_execute,
    )
    assert receipt["handled"] == "execution_rejected"
    assert receipt["error_code"] == "proposal_not_found_or_expired"
    assert "proposal_not_found_or_expired" in transport.calls[0][1]["text"]


def test_proposals_command_pushes_and_other_text_ignored():
    transport = RecordingTransport()
    pushed = []
    receipt = handle_telegram_update(
        {"update_id": 3, "message": {"chat": {"id": FAKE_CHAT}, "text": "/proposals"}},
        cfg=_cfg(),
        now=NOW,
        transport=transport,
        push_proposals=lambda **kwargs: pushed.append(kwargs) or {"sent": True},
    )
    assert receipt["handled"] == "proposals_pushed"
    assert len(pushed) == 1

    receipt = handle_telegram_update(
        {"update_id": 4, "message": {"chat": {"id": FAKE_CHAT}, "text": "隨便聊聊"}},
        cfg=_cfg(),
        now=NOW,
        transport=transport,
    )
    assert receipt["handled"] == "message_ignored"  # 不是聊天介面，不回應


# ---- 推播組裝契約 ----


def _proposals_result():
    return {
        "proposals": [
            {
                "proposal_id": "a" * 20,
                "priority": "high",
                "project_key": "demo",
                "title": "PR #7 CI 失敗",
                "suggested_action": "更新本機 remote-tracking",
                "actions": [
                    {"template_id": "repo_fetch", "risk_level": "L1_ASSIST", "label": "git fetch", "requires_confirmation": False},
                    {"template_id": "agent_draft_plan", "risk_level": "L2_MUTATE", "label": "起草計畫", "requires_confirmation": True},
                ],
            },
            {
                "proposal_id": "b" * 20,
                "priority": "medium",
                "project_key": "demo2",
                "title": "只有 L2 動作的建議",
                "suggested_action": "",
                "actions": [
                    {"template_id": "agent_draft_plan", "risk_level": "L2_MUTATE", "label": "起草計畫", "requires_confirmation": True},
                ],
            },
            {
                "proposal_id": "c" * 20,
                "priority": "low",
                "project_key": "demo3",
                "title": "沒有可執行動作的建議",
                "suggested_action": "",
            },
        ]
    }


def test_build_proposals_push_only_l1_buttons_when_armed():
    arm_approvals(cfg=_cfg(), now=NOW)
    text, keyboard, stats = build_proposals_push(
        cfg=_cfg(), now=NOW, proposals_result=_proposals_result()
    )
    assert stats == {"total": 3, "actionable_buttons": 1}
    assert keyboard is not None and len(keyboard) == 1
    button = keyboard[0][0]
    data = button["callback_data"]
    assert len(data.encode("utf-8")) <= 64
    assert parse_callback_data(data) == ("a" * 20, "repo_fetch")
    # L2（requires_confirmation）永不出現按鈕
    assert all("agent_draft_plan" not in row[0]["callback_data"] for row in keyboard)
    assert "PR #7 CI 失敗" in text


def test_build_proposals_push_read_only_when_not_armed():
    text, keyboard, stats = build_proposals_push(
        cfg=_cfg(), now=NOW, proposals_result=_proposals_result()
    )
    assert keyboard is None
    assert stats["actionable_buttons"] == 0
    assert "未解鎖" in text


def test_parse_callback_data_roundtrip_and_rejects():
    assert parse_callback_data("ap:pid:template") == ("pid", "template")
    for bad in ("", "ap:", "ap:pid:", "other:pid:tid", "ap"):
        assert parse_callback_data(bad) is None


# ---- API boundary ----


def test_arm_endpoint_requires_execution_token():
    client = TestClient(app)
    headers = {"Origin": "http://127.0.0.1:8765"}
    # 預設環境沒有 execution token → 401（fail-closed）
    assert client.post("/api/v1/telegram/approvals/arm", headers=headers).status_code == 401
    # status 與 disarm 為唯讀／降權方向，不需 token
    status = client.get("/api/v1/telegram/approvals/status", headers=headers)
    assert status.status_code == 200
    assert status.json()["armed"] is False
    assert client.post("/api/v1/telegram/approvals/disarm", headers=headers).status_code == 200
