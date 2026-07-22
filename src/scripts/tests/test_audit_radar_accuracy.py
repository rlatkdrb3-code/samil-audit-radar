from __future__ import annotations

import sys
import unittest
from datetime import date
from pathlib import Path


SCRIPTS = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SCRIPTS))

import audit_radar as radar  # noqa: E402


class AuditRadarAccuracyTests(unittest.TestCase):
    def test_appointment_deadlines_follow_verified_rules(self):
        self.assertEqual(radar.appointment_deadline(2025, 12), date(2025, 2, 14))
        self.assertEqual(radar.appointment_deadline(2026, 12), date(2026, 2, 19))
        self.assertEqual(
            radar.appointment_deadline(2026, 12, audit_committee_required=True),
            date(2025, 12, 31),
        )

    def test_past_event_is_not_returned_as_next(self):
        schedule = [
            {"event_date": "2026-07-22", "title": "과거"},
            {"event_date": "2026-07-23", "title": "오늘"},
            {"event_date": "2026-08-01", "title": "미래"},
        ]
        self.assertEqual(
            radar.next_timeline_event(schedule, as_of=date(2026, 7, 23))["title"],
            "오늘",
        )
        self.assertEqual(
            radar.next_timeline_event(schedule[:1], as_of=date(2026, 7, 23)),
            {},
        )

    def test_three_year_boundary_rolls_forward_from_latest_completed_year(self):
        event = radar.build_three_year_term_event(
            {
                "auditor": "테스트회계법인",
                "start_year": 2023,
                "end_year": 2025,
                "length": 3,
            },
            12,
            date(2026, 7, 23),
            audit_committee_required=True,
        )
        self.assertIsNotNone(event)
        self.assertEqual(event["event_date"], "2028-12-31")
        self.assertIn("계산상 가정", event["detail"])

    def test_periodic_designation_is_not_inferred_from_same_auditor_tenure(self):
        company = {"corp_cls": "Y"}
        history = [
            {"bsns_year": str(year), "adtor": "테스트회계법인", "corp_cls": "Y"}
            for year in range(2025, 2019, -1)
        ]
        analysis = radar.analyze_history(company, history, 2026)
        self.assertEqual(analysis["estimated_event"]["type"], "periodic_cycle_review")
        self.assertIsNone(analysis["estimated_event"]["years_remaining"])
        self.assertEqual(radar.periodic_subject_estimate({}, "N")["status"], "excluded")

    def test_audit_committee_deadline_requires_current_role_evidence(self):
        career_only = [{"nm": "김이사", "ofcps": "사외이사", "chrg_job": "자문", "main_career": "전 감사위원회 위원"}]
        current_role = [{"nm": "박위원", "ofcps": "사외이사", "chrg_job": "감사위원회 위원"}]
        self.assertEqual(radar.audit_committee_evidence(career_only), "")
        self.assertIn("박위원", radar.audit_committee_evidence(current_role))

    def test_coverage_checks_the_entire_requested_window(self):
        coverage = radar.build_coverage_summary(
            [{"bsns_year": "2025"}],
            [{"bsns_year": "2025"}],
            [],
            [],
            [{"business_year": "2025"}],
            years=10,
            current_year=2026,
            external_error=None,
        )
        self.assertEqual(coverage["requested_years"], [str(year) for year in range(2025, 2015, -1)])
        self.assertEqual(coverage["annual_report_gap_years"], [])
        self.assertEqual(coverage["missing_recent_years"], [str(year) for year in range(2024, 2015, -1)])


if __name__ == "__main__":
    unittest.main()
