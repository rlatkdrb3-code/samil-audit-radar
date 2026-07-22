import sys
import tempfile
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
            {"auditor_group": "other_or_unknown", "audit_actual_fee": "nan"},
            {"auditor_group": "other_or_unknown", "audit_actual_fee": "inf"},
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
            "audit_evidence_rcept_no": "20240101000001",
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
            "audit_evidence_rcept_no",
        ):
            self.assertEqual(row[field], "")
        self.assertEqual(row["revenue"], "999000000")
        self.assertEqual(row["revenue_source"], "fnlttSinglAcntAll")
        self.assertEqual(
            row["warnings"],
            "fnlttSinglAcntAll request failed: TimeoutError",
        )
        self.assertEqual(row["validation_status"], "pending")

    def test_verified_override_records_evidence_and_can_clear_foreign_fees(self):
        rows = [
            {
                "year": "2024",
                "corp_code": "00000001",
                "corp_name": "외화회사",
                "audit_contract_fee": "100",
                "audit_actual_fee": "100",
                "audit_contract_hours": "1",
                "audit_actual_hours": "1",
                "warnings": "",
            }
        ]
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "overrides.csv"
            path.write_text(
                "year,corp_code,corp_name,audit_contract_fee,audit_actual_fee,"
                "audit_contract_hours,audit_actual_hours,"
                "audit_evidence_rcept_no,override_status,override_reason\n"
                "2024,00000001,외화회사,__NULL__,__NULL__,__NULL__,2405,"
                "20250422000461,foreign_currency_excluded,CNY_excluded\n",
                encoding="utf-8",
            )
            applied = reconcile.apply_verified_overrides(rows, path)

        self.assertEqual(applied, 1)
        self.assertEqual(rows[0]["audit_contract_fee"], "")
        self.assertEqual(rows[0]["audit_actual_hours"], "2405")
        self.assertEqual(rows[0]["fee_source"], "foreign_currency_excluded")
        self.assertEqual(rows[0]["audit_evidence_rcept_no"], "20250422000461")
        self.assertEqual(rows[0]["audit_metric_override_reason"], "CNY_excluded")
        self.assertEqual(
            rows[0]["audit_metric_override_status"],
            "foreign_currency_excluded",
        )
        self.assertIn("foreign_currency_fee", rows[0]["warnings"])
        self.assertIn("source_verified_override", rows[0]["warnings"])

        reconcile.finalize_validation(rows)
        self.assertEqual(rows[0]["validation_status"], "source_excluded")

    def test_verified_override_rejects_nonfinite_values(self):
        rows = [
            {
                "year": "2024",
                "corp_code": "00000001",
                "corp_name": "테스트회사",
                "warnings": "",
            }
        ]
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "overrides.csv"
            path.write_text(
                "year,corp_code,corp_name,audit_contract_fee,audit_actual_fee,"
                "audit_contract_hours,audit_actual_hours,"
                "audit_evidence_rcept_no,override_status,override_reason\n"
                "2024,00000001,테스트회사,nan,100,1,1,"
                "20250422000461,source_verified,bad_value\n",
                encoding="utf-8",
            )
            with self.assertRaisesRegex(ValueError, "invalid audit_contract_fee"):
                reconcile.apply_verified_overrides(rows, path)

    def test_finalize_validation_rejects_negative_contract_fee(self):
        rows = [
            {
                "auditor_raw": "테스트회계법인",
                "audit_contract_fee": "-1",
                "audit_actual_hours": "-2,058",
                "revenue": "nan",
                "fee_source": "adtServcCnclsSttus",
                "warnings": "",
            }
        ]

        reconcile.finalize_validation(rows)

        self.assertEqual(rows[0]["audit_contract_fee"], "")
        self.assertEqual(rows[0]["audit_actual_hours"], "")
        self.assertEqual(rows[0]["revenue"], "")
        self.assertIn("invalid_audit_contract_fee_excluded", rows[0]["warnings"])
        self.assertIn("invalid_audit_actual_hours_excluded", rows[0]["warnings"])
        self.assertIn("invalid_revenue_excluded", rows[0]["warnings"])
        self.assertEqual(rows[0]["validation_status"], "unresolved")

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
        self.assertIsNone(reconcile.parse_hours("(*1)"))
        self.assertIsNone(reconcile.parse_hours("7일"))
        self.assertEqual(reconcile.parse_hours("719 798 1,633 1,916"), 5_066)
        self.assertEqual(reconcile.parse_hours("1,425 / 796"), 2_221)

    def test_money_honors_table_unit_and_korean_thousands_separators(self):
        self.assertEqual(reconcile.parse_money("65.000", "천원"), (65_000_000, None))
        self.assertEqual(
            reconcile.parse_money("1,197,000", "천원"),
            (1_197_000_000, None),
        )
        self.assertEqual(
            reconcile.parse_money("RMB 1,400,000", ""),
            (None, "foreign_currency_fee"),
        )
        self.assertEqual(reconcile.parse_money("- 주1)", "백만원"), (None, None))
        self.assertEqual(reconcile.parse_money("114 주2)", ""), (114_000_000, None))
        self.assertEqual(reconcile.parse_money("1.57억", ""), (157_000_000, None))
        self.assertEqual(reconcile.parse_money("2 0,000천원", ""), (20_000_000, None))
        self.assertEqual(
            reconcile.parse_money("25,000", "백만원"),
            (25_000_000, "inconsistent_million_unit"),
        )
        self.assertEqual(
            reconcile.parse_money("2.2", "CNY"),
            (None, "foreign_currency_fee"),
        )

    def test_foreign_issuers_require_currency_verification(self):
        self.assertTrue(
            reconcile.requires_currency_verification(
                {"stock_code": "900290", "corp_name": "GRT"}
            )
        )
        self.assertTrue(
            reconcile.requires_currency_verification(
                {"stock_code": "", "corp_name": "CUCKOO INTERNATIONAL BERHAD"}
            )
        )
        self.assertFalse(
            reconcile.requires_currency_verification(
                {"stock_code": "005930", "corp_name": "삼성전자"}
            )
        )

    def test_audit_metric_anomaly_flags_bad_units_and_corrupted_hours(self):
        self.assertTrue(
            reconcile.audit_metric_anomaly(
                {
                    "audit_contract_fee": "1000000",
                    "audit_actual_fee": "1000000",
                    "audit_contract_hours": "9000",
                    "audit_actual_hours": "8000",
                }
            )
        )
        self.assertTrue(
            reconcile.audit_metric_anomaly(
                {
                    "audit_contract_fee": "470000000",
                    "audit_actual_fee": "470000000",
                    "audit_contract_hours": "4700",
                    "audit_actual_hours": "719798",
                }
            )
        )
        self.assertFalse(
            reconcile.audit_metric_anomaly(
                {
                    "audit_contract_fee": "470000000",
                    "audit_actual_fee": "470000000",
                    "audit_contract_hours": "4700",
                    "audit_actual_hours": "4980",
                }
            )
        )

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

    def test_document_foreign_currency_fee_is_detected_for_global_auditor(self):
        document = """
        <P>(단위 : CNY, 시간)</P>
        <TABLE><THEAD><TR><TH>사업연도</TH><TH>감사인</TH><TH>감사계약내역</TH><TH>보수</TH><TH>시간</TH><TH>실제수행내역</TH></TR></THEAD><TBODY>
        <TR><TD>제7기(당기)</TD><TD>KPMG MALAYSIA</TD><TD>감사</TD><TD>2.2</TD><TD>1,000</TD><TD>2.2</TD><TD>900</TD></TR>
        </TBODY></TABLE>
        <TABLE><THEAD><TR><TH>사업연도</TH><TH>감사인</TH><TH>감사의견</TH></TR></THEAD><TBODY>
        <TR><TD>제7기(당기)</TD><TD>KPMG MALAYSIA</TD><TD>적정</TD></TR>
        </TBODY></TABLE>
        """

        parsed = reconcile.parse_audit_service_table(document)

        self.assertEqual(parsed["auditor"], "KPMG MALAYSIA")
        self.assertEqual(parsed["fee_unit"], "CNY")
        self.assertIsNone(parsed["contract_fee"])
        self.assertIn("foreign_currency_fee", parsed["warnings"])
        self.assertEqual(
            reconcile.parse_auditor_from_opinion_table(document),
            "KPMG MALAYSIA",
        )

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
