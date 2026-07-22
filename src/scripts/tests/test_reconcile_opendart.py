import sys
import unittest
from pathlib import Path


SCRIPTS = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SCRIPTS))

import reconcile_opendart as reconcile  # noqa: E402


class ReconcileUniverseTests(unittest.TestCase):
    def test_refresh_removes_stale_rows_from_selected_year(self):
        rows = [
            {"year": "2024", "corp_code": "kept", "source_rcept_no": "old"},
            {"year": "2024", "corp_code": "stale", "source_rcept_no": "old"},
            {"year": "2023", "corp_code": "untouched", "source_rcept_no": "old"},
        ]
        filings = {
            2024: {
                "kept": {
                    "corp_code": "kept",
                    "corp_name": "현재회사",
                    "rcept_no": "new",
                }
            }
        }

        merged, changed = reconcile.merge_universe(rows, filings)

        self.assertEqual(
            [(row["year"], row["corp_code"]) for row in merged],
            [("2024", "kept"), ("2023", "untouched")],
        )
        self.assertIn(("2024", "kept"), changed)

    def test_negative_metric_is_excluded_from_denominator(self):
        rows = [
            {"auditor_group": "samil_pwc", "audit_actual_fee": "100"},
            {"auditor_group": "other_or_unknown", "audit_actual_fee": "-500"},
        ]

        coverage, big4_share = reconcile.metric_summary(rows, "audit_actual_fee")

        self.assertEqual(coverage, 1)
        self.assertEqual(big4_share, 100.0)

    def test_forced_audit_refresh_preserves_revenue_only(self):
        row = {
            "auditor_raw": "과거회계법인",
            "auditor_group": "other_or_unknown",
            "audit_contract_fee": "100000000",
            "audit_actual_fee": "110000000",
            "audit_contract_hours": "1000",
            "audit_actual_hours": "1100",
            "auditor_source": "document.xml",
            "fee_source": "document.xml",
            "warnings": (
                "document_fields_unresolved;"
                "fnlttSinglAcntAll request failed: TimeoutError"
            ),
            "validation_status": "unresolved",
            "revenue": "999000000",
            "revenue_source": "fnlttSinglAcntAll",
        }

        reconcile.clear_audit_fields(row)

        for field in (
            "auditor_raw",
            "audit_contract_fee",
            "audit_actual_fee",
            "audit_contract_hours",
            "audit_actual_hours",
            "auditor_source",
            "fee_source",
        ):
            self.assertEqual(row[field], "")
        self.assertEqual(row["revenue"], "999000000")
        self.assertEqual(row["revenue_source"], "fnlttSinglAcntAll")
        self.assertEqual(
            row["warnings"],
            "fnlttSinglAcntAll request failed: TimeoutError",
        )
        self.assertEqual(row["validation_status"], "pending")

    def test_primary_tables_choose_unique_maximum_term(self):
        document = """
        <TABLE><THEAD><TR><TH>사업연도</TH><TH>감사인</TH><TH>감사계약내역</TH><TH>보수</TH><TH>시간</TH><TH>실제수행내역</TH></TR></THEAD><TBODY>
        <TR><TD>제57기</TD><TD>과거회계법인</TD><TD>감사</TD><TD>90백만원</TD><TD>900</TD><TD>90백만원</TD><TD>900</TD></TR>
        <TR><TD>제58기</TD><TD>현재회계법인</TD><TD>감사</TD><TD>100백만원</TD><TD>1,000</TD><TD>100백만원</TD><TD>1,000</TD></TR>
        </TBODY></TABLE>
        <TABLE><THEAD><TR><TH>사업연도</TH><TH>감사인</TH><TH>감사의견</TH></TR></THEAD><TBODY>
        <TR><TD>제57기</TD><TD>과거회계법인</TD><TD>적정</TD></TR>
        <TR><TD>제58기</TD><TD>현재회계법인</TD><TD>적정</TD></TR>
        </TBODY></TABLE>
        """
        service = reconcile.parse_audit_service_table(document)
        self.assertEqual(service["auditor"], "현재회계법인")
        self.assertEqual(
            reconcile.parse_auditor_from_opinion_table(document),
            "현재회계법인",
        )

    def test_opinion_tables_reject_current_auditor_conflict(self):
        document = """
        <TABLE><THEAD><TR><TH>사업연도</TH><TH>감사인</TH><TH>감사의견</TH></TR></THEAD><TBODY>
        <TR><TD>제58기</TD><TD>한영회계법인</TD><TD>적정</TD></TR>
        </TBODY></TABLE>
        <TABLE><THEAD><TR><TH>사업연도</TH><TH>감사인</TH><TH>감사의견</TH></TR></THEAD><TBODY>
        <TR><TD>제58기</TD><TD>삼정회계법인</TD><TD>적정</TD></TR>
        </TBODY></TABLE>
        """
        self.assertEqual(reconcile.parse_auditor_from_opinion_table(document), "")

    def test_hours_accept_korean_thousands_separators(self):
        self.assertEqual(reconcile.parse_hours("39.012"), 39012)
        self.assertEqual(reconcile.parse_hours("40,370"), 40370)
        self.assertEqual(reconcile.parse_hours("1,200.00"), 1200)

    def test_structured_audit_rows_choose_current_term_and_parse_units(self):
        rows = [
            {
                "bsns_year": "제57기",
                "adtor": "과거회계법인",
                "adt_cntrct_dtls_mendng": "900",
                "adt_cntrct_dtls_time": "9,000",
            },
            {
                "bsns_year": "제58기",
                "adtor": "현재회계법인",
                "adt_cntrct_dtls_mendng": "1,000",
                "adt_cntrct_dtls_time": "10,000",
                "real_exc_dtls_mendng": "1,100백만원",
                "real_exc_dtls_time": "10.500",
                "rcept_no": "20260301000001",
            },
        ]
        parsed = reconcile.parse_audit_service_api_rows(rows)
        self.assertEqual(parsed["auditor"], "현재회계법인")
        self.assertEqual(parsed["contract_fee"], 1_000_000_000)
        self.assertEqual(parsed["actual_fee"], 1_100_000_000)
        self.assertEqual(parsed["contract_hours"], 10_000)
        self.assertEqual(parsed["actual_hours"], 10_500)

    def test_structured_prior_term_rows_are_not_promoted_to_report_year(self):
        rows = [
            {
                "bsns_year": "제58기(전기)",
                "adtor": "과거회계법인",
                "adt_cntrct_dtls_mendng": "100백만원",
            },
            {
                "bsns_year": "제57기(전전기)",
                "adtor": "더과거회계법인",
                "adt_cntrct_dtls_mendng": "90백만원",
            },
        ]

        self.assertIsNone(
            reconcile.parse_audit_service_api_rows(rows, report_year=2025)
        )

    def test_structured_explicit_report_year_is_selected_over_prior_terms(self):
        rows = [
            {
                "bsns_year": "제58기(전기)",
                "adtor": "과거회계법인",
                "adt_cntrct_dtls_mendng": "90백만원",
            },
            {
                "bsns_year": "2025 사업연도",
                "adtor": "현재회계법인",
                "adt_cntrct_dtls_mendng": "100백만원",
            },
        ]

        parsed = reconcile.parse_audit_service_api_rows(rows, report_year=2025)

        self.assertEqual(parsed["auditor"], "현재회계법인")

    def test_document_auditor_requires_matching_period_keys(self):
        document = """
        <TABLE><THEAD><TR><TH>사업연도</TH><TH>감사인</TH><TH>감사계약내역</TH><TH>보수</TH><TH>시간</TH><TH>실제수행내역</TH></TR></THEAD><TBODY>
        <TR><TD>제58기</TD><TD>현재회계법인</TD><TD>감사</TD><TD>100백만원</TD><TD>1,000</TD><TD>100백만원</TD><TD>1,000</TD></TR>
        </TBODY></TABLE>
        <TABLE><THEAD><TR><TH>사업연도</TH><TH>감사인</TH><TH>감사의견</TH></TR></THEAD><TBODY>
        <TR><TD>제57기</TD><TD>현재회계법인</TD><TD>적정</TD></TR>
        </TBODY></TABLE>
        """

        class Client:
            def get_document(self, receipt):
                self.receipt = receipt
                return document

        _, parsed, error = reconcile.reconcile_document(
            Client(),
            {
                "year": "2025",
                "corp_code": "00000001",
                "source_rcept_no": "20260301000001",
            },
        )

        self.assertEqual(error, "")
        self.assertEqual(parsed["period_key"], "term:58")
        self.assertEqual(parsed["auditor"], "")
        self.assertTrue(parsed["auditor_conflict"])
        self.assertIn("auditor_period_conflict", parsed["warnings"])

    def test_document_auditor_is_confirmed_when_both_tables_match(self):
        document = """
        <TABLE><THEAD><TR><TH>사업연도</TH><TH>감사인</TH><TH>감사계약내역</TH><TH>보수</TH><TH>시간</TH><TH>실제수행내역</TH></TR></THEAD><TBODY>
        <TR><TD>제58기(당기)</TD><TD>현재회계법인</TD><TD>감사</TD><TD>100백만원</TD><TD>1,000</TD><TD>100백만원</TD><TD>1,000</TD></TR>
        </TBODY></TABLE>
        <TABLE><THEAD><TR><TH>사업연도</TH><TH>감사인</TH><TH>감사의견</TH></TR></THEAD><TBODY>
        <TR><TD>제58기</TD><TD>현재회계법인</TD><TD>적정</TD></TR>
        </TBODY></TABLE>
        """

        class Client:
            def get_document(self, receipt):
                return document

        _, parsed, error = reconcile.reconcile_document(
            Client(),
            {
                "year": "2025",
                "corp_code": "00000001",
                "source_rcept_no": "20260301000001",
            },
        )

        self.assertEqual(error, "")
        self.assertEqual(parsed["period_key"], "term:58")
        self.assertEqual(parsed["auditor"], "현재회계법인")

    def test_document_matching_prior_period_tables_are_not_current_evidence(self):
        document = """
        <TABLE><THEAD><TR><TH>사업연도</TH><TH>감사인</TH><TH>감사계약내역</TH><TH>보수</TH><TH>시간</TH><TH>실제수행내역</TH></TR></THEAD><TBODY>
        <TR><TD>제57기(전기)</TD><TD>과거회계법인</TD><TD>감사</TD><TD>100백만원</TD><TD>1,000</TD><TD>100백만원</TD><TD>1,000</TD></TR>
        </TBODY></TABLE>
        <TABLE><THEAD><TR><TH>사업연도</TH><TH>감사인</TH><TH>감사의견</TH></TR></THEAD><TBODY>
        <TR><TD>제57기(전기)</TD><TD>과거회계법인</TD><TD>적정</TD></TR>
        </TBODY></TABLE>
        """

        self.assertIsNone(reconcile.parse_audit_service_table(document))
        self.assertEqual(reconcile.parse_auditor_from_opinion_table(document), "")

    def test_document_service_table_alone_does_not_confirm_auditor(self):
        document = """
        <TABLE><THEAD><TR><TH>사업연도</TH><TH>감사인</TH><TH>감사계약내역</TH><TH>보수</TH><TH>시간</TH><TH>실제수행내역</TH></TR></THEAD><TBODY>
        <TR><TD>제58기</TD><TD>단독회계법인</TD><TD>감사</TD><TD>100백만원</TD><TD>1,000</TD><TD>100백만원</TD><TD>1,000</TD></TR>
        </TBODY></TABLE>
        """

        class Client:
            def get_document(self, receipt):
                return document

        _, parsed, error = reconcile.reconcile_document(
            Client(),
            {
                "year": "2025",
                "corp_code": "00000001",
                "source_rcept_no": "20260301000001",
            },
        )

        self.assertEqual(error, "")
        self.assertEqual(parsed["auditor"], "")
        self.assertIn("auditor_cross_table_unverified", parsed["warnings"])

    def test_parallel_row_failure_does_not_abort_the_batch(self):
        rows = [
            {"year": "2025", "corp_code": "ok"},
            {"year": "2025", "corp_code": "bad"},
        ]

        def task(row):
            if row["corp_code"] == "bad":
                raise ValueError("bad row")
            return (row["year"], row["corp_code"]), {"ok": True}, ""

        results = reconcile.run_parallel(task, rows, workers=2)
        self.assertEqual(results[0][1], {"ok": True})
        self.assertIsNone(results[1][1])
        self.assertIn("ValueError", results[1][2])


if __name__ == "__main__":
    unittest.main()
