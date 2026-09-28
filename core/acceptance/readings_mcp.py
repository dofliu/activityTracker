"""MCP Context Server 那四項驗收「去查什麼」（ADR-032，TODO E4）。

**為什麼自成一個檔案**：`readings.py` 在 D12 之後就停在 594 行，而
`tests/test_acceptance_declarative.py` 的 god-object 守門是 600 行。加這四項會直接撞上去
——那條線本來就是為了在這種時候擋住人，所以正確的回應是拆，不是把上限調高。
拆的線是功能邊界：這四項全部只讀 `reports/mcp/` 底下的檔案，與其他 22 項零交集。

相依方向與 `readings.py` 相同：只往下依賴 `rules`，不碰 `items`。額外多一個
`mcpserver.receipts`——它只用到 json／os／datetime／pathlib，沒有官方 SDK、也沒有
`core` 的模組層相依，所以不會成環，也不會把 `[mcp]` extra 變成必要。
**刻意 import 而不是在這裡自己再寫一次 glob**：檔名規則有兩份就會漂移，
而漂移的症狀是驗收永遠說「還沒跑過」——一個查不出原因的假紅燈。
"""

from __future__ import annotations

from typing import Any

from mcpserver import receipts as mcp_receipts

from .rules import Ctx, Reading, latest_files, reports_dir

# 收據只有這六個鍵（`mcpserver/receipts.py` 的 RECEIPT_FIELDS）。A26 要證的是
# 「不含 query 原文與參數值」——證法是**鍵的集合**，不是去猜哪些值看起來像查詢字串：
# 白名單之外一個鍵都不准有，這樣連未來新增欄位也會被這一項接住。
MCP_RECEIPT_FIELDS = frozenset(mcp_receipts.RECEIPT_FIELDS)


def _mcp_receipt_dir(ctx: Ctx) -> Any:
    return reports_dir(ctx.cfg) / "mcp"


def a23_mcp_selftest(ctx: Ctx) -> Reading:
    """A23：`omni mcp --selftest` 七個 tool 各回一次、全部帶 source_ref。

    只讀最近一次 selftest 落下的收據——驗收中心不替使用者跑 tool（那會變成
    「驗收自己製造收據」，也會在唯讀證明裡混進驗收自己造成的讀取）。
    """
    latest = mcp_receipts.latest_selftest_receipt(_mcp_receipt_dir(ctx))
    if latest is None:
        return Reading({"receipt_available": False, "dir": str(_mcp_receipt_dir(ctx))}, {"never_ran": True})
    checks = latest.get("checks") or {}
    answered = list(checks.get("tools_answered") or [])
    skipped = dict(checks.get("tools_skipped") or {})
    failed = dict(checks.get("tools_failed") or {})
    evidence = {
        "receipt_available": True,
        "receipt_name": latest.get("receipt_name"),
        "ran_at": latest.get("ts"),
        "status": latest.get("status"),
        "tools_answered": answered,
        "tools_skipped": skipped,
        "tools_failed": failed,
        "source_refs_seen": checks.get("source_refs_seen", 0),
        "source_refs_unresolved": list(checks.get("source_refs_unresolved") or []),
        "refs_resolved": checks.get("refs_resolved") or {},
    }
    facts = {
        "answered": len(answered),
        "expected": len(mcp_receipts.EXPECTED_TOOLS),
        "missing": sorted(set(mcp_receipts.EXPECTED_TOOLS) - set(answered) - set(skipped)),
        "skipped": sorted(skipped),
        "unresolved": len(evidence["source_refs_unresolved"]),
        "refs": checks.get("source_refs_seen", 0),
    }
    return Reading(evidence, facts)


def a25_mcp_read_only(ctx: Ctx) -> Reading:
    """A25：唯讀證明。判準是每張表的 **(列數, 內容雜湊)** 都不變——不是只比列數。

    ADR-032 明寫這條取代 REVIEW §A.4 D1 的原措辭：只數列數會放行 UPSERT（實測）。
    """
    latest = mcp_receipts.latest_selftest_receipt(_mcp_receipt_dir(ctx))
    if latest is None:
        return Reading({"receipt_available": False}, {"never_ran": True})
    checks = latest.get("checks") or {}
    evidence = {
        "receipt_available": True,
        "receipt_name": latest.get("receipt_name"),
        "ran_at": latest.get("ts"),
        "contract_unchanged": bool(checks.get("read_only_contract_unchanged")),
        "tables_checked": checks.get("tables_checked", 0),
        "forbidden_output_hits": list(checks.get("forbidden_output_hits") or []),
        "criterion": "每張表的 (列數, 全表內容雜湊) 前後相同",
    }
    return Reading(evidence, {
        "unchanged": bool(checks.get("read_only_contract_unchanged")),
        "tables": checks.get("tables_checked", 0),
        "leaks": len(evidence["forbidden_output_hits"]),
    })


def a26_mcp_receipts(ctx: Ctx) -> Reading:
    """A26：tool call receipt 存在，且**不含 query 原文與參數值**。

    證法是鍵的集合：白名單（六個鍵）之外一個都不准有。這比「掃描值裡有沒有查詢字串」
    硬——後者只擋得住你想得到的那幾種字串。
    """
    folder = _mcp_receipt_dir(ctx)
    files = latest_files(folder, "mcp-receipts-*.jsonl", limit=3)
    if not files["exists"] or files["count"] == 0:
        return Reading({"receipt_available": False, **files}, {"never_ran": True})
    lines = 0
    offenders: list[dict[str, Any]] = []
    tools_seen: set[str] = set()
    for entry in files["latest"]:
        path = folder / entry["name"]
        try:
            records = mcp_receipts.read_receipts(path)
        except (OSError, ValueError):
            offenders.append({"file": entry["name"], "extra_keys": ["<unreadable>"]})
            continue
        for record in records:
            lines += 1
            tools_seen.add(str(record.get("tool") or ""))
            extra = sorted(set(record) - MCP_RECEIPT_FIELDS)
            if extra:
                offenders.append({"file": entry["name"], "extra_keys": extra})
    evidence = {
        "receipt_available": True,
        "dir_exists": files["exists"],
        "files": files["count"],
        "latest": files["latest"],
        "records_scanned": lines,
        "tools_seen": sorted(t for t in tools_seen if t),
        "allowed_fields": sorted(MCP_RECEIPT_FIELDS),
        "records_with_extra_fields": offenders[:5],
    }
    return Reading(evidence, {"records": lines, "offenders": len(offenders)})


def a24_mcp_live_client(ctx: Ctx) -> Reading:
    """A24：實機把 `omni mcp` 掛上 Claude Code／Codex，引用回查得到。

    **機器查得到的只有前置條件，判斷永遠是人的**：agent 說出來的話對不對、展開後
    是不是同一件事，沒有任何本機收據證得了。

    前置條件是這樣分辨的：selftest 每跑一次會同時留下 `mcp-receipts-<日期>-<pid>.jsonl`
    與 `mcp-selftest-<時間>-<pid>.json`，**pid 相同**。所以一個「有 tool call 收據、
    卻沒有同 pid selftest 收據」的檔案，就是一個真的 client 連上來開的程序。
    這是唯一不需要新增欄位就能分出「自己測的」與「真的用過的」的訊號。
    """
    folder = _mcp_receipt_dir(ctx)
    calls = latest_files(folder, "mcp-receipts-*.jsonl", limit=50)
    selftests = latest_files(folder, mcp_receipts.SELFTEST_GLOB, limit=50)
    selftest_pids = {_pid_of(entry["name"]) for entry in selftests["latest"]}
    live = [
        entry for entry in calls["latest"]
        if _pid_of(entry["name"]) not in selftest_pids
    ]
    evidence = {
        "receipt_available": bool(calls["exists"] and calls["count"]),
        "call_receipt_files": calls["count"],
        "selftest_receipt_files": selftests["count"],
        "live_client_sessions": len(live),
        "latest_live_session": live[0] if live else None,
        "criterion": "agent 答案裡的每個引用都能用 omni_resolve_ref 展開成同一列（人眼確認）",
    }
    return Reading(evidence, {
        "never_ran": not calls["exists"] or calls["count"] == 0,
        "live": len(live),
        "selftest_only": bool(calls["count"]) and not live,
    })


def _pid_of(filename: str) -> str:
    """收據檔名尾巴的 pid。兩種檔名都是 `…-<pid>.<副檔名>`。"""
    return filename.rsplit("-", 1)[-1].split(".")[0]
