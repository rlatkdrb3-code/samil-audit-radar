---
name: audit-radar
description: Use when researching a Korean company's external auditor, auditor tenure, audit opinion history, OpenDART audit disclosures, periodic auditor designation timing, or external auditor appointment events.
---

# Audit Lead Radar

Use this skill to produce a focused audit-market research memo and audit-sales lead recommendation for a configured accounting firm context.

The core question is narrow:

> Is this company a good audit-sales target for the configured firm, why, and what appointment or periodic designation event creates the opening?

Also classify the company into a sales-research case when possible:

- listed company
- large private company or business-report filer candidate
- private external-audit subject found through audit-report filings
- financial company candidate
- limited company candidate
- external-audit threshold candidate requiring financial-statement checks

Use the sample ERP/CRM firm context by default:

- audit-led relationship building
- expansion into tax, deals, internal control, industry, and advisory work where independence permits
- preference for companies with near-term auditor-change timing, strong disclosure/regulatory needs, or verifiable external-audit filings
- optional ERP/CRM signals such as priority accounts, restricted accounts, warm introductions, service lines, and industry focus

## Data Sources

Use public information only:

- OpenDART corporation code list.
- OpenDART "회계감사인의 명칭 및 감사의견" API.
- OpenDART "감사용역체결현황" API when fee/time context is requested.
- OpenDART disclosure search for `외부감사관련` audit reports, including `감사보고서`, `연결감사보고서`, and `감사전재무제표미제출신고서`.
- Public FSC/FSS guidance on external auditor appointment and periodic designation.

Do not claim access to any real firm's internal CRM, independence, audit acceptance, or client systems unless the user explicitly provides that data in the workspace. The public demo uses only OpenDART plus `src/examples/firm_context.sample.json`.

## Commands

From the plugin root:

```bash
python3 scripts/audit_radar.py search 삼성전자
python3 scripts/audit_radar.py report 삼성전자 --years 10 --output audit-radar-report.md
python3 scripts/audit_radar.py recommend 삼성전자 --years 10
python3 scripts/audit_radar.py serve --port 8765
```

The tool reads the API key from `DART_API_KEY`, `OPEN_DART_API_KEY`, `OPENDART_API_KEY`, or `.env.local`. It reads optional firm context from `AUDIT_FIRM_CONTEXT` or `firm_context.local.json`; otherwise it uses `src/examples/firm_context.sample.json`.

## Interpretation Rules

Always label the timing analysis as an estimate. Public DART data does not always reveal whether an auditor was freely appointed, periodically designated, deferred, or designated for another reason.

High-confidence statements:

- Current auditor shown in the latest available annual report.
- Recent auditor names and audit opinions shown in OpenDART annual-report API results.
- Legal/market category from OpenDART `corp_cls`: Y = KOSPI, K = KOSDAQ, N = KONEX, E = other.

Medium-confidence statements:

- Auditor inferred from the submitter of an OpenDART `외부감사관련` audit-report filing.
- Private-company audit history where the structured annual-report API is empty but DART audit-report filings exist.

Lower-confidence statements:

- Whether the current auditor is a freely appointed auditor or a designated auditor.
- Whether a private company is a large non-listed company subject to periodic designation.
- Whether a financial-company or limited-company signal from name/industry code fully determines the legal appointment rule.
- Whether assets, revenue, liabilities, employees, or member-count thresholds are satisfied.
- Exact FSS notification timing for a specific company.
- Whether a missing public filing is a legal non-submission, delayed submission, non-subject year, or naming/API mismatch.
- Whether a firm-context recommendation would pass independence, conflict, quality-control, or internal acceptance review.

## Output Style

For Korean users, answer in Korean.

Lead with:

- firm-context recommendation grade and fit score
- target type and first outreach angle
- current auditor
- consecutive tenure
- estimated next event
- sales case segment and recommended next action
- confidence level
- why the conclusion is limited

Then show the year-by-year audit history table and follow-up checks.

When special filings are present, show them separately from auditor tenure:

- `감사전재무제표미제출신고서`
- audit report submission delay or deadline extension notices
- corrected audit reports
