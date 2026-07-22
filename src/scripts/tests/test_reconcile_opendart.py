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


if __name__ == "__main__":
    unittest.main()
