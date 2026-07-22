---
name: audit-market-share
description: Analyze, reconcile, explain, or visualize Korean external-audit market share using OpenDART annual-report auditor data. Use when comparing accounting firms, checking period-over-period audited-company counts, reproducing the bundled dataset, or opening the bundled dashboard.
---

# Audit Market Share

Use only publicly disclosed OpenDART annual-report data and state the observation period, population, and metric on every result.

## Workflow

1. Inspect `examples/audit_market_2023_2024_annual_report_all.csv` before answering.
2. Use `scripts/reconcile_opendart.py` when new annual-report extracts must be normalized or combined. Run `--help` first and preserve the source files.
3. Report market share primarily by audited-company count unless the user supplies a validated revenue metric. Never label company-count share as revenue share.
4. Distinguish missing filings, API gaps, and non-subject companies from confirmed zero values.
5. Open `web/index.html` when the user requests the interactive dashboard.
6. Include data limitations and do not infer audit quality, independence, or future appointments from market share.

## Output

Return the covered years and population, accounting-firm ranking, share calculation, year-over-year changes, notable limitations, and paths to the source CSV and dashboard.
