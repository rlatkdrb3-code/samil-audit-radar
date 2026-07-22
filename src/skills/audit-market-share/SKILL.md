---
name: audit-market-share
description: Analyze, reconcile, explain, or visualize Korean external-audit market share using OpenDART annual-report auditor data. Use when comparing accounting firms, checking period-over-period audited-company counts, reproducing the bundled dataset, or opening the bundled dashboard.
---

# Audit Market Share

Use only publicly disclosed OpenDART annual-report data and state the observation period, population, and metric on every result.

## Workflow

1. Inspect `examples/audit_market_annual_report_snapshot.csv`, including `validation_status`, source fields, warnings, and metric coverage, before answering.
2. Inspect `examples/audit_market_verified_overrides.csv` before reproducing totals. Its rows are keyed by year and DART corporation code and retain the evidence receipt and reason for a correction or exclusion.
3. Use `scripts/reconcile_opendart.py` when new annual-report extracts must be normalized or combined. Run `--help` first, preserve the source files, and apply reviewed overrides only after API and source-document reconciliation.
4. Never guess through an internally conflicting disclosure unit. Exclude the affected fee metric with its evidence receipt until a later comparative disclosure resolves it.
5. Report market share primarily by audited-company count. Treat audit-fee and client revenue/income shares as supplemental metrics with their own coverage and denominator; never relabel one metric as another.
6. Distinguish missing filings, API gaps, foreign-currency exclusions, source conflicts, and non-subject companies from confirmed zero values.
7. Keep the dashboard's annual-report-filer population separate from FSS whole-market benchmarks and state the benchmark year.
8. Open `web/market_share.html` when the user requests the interactive dashboard.
9. Include data limitations and do not infer audit quality, independence, or future appointments from market share.

## Output

Return the covered years and population, accounting-firm ranking, share calculation, year-over-year changes, notable limitations, and paths to the source CSV and dashboard.
