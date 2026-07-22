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

    def test_three_year_boundary_does_not_roll_forward_without_current_source(self):
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
        self.assertEqual(event["event_date"], "")
        self.assertEqual(event["dday_label"], "원문 확인 필요")
        self.assertEqual(event["date_status"], "source_verification_required")

    def test_unexpired_history_still_requires_an_appointment_source(self):
        event = radar.build_three_year_term_event(
            {
                "auditor": "테스트회계법인",
                "start_year": 2024,
                "end_year": 2025,
                "length": 2,
            },
            12,
            date(2026, 7, 23),
            audit_committee_required=True,
        )
        self.assertIsNotNone(event)
        self.assertEqual(event["event_date"], "")
        self.assertEqual(event["date_status"], "source_verification_required")
        self.assertEqual(event["confidence"], "low")

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
        self.assertEqual(coverage["missing_requested_years"], coverage["missing_recent_years"])

    def test_non_december_fiscal_year_can_include_current_calendar_year(self):
        self.assertEqual(
            radar.latest_completed_business_year(date(2026, 7, 23), 3),
            2026,
        )
        self.assertEqual(
            radar.latest_completed_business_year(date(2026, 7, 23), 12),
            2025,
        )

    def test_ambiguous_period_rows_are_not_arbitrarily_relabelled(self):
        rows = [
            {"bsns_year": "전기", "adtor": "A회계법인"},
            {"bsns_year": "전전기", "adtor": "B회계법인"},
        ]
        self.assertEqual(
            radar.select_current_period_rows(rows, report_year=2025),
            [],
        )

    def test_filing_submitter_is_not_used_as_auditor(self):
        row = radar.filing_to_history_row(
            {
                "period_year": "2025",
                "period_month": "12",
                "flr_nm": "제출인명",
                "corp_cls": "Y",
                "corp_code": "001",
                "corp_name": "테스트",
                "report_nm": "감사보고서 (2025.12)",
                "rcept_no": "20260301000001",
                "rcept_dt": "20260301",
                "rcept_url": "https://example.test",
            }
        )
        self.assertIsNotNone(row)
        self.assertEqual(row["adtor"], "")
        self.assertFalse(row["auditor_verified"])

    def test_stale_latest_auditor_is_explicitly_flagged(self):
        analysis = {
            "status": "ok",
            "latest_business_year": "2023",
            "timeline_verification": {"detail": "기존 설명"},
        }
        radar.attach_coverage_status(
            analysis,
            {"requested_years": ["2025", "2024", "2023"]},
        )
        self.assertEqual(analysis["data_quality_status"], "data_gap")
        self.assertIn("2025년", analysis["data_quality_message"])
        self.assertIn("오래된 감사인", analysis["timeline_verification"]["detail"])


if __name__ == "__main__":
    unittest.main()
