"""秘書四層與兩個定型契約（ADR-024，TODO D8）。

D8 之前，秘書相關的程式是 `core/` 底下十一個平輩模組：沒有共同型別（六個收集器各自
回傳 `dict[str, Any]`）、有一個真實的循環依賴（`proactive_secretary ↔ secretary_memory
↔ secretary_home ↔ agent_executor`），靠 116 處指向 `core.*` 的函式內 import 撐著。

這裡把分層寫成測試：

1. 方向只准往下：下層不得 import 上層（掃 import，環一長回來就失敗）。
2. 指向 `core.*` 的函式內延遲 import 必須保持在個位數量級——那是環的溫床。
3. `Signal` 是收集器與聚合層之間唯一的契約：缺必填欄位當場失敗，訊息說得出是誰給的。
4. `Proposal.to_dict()` 就是 API 的形狀：鍵一個都不多、一個都不少。
5. 記憶層不自己往上拿資料：今日視圖與提案由呈現層的組合點注入。
"""

from __future__ import annotations

import ast
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from core.models import Base, SecretaryNote
from core.secretary.aggregate import _proposal_from_signal
from core.secretary.memory import memory_context
from core.secretary.types import Proposal, Signal

ROOT = Path(__file__).resolve().parents[1]
PKG = ROOT / "core" / "secretary"
NOW = datetime(2026, 9, 16, 10, 0)

# 層級：越下面越基礎，只能往下 import
# 由下而上：packs（L0 執行組合）與 greeting（問候卡）在 present 之下——
# 「01 今天」視圖是由它們的產物組出來的，所以呈現層在最上面。
LAYERS = ("types", "memory", "signals", "aggregate", "packs", "greeting", "present")
LOWER_THAN = {name: set(LAYERS[:index]) for index, name in enumerate(LAYERS)}

# D8 之前的提案 JSON 形狀（逐鍵；選擇性欄位單獨列出）
REQUIRED_PROPOSAL_KEYS = {
    "proposal_id", "proposal_type", "project_key", "subject_ref", "title", "detail",
    "reason", "reasons", "suggested_action", "why_now", "priority", "risk_level",
    "execution_available", "url", "age_days", "evidence_refs", "evidence", "score",
}
OPTIONAL_PROPOSAL_KEYS = {
    "habit_boosted", "priority_declared", "docs_facts",
    "meeting_followups", "meeting_note_id", "memory_note",
}


class DictConfig:
    def __init__(self, data=None):
        self.data = data or {}

    def get(self, key_path, default=None):
        value = self.data
        for key in key_path.split("."):
            if not isinstance(value, dict) or key not in value:
                return default
            value = value[key]
        return value


class TempDatabase:
    def __init__(self):
        self.engine = create_engine("sqlite:///:memory:")
        Base.metadata.create_all(self.engine)
        self.factory = sessionmaker(bind=self.engine)

    @contextmanager
    def session_scope(self):
        session = self.factory()
        try:
            yield session
            session.commit()
        finally:
            session.close()


def _imports(path: Path) -> set[str]:
    """該模組 import 了哪些同套件的兄弟模組（含函式內）。"""
    tree = ast.parse(path.read_text(encoding="utf-8"))
    siblings = set()
    for node in ast.walk(tree):
        module = None
        if isinstance(node, ast.ImportFrom):
            module = node.module or ""
        elif isinstance(node, ast.Import):
            module = node.names[0].name if node.names else ""
        if module and module.startswith("core.secretary."):
            siblings.add(module.split(".")[2])
    return siblings


# ---- 1./2. 分層與延遲 import ---------------------------------------------


@pytest.mark.parametrize("layer", LAYERS)
def test_a_layer_only_imports_layers_below_it(layer):
    path = PKG / f"{layer}.py"
    assert path.exists(), f"{layer}.py 不見了"
    upward = {name for name in _imports(path) if name != layer and name not in LOWER_THAN[layer]}
    assert upward == set(), f"{layer}.py 往上 import 了 {sorted(upward)}——環又長回來了"


def test_the_old_flat_modules_are_gone():
    for old in ("secretary_memory", "secretary_profile", "proactive_secretary", "secretary_advisor",
                "secretary_home", "secretary_ask", "secretary_greeting", "secretary_packs", "triage_signals"):
        assert not (ROOT / "core" / f"{old}.py").exists(), old


def test_function_level_core_imports_stay_rare():
    """指向 `core.*` 的函式內 import 是循環依賴的溫床；第三方／選用依賴不在此列。"""
    offenders = []
    for path in sorted((ROOT / "core").rglob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                for sub in ast.walk(node):
                    if isinstance(sub, (ast.Import, ast.ImportFrom)):
                        module = getattr(sub, "module", None) or (sub.names[0].name if sub.names else "")
                        if module.startswith("core"):
                            offenders.append(f"{path.relative_to(ROOT).as_posix()}:{sub.lineno}")
    assert len(offenders) < 20, f"{len(offenders)} 處：{offenders}"


# ---- 3. Signal 契約 --------------------------------------------------------


def _signal_dict(**overrides):
    base = {
        "signal_type": "stalled_open_loop", "project_key": "alpha", "subject_ref": "open_loops:1",
        "title": "alpha 有未結事項", "evidence_ref": "open_loops:1", "score": 0.7,
        "reasons": ["48 小時沒動"], "age_days": 2.0,
    }
    base.update(overrides)
    return base


def test_signal_requires_the_fields_the_aggregator_reads():
    signal = Signal.from_dict(_signal_dict())
    assert signal.reasons == ("48 小時沒動",) and signal.detail == "" and signal.url is None
    with pytest.raises(ValueError) as exc:
        Signal.from_dict({"signal_type": "aging_pr", "project_key": "a"})
    # 訊息要說得出是哪個收集器、缺哪些欄位
    assert "aging_pr" in str(exc.value) and "title" in str(exc.value)


def test_signal_is_frozen_so_weighting_never_mutates_someone_elses_copy():
    signal = Signal.from_dict(_signal_dict())
    boosted = signal.with_score(0.85, habit_boosted=True)
    assert (signal.score, signal.habit_boosted) == (0.7, False)
    assert (boosted.score, boosted.habit_boosted) == (0.85, True)
    with pytest.raises(Exception):
        signal.score = 1.0  # type: ignore[misc]


# ---- 4. Proposal 就是 API 的形狀 -------------------------------------------


def test_proposal_to_dict_is_exactly_the_api_shape():
    proposal = _proposal_from_signal(Signal.from_dict(_signal_dict()), NOW)
    payload = proposal.to_dict()
    assert set(payload) == REQUIRED_PROPOSAL_KEYS  # 沒有成立的選擇性欄位就不該出現
    assert payload["risk_level"] == "L0_READ_ONLY" and payload["execution_available"] is False
    assert payload["reason"] == "48 小時沒動" and payload["reasons"] == ["48 小時沒動"]
    assert payload["evidence_refs"] == ["open_loops:1"] and payload["evidence"][0]["source_ref"] == "open_loops:1"

    rich = _proposal_from_signal(Signal.from_dict(_signal_dict(
        signal_type="docs_behind_code", habit_boosted=True, priority_declared=True,
        docs_facts="8 個 commit", meeting_followups=["寄報告"], meeting_note_id=3,
    )), NOW).to_dict()
    assert set(rich) - REQUIRED_PROPOSAL_KEYS <= OPTIONAL_PROPOSAL_KEYS
    assert rich["habit_boosted"] is True and rich["docs_facts"] == "8 個 commit"
    assert rich["meeting_followups"] == ["寄報告"] and rich["meeting_note_id"] == 3


def test_proposal_is_read_only_by_construction():
    proposal = _proposal_from_signal(Signal.from_dict(_signal_dict()), NOW)
    assert isinstance(proposal, Proposal)
    with pytest.raises(Exception):
        proposal.priority = "high"  # type: ignore[misc]


# ---- 5. 記憶層不自己往上拿 -------------------------------------------------


def test_memory_context_no_longer_fetches_the_today_view_or_proposals():
    """記憶層只讀自己的表；今日視圖與提案由呈現層注入（ADR-024 的環拆在這裡）。"""
    source = (PKG / "memory.py").read_text(encoding="utf-8")
    assert "build_today_view" not in source and "build_action_proposals" not in source

    database = TempDatabase()
    with database.session_scope() as session:
        session.add(SecretaryNote(kind="preference", body="回答用繁體中文", source="web", created_at=NOW))
    cfg = DictConfig({"secretary_memory": {"enabled": True}})

    bare = memory_context(database=database, cfg=cfg, now=NOW)
    assert "回答用繁體中文" in bare["text"]           # 筆記照常
    assert "resume" not in bare["receipt"]["sections"] and "proposals" not in bare["receipt"]["sections"]

    injected = memory_context(
        database=database, cfg=cfg, now=NOW,
        today={"resume": {"display_name": "alpha", "last_activity_at": "2026-09-16T09:00"}, "pack_line": "早晨包：…"},
        proposals=[{"project_key": "alpha", "title": "收尾 ADR", "why_now": "脈絡還新鮮"}],
    )
    assert "alpha" in injected["text"] and "收尾 ADR" in injected["text"]
    assert {"resume", "pack", "proposals"} <= set(injected["receipt"]["sections"])


def test_the_composition_point_lives_in_the_presentation_layer():
    from core.secretary.present import full_memory_context

    source = (PKG / "present.py").read_text(encoding="utf-8")
    assert "def full_memory_context" in source
    assert callable(full_memory_context)
