"""从 JSON 夹具加载故事谱系事件。

夹具是外部数据，加载只做字段映射与日期解析，不 eval、不导入其中内容；
调用方应把夹具放在与代码分离的目录，并以路径参数传入。
"""

from __future__ import annotations

import json
from datetime import date, datetime

from .lineage import (
    LineageEvent,
    RelationEvidence,
    LineageLedger,
)


def _parse_datetime(value: str) -> datetime:
    return datetime.fromisoformat(value)


def _parse_date(value: str) -> date:
    return date.fromisoformat(value)


def _evidence(row: dict) -> RelationEvidence:
    return RelationEvidence(
        evidence_id=row["evidence_id"],
        kind=row["kind"],
        detail=row.get("detail", ""),
        recorded_by=row["recorded_by"],
        recorded_at=_parse_datetime(row["recorded_at"]),
        similarity=row.get("similarity"),
    )


def load_events(path) -> list[LineageEvent]:
    with open(path, "r", encoding="utf-8") as handle:
        rows = json.load(handle)

    events: list[LineageEvent] = []
    for row in rows:
        payload = dict(row.get("payload", {}))
        if "evidence" in payload:
            payload["evidence"] = tuple(_evidence(item) for item in payload["evidence"])
        valid_on = _parse_date(row["valid_on"]) if row.get("valid_on") else None
        events.append(LineageEvent(
            event_id=row["event_id"],
            entity_type=row["entity_type"],
            entity_id=row["entity_id"],
            kind=row["kind"],
            recorded_at=_parse_datetime(row["recorded_at"]),
            recorded_by=row["recorded_by"],
            valid_on=valid_on,
            payload=payload,
        ))
    return events


def load_ledger(path) -> LineageLedger:
    return LineageLedger(load_events(path))
