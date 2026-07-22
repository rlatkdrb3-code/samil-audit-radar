from __future__ import annotations

import csv
import math
import unittest
from collections import Counter
from pathlib import Path


ROOT = Path(__file__).resolve().parents[3]
SNAPSHOT = ROOT / "src" / "examples" / "audit_market_annual_report_snapshot.csv"
GROUPS = (
    "samil_pwc",
    "samjong_kpmg",
    "ey_hanyoung",
    "deloitte_anjin",
    "other_or_unknown",
)


def parse_nonnegative(value: str) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) and number >= 0 else None


class MarketSnapshotTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        with SNAPSHOT.open(encoding="utf-8-sig", newline="") as handle:
            cls.rows = list(csv.DictReader(handle))

    def grouped(self, year: str, field: str) -> tuple[list[int], int]:
        selected = [row for row in self.rows if row["year"] == year]
        values = []
        for row in selected:
            value = 1.0 if field == "company_count" else parse_nonnegative(row[field])
            if value is not None:
                values.append((row["auditor_group"], value))
        totals = [int(sum(value for group, value in values if group == target)) for target in GROUPS]
        return totals, len(values)

    def test_snapshot_has_exact_current_filing_universe(self):
        counts = Counter(row["year"] for row in self.rows)
        self.assertEqual(counts, {"2023": 3234, "2024": 3323, "2025": 3343})
        keys = [(row["year"], row["corp_code"]) for row in self.rows]
        self.assertEqual(len(keys), len(set(keys)))
        self.assertTrue(all(row["source_rcept_no"] for row in self.rows))

    def test_2023_verified_totals(self):
        self.assertEqual(self.grouped("2023", "company_count"), ([451, 365, 230, 162, 2026], 3234))
        self.assertEqual(
            self.grouped("2023", "audit_contract_fee"),
            ([174995991000, 174153584000, 128148293273, 77603700000, 272316897500], 3182),
        )
        self.assertEqual(
            self.grouped("2023", "audit_actual_fee"),
            ([172454295000, 173312515000, 125269586092, 76632100000, 269264245470], 3165),
        )

    def test_2024_verified_totals(self):
        self.assertEqual(self.grouped("2024", "company_count"), ([461, 383, 251, 167, 2061], 3323))
        self.assertEqual(
            self.grouped("2024", "audit_contract_fee"),
            ([188517964000, 160490960000, 140374097000, 81859935000, 265696802500], 3282),
        )
        self.assertEqual(
            self.grouped("2024", "audit_actual_fee"),
            ([185003797000, 158752320000, 138909997000, 80832929250, 262092077800], 3270),
        )
        self.assertEqual(
            self.grouped("2024", "revenue"),
            (
                [
                    868420868049183,
                    1463238271131869,
                    1241198899876122,
                    438918778013089,
                    384391340326273,
                ],
                2876,
            ),
        )

    def test_negative_and_nonfinite_metrics_are_absent(self):
        for field in ("audit_contract_fee", "audit_actual_fee", "revenue"):
            for row in self.rows:
                value = parse_nonnegative(row[field])
                if row[field]:
                    self.assertIsNotNone(value, f"{row['year']} {row['corp_name']} {field}")


if __name__ == "__main__":
    unittest.main()
