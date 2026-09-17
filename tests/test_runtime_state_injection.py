"""D11 的契約：程序內狀態可注入、重設鉤子消失、CORS 改設定不必重啟（ADR-027）。

這支檔案盯著四件事——每一件都是 `docs/TODO.md` D11 那一列寫下的收據：

1. `POST /api/v1/config` 改 `security.allowed_origins`，**同一個行程、同一個 app**
   下一個請求的 CORS 標頭就跟著變（D11 之前必須重啟服務）。
2. 產品程式碼裡再也沒有 `_reset_*_for_tests` 這類「只為測試存在」的函式。
3. `core/runtime_state.py` 不碰磁碟、不碰網路、不碰資料庫——狀態只在記憶體裡。
4. 兩份 `RuntimeState` 互不影響，而且一次性碼只留雜湊。
"""

from __future__ import annotations

import ast
import copy
import subprocess
from datetime import datetime, timedelta
from pathlib import Path

import pytest
import yaml
from fastapi.testclient import TestClient

import core.config as config_module
from core.agent_executor import discard_pending_confirm
from core.config import get_config
from core.runtime_state import (
    ApprovalState,
    ChatState,
    ConfirmStore,
    ProjectCache,
    RuntimeState,
    TtlCache,
    new_runtime_state,
    runtime_state,
)
from core.server import app
from notifiers.telegram_approvals import approvals_status
from notifiers.telegram_chat import chat_status

REPO_ROOT = Path(__file__).resolve().parents[1]
LOCAL_ORIGIN = "http://127.0.0.1:8765"
client = TestClient(app)


class DictConfig:
    """只回答 `get()` 的最小設定替身（與 tests/test_security.py 同款）。"""

    def __init__(self, values: dict | None = None):
        self.values = values or {}

    def get(self, key, default=None):
        current = self.values
        for part in key.split("."):
            if not isinstance(current, dict) or part not in current:
                return default
            current = current[part]
        return current


@pytest.fixture
def temp_config(tmp_path):
    """把設定檔換到 tmp_path，讓測試可以真的走一次 `POST /api/v1/config`。

    `reload_config()` 走的是 `default_config_path()`，所以連那個函式一起換掉；
    收尾時用原本的路徑重載一次，行程裡的 Config 單例就回到測試前的樣子。
    """
    cfg = get_config()
    original_path = cfg.config_path
    original_data = copy.deepcopy(cfg.data)
    path = tmp_path / "config.yaml"
    path.write_text(yaml.safe_dump(original_data or {}, allow_unicode=True), encoding="utf-8")
    original_default = config_module.default_config_path
    config_module.default_config_path = lambda: path
    cfg.load(path)
    try:
        yield cfg
    finally:
        config_module.default_config_path = original_default
        cfg.load(original_path)


# --------------------------------------------------------------------------
# 收據 1：改 allowed_origins 不必重啟
# --------------------------------------------------------------------------


def test_config_post_updates_cors_allowlist_without_restart(temp_config):
    """同一個 app 物件、同一個 TestClient：存檔後的下一個請求就拿到新的 CORS 標頭。

    D11 之前 `allow_origins` 凍結在 import 時刻，安全邊界 middleware 會放行新來源、
    CORS 標頭卻還是舊的——瀏覽器照樣擋下來，而且只有重啟服務才會好。
    """
    new_origin = "http://127.0.0.1:9787"

    before = client.get("/api/v1/config", headers={"Origin": new_origin})
    assert before.status_code == 403, "前置條件：這個來源一開始不在允許清單裡"

    body = client.get("/api/v1/config", headers={"Origin": LOCAL_ORIGIN}).json()
    existing = list(body.get("security", {}).get("allowed_origins") or [LOCAL_ORIGIN])
    assert new_origin not in existing
    body.setdefault("security", {})["allowed_origins"] = existing + [new_origin]
    saved = client.post("/api/v1/config", json=body, headers={"Origin": LOCAL_ORIGIN})
    assert saved.status_code == 200, saved.text

    after = client.get("/api/v1/config", headers={"Origin": new_origin})
    assert after.status_code == 200
    assert after.headers.get("access-control-allow-origin") == new_origin

    # 舊的來源沒有因此被擠掉。
    still = client.get("/api/v1/config", headers={"Origin": LOCAL_ORIGIN})
    assert still.status_code == 200
    assert still.headers.get("access-control-allow-origin") == LOCAL_ORIGIN


def test_dynamic_cors_never_echoes_wildcard(temp_config):
    """邊界沒有被放寬：設定檔寫 `*` 依然一個來源都不放行。"""
    body = client.get("/api/v1/config", headers={"Origin": LOCAL_ORIGIN}).json()
    body.setdefault("security", {})["allowed_origins"] = ["*"]
    assert client.post("/api/v1/config", json=body, headers={"Origin": LOCAL_ORIGIN}).status_code == 200

    blocked = client.get("/api/v1/config", headers={"Origin": "https://attacker.example"})
    assert blocked.status_code == 403
    assert blocked.headers.get("access-control-allow-origin") != "*"


# --------------------------------------------------------------------------
# 收據 2：重設鉤子從產品程式碼消失
# --------------------------------------------------------------------------


def _product_sources() -> list[Path]:
    """git 追蹤中的 .py，扣掉測試與腳本——也就是會進 wheel 的那些。"""
    tracked = subprocess.run(
        ["git", "ls-files", "*.py"], cwd=REPO_ROOT, capture_output=True, text=True, check=True
    ).stdout.split()
    return [
        REPO_ROOT / name
        for name in tracked
        if not name.startswith("tests/") and not name.startswith("scripts/")
    ]


def test_no_reset_for_tests_hooks_in_product_code():
    """五個只為測試存在的函式全部刪掉了，一個都不准長回來。"""
    banned = [
        "_reset_state_for_tests",
        "_reset_llm_cache_for_tests",
        "_reset_pending_confirms",
        "reset_advisor_cache",
        "_for_tests",
    ]
    offenders = []
    for path in _product_sources():
        text = path.read_text(encoding="utf-8")
        for needle in banned:
            if needle in text:
                offenders.append(f"{path.relative_to(REPO_ROOT)}: {needle}")
    assert offenders == [], f"產品程式碼不該帶測試專用的重設鉤子：{offenders}"


def test_old_module_level_globals_are_gone():
    """舊的模組層可變全域名稱不該再出現在產品程式碼裡。"""
    banned = [
        "_PENDING_L2_CONFIRMS",
        "_ARMED_UNTIL",
        "_PENDING_ARM_CODE",
        "_PROCESSED_CALLBACK_IDS",
        "_ASK_IN_FLIGHT",
        "_ASKS_ANSWERED",
        "_LLM_CACHE",
        "_PROJECT_CACHE",
        "_LAST_PROJECT_REFRESH_TIME",
    ]
    offenders = []
    for path in _product_sources():
        if path.name.endswith(".md"):
            continue
        text = path.read_text(encoding="utf-8")
        for needle in banned:
            if needle in text:
                offenders.append(f"{path.relative_to(REPO_ROOT)}: {needle}")
    assert offenders == [], f"這些狀態已經搬進 core/runtime_state.py：{offenders}"


# --------------------------------------------------------------------------
# 收據 3：runtime_state 只碰記憶體
# --------------------------------------------------------------------------


def test_runtime_state_module_touches_no_io():
    """狀態只在記憶體裡：不開檔、不進資料庫、不發請求、不開子程序。"""
    source = (REPO_ROOT / "core" / "runtime_state.py").read_text(encoding="utf-8")
    for needle in ("open(", "session_scope", "requests", "subprocess", "socket", "sqlite", "json.dump"):
        assert needle not in source, f"core/runtime_state.py 不該出現 {needle}"


def test_runtime_state_imports_only_stdlib():
    """import 清單本身就是一份證據——沒有任何專案內模組被拉進來。"""
    tree = ast.parse((REPO_ROOT / "core" / "runtime_state.py").read_text(encoding="utf-8"))
    modules = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            modules.update(alias.name.split(".")[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            if node.level:  # 相對 import＝專案內模組
                modules.add(f".{node.module}")
            elif node.module:
                modules.add(node.module.split(".")[0])
    assert modules <= {"__future__", "threading", "collections", "dataclasses", "datetime", "typing"}, modules


# --------------------------------------------------------------------------
# 收據 4：兩份狀態互不影響，一次性碼只留雜湊
# --------------------------------------------------------------------------


def test_two_runtime_states_are_independent():
    """這是 D11 之前**無法表達**的測試：兩份狀態，六個 store，互不干涉。"""
    a, b = new_runtime_state(), new_runtime_state()
    now = datetime(2026, 9, 17, 9, 0, 0)

    a.confirms.issue("p1", code_hash="h", expires_at=now + timedelta(minutes=5), template_id="t")
    a.approvals.arm(now + timedelta(hours=1))
    a.chat.begin_ask()
    a.greeting_cache.put("k", "早安", now, timedelta(minutes=30))
    a.advisor_cache.put("k", "摘要", now, timedelta(minutes=30))
    a.projects.mark_states_refreshed(1000.0)

    assert "p1" in a.confirms and "p1" not in b.confirms
    assert a.approvals.is_armed(now) and not b.approvals.is_armed(now)
    assert a.chat.snapshot() == (True, 0) and b.chat.snapshot() == (False, 0)
    assert a.greeting_cache.get("k", now) == "早安" and b.greeting_cache.get("k", now) is None
    assert a.advisor_cache.get("k", now) == "摘要" and b.advisor_cache.get("k", now) is None
    assert a.projects.states_fresh(1010.0) and not b.projects.states_fresh(1010.0)


def test_greeting_and_advisor_caches_are_not_the_same_object():
    """兩份快取共用 `TtlCache` 類別，但**不是**同一個物件——否則問候卡會拿到 advisor 的值。"""
    state = new_runtime_state()
    assert state.greeting_cache is not state.advisor_cache


def test_stores_keep_hashes_not_codes():
    """ADR-008／ADR-014 的性質：一次性碼只留雜湊與到期時間，明碼不進任何 store。"""
    now = datetime(2026, 9, 17, 9, 0, 0)
    confirms = ConfirmStore()
    confirms.issue("p1", code_hash="sha256-of-123456", expires_at=now, template_id="t")
    assert set(confirms.peek("p1")) == {"code_hash", "expires_at", "template_id"}

    approvals = ApprovalState()
    approvals.put_arm_code(code_hash="sha256-of-654321", expires_at=now)
    assert set(approvals.peek_arm_code()) == {"code_hash", "expires_at"}


def test_arm_code_is_single_use():
    """取出即銷毀——不論驗證成不成功，同一個碼不會有第二次機會。"""
    approvals = ApprovalState()
    approvals.put_arm_code(code_hash="h", expires_at=datetime(2026, 9, 17, 9, 0, 0))
    assert approvals.take_arm_code() is not None
    assert approvals.take_arm_code() is None


def test_disarm_destroys_pending_arm_code():
    approvals = ApprovalState()
    approvals.arm(datetime(2026, 9, 17, 10, 0, 0))
    approvals.put_arm_code(code_hash="h", expires_at=datetime(2026, 9, 17, 9, 5, 0))
    approvals.disarm()
    assert approvals.peek_arm_code() is None
    assert not approvals.is_armed(datetime(2026, 9, 17, 9, 1, 0))


def test_processed_callbacks_are_deduped_and_bounded():
    """去重表會擋掉重放，而且有上限——長跑的 poller 不會把記憶體吃光。"""
    approvals = ApprovalState()
    assert approvals.seen_callback("cb-1") is False
    assert approvals.seen_callback("cb-1") is True

    for i in range(10):
        approvals.seen_callback(f"x-{i}", cap=4)
    assert approvals.seen_callback("x-9", cap=4) is True
    assert approvals.seen_callback("x-0", cap=4) is False  # 最舊的已經被擠掉


def test_project_cache_separates_timestamp_from_rows():
    """時間戳與列表是兩件事，與 D11 之前逐字相同（見 ProjectCache 的 docstring）。"""
    cache = ProjectCache(ttl_seconds=30.0)
    cache.mark_states_refreshed(1000.0)
    assert cache.states_fresh(1010.0) is True
    assert cache.rows_if_fresh(1010.0) is None  # 時間戳夠新，但列表是空的
    cache.put_rows([{"project_key": "a"}])
    assert cache.rows_if_fresh(1010.0) == [{"project_key": "a"}]
    cache.invalidate()
    assert cache.rows_if_fresh(1010.0) is None
    assert cache.states_fresh(1010.0) is False


# --------------------------------------------------------------------------
# 注入真的接上了：給 state 就用 state，不給才用行程預設
# --------------------------------------------------------------------------


def test_approvals_status_reads_the_injected_state():
    now = datetime(2026, 9, 17, 9, 0, 0)
    injected = ApprovalState()
    injected.arm(now + timedelta(hours=1))
    cfg = DictConfig()

    assert approvals_status(cfg, now, state=injected)["armed"] is True
    assert approvals_status(cfg, now)["armed"] is False, "行程預設不該被注入的那一份影響"


def test_chat_status_reads_the_injected_state():
    injected = ChatState()
    injected.begin_ask()
    injected.finish_ask(answered=True)
    cfg = DictConfig()

    assert chat_status(cfg, state=injected)["asks_answered"] == 1
    assert chat_status(cfg)["asks_answered"] == 0


def test_discard_pending_confirm_uses_the_injected_store():
    now = datetime(2026, 9, 17, 9, 0, 0)
    store = ConfirmStore()
    store.issue("p1", code_hash="h", expires_at=now, template_id="t")
    runtime_state().confirms.issue("p1", code_hash="h", expires_at=now, template_id="t")

    discard_pending_confirm("p1", confirms=store)
    assert "p1" not in store
    assert "p1" in runtime_state().confirms, "只動被注入的那一份"


# --------------------------------------------------------------------------
# conftest 的 autouse fixture：每個測試拿到全新的行程預設
# --------------------------------------------------------------------------


def _dirty_the_process_default() -> None:
    now = datetime(2026, 9, 17, 9, 0, 0)
    runtime_state().confirms.issue("leak", code_hash="h", expires_at=now, template_id="t")
    runtime_state().approvals.arm(now + timedelta(hours=1))
    runtime_state().chat.begin_ask()


def test_process_default_starts_clean_first():
    """這兩支測試互相驗證對方：都把行程預設弄髒，也都要求開場是乾淨的。"""
    assert len(runtime_state().confirms) == 0
    assert runtime_state().chat.snapshot() == (False, 0)
    _dirty_the_process_default()


def test_process_default_starts_clean_second():
    assert len(runtime_state().confirms) == 0
    assert runtime_state().chat.snapshot() == (False, 0)
    _dirty_the_process_default()


def test_runtime_state_clear_is_equivalent_to_a_restart():
    now = datetime(2026, 9, 17, 9, 0, 0)
    state = RuntimeState()
    state.confirms.issue("p1", code_hash="h", expires_at=now, template_id="t")
    state.approvals.arm(now + timedelta(hours=1))
    state.greeting_cache.put("k", "v", now, timedelta(minutes=5))
    state.advisor_cache.put("k", "v", now, timedelta(minutes=5))
    state.projects.mark_states_refreshed(1000.0)

    state.clear()

    assert len(state.confirms) == 0
    assert not state.approvals.is_armed(now)
    assert state.greeting_cache.get("k", now) is None
    assert state.advisor_cache.get("k", now) is None
    assert not state.projects.states_fresh(1000.0)


def test_ttl_cache_expires_and_rejects_zero_ttl():
    now = datetime(2026, 9, 17, 9, 0, 0)
    cache = TtlCache()
    cache.put("k", "v", now, timedelta(minutes=10))
    assert cache.get("k", now + timedelta(minutes=9)) == "v"
    assert cache.get("k", now + timedelta(minutes=11)) is None
    assert cache.get("other", now) is None

    cache.clear()
    cache.put("k", "v", now, timedelta(0))
    assert cache.get("k", now) is None, "TTL 為 0 代表不快取"
