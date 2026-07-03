---
name: audit-radar
description: Use when researching a Korean listed company's external auditor, auditor tenure, audit opinion history, audit fee/time, executive career signals, OpenDART audit disclosures, periodic auditor designation timing, or external auditor appointment events.
---

# Samil Listed Audit Radar

Use this skill to produce a focused audit-market research memo and audit-sales lead recommendation for the Samil PwC persona or another explicitly configured accounting firm context.

The core question is narrow:

> Is this listed company a good audit-sales target for Samil PwC, why, and what appointment or periodic designation event creates the opening?

Classify the company into a listed-company sales-research case when possible:

- listed company
- financial company candidate
- auditor-change timing candidate
- audit committee or outside-director outreach candidate
- adjacent Tax, Deals, internal-control, or industry-advisory candidate

Use the Samil PwC persona by default:

- audit-led relationship building
- expansion into tax, deals, internal control, industry, and advisory work where independence permits
- preference for listed companies with near-term auditor-change timing, strong disclosure/regulatory needs, verifiable audit-fee/time data, or visible audit committee signals
- optional ERP/CRM signals such as priority accounts, restricted accounts, warm introductions, service lines, and industry focus
- optional personnel and relationship signals such as firm-side industry audit experience, domain knowledge, target-company decision-maker roles, education/career/network tags, revenue trend, and audit-fee trend when lawfully provided

## Data Sources

Use public information only:

- OpenDART corporation code list.
- OpenDART "회계감사인의 명칭 및 감사의견" API.
- OpenDART "감사용역체결현황" API when fee/time context is requested.
- OpenDART "임원 현황" API for executive name, position, registered/full-time status, duty, major career, tenure, and tenure-end fields.
- OpenDART disclosure search for `외부감사관련` filings, including `감사전재무제표미제출신고서`, delayed/extended submissions, and corrected audit-related filings.
- Saramin Job Search API for early hiring signals around internal control, consolidation, tax, M&A, valuation, IPO, and finance-system needs.
- Public FSC/FSS guidance on external auditor appointment and periodic designation.

Do not claim access to Samil PwC internal CRM, independence, audit acceptance, or client systems. The public demo uses only OpenDART plus `src/examples/firm_context.sample.json`. Treat personal information, education, career, and network data as user-provided or lawfully/publicly available business tags; do not infer or expose raw personal details. OpenDART executive data has a `main_career` text field, but no stable structured education field, so education should be described only as a possible text clue requiring original-source review.

## Commands

From the plugin root:

```bash
python3 scripts/audit_radar.py search 삼성전자
python3 scripts/audit_radar.py report 삼성전자 --years 10 --output audit-radar-report.md
python3 scripts/audit_radar.py recommend 삼성전자 --years 10
python3 scripts/audit_radar.py jobs --days 14 --limit 30
python3 scripts/audit_radar.py jobs --company 삼성전자 --seed 내부회계 --seed 이전가격 --format json
python3 scripts/audit_radar.py serve --port 8765
```

The tool reads the DART API key from `DART_API_KEY`, `OPEN_DART_API_KEY`, `OPENDART_API_KEY`, or `.env.local`. It reads the Saramin API key from `SARAMIN_ACCESS_KEY` or `SARAMIN_API_KEY`. It reads optional firm context from `AUDIT_FIRM_CONTEXT` or `firm_context.local.json`; otherwise it uses `src/examples/firm_context.sample.json`.

## Interpretation Rules

Always label the timing analysis as an estimate. Public DART data does not always reveal whether an auditor was freely appointed, periodically designated, deferred, or designated for another reason.

High-confidence statements:

- Current auditor shown in the latest available annual report.
- Recent auditor names and audit opinions shown in OpenDART annual-report API results.
- Legal/market category from OpenDART `corp_cls`: Y = KOSPI, K = KOSDAQ, N = KONEX, E = other.
- Audit service fee/time fields and executive status fields when present in the annual report API response.
- Saramin API search-result metadata that was returned for explicit service-demand seed keywords.

Medium-confidence statements:

- Audit committee, statutory auditor, outside-director, or CEO outreach signal inferred from executive position/duty/major-career text.
- Special filing flags inferred from audit-related disclosure titles.
- Service-demand category inferred from Saramin search seed matches and job-title metadata.

Lower-confidence statements:

- Whether the current auditor is a freely appointed auditor or a designated auditor.
- Whether the current auditor was designated for periodic designation, split designation, penalty designation, or another reason.
- Whether a financial-company signal from name/industry code fully determines the legal appointment rule.
- Exact FSS notification timing for a specific company.
- Whether a missing public filing is a legal non-submission, delayed submission, non-subject year, or naming/API mismatch.
- Whether a firm-context recommendation would pass independence, conflict, quality-control, or internal acceptance review.
- Whether personal relationship tags are lawful, current, complete, or appropriate for outreach without separate internal review.
- Whether a hiring signal represents new service demand rather than replacement hiring or ordinary team growth.

## Output Style

For Korean users, answer in Korean.

Lead with:

- firm-context recommendation grade and fit score
- target type and first outreach angle
- current auditor
- consecutive tenure
- estimated next event
- sales case segment and recommended next action
- audit service fee/time signals when available
- executive and audit-committee candidate signals when available
- confidence level
- why the conclusion is limited

Then show the year-by-year audit history table and follow-up checks.

When special filings are present, show them separately from auditor tenure:

- `감사전재무제표미제출신고서`
- audit report submission delay or deadline extension notices
- corrected audit reports
