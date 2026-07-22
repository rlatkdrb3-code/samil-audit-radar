from __future__ import annotations

import csv
import json
import math
import re
import unittest
from collections import Counter
from decimal import Decimal, InvalidOperation
from pathlib import Path


ROOT = Path(__file__).resolve().parents[3]
SNAPSHOT = ROOT / "src" / "examples" / "audit_market_annual_report_snapshot.csv"
DASHBOARD = ROOT / "src" / "web" / "market_share.html"
OVERRIDES = ROOT / "src" / "examples" / "audit_market_verified_overrides.csv"
GROUPS = (
    "samil_pwc",
    "samjong_kpmg",
    "ey_hanyoung",
    "deloitte_anjin",
    "other_or_unknown",
)
EXPECTED_YEAR_COUNTS = {"2023": 3234, "2024": 3323, "2025": 3343}
NUMERIC_FIELDS = (
    "audit_contract_fee",
    "audit_actual_fee",
    "audit_contract_hours",
    "audit_actual_hours",
    "revenue",
)
DASHBOARD_METRICS = {
    "company_count": None,
    "audit_contract_fee": "audit_contract_fee",
    "audit_actual_fee": "audit_actual_fee",
    "client_revenue": "revenue",
}
OVERRIDE_FIELDS = {
    "audit_evidence_rcept_no",
    "audit_metric_override_reason",
    "audit_metric_override_status",
}
VALID_OVERRIDE_STATUSES = {
    "source_verified",
    "partial_source_verified",
    "foreign_currency_excluded",
    "source_conflict_excluded",
}
VALIDATION_STATUSES = {
    "api_complete",
    "source_verified",
    "source_partially_verified",
    "source_excluded",
    "unresolved",
}
EXCLUDED_FEE_SOURCES = {
    "foreign_currency_excluded",
    "source_conflict_excluded",
}


def parse_nonnegative(value: str) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) and number >= 0 else None


def parse_nonnegative_decimal(value: str) -> Decimal | None:
    text = str(value or "").strip().replace(",", "")
    if not text:
        return None
    try:
        number = Decimal(text)
    except InvalidOperation:
        return None
    return number if number.is_finite() and number >= 0 else None


def json_number(value: Decimal) -> int | float:
    integral = value.to_integral_value()
    return int(integral) if value == integral else float(value)


class MarketSnapshotTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        with SNAPSHOT.open(encoding="utf-8-sig", newline="") as handle:
            reader = csv.DictReader(handle)
            cls.rows = list(reader)
            cls.fieldnames = set(reader.fieldnames or [])
        with OVERRIDES.open(encoding="utf-8-sig", newline="") as handle:
            cls.override_rows = list(csv.DictReader(handle))
        cls.dashboard_html = DASHBOARD.read_text(encoding="utf-8")

    def grouped(self, year: str, field: str) -> tuple[list[int], int]:
        selected = [row for row in self.rows if row["year"] == year]
        values = []
        for row in selected:
            value = 1.0 if field == "company_count" else parse_nonnegative(row[field])
            if value is not None:
                values.append((row["auditor_group"], value))
        totals = [
            int(sum(value for group, value in values if group == target))
            for target in GROUPS
        ]
        return totals, len(values)

    def expected_validation_status(self, row: dict[str, str]) -> str:
        fee_source = row.get("fee_source", "")
        override_status = row.get("audit_metric_override_status", "")
        if fee_source in EXCLUDED_FEE_SOURCES:
            return "source_excluded"
        if override_status == "partial_source_verified":
            return "source_partially_verified"
        auditor_ok = bool(row.get("auditor_raw", "").strip())
        fee_ok = parse_nonnegative(row.get("audit_contract_fee", "")) is not None
        if auditor_ok and fee_ok:
            sources = (row.get("auditor_source", ""), fee_source)
            return (
                "source_verified"
                if any("document.xml" in source for source in sources)
                else "api_complete"
            )
        return "unresolved"

    def csv_dashboard_summary(self) -> dict[str, object]:
        years = sorted({row["year"] for row in self.rows})
        record_counts = Counter(row["year"] for row in self.rows)
        metrics_by_year: dict[str, dict[str, list[list[int | float]]]] = {}
        for year in years:
            selected = [row for row in self.rows if row["year"] == year]
            year_metrics: dict[str, list[list[int | float]]] = {}
            for metric, field in DASHBOARD_METRICS.items():
                totals = {group: Decimal(0) for group in GROUPS}
                coverage = Counter({group: 0 for group in GROUPS})
                for row in selected:
                    group = row.get("auditor_group", "")
                    if group not in GROUPS:
                        group = "other_or_unknown"
                    value = (
                        Decimal(1)
                        if field is None
                        else parse_nonnegative_decimal(row.get(field, ""))
                    )
                    if value is None:
                        continue
                    totals[group] += value
                    coverage[group] += 1
                denominator = sum(totals.values(), Decimal(0))
                entries: list[list[int | float]] = []
                for group in GROUPS:
                    if denominator == 0:
                        share: int | float = 0
                    else:
                        percentage = float(totals[group]) / float(denominator) * 100
                        share = math.floor(percentage * 100 + 0.5) / 100
                    entries.append(
                        [
                            json_number(totals[group]),
                            share,
                            coverage[group],
                        ]
                    )
                year_metrics[metric] = entries
            metrics_by_year[year] = year_metrics
        return {
            "years": years,
            "recordCounts": {year: record_counts[year] for year in years},
            "totalRecords": len(self.rows),
            "metricsByYear": metrics_by_year,
        }

    def html_default_summary(self) -> dict[str, object]:
        match = re.search(
            r"\bconst\s+DEFAULT_SUMMARY\s*=\s*(\{.*?\})\s*;",
            self.dashboard_html,
            flags=re.DOTALL,
        )
        self.assertIsNotNone(match, "market_share.html has no DEFAULT_SUMMARY JSON")
        object_literal = re.sub(
            r"(?<=[{,])\s*([A-Za-z_][A-Za-z0-9_]*)\s*:",
            r'"\1":',
            match.group(1),
        )
        return json.loads(object_literal)

    def test_snapshot_has_exact_current_filing_universe(self):
        counts = Counter(row["year"] for row in self.rows)
        self.assertEqual(counts, EXPECTED_YEAR_COUNTS)

    def test_snapshot_keys_are_unique(self):
        keys = [(row["year"], row["corp_code"]) for row in self.rows]
        duplicate_keys = [key for key, count in Counter(keys).items() if count > 1]
        self.assertFalse(
            duplicate_keys,
            f"duplicate year/corp_code keys: {duplicate_keys[:10]}",
        )

    def test_snapshot_source_receipts_are_present_and_well_formed(self):
        missing_receipts = [
            (row["year"], row["corp_code"], row["corp_name"])
            for row in self.rows
            if not row.get("source_rcept_no", "").strip()
        ]
        self.assertFalse(
            missing_receipts,
            f"rows missing source receipts: {missing_receipts[:10]}",
        )
        invalid_receipts = [
            (row["year"], row["corp_code"], row.get("source_rcept_no", ""))
            for row in self.rows
            if not re.fullmatch(r"\d{14}", row.get("source_rcept_no", "").strip())
        ]
        self.assertFalse(
            invalid_receipts,
            f"invalid source receipts: {invalid_receipts[:10]}",
        )

    def test_snapshot_metrics_are_finite_and_nonnegative(self):
        invalid = []
        for row in self.rows:
            for field in NUMERIC_FIELDS:
                raw = row.get(field, "").strip()
                if raw and parse_nonnegative_decimal(raw) is None:
                    invalid.append(
                        (row["year"], row["corp_code"], row["corp_name"], field, raw)
                    )
        self.assertFalse(invalid, f"invalid numeric metrics: {invalid[:10]}")

    def test_snapshot_validation_status_matches_provenance(self):
        self.assertTrue(
            OVERRIDE_FIELDS <= self.fieldnames,
            f"snapshot missing provenance fields: {sorted(OVERRIDE_FIELDS - self.fieldnames)}",
        )
        invalid_statuses = [
            (row["year"], row["corp_code"], row.get("validation_status", ""))
            for row in self.rows
            if row.get("validation_status", "") not in VALIDATION_STATUSES
        ]
        self.assertFalse(
            invalid_statuses,
            f"invalid validation statuses: {invalid_statuses[:10]}",
        )
        mismatches = [
            (
                row["year"],
                row["corp_code"],
                row["corp_name"],
                row.get("validation_status", ""),
                self.expected_validation_status(row),
            )
            for row in self.rows
            if row.get("validation_status", "") != self.expected_validation_status(row)
        ]
        self.assertFalse(
            mismatches,
            f"validation/provenance mismatches: {mismatches[:10]}",
        )

    def test_reviewed_overrides_are_materialized_with_provenance(self):
        snapshot_by_key = {(row["year"], row["corp_code"]): row for row in self.rows}
        override_by_key = {
            (row["year"], row["corp_code"]): row for row in self.override_rows
        }
        self.assertEqual(
            len(override_by_key),
            len(self.override_rows),
            "override file has duplicate keys",
        )
        materialized_keys = {
            (row["year"], row["corp_code"])
            for row in self.rows
            if row.get("audit_metric_override_status", "")
        }
        missing_materialized = sorted(set(override_by_key) - materialized_keys)
        unexpected_materialized = sorted(materialized_keys - set(override_by_key))
        self.assertFalse(
            missing_materialized or unexpected_materialized,
            "override key mismatch: "
            f"missing {len(missing_materialized)} {missing_materialized[:10]}; "
            f"unexpected {len(unexpected_materialized)} {unexpected_materialized[:10]}",
        )

        issues = []
        for key, override in override_by_key.items():
            row = snapshot_by_key.get(key)
            if row is None:
                issues.append((*key, "missing snapshot target"))
                continue
            status = override["override_status"]
            if status not in VALID_OVERRIDE_STATUSES:
                issues.append((*key, f"invalid override status {status}"))
                continue
            expected_metrics = {
                field: "" if override[field] == "__NULL__" else override[field]
                for field in (
                    "audit_contract_fee",
                    "audit_actual_fee",
                    "audit_contract_hours",
                    "audit_actual_hours",
                )
            }
            for field, expected in expected_metrics.items():
                if row.get(field, "") != expected:
                    issues.append((*key, f"{field}: {row.get(field, '')} != {expected}"))
            expected_fee_source = {
                "foreign_currency_excluded": "foreign_currency_excluded",
                "source_conflict_excluded": "source_conflict_excluded",
            }.get(status, "document.xml:verified_override")
            expected_validation = {
                "foreign_currency_excluded": "source_excluded",
                "source_conflict_excluded": "source_excluded",
                "partial_source_verified": "source_partially_verified",
                "source_verified": "source_verified",
            }[status]
            expected_warning = {
                "foreign_currency_excluded": "foreign_currency_fee",
                "source_conflict_excluded": "source_disclosed_metric_conflict",
                "partial_source_verified": "source_disclosed_metric_conflict",
            }.get(status)
            warnings = {
                warning
                for warning in re.split(r"[;,]", row.get("warnings", ""))
                if warning
            }
            checks = {
                "corp_name": override["corp_name"],
                "audit_evidence_rcept_no": override["audit_evidence_rcept_no"],
                "audit_metric_override_reason": override["override_reason"],
                "audit_metric_override_status": status,
                "fee_source": expected_fee_source,
                "validation_status": expected_validation,
            }
            for field, expected in checks.items():
                if row.get(field, "") != expected:
                    issues.append((*key, f"{field}: {row.get(field, '')} != {expected}"))
            if "source_verified_override" not in warnings:
                issues.append((*key, "missing source_verified_override warning"))
            if expected_warning and expected_warning not in warnings:
                issues.append((*key, f"missing {expected_warning} warning"))
        self.assertFalse(issues, f"override materialization issues: {issues[:10]}")

    def test_dashboard_default_summary_scope_matches_snapshot(self):
        expected = self.csv_dashboard_summary()
        actual = self.html_default_summary()
        for field in ("years", "recordCounts", "totalRecords"):
            with self.subTest(field=field):
                self.assertEqual(actual.get(field), expected[field])

    def test_dashboard_default_summary_metrics_match_snapshot(self):
        expected = self.csv_dashboard_summary()["metricsByYear"]
        actual = self.html_default_summary().get("metricsByYear", {})
        with self.subTest(dimension="years"):
            self.assertEqual(
                set(actual),
                set(expected),
                "DEFAULT_SUMMARY metric years differ from CSV",
            )
        for year, expected_metrics in expected.items():
            if year not in actual:
                continue
            actual_metrics = actual.get(year, {})
            with self.subTest(year=year, dimension="metric names"):
                self.assertEqual(set(actual_metrics), set(expected_metrics))
            for metric, expected_entries in expected_metrics.items():
                with self.subTest(year=year, metric=metric):
                    self.assertEqual(actual_metrics.get(metric), expected_entries)

    def test_2023_verified_totals(self):
        self.assertEqual(
            self.grouped("2023", "company_count"),
            ([451, 365, 230, 162, 2026], 3234),
        )
        self.assertEqual(
            self.grouped("2023", "audit_contract_fee"),
            (
                [180133470000, 174626284000, 128968623273, 77578665000, 272732745000],
                3167,
            ),
        )
        self.assertEqual(
            self.grouped("2023", "audit_actual_fee"),
            (
                [177367471000, 173727015000, 125620386092, 76607065000, 268029592970],
                3147,
            ),
        )

    def test_2024_verified_totals(self):
        self.assertEqual(
            self.grouped("2024", "company_count"),
            ([461, 383, 251, 167, 2061], 3323),
        )
        self.assertEqual(
            self.grouped("2024", "audit_contract_fee"),
            (
                [189959440000, 164951460000, 141924900000, 82077910000, 268118546000],
                3265,
            ),
        )
        self.assertEqual(
            self.grouped("2024", "audit_actual_fee"),
            (
                [186508670000, 163212820000, 140460800000, 81050910000, 264516521300],
                3252,
            ),
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

    def test_2025_verified_totals(self):
        self.assertEqual(
            self.grouped("2025", "company_count"),
            ([478, 401, 276, 188, 2000], 3343),
        )
        self.assertEqual(
            self.grouped("2025", "audit_contract_fee"),
            (
                [189907170000, 175260200000, 129373000000, 81113500000, 245812000000],
                3262,
            ),
        )
        self.assertEqual(
            self.grouped("2025", "audit_actual_fee"),
            (
                [187461480000, 174079200000, 127664500000, 80417500000, 241443500000],
                3245,
            ),
        )
        self.assertEqual(
            self.grouped("2025", "revenue"),
            (
                [
                    1006182040067297,
                    1539668715439808,
                    1279900491846896,
                    412316201076840,
                    355268009648748,
                ],
                2941,
            ),
        )


if __name__ == "__main__":
    unittest.main()
