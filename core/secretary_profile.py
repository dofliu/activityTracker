"""宣告式個人檔案（ADR-018）：你自己說的，不是推測的。

ADR-012 的 ``preference`` 筆記原本只有一種句型是「真的」：``mute:<X>``（不要提醒 X）
會改變行為，其餘全部只是注入對話的文字。秘書因此無從知道你**現在**在乎什麼、
希望它怎麼講話。

這個模組把偏好筆記裡另外兩種**明確宣告**變成會改變行為的設定：

- ``優先：<專案>``（``priority:``、``本期優先``）——本期優先專案。這些專案的提案分數加
  ``priority_boost``（預設 0.2，刻意大於 ADR-017 的習慣加權 0.15：**你說的優先
  勝過我從活動推出來的主線**）；被冷落的優先專案也因此浮上來。
- ``語氣：簡潔｜直接｜溫暖``（``tone:brief|direct|warm``）——問候卡、晨報開頭與
  Telegram ``/today`` 的鼓勵語怎麼講：簡潔＝不講、直接＝一句話、溫暖＝原本的池子。
  語氣只改措辭，不改任何數字（事實閘照樣生效）。

刻意**不做**的事：不從活動、prompt 或任何資料**推斷**你的優先或個性——只認你寫下來
的字。稱呼（``display_name``）與安靜時段（``quiet_hours``）已是設定檔的一部分，不在
這裡重複第二套來源。
"""

from __future__ import annotations

import re
from typing import Any, Iterable

from core.config import get_config
from core.database import get_db
from core.models import SecretaryNote

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
