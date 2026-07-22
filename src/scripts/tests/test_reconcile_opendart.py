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


if __name__ == "__main__":
    unittest.main()
