"""秘書叢集的兩個定型契約：`Signal` 與 `Proposal`（ADR-024，TODO D8）。

D8 之前，六個訊號收集器回傳 `dict[str, Any]`，`build_action_proposals` 直接對那些 dict
取鍵：少一個鍵是 `KeyError`，多一個鍵沒人發現，要知道「一個訊號到底有哪些欄位」只能把
六個收集器全讀一遍。

現在欄位只定義一次：

- :class:`Signal`——收集器產出、聚合層消費。收集器仍可回傳 dict（它們住在各自的 domain
  模組），由 :meth:`Signal.from_dict` 在**聚合層的入口**一次驗完；少必填欄位就在那裡爆，
  不會拖到排序或呈現階段才出事。
- :class:`Proposal`——聚合層產出。:meth:`Proposal.to_dict` **就是 API 回傳的形狀**，
  鍵一個都不多、一個都不少（有逐鍵比對的契約測試）。

兩個都是 frozen dataclass：提案是唯讀建議（ADR-007），型別本身就該說出這件事。
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from datetime import datetime
from typing import Any, Mapping


@dataclass(frozen=True)
class Signal:
    """一個觀察到的訊號：某個專案的某件事，值得使用者現在看一眼。"""

    signal_type: str
    project_key: str
    subject_ref: str
    title: str
    evidence_ref: str
    score: float
    reasons: tuple[str, ...] = ()
    detail: str = ""
    observed_at: datetime | None = None
    age_days: float = 0.0
    url: str | None = None
    open_loop_refs: tuple[str, ...] = ()
    # 模式提案把已寫進記憶區的工作誌當旁證附上（有才附，沒有不編）
    evidence_extra: tuple[Mapping[str, Any], ...] = ()
    habit_boosted: bool = False
    priority_declared: bool = False
    docs_facts: str = ""
    meeting_followups: tuple[str, ...] = ()
    meeting_note_id: int | None = None

    REQUIRED: tuple[str, ...] = field(
        default=("signal_type", "project_key", "subject_ref", "title", "evidence_ref", "score"),
        init=False,
        repr=False,
        compare=False,
    )

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "Signal":
        """把收集器回傳的 dict 驗成 Signal；缺必填欄位就在這裡失敗，錯誤訊息說得出是哪個收集器。"""
        missing = [key for key in cls.REQUIRED if data.get(key) in (None, "")]
        if missing:
            raise ValueError(
                f"signal from {data.get('signal_type') or 'unknown collector'} is missing: {', '.join(missing)}"
            )
        return cls(
            signal_type=str(data["signal_type"]),
            project_key=str(data["project_key"]),
            subject_ref=str(data["subject_ref"]),
            title=str(data["title"]),
            evidence_ref=str(data["evidence_ref"]),
            score=float(data["score"]),
            reasons=tuple(data.get("reasons") or ()),
            detail=str(data.get("detail") or ""),
            observed_at=data.get("observed_at"),
            age_days=float(data.get("age_days") or 0.0),
            url=data.get("url"),
            open_loop_refs=tuple(data.get("open_loop_refs") or ()),
            evidence_extra=tuple(data.get("evidence_extra") or ()),
            habit_boosted=bool(data.get("habit_boosted")),
            priority_declared=bool(data.get("priority_declared")),
            docs_facts=str(data.get("docs_facts") or ""),
            meeting_followups=tuple(data.get("meeting_followups") or ()),
            meeting_note_id=data.get("meeting_note_id"),
        )

    def with_score(self, score: float, *, habit_boosted: bool = False, priority_declared: bool = False) -> "Signal":
        """加權後的新訊號（frozen：加權不會就地改掉別人手上的那一份）。"""
        return replace(
            self,
            score=float(score),
            habit_boosted=self.habit_boosted or habit_boosted,
            priority_declared=self.priority_declared or priority_declared,
        )


@dataclass(frozen=True)
class Proposal:
    """一張唯讀建議卡；`to_dict()` 就是 `/api/v1/secretary/proposals` 回傳的形狀。"""

    proposal_id: str
    proposal_type: str
    project_key: str
    subject_ref: str
    title: str
    detail: str
    reason: str
    reasons: tuple[str, ...]
    suggested_action: str
    why_now: str
    priority: str
    score: float
    age_days: float
    evidence_refs: tuple[str, ...]
    evidence: tuple[Mapping[str, Any], ...]
    url: str | None = None
    risk_level: str = "L0_READ_ONLY"
    execution_available: bool = False
    # 只有成立時才出現在 JSON 裡的欄位——形狀與 D8 之前逐鍵相同
    habit_boosted: bool = False
    priority_declared: bool = False
    docs_facts: str = ""
    meeting_followups: tuple[str, ...] = ()
    meeting_note_id: int | None = None
    memory_note: str | None = None

    def to_dict(self) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "proposal_id": self.proposal_id,
            "proposal_type": self.proposal_type,
            "project_key": self.project_key,
            "subject_ref": self.subject_ref,
            "title": self.title,
            "detail": self.detail,
            "reason": self.reason,
            "reasons": list(self.reasons),
            "suggested_action": self.suggested_action,
            "why_now": self.why_now,
            "priority": self.priority,
            "risk_level": self.risk_level,
            "execution_available": self.execution_available,
            "url": self.url,
            "age_days": self.age_days,
            "evidence_refs": list(self.evidence_refs),
            "evidence": [dict(item) for item in self.evidence],
            "score": round(float(self.score), 3),
        }
        if self.habit_boosted:
            payload["habit_boosted"] = True
        if self.priority_declared:
            payload["priority_declared"] = True
        if self.docs_facts:
            payload["docs_facts"] = self.docs_facts
        if self.meeting_followups:
            payload["meeting_followups"] = list(self.meeting_followups)
            payload["meeting_note_id"] = self.meeting_note_id
        if self.memory_note:
            payload["memory_note"] = self.memory_note
        return payload
