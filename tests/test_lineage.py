import unittest
from datetime import date, datetime, timezone

from signal_lab.lineage import (
    LineageLedger,
    RelationEvidence,
    TextVersion,
    content_hash,
    publication_event,
    relation_event,
)
from signal_lab.lineage_analysis import (
    build_lineage_report,
    summarize,
    summarize_story,
)

UTC = timezone.utc


def dt(value):
    return datetime.fromisoformat(value)


def publish(eid, pub_id, source, *, at, on, version="v1", license="unknown",
            addition=False, story="story-1", raw_ref=""):
    return publication_event(
        eid, pub_id, "published", dt(at), "ingest-bot", date.fromisoformat(on),
        {"story_id": story, "source_id": source, "version_id": version,
         "license": license, "has_editorial_addition": addition, "raw_ref": raw_ref},
    )


def sim_evidence(eid, score, at):
    return RelationEvidence(eid, "text-similarity", f"similarity {score}",
                            "similarity-job", dt(at), similarity=score)


def assert_rel(eid, rid, parent, child, *, at, kind="republish", evidence=()):
    return relation_event(
        eid, rid, "asserted", dt(at), "similarity-job", None,
        {"story_id": "story-1", "parent_publication_id": parent,
         "child_publication_id": child, "kind": kind, "evidence": tuple(evidence)},
    )


def decide(eid, rid, outcome, *, at, by="analyst-1", rationale=""):
    return relation_event(eid, rid, outcome, dt(at), by, None, {"rationale": rationale})


class LineageCountingTests(unittest.TestCase):
    def _base_events(self):
        # 原稿 + 纯转载 + 本地增补 + 独立报道（关系尚未判定）
        return [
            publish("e1", "pub-origin", "global-desk", at="2026-11-01T08:00:00+00:00",
                    on="2026-11-01", license="licensed", raw_ref="raw/original"),
            publish("e2", "pub-repub", "wire-site", at="2026-11-02T08:00:00+00:00",
                    on="2026-11-02"),
            publish("e3", "pub-local", "regional-review", at="2026-11-03T08:00:00+00:00",
                    on="2026-11-03", version="v2", license="fair-use", addition=True),
            publish("e4", "pub-indie", "tribune", at="2026-11-04T08:00:00+00:00",
                    on="2026-11-04", version="v3", license="restricted", addition=True),
        ]

    def test_reach_counts_every_publication_including_reprints(self):
        ledger = LineageLedger(self._base_events())
        summary = summarize_story(ledger.state_at(), "story-1")
        # 没有任何确认关系时，四个来源都计入传播，也都计入独立报道。
        self.assertEqual(summary.reached_sources,
                         ("global-desk", "regional-review", "tribune", "wire-site"))
        self.assertEqual(summary.reached_count, 4)
        self.assertEqual(summary.independent_count, 4)

    def test_confirmed_pure_republish_is_excluded_from_independent_only(self):
        events = self._base_events() + [
            assert_rel("e5", "rel-1", "pub-origin", "pub-repub",
                       at="2026-11-02T12:00:00+00:00", evidence=[sim_evidence("ev1", 0.98, "2026-11-02T12:00:00+00:00")]),
            decide("e6", "rel-1", "confirmed", at="2026-11-03T09:00:00+00:00",
                   rationale="署名与链接齐全"),
        ]
        summary = summarize_story(LineageLedger(events).state_at(), "story-1")
        # 传播范围不缩小。
        self.assertEqual(summary.reached_count, 4)
        # 纯转载来源不再算独立报道，其余三个仍算。
        self.assertNotIn("wire-site", summary.independent_reporting_sources)
        self.assertIn("wire-site", summary.confirmed_republished_sources)
        self.assertEqual(summary.independent_count, 3)
        self.assertEqual(summary.children_by_parent, {"pub-origin": ("pub-repub",)})

    def test_confirmed_relation_with_editorial_addition_stays_independent(self):
        events = self._base_events() + [
            assert_rel("e5", "rel-2", "pub-origin", "pub-local",
                       at="2026-11-03T12:00:00+00:00", kind="derivative"),
            decide("e6", "rel-2", "confirmed", at="2026-11-04T09:00:00+00:00"),
        ]
        summary = summarize_story(LineageLedger(events).state_at(), "story-1")
        self.assertIn("regional-review", summary.reached_sources)
        self.assertIn("regional-review", summary.independent_reporting_sources)
        self.assertNotIn("regional-review", summary.confirmed_republished_sources)

    def test_unconfirmed_similarity_remains_visible_and_is_not_deduplicated(self):
        events = self._base_events() + [
            assert_rel("e5", "rel-3", "pub-origin", "pub-indie",
                       at="2026-11-04T12:00:00+00:00", evidence=[sim_evidence("ev1", 0.54, "2026-11-04T12:00:00+00:00")]),
            # 停在 candidate，不做确认。
        ]
        summary = summarize_story(LineageLedger(events).state_at(), "story-1")
        self.assertEqual(summary.uncertain_pairs, (("pub-origin", "pub-indie"),))
        self.assertEqual(summary.uncertain_count, 1)
        # 候选关系两端都按独立处理，不偷偷去重。
        self.assertIn("tribune", summary.independent_reporting_sources)
        self.assertEqual(summary.independent_count, 4)

    def test_rejected_relation_counts_as_independent(self):
        events = self._base_events() + [
            assert_rel("e5", "rel-3", "pub-origin", "pub-indie",
                       at="2026-11-04T12:00:00+00:00"),
            decide("e6", "rel-3", "rejected", at="2026-11-05T09:00:00+00:00",
                   rationale="独立采访成稿"),
        ]
        summary = summarize_story(LineageLedger(events).state_at(), "story-1")
        self.assertEqual(summary.uncertain_pairs, ())
        self.assertIn("tribune", summary.independent_reporting_sources)
        self.assertEqual(summary.independent_count, 4)


class CorrectionAndBitemporalTests(unittest.TestCase):
    def _events_with_confirmed_reprint(self):
        return [
            publish("e1", "pub-origin", "global-desk", at="2026-11-01T08:00:00+00:00", on="2026-11-01"),
            publish("e2", "pub-repub", "wire-site", at="2026-11-02T08:00:00+00:00", on="2026-11-02"),
            assert_rel("e3", "rel-1", "pub-origin", "pub-repub", at="2026-11-02T12:00:00+00:00"),
            decide("e4", "rel-1", "confirmed", at="2026-11-03T09:00:00+00:00"),
        ]

    def test_retraction_after_correction_restores_independent_count(self):
        events = self._events_with_confirmed_reprint() + [
            decide("e5", "rel-1", "retracted", at="2026-11-08T10:00:00+00:00",
                   rationale="来函更正：该站实为自行编译，非转载"),
        ]
        ledger = LineageLedger(events)
        current = summarize_story(ledger.state_at(), "story-1")
        self.assertEqual(current.independent_count, 2)
        self.assertNotIn("wire-site", current.confirmed_republished_sources)
        # 关系仍保留为 retracted，证据链可回看，而不是被物理删除。
        self.assertEqual(ledger.state_at().relations["rel-1"]["status"], "retracted")

    def test_takedown_withdraws_publication_from_current_totals(self):
        events = self._events_with_confirmed_reprint() + [
            publication_event("e5", "pub-repub", "withdrawn",
                              dt("2026-11-09T08:00:00+00:00"), "editor",
                              date(2026, 11, 9), {"reason": "应要求删稿"}),
        ]
        ledger = LineageLedger(events)
        summary = summarize_story(ledger.state_at(), "story-1")
        self.assertEqual(summary.reached_sources, ("global-desk",))
        self.assertIn("pub-repub", summary.withdrawn_publications)

    def test_historical_report_reproduces_counts_at_its_cutoff(self):
        events = self._events_with_confirmed_reprint() + [
            decide("e5", "rel-1", "retracted", at="2026-11-08T10:00:00+00:00",
                   rationale="更正：非转载"),
        ]
        ledger = LineageLedger(events)

        # 已发布报告的截止时间早于更正事件——必须保持当时的计数。
        as_of_publish = dt("2026-11-05T00:00:00+00:00")
        old_view = summarize_story(ledger.state_at(as_of_publish), "story-1")
        self.assertEqual(old_view.independent_count, 1)
        self.assertIn("wire-site", old_view.confirmed_republished_sources)

        # 当前视图反映更正后的新判断。
        new_view = summarize_story(ledger.state_at(), "story-1")
        self.assertEqual(new_view.independent_count, 2)

        report = build_lineage_report(
            "r-1", "历史快照", ledger.state_at(as_of_publish),
            created_at=as_of_publish,
        )
        self.assertEqual(report.as_of, "2026-11-05T00:00:00+00:00")
        self.assertEqual(report.measures["independent_reporting_sources_total"], 1)


class IdentityAndLicenseTests(unittest.TestCase):
    def test_same_text_different_source_keeps_distinct_identity_and_license(self):
        text = "wire body"
        version = TextVersion("v1", "story-1", text, "en", date(2026, 11, 1))
        self.assertEqual(version.content_sha256, content_hash(text))

        events = [
            publish("e1", "p1", "source-a", at="2026-11-01T08:00:00+00:00",
                    on="2026-11-01", license="licensed", raw_ref="raw/a"),
            publish("e2", "p2", "source-b", at="2026-11-01T09:00:00+00:00",
                    on="2026-11-01", license="unknown", raw_ref="raw/b"),
        ]
        summary = summarize_story(LineageLedger(events).state_at(), "story-1")
        self.assertEqual(summary.reached_count, 2)
        self.assertEqual(summary.licenses_by_source,
                         {"source-a": "licensed", "source-b": "unknown"})

    def test_confirmed_reprint_does_not_infer_shared_license(self):
        events = [
            publish("e1", "p1", "source-a", at="2026-11-01T08:00:00+00:00",
                    on="2026-11-01", license="licensed"),
            publish("e2", "p2", "source-b", at="2026-11-02T08:00:00+00:00",
                    on="2026-11-02", license="unknown"),
            assert_rel("e3", "rel-1", "p1", "p2", at="2026-11-02T12:00:00+00:00"),
            decide("e4", "rel-1", "confirmed", at="2026-11-03T08:00:00+00:00"),
        ]
        summary = summarize_story(LineageLedger(events).state_at(), "story-1")
        # 转载确认只影响“是否独立报道”，许可仍按各自发布记录，保持 unknown。
        self.assertEqual(summary.licenses_by_source["source-b"], "unknown")
        self.assertEqual(summary.licenses_by_source["source-a"], "licensed")


class FixtureLoaderTests(unittest.TestCase):
    def test_loads_demo_fixture_and_produces_expected_split(self):
        from pathlib import Path
        from signal_lab.lineage_io import load_ledger

        path = Path(__file__).resolve().parent.parent / "fixtures" / "lineage-events.json"
        ledger = load_ledger(path)
        stories = summarize(ledger.state_at())
        summary = stories["story-1"]

        self.assertEqual(summary.reached_count, 4)
        # 纯转载 wire-copy-site 被确认且无增补；regional-review 虽确认底本关系但有增补；
        # independent-tribune 的转载断言被驳回。
        self.assertEqual(summary.confirmed_republished_sources, ("wire-copy-site",))
        self.assertEqual(
            summary.independent_reporting_sources,
            ("global-desk", "independent-tribune", "regional-review"),
        )
        # 夹具里没有悬而未决的候选关系。
        self.assertEqual(summary.uncertain_pairs, ())


if __name__ == "__main__":
    unittest.main()
