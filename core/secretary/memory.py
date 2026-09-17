"""小秘書記憶區（ADR-012）：秘書回答與主動思考時的固定參考來源（「大腦」）。

三層：

1. **筆記表** ``secretary_notes``：使用者交代的 ``user_note``（記下來）、``preference``
   （偏好）、``decision``（決定），以及秘書自己從 L0 收據推出的 ``observation``
   （標記來源、可一鍵刪除、同一來源同一天只寫一次）。
2. **既有產物**：筆記與時段微摘要由 ``core/semantic_index`` 索引（ADR-023：活動記憶
   只有這一份，每筆只 embedding 一次）；秘書寫出的報告檔（Handoff、同步報告、STATUS
   草稿、每日入口）是文件，由 ``rag/report_indexer.py`` 併入知識庫。兩者提問時都引用得到。
3. **固定脈絡** ``memory_context()``：每次對話注入 system prompt 的一段短文
   （今日狀態、top 提案、最近筆記），以及提案引擎讀偏好（「不要提醒 X」）。

契約：

- 全部本機、唯讀來源；記憶區只存使用者輸入的短文字與收據推出的觀察，不存 prompt／
  response 原文，也不寫進任何 repo。
- 注入對話的脈絡有字數上限，且每則回應都附一份收據（用了幾筆、幾個字、是否被截斷），
  介面能看到秘書「當下記得什麼」（``GET /api/v1/secretary/memory/context``）。
- 偏好只做兩種確定性的事：``mute:<proposal_type|project_key>`` 壓掉提案、其餘當作
  純文字脈絡；不解析成任何會執行的動作。
"""

from __future__ import annotations

import logging
import re
from datetime import datetime, timedelta
from typing import Any, Iterable

from sqlalchemy import func

from core.config import get_config
from core.database import get_db
from core.models import SecretaryNote
from core.time_utils import get_local_now


logger = logging.getLogger("OmniContext.SecretaryMemory")

NOTE_KINDS: tuple[str, ...] = ("user_note", "preference", "decision", "observation")
USER_KINDS: tuple[str, ...] = ("user_note", "preference", "decision")
KIND_LABELS = {
    "user_note": "筆記",
    "preference": "偏好",
    "decision": "決定",
    "observation": "觀察",
}
MAX_BODY_CHARS = 4000
MAX_TITLE_CHARS = 200
DEFAULT_CONTEXT_MAX_CHARS = 2500
DEFAULT_OBSERVATION_TTL_DAYS = 14

MEMORY_CLAIM_BOUNDARY = (
    "記憶區只含使用者輸入的短文字與由本機唯讀收據推出的觀察；"
    "注入對話的脈絡有字數上限並附收據，不代表秘書看過全部歷史。"
)

# 對話框前綴：「記下來：…」「偏好：…」「決定：…」與英文 /note /pref /decision。
_COMMAND_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("user_note", re.compile(r"^\s*(?:記下來|記住|筆記|/note|remember)\s*[:：]?\s*(.+)$", re.S)),
    ("preference", re.compile(r"^\s*(?:偏好|/pref(?:erence)?)\s*[:：]?\s*(.+)$", re.S)),
    ("decision", re.compile(r"^\s*(?:決定|/decision|decide)\s*[:：]?\s*(.+)$", re.S)),
)
# 可選的專案標記：「記下來 @OmniContext：…」或「記下來：[OmniContext] …」
_PROJECT_AT = re.compile(r"^\s*@([\w.\-]+)\s*[:：]?\s*(.*)$", re.S)
_PROJECT_BRACKET = re.compile(r"^\s*\[([^\]]{1,120})\]\s*(.*)$", re.S)
_MUTE_DIRECTIVE = re.compile(r"^\s*(?:mute|不要提醒|不再提醒|忽略)\s*[:：]?\s*(.+?)\s*$", re.I)


class MemoryRejected(ValueError):
    def __init__(self, error_code: str, message: str):
        super().__init__(message)
        self.error_code = error_code
        self.http_status = 422


def _naive(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    return value.replace(tzinfo=None) if value.tzinfo else value


def memory_enabled(cfg: Any | None = None) -> bool:
    cfg = cfg or get_config()
    return bool(cfg.get("secretary_memory.enabled", True))


def chat_context_enabled(cfg: Any | None = None) -> bool:
    cfg = cfg or get_config()
    return memory_enabled(cfg) and bool(cfg.get("secretary_memory.chat_context.enabled", True))


def context_max_chars(cfg: Any | None = None) -> int:
    cfg = cfg or get_config()
    try:
        value = int(cfg.get("secretary_memory.chat_context.max_chars", DEFAULT_CONTEXT_MAX_CHARS))
    except (TypeError, ValueError):
        value = DEFAULT_CONTEXT_MAX_CHARS
    return max(400, min(value, 12000))


def observation_ttl(cfg: Any | None = None) -> timedelta:
    cfg = cfg or get_config()
    try:
        days = int(cfg.get("secretary_memory.observation_ttl_days", DEFAULT_OBSERVATION_TTL_DAYS))
    except (TypeError, ValueError):
        days = DEFAULT_OBSERVATION_TTL_DAYS
    return timedelta(days=max(1, min(days, 365)))


def serialize_note(row: SecretaryNote) -> dict[str, Any]:
    created = _naive(row.created_at)
    return {
        "id": row.id,
        "kind": row.kind,
        "kind_label": KIND_LABELS.get(row.kind, row.kind),
        "project_key": row.project_key,
        "title": row.title,
        "body": row.body,
        "source": row.source,
        "source_ref": row.source_ref,
        "pinned": bool(row.pinned),
        "deletable": True,
        "created_at": created.isoformat(timespec="seconds") if created else None,
    }


# ---------------------------------------------------------------- 對話前綴解析


def parse_note_command(text: str) -> dict[str, Any] | None:
    """把「記下來：…」這類訊息解析成筆記；不是命令就回 None（照常走 LLM）。"""
    if not text or len(text) > MAX_BODY_CHARS + 200:
        return None
    for kind, pattern in _COMMAND_PATTERNS:
        match = pattern.match(text)
        if not match:
            continue
        body = match.group(1).strip().lstrip(":：").strip()
        project_key: str | None = None
        for project_pattern in (_PROJECT_AT, _PROJECT_BRACKET):
            pm = project_pattern.match(body)
            if pm:
                project_key = pm.group(1).strip() or None
                body = pm.group(2).strip()
                break
        if not body:
            return None
        return {"kind": kind, "body": body, "project_key": project_key}
    return None


# ---------------------------------------------------------------- CRUD


def add_note(
    *,
    kind: str,
    body: str,
    project_key: str | None = None,
    title: str | None = None,
    source: str = "api",
    source_ref: str | None = None,
    pinned: bool = False,
    database: Any | None = None,
    now: datetime | None = None,
) -> dict[str, Any]:
    if kind not in NOTE_KINDS:
        raise MemoryRejected("invalid_kind", f"kind 必須是 {', '.join(NOTE_KINDS)} 之一")
    body = (body or "").strip()
    if not body:
        raise MemoryRejected("empty_body", "筆記內容不可為空")
    if len(body) > MAX_BODY_CHARS:
        raise MemoryRejected("body_too_long", f"筆記內容不可超過 {MAX_BODY_CHARS} 字")
    title = (title or "").strip()[:MAX_TITLE_CHARS] or None
    project_key = (project_key or "").strip()[:255] or None
    database = database or get_db()
    now = _naive(now or get_local_now())
    with database.session_scope() as session:
        row = SecretaryNote(
            kind=kind,
            project_key=project_key,
            title=title,
            body=body,
            source=(source or "api")[:40],
            source_ref=(source_ref or None) and source_ref[:255],
            pinned=bool(pinned),
            created_at=now,
            updated_at=now,
        )
        session.add(row)
        session.flush()
        return serialize_note(row)


def list_notes(
    *,
    kind: str | None = None,
    project_key: str | None = None,
    limit: int = 50,
    database: Any | None = None,
) -> dict[str, Any]:
    if kind is not None and kind not in NOTE_KINDS:
        raise MemoryRejected("invalid_kind", f"kind 必須是 {', '.join(NOTE_KINDS)} 之一")
    database = database or get_db()
    limit = max(1, min(int(limit), 500))
    with database.session_scope() as session:
        query = session.query(SecretaryNote)
        if kind:
            query = query.filter(SecretaryNote.kind == kind)
        if project_key:
            query = query.filter(SecretaryNote.project_key == project_key)
        rows = (
            query.order_by(SecretaryNote.pinned.desc(), SecretaryNote.created_at.desc(), SecretaryNote.id.desc())
            .limit(limit)
            .all()
        )
        notes = [serialize_note(row) for row in rows]
        counts = {k: 0 for k in NOTE_KINDS}
        for k, count in (
            session.query(SecretaryNote.kind, func.count(SecretaryNote.id))
            .group_by(SecretaryNote.kind)
            .all()
        ):
            counts[k] = int(count)
    return {
        "notes": notes,
        "counts": counts,
        "total": sum(counts.values()),
        "kinds": list(NOTE_KINDS),
        "claim_boundary": MEMORY_CLAIM_BOUNDARY,
    }


def delete_note(note_id: int, *, database: Any | None = None) -> dict[str, Any]:
    database = database or get_db()
    with database.session_scope() as session:
        row = session.get(SecretaryNote, int(note_id))
        if row is None:
            return {"deleted": False, "id": int(note_id), "reason": "not_found"}
        session.delete(row)
        return {"deleted": True, "id": int(note_id), "kind": row.kind}


def clear_notes(*, kind: str, database: Any | None = None) -> dict[str, Any]:
    """一鍵清掉某一類筆記；預期用在 observation（秘書自己的觀察）。"""
    if kind not in NOTE_KINDS:
        raise MemoryRejected("invalid_kind", f"kind 必須是 {', '.join(NOTE_KINDS)} 之一")
    database = database or get_db()
    with database.session_scope() as session:
        deleted = session.query(SecretaryNote).filter(SecretaryNote.kind == kind).delete(synchronize_session=False)
    return {"deleted": int(deleted or 0), "kind": kind}


# ---------------------------------------------------------------- 秘書自己的觀察


def record_observation(
    *,
    title: str,
    body: str,
    source_ref: str,
    project_key: str | None = None,
    source: str = "morning_pack",
    database: Any | None = None,
    now: datetime | None = None,
) -> dict[str, Any] | None:
    """寫一則 observation；同一個 source_ref 已存在就不重複（回 None）。"""
    database = database or get_db()
    with database.session_scope() as session:
        exists = (
            session.query(SecretaryNote.id)
            .filter(SecretaryNote.kind == "observation", SecretaryNote.source_ref == source_ref)
            .first()
        )
    if exists:
        return None
    return add_note(
        kind="observation",
        body=body,
        title=title,
        project_key=project_key,
        source=source,
        source_ref=source_ref,
        database=database,
        now=now,
    )


def observations_from_pack(
    receipt: dict[str, Any],
    *,
    database: Any | None = None,
    now: datetime | None = None,
    cfg: Any | None = None,
) -> list[dict[str, Any]]:
    """把早晨包收據裡值得記住的數字寫成當日觀察（每天每項最多一則）。"""
    cfg = cfg or get_config()
    if not memory_enabled(cfg):
        return []
    now = _naive(now or get_local_now())
    day = now.strftime("%Y-%m-%d")
    written: list[dict[str, Any]] = []

    def _write(key: str, title: str, body: str) -> None:
        try:
            note = record_observation(
                title=title, body=body, source_ref=f"morning_pack:{day}:{key}", database=database, now=now,
            )
        except Exception as exc:  # noqa: BLE001 — 觀察寫不進去不該讓早晨包失敗
            logger.warning("observation %s not written: %s", key, exc)
            return
        if note:
            written.append(note)

    pull = receipt.get("needs_pull") or 0
    push = receipt.get("needs_push") or 0
    diverged = receipt.get("diverged") or 0
    if pull or push or diverged:
        _write(
            "repo_sync",
            f"{day} repo 同步狀態",
            f"早晨包掃描 {receipt.get('repos_scanned') or 0} 個 repo：需要 pull {pull}、需要 push {push}、分歧 {diverged}"
            "（cached 認知，不代表遠端當下狀態）。",
        )
    stale = receipt.get("stale_status") or 0
    if stale:
        _write("stale_status", f"{day} STATUS 過期", f"有 {stale} 個專案的 STATUS 已過期，草稿在 reports/status_drafts/。")
    errors = receipt.get("errors") or []
    if errors:
        _write("pack_errors", f"{day} 早晨包有步驟失敗", "失敗步驟：" + "；".join(str(e) for e in errors[:5]))
    return written


# ---------------------------------------------------------------- 提案引擎讀偏好


def preference_mutes(*, database: Any | None = None) -> set[str]:
    """偏好筆記中的 ``mute:<proposal_type|project_key>``（或「不要提醒 X」）目標集合。"""
    database = database or get_db()
    targets: set[str] = set()
    with database.session_scope() as session:
        rows = session.query(SecretaryNote.body).filter(SecretaryNote.kind == "preference").all()
    for (body,) in rows:
        for line in str(body or "").splitlines():
            match = _MUTE_DIRECTIVE.match(line)
            if match:
                target = match.group(1).strip().rstrip("。.")
                if target:
                    targets.add(target.lower())
    return targets


def project_memory_lines(*, database: Any | None = None, max_per_project: int = 1) -> dict[str, list[str]]:
    """每個專案最近的決定／筆記（供提案卡顯示「你之前記過」）。"""
    database = database or get_db()
    result: dict[str, list[str]] = {}
    with database.session_scope() as session:
        rows = (
            session.query(SecretaryNote)
            .filter(SecretaryNote.kind.in_(("decision", "user_note")), SecretaryNote.project_key.isnot(None))
            .order_by(SecretaryNote.created_at.desc(), SecretaryNote.id.desc())
            .all()
        )
        for row in rows:
            key = str(row.project_key)
            bucket = result.setdefault(key, [])
            if len(bucket) >= max_per_project:
                continue
            created = _naive(row.created_at)
            stamp = created.strftime("%m-%d") if created else ""
            bucket.append(f"{KIND_LABELS.get(row.kind, row.kind)} {stamp}：{row.body[:160]}")
    return result


# ---------------------------------------------------------------- 對話用固定脈絡


def _fmt_note(note: dict[str, Any]) -> str:
    stamp = str(note.get("created_at") or "")[:10]
    project = f"[{note['project_key']}] " if note.get("project_key") else ""
    label = note.get("kind_label") or note.get("kind")
    body = str(note.get("body") or "").replace("\n", " ").strip()
    return f"- ({label} {stamp}) {project}{body}"


def memory_context(
    *,
    database: Any | None = None,
    cfg: Any | None = None,
    now: datetime | None = None,
    today: dict[str, Any] | None = None,
    proposals: list[dict[str, Any]] | None = None,
    max_chars: int | None = None,
    max_notes: int = 12,
) -> dict[str, Any]:
    """組出注入 system prompt 的「記憶區」段落與收據。

    順序固定：今日狀態 → top 提案 → 偏好／決定 → 最近筆記 → 未過期觀察。
    超過上限就從尾端截斷並在收據標記 ``truncated``。
    """
    cfg = cfg or get_config()
    database = database or get_db()
    now = _naive(now or get_local_now())
    limit = max_chars or context_max_chars(cfg)
    receipt: dict[str, Any] = {
        "included": False, "notes_used": 0, "chars": 0, "truncated": False,
        "sections": [], "claim_boundary": MEMORY_CLAIM_BOUNDARY,
    }
    if not memory_enabled(cfg):
        receipt["reason"] = "disabled"
        return {"text": "", "receipt": receipt}

    lines: list[str] = [f"【小秘書記憶區 · {now.strftime('%Y-%m-%d %H:%M')}】"]

    # 1. 今日狀態（上次做到哪、早晨包）
    # ADR-024：今日視圖與提案由**呈現層**取得後注入（`present.full_memory_context`）。
    # 記憶層不准往上 import；沒給就少那一段，筆記照常。
    if today:
        resume = today.get("resume") or {}
        if resume.get("display_name") or resume.get("project_key"):
            lines.append(
                f"上次做到哪：{resume.get('display_name') or resume.get('project_key')}"
                f"（{str(resume.get('last_activity_at') or '')[:16]}）"
                + (f"：{str(resume.get('last_action_summary'))[:160]}" if resume.get("last_action_summary") else "")
            )
            receipt["sections"].append("resume")
        if today.get("pack_line"):
            lines.append(str(today["pack_line"]))
            receipt["sections"].append("pack")
        if today.get("active_project_count") is not None:
            lines.append(f"進行中專案：{today.get('active_project_count')} 個")

    # 2. top 提案
    if proposals:
        lines.append("目前建議（依「為什麼是現在」排序）：")
        for item in proposals[:3]:
            why = f"；{item.get('why_now')}" if item.get("why_now") else ""
            lines.append(f"- [{item.get('project_key')}] {item.get('title')}{why}")
        receipt["sections"].append("proposals")

    # 3. 筆記：偏好／決定 → 一般筆記 → 未過期觀察
    ttl_cutoff = now - observation_ttl(cfg)
    with database.session_scope() as session:
        rows = (
            session.query(SecretaryNote)
            .order_by(SecretaryNote.pinned.desc(), SecretaryNote.created_at.desc(), SecretaryNote.id.desc())
            .limit(200)
            .all()
        )
        notes = [serialize_note(r) for r in rows]
    prefs = [n for n in notes if n["kind"] in ("preference", "decision")]
    plain = [n for n in notes if n["kind"] == "user_note"]
    observations = [
        n for n in notes
        if n["kind"] == "observation" and n.get("created_at") and datetime.fromisoformat(n["created_at"]) >= ttl_cutoff
    ]
    # ADR-018：偏好筆記裡明確宣告的「優先：」「語氣：」先用一行講清楚，讓秘書答題時知道
    # 你現在在乎什麼；來源就是下面那些偏好筆記，不另外推斷。
    try:

        summary = profile_summary_line(
            parse_profile_directives(n["body"] for n in reversed(prefs) if n["kind"] == "preference")
        )
    except Exception as exc:  # noqa: BLE001 — 個人檔案解析失敗不該讓脈絡壞掉
        logger.warning("profile summary unavailable: %s", type(exc).__name__)
        summary = ""
    if summary:
        lines.append(f"個人檔案（你宣告的）：{summary}")
        receipt["sections"].append("profile")

    used = 0
    for heading, bucket in (("偏好與決定：", prefs), ("使用者筆記：", plain), ("秘書觀察（可刪除）：", observations)):
        picked = bucket[: max(0, max_notes - used)]
        if not picked:
            continue
        lines.append(heading)
        lines.extend(_fmt_note(n) for n in picked)
        used += len(picked)
    receipt["notes_used"] = used
    if used:
        receipt["sections"].append("notes")
    lines.append("以上為本機記憶區摘要；回答時可引用，但不要當成完整歷史。")

    text = "\n".join(lines)
    if len(text) > limit:
        text = text[: limit - 1].rstrip() + "…"
        receipt["truncated"] = True
    receipt["chars"] = len(text)
    receipt["included"] = bool(receipt["sections"])
    return {"text": text if receipt["included"] else "", "receipt": receipt}


# ---------------------------------------------------------------- ADR-018 宣告式個人檔案（原 core/secretary_profile.py）
#
# 宣告式個人檔案（ADR-018）：你自己說的，不是推測的。
# ADR-012 的 ``preference`` 筆記原本只有一種句型是「真的」：``mute:<X>``（不要提醒 X）
# 會改變行為，其餘全部只是注入對話的文字。秘書因此無從知道你**現在**在乎什麼、
# 希望它怎麼講話。
# 這個模組把偏好筆記裡另外兩種**明確宣告**變成會改變行為的設定：
# - ``優先：<專案>``（``priority:``、``本期優先``）——本期優先專案。這些專案的提案分數加
#   ``priority_boost``（預設 0.2，刻意大於 ADR-017 的習慣加權 0.15：**你說的優先
#   勝過我從活動推出來的主線**）；被冷落的優先專案也因此浮上來。
# - ``語氣：簡潔｜直接｜溫暖``（``tone:brief|direct|warm``）——問候卡、晨報開頭與
#   Telegram ``/today`` 的鼓勵語怎麼講：簡潔＝不講、直接＝一句話、溫暖＝原本的池子。
#   語氣只改措辭，不改任何數字（事實閘照樣生效）。
# 刻意**不做**的事：不從活動、prompt 或任何資料**推斷**你的優先或個性——只認你寫下來
# 的字。稱呼（``display_name``）與安靜時段（``quiet_hours``）已是設定檔的一部分，不在
# 這裡重複第二套來源。



PROFILE_CLAIM_BOUNDARY = (
    "個人檔案只來自你在偏好筆記裡明確寫下的「優先：」與「語氣：」；不從活動或對話推斷。"
    "優先只影響提案排序（加分），語氣只影響問候措辭，都不改任何事實或數字。"
)

TONES: dict[str, str] = {"warm": "溫暖", "brief": "簡潔", "direct": "直接"}
DEFAULT_TONE = "warm"
DEFAULT_PRIORITY_BOOST = 0.2
MAX_PRIORITIES = 8

_PRIORITY_DIRECTIVE = re.compile(
    r"^\s*(?:priority|priorities|優先|本期優先|優先專案)\s*[:：]\s*(.+?)\s*$", re.I
)
_TONE_DIRECTIVE = re.compile(r"^\s*(?:tone|語氣)\s*[:：]\s*(.+?)\s*$", re.I)
_TONE_ALIASES: dict[str, str] = {
    "warm": "warm", "溫暖": "warm", "温暖": "warm", "親切": "warm",
    "brief": "brief", "簡潔": "brief", "精簡": "brief", "簡短": "brief", "short": "brief",
    "direct": "direct", "直接": "direct", "乾脆": "direct", "terse": "direct",
}
_PRIORITY_SPLIT = re.compile(r"[、,，;；/／]+")

HOW_TO_SET = (
    "在對話框或 Telegram 打「偏好：優先：uavMonitor、論文」宣告本期優先專案，"
    "「偏好：語氣：簡潔」（或 直接／溫暖）決定問候怎麼講；刪掉那則偏好筆記就恢復。"
)


def parse_profile_directives(bodies: Iterable[str]) -> dict[str, Any]:
    """從偏好筆記的內容抽出宣告；非指令的行原樣忽略（它們仍會注入對話脈絡）。"""
    priorities: list[str] = []
    seen: set[str] = set()
    tone = DEFAULT_TONE
    tone_declared = False
    ignored: list[str] = []
    for body in bodies:
        for raw_line in str(body or "").splitlines():
            line = raw_line.strip().rstrip("。.")
            if not line:
                continue
            match = _PRIORITY_DIRECTIVE.match(line)
            if match:
                for name in _PRIORITY_SPLIT.split(match.group(1)):
                    name = name.strip().strip("「」\"'`")
                    key = name.casefold()
                    if name and key not in seen and len(priorities) < MAX_PRIORITIES:
                        seen.add(key)
                        priorities.append(name)
                continue
            match = _TONE_DIRECTIVE.match(line)
            if match:
                wanted = match.group(1).strip().casefold()
                resolved = _TONE_ALIASES.get(wanted)
                if resolved is None:
                    ignored.append(f"tone:{match.group(1).strip()}")
                else:
                    tone = resolved       # 後寫的覆蓋先寫的（筆記依時間倒序時由呼叫端決定順序）
                    tone_declared = True
    return {
        "priorities": priorities,
        "tone": tone,
        "tone_label": TONES[tone],
        "tone_declared": tone_declared,
        "ignored": ignored,
    }


def load_profile(*, database: Any | None = None) -> dict[str, Any]:
    """讀全部偏好筆記組成個人檔案；越新的筆記越後套用（語氣以最新宣告為準）。"""
    database = database or get_db()
    with database.session_scope() as session:
        rows = (
            session.query(SecretaryNote.body, SecretaryNote.id)
            .filter(SecretaryNote.kind == "preference")
            .order_by(SecretaryNote.created_at.asc(), SecretaryNote.id.asc())
            .all()
        )
    profile = parse_profile_directives(body for body, _ in rows)
    profile["declared"] = bool(profile["priorities"]) or profile["tone_declared"]
    profile["source"] = "secretary_notes.kind=preference"
    profile["how_to_set"] = HOW_TO_SET
    profile["claim_boundary"] = PROFILE_CLAIM_BOUNDARY
    return profile


def priority_boost_value(cfg: Any | None = None) -> float:
    cfg = cfg or get_config()
    try:
        return min(0.5, max(0.0, float(cfg.get("proactive_secretary.profile.priority_boost", DEFAULT_PRIORITY_BOOST))))
    except (TypeError, ValueError):
        return DEFAULT_PRIORITY_BOOST


def apply_priority_boost(
    signals: list[dict[str, Any]], priorities: list[str], *, boost: float
) -> int:
    """宣告為優先的專案，其**所有**訊號（含 ADR-017 的模式訊號）加分並附理由。

    與習慣加權不同，這裡不跳過模式訊號：一個被冷落的優先專案正是最該浮上來的。
    """
    if not priorities or boost <= 0:
        return 0
    wanted = {p.casefold() for p in priorities}
    boosted = 0
    for signal in signals:
        if str(signal.get("project_key") or "").casefold() not in wanted:
            continue
        signal["score"] = round(min(1.0, float(signal.get("score", 0.0)) + boost), 3)
        signal.setdefault("reasons", []).append("你把這個專案標為本期優先")
        signal["priority_declared"] = True
        boosted += 1
    return boosted


def profile_summary_line(profile: dict[str, Any]) -> str:
    """一行給對話脈絡與介面用；沒有任何宣告就回空字串。"""
    parts: list[str] = []
    if profile.get("priorities"):
        parts.append("本期優先：" + "、".join(profile["priorities"]))
    if profile.get("tone_declared"):
        parts.append(f"語氣：{TONES.get(profile.get('tone', DEFAULT_TONE), '溫暖')}")
    return "／".join(parts)
