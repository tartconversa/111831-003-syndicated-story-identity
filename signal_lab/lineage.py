"""故事谱系（story lineage）与编辑增补的领域模型。

设计原则见 docs/analysis-notes.md：

* 内容（Story / TextVersion）与发布（Publication）分离——文本相似只是线索，
  不代表来源身份相同，也不代表使用许可相同；
* 转载关系是带证据的断言（LineageRelation），状态可为候选 / 确认 / 驳回 / 撤销，
  证据不足时停留在候选，由统计显式暴露而不是静默去重；
* 一切更正、删稿、人工改判都以只追加事件（LineageEvent）表达，
  recorded_at 是断言进入账本的时间（系统时间），valid_on 是事实生效日期（业务时间），
  从而当前统计反映最新判断、历史报告可按截止时间复现。
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Any, Iterable, Literal

# ---------------------------------------------------------------------------
# 许可：使用许可属于“具体来源的某次发布”，永不从转载关系推断。
# ---------------------------------------------------------------------------
LicenseStatus = Literal["licensed", "fair-use", "unknown", "restricted"]

# 关系状态机：
#   candidate  证据不足，仅供人工复核，不参与去重
#   confirmed  证据足以确认转载 / 通讯社供稿关系
#   rejected   经核验不构成转载（相似但独立）
#   retracted  曾确认后被更正或删稿推翻
RelationStatus = Literal["candidate", "confirmed", "rejected", "retracted"]
RelationKind = Literal["republish", "syndication", "derivative"]

PublicationStatus = Literal["active", "withdrawn"]
EntityType = Literal["relation", "publication"]
EventKind = Literal["asserted", "confirmed", "rejected", "retracted", "reopened",
                    "published", "withdrawn"]


def content_hash(text: str) -> str:
    """文本版本的内容指纹，用于复核与版本比对（不承担身份认定职责）。"""
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class Story:
    """一条通讯稿 / 一篇被追踪内容的逻辑实体。"""
    story_id: str
    topic: str
    title: str


@dataclass(frozen=True)
class TextVersion:
    """故事的一个文本版本。原文保留在文本字段，另存哈希供完整性复核。"""
    version_id: str
    story_id: str
    text: str
    language: str
    valid_from: date
    content_sha256: str = ""

    def __post_init__(self) -> None:
        if not self.content_sha256:
            object.__setattr__(self, "content_sha256", content_hash(self.text))


@dataclass(frozen=True)
class Publication:
    """某来源在某时刻对故事某版本的一次发布。

    来源身份（source_id）与使用许可（license）各自独立记录；
    两个发布即便文本完全相同，也可能分属不同来源、持有不同许可。
    """
    publication_id: str
    story_id: str
    source_id: str
    version_id: str
    observed_on: date
    license: LicenseStatus = "unknown"
    has_editorial_addition: bool = False
    addition_summary: str = ""
    # 原始材料定位符（抓取存档 / 快照 URL / 内部对象存储键），供人工回看原文。
    raw_ref: str = ""


@dataclass(frozen=True)
class RelationEvidence:
    """单条证据：相似度线索、人工比对记录、通讯社署名等。"""
    evidence_id: str
    kind: str            # 例如 text-similarity / byline / manual-review / takedown-notice
    detail: str
    recorded_by: str
    recorded_at: datetime
    similarity: float | None = None


@dataclass(frozen=True)
class LineageRelation:
    """两个发布之间“转载 / 供稿”关系的断言。

    一条关系聚合其全部证据与状态历史；状态不是布尔值，证据不足时保持 candidate。
    """
    relation_id: str
    story_id: str
    parent_publication_id: str
    child_publication_id: str
    kind: RelationKind
    status: RelationStatus = "candidate"
    evidence: tuple[RelationEvidence, ...] = ()
    decided_by: str = ""
    decided_at: datetime | None = None
    rationale: str = ""
    updated_at: datetime | None = None


@dataclass(frozen=True)
class LineageEvent:
    """只追加的账本事件。

    recorded_at：事件进入系统的时间（系统时间轴），历史报告按它截断复现；
    valid_on：事实在业务世界的生效日期（业务时间轴），如实际发布日 / 删稿日。
    payload 携带实体字段或状态变更参数，实体本身可由事件流重建。
    """
    event_id: str
    entity_type: EntityType
    entity_id: str
    kind: EventKind
    recorded_at: datetime
    recorded_by: str
    valid_on: date | None = None
    payload: dict[str, Any] = field(default_factory=dict)


# ---------------------------------------------------------------------------
# 事件构造助手：集中校验实体类型，避免调用方拼错 payload。
# ---------------------------------------------------------------------------
def publication_event(event_id, publication_id, kind, recorded_at, recorded_by,
                      valid_on, payload=None) -> LineageEvent:
    return LineageEvent(event_id, "publication", publication_id, kind,
                        recorded_at, recorded_by, valid_on, dict(payload or {}))


def relation_event(event_id, relation_id, kind, recorded_at, recorded_by,
                   valid_on, payload=None) -> LineageEvent:
    return LineageEvent(event_id, "relation", relation_id, kind,
                        recorded_at, recorded_by, valid_on, dict(payload or {}))


class LineageLedger:
    """只追加事件账本：实体状态是事件流的折叠结果，可按任意截止时间重放。"""

    def __init__(self, events: Iterable[LineageEvent] = ()):
        # 防御性拷贝并按系统时间排序，保证重放确定性。
        self._events: tuple[LineageEvent, ...] = tuple(
            sorted(events, key=lambda e: (e.recorded_at, e.event_id))
        )

    @property
    def events(self) -> tuple[LineageEvent, ...]:
        return self._events

    def state_at(self, as_of: datetime | None = None) -> "LineageState":
        publications: dict[str, dict[str, Any]] = {}
        relations: dict[str, dict[str, Any]] = {}
        for event in self._events:
            if as_of is not None and event.recorded_at > as_of:
                break
            if event.entity_type == "publication":
                self._apply_publication(publications, event)
            else:
                self._apply_relation(relations, event)
        return LineageState(publications, relations)

    @staticmethod
    def _apply_publication(store: dict[str, dict[str, Any]], event: LineageEvent) -> None:
        if event.kind == "published":
            store[event.entity_id] = {
                "publication_id": event.entity_id,
                "story_id": event.payload["story_id"],
                "source_id": event.payload["source_id"],
                "version_id": event.payload["version_id"],
                "observed_on": event.valid_on,
                "license": event.payload.get("license", "unknown"),
                "has_editorial_addition": event.payload.get("has_editorial_addition", False),
                "addition_summary": event.payload.get("addition_summary", ""),
                "raw_ref": event.payload.get("raw_ref", ""),
                "status": "active",
                "updated_at": event.recorded_at,
            }
            return
        record = store.get(event.entity_id)
        if record is None:
            raise ValueError(f"事件 {event.event_id} 引用了未知发布 {event.entity_id}")
        if event.kind == "withdrawn":
            record["status"] = "withdrawn"
            record["withdrawn_on"] = event.valid_on
            record["withdrawal_reason"] = event.payload.get("reason", "")
            record["updated_at"] = event.recorded_at

    @staticmethod
    def _apply_relation(store: dict[str, dict[str, Any]], event: LineageEvent) -> None:
        if event.kind == "asserted":
            store[event.entity_id] = {
                "relation_id": event.entity_id,
                "story_id": event.payload["story_id"],
                "parent_publication_id": event.payload["parent_publication_id"],
                "child_publication_id": event.payload["child_publication_id"],
                "kind": event.payload.get("kind", "republish"),
                "status": "candidate",
                "evidence": tuple(event.payload.get("evidence", ())),
                "decided_by": "",
                "decided_at": None,
                "rationale": "",
                "updated_at": event.recorded_at,
            }
            return
        record = store.get(event.entity_id)
        if record is None:
            raise ValueError(f"事件 {event.event_id} 引用了未知关系 {event.entity_id}")
        if event.kind in ("confirmed", "rejected", "retracted", "reopened"):
            new_status = {
                "confirmed": "confirmed",
                "rejected": "rejected",
                "retracted": "retracted",
                "reopened": "candidate",
            }[event.kind]
            record["status"] = new_status
            record["decided_by"] = event.recorded_by
            record["decided_at"] = event.recorded_at
            record["rationale"] = event.payload.get("rationale", "")
            if event.payload.get("evidence"):
                record["evidence"] = record["evidence"] + tuple(event.payload["evidence"])
            record["updated_at"] = event.recorded_at


@dataclass(frozen=True)
class LineageState:
    """事件流折叠后的实体快照（账本在某一系统时间点的视图）。"""
    publications: dict[str, dict[str, Any]]
    relations: dict[str, dict[str, Any]]

    def active_publications(self, story_id: str | None = None) -> list[dict[str, Any]]:
        rows = (p for p in self.publications.values() if p["status"] == "active")
        if story_id is not None:
            rows = (p for p in rows if p["story_id"] == story_id)
        return list(rows)

    def relations_with_status(self, status: RelationStatus) -> list[dict[str, Any]]:
        return [r for r in self.relations.values() if r["status"] == status]
