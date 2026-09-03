"""Telegram 小秘書對話（ADR-013）的 contract tests。

- 雙開關預設關閉；關閉時行為與今天完全相同（自由文字仍然不回應）。
- 綁定 chat 邊界不因對話啟用而鬆動。
- 自由文字→問答：先回「查一下」再回答案，同時間只允許一題，過長拒絕。
- 「記下來／偏好／決定」寫進記憶區（source=telegram），完全不呼叫 LLM。
- /today /notes /status 唯讀；/arm 需開關＋execution token 且訊息立刻刪除、
  token 不進收據；/disarm（降低權限）永遠可用。
- ask_secretary：問題驗證、收據誠實、LLM 失敗與逾時都降級成可讀答案。
全部以 fake transport／fake gateway 執行，不需真實 bot、網路或索引。
"""

from __future__ import annotations

import asyncio
from contextlib import contextmanager
from datetime import datetime, timedelta
from pathlib import Path

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from core.models import Base
from core.secretary_ask import AskRejected, ask_secretary
from notifiers import telegram_approvals as approvals
from notifiers import telegram_chat as chat_module
from notifiers.telegram_approvals import handle_telegram_update
from notifiers.telegram_chat import (
    chat_status,
    handle_chat_message,
    remote_arm_enabled,
    telegram_chat_enabled,
    telegram_updates_poller_enabled,
)

FAKE_TOKEN = "123456789:AAFakeTokenForChatTests"
FAKE_CHAT = "987654321"
EXEC_TOKEN = "exec-token-for-tests"
NOW = datetime(2026, 9, 3, 9, 0, 0)


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


def _cfg(chat=True, approvals_on=True, remote_arm=False, max_question_chars=1000, notifier=True):
    return DictConfig(
        {
            "proactive_secretary": {
                "executor": {
                    "enabled": True,
                    "telegram_approvals": {
                        "enabled": approvals_on,
                        "arm_ttl_hours": 24,
                        "allow_remote_arm": remote_arm,
                    },
                }
            },
            "notifiers": {
                "telegram": {
                    "enabled": notifier,
                    "bot_token": FAKE_TOKEN,
                    "chat_id": FAKE_CHAT,
                    "chat": {"enabled": chat, "max_question_chars": max_question_chars},
                }
            },
            "security": {"execution_token": EXEC_TOKEN},
            "secretary_memory": {"enabled": True},
        }
    )


class TempDatabase:
    def __init__(self, path: Path | None = None):
        self.engine = create_engine(f"sqlite:///{path}" if path else "sqlite:///:memory:")
        Base.metadata.create_all(self.engine)
        self.factory = sessionmaker(bind=self.engine)

    @contextmanager
    def session_scope(self):
        session = self.factory()
        try:
            yield session
            session.commit()
        except Exception:
            session.rollback()
            raise
        finally:
            session.close()


class RecordingTransport:
    def __init__(self, ok=True):
        self.calls = []
        self.ok = ok

    def __call__(self, url, payload, timeout):
        self.calls.append((url.rsplit("/", 1)[1], payload))
        return 200, {"ok": self.ok, "result": {}}

    def methods(self):
        return [method for method, _ in self.calls]

    def texts(self):
        return [payload.get("text", "") for _, payload in self.calls if "text" in payload]


@pytest.fixture(autouse=True)
def _clean_state(monkeypatch):
    monkeypatch.delenv("TELEGRAM_BOT_TOKEN", raising=False)
    monkeypatch.delenv("TELEGRAM_CHAT_ID", raising=False)
    monkeypatch.delenv("OMNICONTEXT_EXECUTION_TOKEN", raising=False)
    approvals._reset_state_for_tests()
    chat_module._reset_state_for_tests()
    yield
    approvals._reset_state_for_tests()
    chat_module._reset_state_for_tests()


def _inline(fn):
    """測試用 submit：把背景問答就地跑完。"""
    fn()


def _ask_stub(answer="好的。", citations=None, memory=None, calls=None):
    def _ask(question, **kwargs):
        if calls is not None:
            calls.append((question, kwargs))
        return {
            "answer": answer,
            "citations": citations or [],
            "memory": memory or {"included": False},
        }

    return _ask


# ---- 開關 ----


def test_switches_default_off_and_layer():
    assert telegram_chat_enabled(DictConfig({})) is False
    assert telegram_chat_enabled(_cfg(chat=False)) is False
    assert telegram_chat_enabled(_cfg(chat=True, notifier=False)) is False
    assert telegram_chat_enabled(_cfg(chat=True)) is True
    assert remote_arm_enabled(_cfg()) is False
    assert remote_arm_enabled(_cfg(remote_arm=True)) is True


def test_poller_runs_when_either_channel_is_on():
    assert telegram_updates_poller_enabled(_cfg(chat=False, approvals_on=False)) is False
    assert telegram_updates_poller_enabled(_cfg(chat=True, approvals_on=False)) is True
    assert telegram_updates_poller_enabled(_cfg(chat=False, approvals_on=True)) is True


def test_free_text_still_ignored_when_chat_disabled():
    transport = RecordingTransport()
    update = {"update_id": 1, "message": {"message_id": 5, "chat": {"id": FAKE_CHAT}, "text": "今天怎麼樣？"}}
    receipt = handle_telegram_update(update, cfg=_cfg(chat=False), now=NOW, transport=transport)
    assert receipt == {"handled": "message_ignored"} and transport.calls == []


def test_foreign_chat_never_reaches_the_chat_layer():
    transport = RecordingTransport()
    called = []
    update = {"update_id": 1, "message": {"message_id": 5, "chat": {"id": "111"}, "text": "hi"}}
    receipt = handle_telegram_update(
        update, cfg=_cfg(chat=True), now=NOW, transport=transport,
        chat_handler=lambda *a, **k: called.append(1) or {"handled": "should_not_happen"},
    )
    assert receipt["handled"] == "ignored_foreign_chat" and called == [] and transport.calls == []


def test_bound_chat_free_text_is_routed_to_the_chat_layer():
    seen = {}

    def handler(text, **kwargs):
        seen["text"] = text
        seen["message_id"] = kwargs.get("message_id")
        return {"handled": "chat_answering"}

    update = {"update_id": 1, "message": {"message_id": 42, "chat": {"id": FAKE_CHAT}, "text": "記下來：X"}}
    receipt = handle_telegram_update(update, cfg=_cfg(chat=True), now=NOW, chat_handler=handler)
    assert receipt["handled"] == "chat_answering"
    # 原文（大小寫與全形冒號）必須完整傳下去，否則筆記前綴會被破壞
    assert seen == {"text": "記下來：X", "message_id": 42}


# ---- 問答 ----


def test_question_acknowledges_then_answers_with_receipts():
    transport = RecordingTransport()
    calls = []
    ask = _ask_stub(
        answer="先修 CI。",
        citations=[{"filename": "ADR-008.md"}, {"filename": "STATUS.yaml"}],
        memory={"included": True, "notes_used": 3},
        calls=calls,
    )
    receipt = handle_chat_message(
        "接下來該做什麼？", cfg=_cfg(), token=FAKE_TOKEN, chat=FAKE_CHAT,
        transport=transport, now=NOW, ask=ask, submit=_inline,
    )
    assert receipt["handled"] == "chat_answering"
    texts = transport.texts()
    assert texts[0].startswith("🤔")
    assert "先修 CI。" in texts[1] and "ADR-008.md" in texts[1] and "🧠 參考記憶區 3 筆" in texts[1]
    assert calls[0][0] == "接下來該做什麼？"
    assert chat_status(_cfg())["asks_answered"] == 1


def test_only_one_question_in_flight():
    transport = RecordingTransport()
    calls = []
    ask = _ask_stub(calls=calls)
    # submit 不執行 → 問答停在進行中
    first = handle_chat_message(
        "第一題", cfg=_cfg(), token=FAKE_TOKEN, chat=FAKE_CHAT,
        transport=transport, now=NOW, ask=ask, submit=lambda fn: None,
    )
    second = handle_chat_message(
        "第二題", cfg=_cfg(), token=FAKE_TOKEN, chat=FAKE_CHAT,
        transport=transport, now=NOW, ask=ask, submit=_inline,
    )
    assert first["handled"] == "chat_answering" and second["handled"] == "chat_busy"
    assert calls == [] and "上一題還在回答中" in transport.texts()[-1]


def test_long_question_rejected_before_calling_the_model():
    transport = RecordingTransport()
    calls = []
    receipt = handle_chat_message(
        "字" * 51, cfg=_cfg(max_question_chars=50), token=FAKE_TOKEN, chat=FAKE_CHAT,
        transport=transport, now=NOW, ask=_ask_stub(calls=calls), submit=_inline,
    )
    assert receipt["handled"] == "question_too_long" and calls == []
    assert "上限 50 字" in transport.texts()[0]


def test_ask_failure_still_replies():
    transport = RecordingTransport()

    def broken(question, **kwargs):
        raise RuntimeError("provider down")

    handle_chat_message(
        "會壞掉嗎", cfg=_cfg(), token=FAKE_TOKEN, chat=FAKE_CHAT,
        transport=transport, now=NOW, ask=broken, submit=_inline,
    )
    assert "回答失敗：RuntimeError" in transport.texts()[-1]
    assert chat_status(_cfg())["ask_in_flight"] is False


def test_unknown_command_is_not_sent_to_the_model():
    transport = RecordingTransport()
    calls = []
    receipt = handle_chat_message(
        "/deploy now", cfg=_cfg(), token=FAKE_TOKEN, chat=FAKE_CHAT,
        transport=transport, now=NOW, ask=_ask_stub(calls=calls), submit=_inline,
    )
    assert receipt["handled"] == "unknown_command" and calls == []


def test_long_answer_is_split_not_truncated():
    transport = RecordingTransport()
    handle_chat_message(
        "長答案", cfg=_cfg(), token=FAKE_TOKEN, chat=FAKE_CHAT, transport=transport,
        now=NOW, ask=_ask_stub(answer="字" * 8000), submit=_inline,
    )
    sends = [payload for method, payload in transport.calls if method == "sendMessage"]
    assert len(sends) >= 4  # 1 則「查一下」＋ 至少 3 段答案
    assert sum(len(p["text"]) for p in sends[1:]) >= 8000


# ---- 筆記 ----


def test_note_prefix_writes_memory_without_calling_the_model(monkeypatch):
    import core.secretary_memory as mem

    db = TempDatabase()
    monkeypatch.setattr(mem, "get_db", lambda: db)
    transport = RecordingTransport()
    calls = []
    receipt = handle_chat_message(
        "記下來 @alpha：週五前收尾", cfg=_cfg(), token=FAKE_TOKEN, chat=FAKE_CHAT,
        transport=transport, now=NOW, ask=_ask_stub(calls=calls), submit=_inline,
    )
    assert receipt["handled"] == "note_saved" and receipt["kind"] == "user_note" and calls == []
    stored = mem.list_notes(database=db)["notes"][0]
    assert stored["body"] == "週五前收尾" and stored["project_key"] == "alpha" and stored["source"] == "telegram"
    assert "🧠 已記下" in transport.texts()[0]


def test_preference_note_from_phone_reaches_the_proposal_mutes(monkeypatch):
    import core.secretary_memory as mem

    db = TempDatabase()
    monkeypatch.setattr(mem, "get_db", lambda: db)
    handle_chat_message(
        "偏好：不要提醒 repo_needs_push", cfg=_cfg(), token=FAKE_TOKEN, chat=FAKE_CHAT,
        transport=RecordingTransport(), now=NOW, ask=_ask_stub(), submit=_inline,
    )
    assert mem.preference_mutes(database=db) == {"repo_needs_push"}


# ---- 唯讀指令 ----


def test_read_only_commands_never_call_the_model(monkeypatch):
    import core.secretary_memory as mem

    db = TempDatabase()
    monkeypatch.setattr(mem, "get_db", lambda: db)
    monkeypatch.setattr(
        "core.secretary_packs.build_today_view",
        lambda **kw: {"resume": {"display_name": "alpha", "last_activity_at": "2026-09-03T08:00:00",
                                 "last_action_summary": "修 CI"}, "pack_line": "早晨包：repo 需 pull 2",
                      "active_project_count": 3},
    )
    monkeypatch.setattr(
        "core.proactive_secretary.build_action_proposals",
        lambda **kw: {"proposals": [{"project_key": "alpha", "title": "PR 等 review", "why_now": "只差一個 merge"}]},
    )
    calls = []
    for command, marker in (("/today", "早晨包"), ("/notes", "記憶區共"), ("/status", "批准通道"), ("/help", "小秘書")):
        transport = RecordingTransport()
        receipt = handle_chat_message(
            command, cfg=_cfg(), token=FAKE_TOKEN, chat=FAKE_CHAT,
            transport=transport, now=NOW, ask=_ask_stub(calls=calls), submit=_inline,
        )
        assert receipt["handled"].endswith("_sent"), command
        assert marker in "\n".join(transport.texts()), command
    assert calls == []


def test_today_survives_a_broken_today_view(monkeypatch):
    monkeypatch.setattr(
        "core.secretary_packs.build_today_view",
        lambda **kw: (_ for _ in ()).throw(RuntimeError("db locked")),
    )
    transport = RecordingTransport()
    receipt = handle_chat_message(
        "/today", cfg=_cfg(), token=FAKE_TOKEN, chat=FAKE_CHAT,
        transport=transport, now=NOW, ask=_ask_stub(), submit=_inline,
    )
    assert receipt["handled"] == "today_sent" and "RuntimeError" in transport.texts()[0]


# ---- 批准（arm / disarm） ----


def test_arm_refused_when_remote_arm_switch_is_off_and_message_deleted():
    transport = RecordingTransport()
    receipt = handle_chat_message(
        f"/arm {EXEC_TOKEN}", cfg=_cfg(remote_arm=False), token=FAKE_TOKEN, chat=FAKE_CHAT,
        message_id=7, transport=transport, now=NOW,
    )
    assert receipt == {"handled": "remote_arm_disabled"}
    assert transport.methods()[0] == "deleteMessage"
    assert approvals.approvals_status(cfg=_cfg(), now=NOW)["armed"] is False
    assert EXEC_TOKEN not in "\n".join(transport.texts())


def test_arm_with_wrong_token_stays_locked():
    transport = RecordingTransport()
    receipt = handle_chat_message(
        "/arm not-the-token", cfg=_cfg(remote_arm=True), token=FAKE_TOKEN, chat=FAKE_CHAT,
        message_id=7, transport=transport, now=NOW,
    )
    assert receipt == {"handled": "arm_rejected"}
    assert transport.methods()[0] == "deleteMessage"
    assert approvals.approvals_status(cfg=_cfg(), now=NOW)["armed"] is False


def test_arm_with_valid_token_arms_and_never_echoes_the_token():
    transport = RecordingTransport()
    cfg = _cfg(remote_arm=True)
    receipt = handle_chat_message(
        f"/arm {EXEC_TOKEN}", cfg=cfg, token=FAKE_TOKEN, chat=FAKE_CHAT,
        message_id=7, transport=transport, now=NOW,
    )
    assert receipt["handled"] == "armed"
    assert transport.methods()[0] == "deleteMessage"
    assert approvals.approvals_status(cfg=cfg, now=NOW)["armed"] is True
    assert approvals.approvals_status(cfg=cfg, now=NOW + timedelta(hours=25))["armed"] is False
    joined = "\n".join(transport.texts()) + str(receipt)
    assert EXEC_TOKEN not in joined and FAKE_TOKEN not in joined


def test_arm_warns_when_the_message_could_not_be_deleted():
    class FailingDelete(RecordingTransport):
        def __call__(self, url, payload, timeout):
            method = url.rsplit("/", 1)[1]
            self.calls.append((method, payload))
            if method == "deleteMessage":
                return 400, {"ok": False}
            return 200, {"ok": True, "result": {}}

    transport = FailingDelete()
    handle_chat_message(
        f"/arm {EXEC_TOKEN}", cfg=_cfg(remote_arm=True), token=FAKE_TOKEN, chat=FAKE_CHAT,
        message_id=7, transport=transport, now=NOW,
    )
    assert "沒能自動刪除" in "\n".join(transport.texts())


def test_disarm_always_available_even_with_remote_arm_off():
    cfg = _cfg(remote_arm=False)
    approvals.arm_approvals(cfg=cfg, now=NOW)
    assert approvals.approvals_status(cfg=cfg, now=NOW)["armed"] is True
    transport = RecordingTransport()
    receipt = handle_chat_message(
        "/disarm", cfg=cfg, token=FAKE_TOKEN, chat=FAKE_CHAT, transport=transport, now=NOW
    )
    assert receipt == {"handled": "disarmed"}
    assert approvals.approvals_status(cfg=cfg, now=NOW)["armed"] is False


# ---- ask_secretary ----


class FakeGateway:
    def __init__(self, tokens=("好", "的"), error=None, delay=0.0):
        self.tokens = tokens
        self.error = error
        self.delay = delay
        self.system_prompt = None

    async def stream_chat(self, messages, system_prompt=None, provider=None, model=None):
        self.system_prompt = system_prompt
        if self.error:
            raise self.error
        for token in self.tokens:
            if self.delay:
                await asyncio.sleep(self.delay)
            yield token


def test_ask_validates_question():
    with pytest.raises(AskRejected) as exc:
        ask_secretary("   ", cfg=_cfg(), enable_rag=False, gateway=FakeGateway())
    assert exc.value.error_code == "empty_question"
    with pytest.raises(AskRejected) as exc:
        ask_secretary("字" * 2001, cfg=_cfg(), enable_rag=False, gateway=FakeGateway())
    assert exc.value.error_code == "question_too_long"


def test_ask_injects_memory_and_reports_receipt():
    gateway = FakeGateway(tokens=("先", "修 CI"))
    result = ask_secretary(
        "接下來做什麼",
        cfg=_cfg(),
        enable_rag=False,
        gateway=gateway,
        memory={"text": "【記憶區】偏好：回答用繁體中文", "receipt": {"included": True, "notes_used": 2}},
    )
    assert result["answer"] == "先修 CI" and result["error"] is None
    assert result["memory"] == {"included": True, "notes_used": 2}
    assert result["rag_used"] is False and result["citations"] == []
    assert "【記憶區】偏好：回答用繁體中文" in gateway.system_prompt
    assert isinstance(result["elapsed_ms"], int) and "claim_boundary" in result


def test_ask_degrades_when_the_provider_fails():
    result = ask_secretary(
        "會壞嗎", cfg=_cfg(), enable_rag=False, memory={"text": "", "receipt": {"included": False}},
        gateway=FakeGateway(error=RuntimeError("connection refused")),
    )
    assert result["error"] == "RuntimeError" and "回答失敗：RuntimeError" in result["answer"]


def test_ask_times_out_without_hanging():
    result = ask_secretary(
        "很慢的題目", cfg=_cfg(), enable_rag=False, timeout_seconds=10,
        memory={"text": "", "receipt": {"included": False}},
        gateway=FakeGateway(tokens=("a",) * 5, delay=5),
    )
    assert result["error"] == "timeout" and "10 秒內沒有得到完整回答" in result["answer"]


def test_ask_never_returns_an_empty_answer():
    result = ask_secretary(
        "空回答", cfg=_cfg(), enable_rag=False, memory={"text": "", "receipt": {"included": False}},
        gateway=FakeGateway(tokens=()),
    )
    assert result["answer"] == "（模型沒有回覆內容。）" and result["error"] is None


# ---- 主服務乾淨 import 契約（ADR-009 不得因本功能被破壞） ----


def test_chat_modules_do_not_import_index_libraries_at_module_level():
    for path in ("notifiers/telegram_chat.py", "core/secretary_ask.py"):
        for line in Path(path).read_text(encoding="utf-8").splitlines():
            if line.startswith(("import ", "from ")):
                assert not any(
                    lib in line for lib in ("chromadb", "fastembed", "rank_bm25", "jieba", "rag.")
                ), f"{path}: {line}"
