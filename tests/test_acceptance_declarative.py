"""驗收中心的宣告式表格契約（ADR-028，TODO D12）。

D12 把 22 個手寫的 `_check_aN` 換成「一個 reading ＋ 一張階梯表」。這支檔案守的是
**那個形狀本身**——不是某一項查到什麼（那在 test_acceptance_center.py），而是：

- 22 項全部都走同一個機構（沒有人偷偷塞一個自訂函式進 probe 欄位）
- 每個階梯都以 OTHERWISE 收尾，而且 OTHERWISE 只能在最後（否則後面那些列永遠到不了）
- 狀態只能來自那七個常數
- 沒有任何一個檔案再長回 god object（D12 的收據：每個檔案 < 600 行）
- 相依方向只准往下：items → readings → rules
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

from core import acceptance
from core.acceptance import items as items_module
from core.acceptance.rules import (
    ATTESTED, NEEDS_HUMAN, NOT_CONFIGURED, OTHERWISE, PARTIAL, PASSED, PENDING,
    RUNTIME_ONLY, Ladder, Reading,
)

PACKAGE = Path(acceptance.__file__ or "").parent
STATUSES = {PASSED, PARTIAL, PENDING, NEEDS_HUMAN, NOT_CONFIGURED, RUNTIME_ONLY}
# 表格裡不准出現 attested——那是人工署名，只有 build_acceptance_report 能加。
MAX_LINES = 600


def test_every_item_is_a_ladder_not_a_hand_written_function():
    """『改成宣告式』要算數，22 項就得真的全部走同一個機構。"""
    for spec in items_module.ITEMS:
        assert isinstance(spec["probe"], Ladder), f"{spec['id']} 的 probe 不是 Ladder"


def test_every_ladder_ends_with_otherwise_and_only_at_the_end():
    """順序就是語意：OTHERWISE 之後的列永遠到不了，所以它只能是最後一列。"""
    for spec in items_module.ITEMS:
        rules = spec["probe"].rules
        assert rules, f"{spec['id']} 的階梯是空的"
        assert rules[-1][0] is OTHERWISE, f"{spec['id']} 的階梯沒有以 OTHERWISE 收尾"
        for index, (predicate, _status, _detail) in enumerate(rules[:-1]):
            assert predicate is not OTHERWISE, f"{spec['id']} 第 {index + 1} 列之後的規則永遠到不了"


def test_rules_only_use_the_status_vocabulary():
    for spec in items_module.ITEMS:
        for predicate, status, detail in spec["probe"].rules:
            assert status in STATUSES, f"{spec['id']} 用了表格不該出現的狀態 {status}"
            assert status != ATTESTED, "attested 是人工署名，不能寫在表格裡"
            assert callable(predicate), f"{spec['id']} 的判準不可呼叫"
            assert isinstance(detail, str) or callable(detail), f"{spec['id']} 的敘述型別不對"


def test_table_covers_a1_to_a22_in_order():
    assert [spec["id"] for spec in items_module.ITEMS] == [f"A{n}" for n in range(1, 23)]
    for spec in items_module.ITEMS:
        for key in ("title", "priority", "blocks_release", "how", "criterion", "probe"):
            assert key in spec, f"{spec['id']} 少了 {key}"
        assert spec["title"] and spec["how"] and spec["criterion"]
        assert spec["priority"] in {"P0", "P1", "P2"}
        assert isinstance(spec["blocks_release"], bool)


def test_every_reading_is_used_exactly_once():
    """readings.py 裡不該留下沒人用的 reading，也不該有兩項共用同一個（那會共用 evidence）。"""
    from core.acceptance import readings

    used = [spec["probe"].read for spec in items_module.ITEMS]
    assert len(set(used)) == len(used), "有兩項共用同一個 reading"
    defined = {
        getattr(readings, name)
        for name in dir(readings)
        if name.startswith("a") and callable(getattr(readings, name)) and name[1:2].isdigit()
    }
    assert defined == set(used), "readings.py 有沒被表格引用的 reading（或反過來）"


def test_ladder_returns_exactly_the_three_contract_keys():
    ladder = Ladder(lambda ctx: Reading({"x": 1}), ((OTHERWISE, PENDING, "沒有收據。"),))
    result = ladder(object())
    assert set(result) == {"status", "detail", "evidence"}
    assert result == {"status": PENDING, "detail": "沒有收據。", "evidence": {"x": 1}}


def test_ladder_takes_the_first_matching_rule():
    reading = Reading({"a": 1, "b": 1})
    ladder = Ladder(lambda ctx: reading, (
        (lambda x: x.evidence["a"] == 1, PASSED, "第一條"),
        (lambda x: x.evidence["b"] == 1, PARTIAL, "第二條"),
        (OTHERWISE, PENDING, "最後"),
    ))
    assert ladder(object())["detail"] == "第一條"


def test_ladder_without_otherwise_fails_loudly_instead_of_returning_none():
    ladder = Ladder(lambda ctx: Reading({}), ((lambda x: False, PASSED, "不會中"),))
    with pytest.raises(AssertionError):
        ladder(object())


def test_detail_may_be_a_plain_string_or_a_function_of_the_reading():
    ladder = Ladder(lambda ctx: Reading({"n": 3}, {"m": 4}), (
        (OTHERWISE, PENDING, lambda x: f"{x.evidence['n']}／{x.facts['m']}"),
    ))
    assert ladder(object())["detail"] == "3／4"


def test_no_acceptance_file_grows_back_into_a_god_object():
    """D12 的收據。改動前是一個 1,560 行的檔案，22 個手寫探針佔了其中 990 行。"""
    sizes = {
        path.name: len(path.read_text(encoding="utf-8").splitlines())
        for path in PACKAGE.glob("*.py")
    }
    assert len(sizes) >= 5, f"套件檔案掃不到（只看到 {sizes}）——這個測試會變成空轉"
    oversized = {name: n for name, n in sizes.items() if n > MAX_LINES}
    assert oversized == {}, f"這些檔案超過 {MAX_LINES} 行：{oversized}"


def _imports(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.level and node.module:
            names.add(node.module.split(".")[0])
    return names


def test_dependency_direction_is_downward_only():
    """items → readings → rules。反過來 import 會把「規格」和「怎麼查」黏成一團。"""
    assert _imports(PACKAGE / "rules.py") == set()
    assert _imports(PACKAGE / "readings.py") <= {"rules"}
    assert _imports(PACKAGE / "items.py") <= {"readings", "rules"}
    assert _imports(PACKAGE / "report.py") <= {"items", "rules"}


# ---- 順序就是語意：每一組都是「對調之後測試仍然全綠」的地方 ----
#
# D12 的差分收據（85 個合成狀態同時餵給改動前與改動後）證明了搬家沒有改行為，但那份
# harness 需要改動前的檔案才跑得動，不會留在 repo 裡。下面這幾支是它留下來的**常駐**部分：
# 每一支鎖住一組「前面那列先接住」的事實——把兩列對調，訊息會變，但既有測試不會紅。

from contextlib import contextmanager  # noqa: E402
from datetime import datetime, timedelta  # noqa: E402

from sqlalchemy import create_engine  # noqa: E402
from sqlalchemy.orm import sessionmaker  # noqa: E402

from core.acceptance import build_acceptance_report  # noqa: E402
from core.models import Base, RAGIndexJob, SecretaryNote  # noqa: E402

NOW = datetime(2026, 9, 4, 14, 0)


class _DictConfig:
    def __init__(self, data):
        self.data = data

    def get(self, key_path, default=None):
        value = self.data
        for key in key_path.split("."):
            if not isinstance(value, dict) or key not in value:
                return default
            value = value[key]
        return value


class _TempDatabase:
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
        except Exception:
            session.rollback()
            raise
        finally:
            session.close()


@pytest.fixture
def ordering_db():
    return _TempDatabase()


def _item_of(db, cfg, item_id):
    report = build_acceptance_report(database=db, cfg=cfg, now=NOW, only=[item_id])
    return report["items"][0]


def _cfg(tmp_path, **tree):
    data = {"exporters": {"reports_dir": str(tmp_path / "reports")}}
    data.update(tree)
    return _DictConfig(data)


def test_a15_keeps_reporting_digests_even_when_the_switch_is_off(ordering_db, tmp_path):
    """A15 的開關在**最後**：已經有工作誌就照實說，不會因為關掉功能就變 not_configured。

    （A16 剛好相反，見下一支——兩者不能被「統一的 enabled 處理」合併。）
    """
    with ordering_db.session_scope() as session:
        session.add(SecretaryNote(kind="observation", source="daily_digest",
                                  body="x", source_ref="daily_digest:2026-09-01",
                                  created_at=NOW - timedelta(hours=2)))
    cfg = _cfg(tmp_path, proactive_secretary={"daily_digest": {"enabled": False}})
    item = _item_of(ordering_db, cfg, "A15")
    assert item["status"] == PARTIAL
    assert "只有 2026-09-01 一天的工作誌" in item["detail"]


def test_a16_says_it_is_switched_off_rather_than_idle(ordering_db, tmp_path):
    """A16 關掉時 facts 是空的，所以「沒有活動」那條**也會成立**——靠順序才說對話。"""
    cfg = _cfg(tmp_path, proactive_secretary={"patterns": {"enabled": False}})
    item = _item_of(ordering_db, cfg, "A16")
    assert item["status"] == NOT_CONFIGURED
    assert item["detail"] == "模式感知提案已關閉。"
    assert item["evidence"] == {"enabled": False,
                                "basis": "activity_patterns.collect_pattern_signals"}


def test_a21_unfinished_beats_orphan_directories(ordering_db, tmp_path):
    """沒跑完的回收要先說「沒跑完」；孤兒目錄那句是留給**跑完了**但刪不掉的情況。"""
    with ordering_db.session_scope() as session:
        session.add(RAGIndexJob(
            id="j1", job_type="compact_chroma", status="failed",
            requested_at=NOW - timedelta(hours=1), completed_at=NOW - timedelta(minutes=30),
            result_json='{"failed_dirs": ["/x/y"], "reclaimed_bytes": 0}',
        ))
    item = _item_of(ordering_db, _cfg(tmp_path), "A21")
    assert item["status"] == PARTIAL
    assert "沒有完成（failed）" in item["detail"]


def test_a21_running_beats_the_older_finished_receipt(ordering_db, tmp_path):
    """進行中優先：同時有舊的完成收據與一個還在跑的工作時，說的是「正在進行中」。"""
    with ordering_db.session_scope() as session:
        session.add(RAGIndexJob(
            id="j-old", job_type="compact_chroma", status="completed",
            requested_at=NOW - timedelta(hours=3), completed_at=NOW - timedelta(hours=2),
            result_json='{"reclaimed_bytes": 0, "removed_dirs": [], "failed_dirs": []}',
        ))
        session.add(RAGIndexJob(
            id="j-run", job_type="compact_chroma", status="running",
            requested_at=NOW - timedelta(seconds=40),
        ))
    item = _item_of(ordering_db, _cfg(tmp_path), "A21")
    assert item["status"] == PENDING
    assert "回收正在進行中（running）" in item["detail"]
    assert item["evidence"]["receipt_available"] is False
