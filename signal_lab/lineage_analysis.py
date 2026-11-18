"""故事谱系统计。

回答两个不同的问题，刻意不合并成单一“报道数”：

1. 这条内容传到了哪些来源？——reached_sources：每一次发布都计入，
   转载并不缩小传播范围；
2. 哪些来源提供了新的报道信息？——independent_reporting_sources：
   仅统计有独立来源身份且带编辑增补、或未被确认存在转载上游的发布，
   纯转载（已确认上游且无增补）不计入。

证据不足的关系停留在 candidate，统计中以 uncertain_pairs 显式呈现，
其两端发布都按独立处理——宁可不 deduplicate，也不制造虚假的确定结论。
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field

from .lineage import LineageState


@dataclass(frozen=True)
class StoryLineageSummary:
    story_id: str
    reached_sources: tuple[str, ...]
    independent_reporting_sources: tuple[str, ...]
    confirmed_republished_sources: tuple[str, ...]
    uncertain_pairs: tuple[tuple[str, str], ...]
    withdrawn_publications: tuple[str, ...]
    licenses_by_source: dict[str, str]
    children_by_parent: dict[str, tuple[str, ...]]
    # 两个口径的计数，方便报告直接引用。
    reached_count: int = 0
    independent_count: int = 0

    @property
    def uncertain_count(self) -> int:
        return len(self.uncertain_pairs)


@dataclass(frozen=True)
class LineageReport:
    """某一系统时间点上的谱系统计快照，供已发布报告长期复现。"""
    report_id: str
    title: str
    as_of: str
    source_ids: tuple[str, ...]
    stories: dict[str, StoryLineageSummary] = field(default_factory=dict)
    measures: dict[str, float] = field(default_factory=dict)


def _story_publications(state: LineageState, story_id: str):
    return [p for p in state.active_publications(story_id)]


def summarize_story(state: LineageState, story_id: str) -> StoryLineageSummary:
    publications = _story_publications(state, story_id)
    by_id = {p["publication_id"]: p for p in publications}

    confirmed_parents: dict[str, str] = {}
    uncertain_pairs: list[tuple[str, str]] = []
    children_by_parent: dict[str, list[str]] = defaultdict(list)

    for relation in state.relations.values():
        if relation["story_id"] != story_id:
            continue
        parent = relation["parent_publication_id"]
        child = relation["child_publication_id"]
        # 已撤销 / 已驳回的关系不影响当前口径；只有 confirmed 才进入去重判断。
        if relation["status"] == "confirmed":
            # 防御：只统计两端发布当前仍有效的关系。
            if parent in by_id and child in by_id:
                confirmed_parents[child] = parent
                children_by_parent[parent].append(child)
        elif relation["status"] == "candidate":
            if parent in by_id and child in by_id:
                uncertain_pairs.append((parent, child))

    reached = {p["source_id"] for p in publications}

    independent: set[str] = set()
    republished_sources: set[str] = set()
    for publication in publications:
        pub_id = publication["publication_id"]
        pure_republish = pub_id in confirmed_parents and not publication["has_editorial_addition"]
        if pure_republish:
            republished_sources.add(publication["source_id"])
        else:
            # 候选关系不构成转载确认，其下游发布仍按独立报道呈现。
            independent.add(publication["source_id"])

    # 同一来源在一个故事里既纯转载又有独立增补时，独立口径优先（来源确实提供了新信息）。
    independent |= {s for s in republished_sources
                    if any(p["source_id"] == s and p["has_editorial_addition"]
                           for p in publications)}
    republished_sources -= independent

    licenses = {p["source_id"]: p["license"] for p in publications}
    withdrawn = tuple(sorted(
        pid for pid, p in state.publications.items()
        if p["story_id"] == story_id and p["status"] == "withdrawn"
    ))

    return StoryLineageSummary(
        story_id=story_id,
        reached_sources=tuple(sorted(reached)),
        independent_reporting_sources=tuple(sorted(independent)),
        confirmed_republished_sources=tuple(sorted(republished_sources)),
        uncertain_pairs=tuple(sorted(uncertain_pairs)),
        withdrawn_publications=withdrawn,
        licenses_by_source=dict(sorted(licenses.items())),
        children_by_parent={parent: tuple(sorted(children))
                            for parent, children in sorted(children_by_parent.items())},
        reached_count=len(reached),
        independent_count=len(independent),
    )


def summarize(state: LineageState) -> dict[str, StoryLineageSummary]:
    story_ids = {p["story_id"] for p in state.publications.values()}
    return {story_id: summarize_story(state, story_id)
            for story_id in sorted(story_ids)}


def build_lineage_report(report_id, title, state, *, created_at) -> LineageReport:
    stories = summarize(state)
    source_ids = tuple(sorted({
        source
        for summary in stories.values()
        for source in summary.reached_sources
    }))
    measures: dict[str, float] = defaultdict(float)
    for summary in stories.values():
        measures["reached_sources_total"] += summary.reached_count
        measures["independent_reporting_sources_total"] += summary.independent_count
        measures["uncertain_pairs_total"] += summary.uncertain_count
    return LineageReport(
        report_id=report_id,
        title=title,
        as_of=created_at.isoformat() if hasattr(created_at, "isoformat") else str(created_at),
        source_ids=source_ids,
        stories=stories,
        measures=dict(measures),
    )
