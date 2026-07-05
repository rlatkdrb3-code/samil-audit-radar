---
name: audit-radar
description: Use when checking a Korean listed company's external auditor history, expected auditor replacement or reappointment timing, audit fee/time, executive disclosures, OpenDART audit disclosures, or public external-auditor tender and appointment notices.
---

# Audit Appointment Radar

Use this skill to summarize public external-auditor appointment information for Korean listed companies.

The scope is intentionally narrow:

> For a listed company, show the current auditor, recent auditor history, estimated auditor-change or reappointment review timing, executive disclosure signals, audit service fee/time, and whether an external-auditor tender or appointment notice is publicly identifiable.

Do not expand the answer into audit-sales lead scoring, RFP proposal drafting, internal personnel matching, CRM relationship analysis, independence acceptance, or private accounting-firm data. If the user uploads a private RFP or internal dataset, treat it as separate user-provided context and clearly label that it is outside the public OpenDART-only baseline.

## Data Sources

Use public information only:

- OpenDART corporation code list.
- OpenDART "회계감사인의 명칭 및 감사의견" API.
- OpenDART "감사용역체결현황" API for audit fee/time and hourly-fee context.
- OpenDART "임원 현황" API for executive name, position, registered/full-time status, duty, major career, tenure, and tenure-end fields.
- OpenDART annual-report and regular-disclosure search for latest available business-year coverage.
- OpenDART disclosure search for `외부감사관련` filings, including auditor appointment, auditor change, tender, proposal-request, delayed submission, and corrected audit-related filings.
- Public company IR, notice, procurement, or tender pages only when the user asks for public auditor tender notices beyond OpenDART.
- Public FSC/FSS guidance on external auditor appointment and periodic designation, when explaining the rule basis.

Do not claim access to a law firm's or accounting firm's internal CRM, staff pool, independence database, proposal archive, or private RFP file. OpenDART executive data has a `main_career` text field, but no stable structured education field, so education or network clues must be described only as possible text clues requiring original-source review.

## Commands

From the plugin root:

```bash
python3 scripts/audit_radar.py search 삼성전자
python3 scripts/audit_radar.py report 삼성전자 --years 10 --output audit-radar-report.md
python3 scripts/audit_radar.py serve --port 8765
python3 scripts/audit_radar.py demo
```

The tool reads the DART API key from `DART_API_KEY`, `OPEN_DART_API_KEY`, `OPENDART_API_KEY`, or `.env.local`.

## Interpretation Rules

Always label timing analysis as an estimate. Public DART data does not always reveal whether an auditor was freely appointed, periodically designated, deferred, or designated for another reason.

Strongly supported statements:

- Current auditor shown in the latest available annual report.
- Recent auditor names and audit opinions shown in OpenDART annual-report API results.
- Legal/market category from OpenDART `corp_cls`: Y = KOSPI, K = KOSDAQ, N = KONEX, E = other.
- Audit service fee/time fields and hourly fee when present in the annual report API response.
- Executive status fields when present in the annual report API response.
- Public notice existence when a matching OpenDART filing or company notice URL is found.

Inference-based statements:

- Audit committee, statutory auditor, outside-director, or CEO relevance inferred from executive position/duty/major-career text.
- Special filing flags inferred from audit-related disclosure titles.
- Tender or proposal-request classification inferred from disclosure-title keywords.

Statements that need follow-up:

- Whether the current auditor is freely appointed or designated.
- Whether the current auditor was designated for periodic designation, split designation, penalty designation, or another reason.
- Exact FSS notification timing for a specific company.
- Whether a missing public notice means no tender exists.
- Whether a missing public filing is a legal non-submission, delayed submission, non-subject year, or naming/API mismatch.

## Output Style

For Korean users, answer in Korean.

Lead with:

- 대상 회사
- 현재 감사인
- 최근 감사인 이력
- 교체/재선임 검토 예상시기 and D-day
- 기준 근거 and source labels
- 감사용역 보수, 감사시간, 시간당 보수 when available
- 외부감사인 입찰/선임 공고 확인 여부 and URL when available
- 임원 and 감사위원회-related signals when available
- public-data limitations and follow-up checks

Then show:

- year-by-year audit history table
- chronological appointment/rotation timeline
- audit service fee/time table
- executive disclosure table
- tender or appointment notice table

When a public tender notice is not found, say:

> OpenDART 외부감사관련 공시목록만으로는 공개입찰/제안요청 공고를 확인하지 못했습니다. 회사 홈페이지, IR 공지, 구매/입찰 게시판에 별도 공고가 있을 수 있습니다.

When a public tender notice is found but the proposal request or RFP is not attached, say:

> 공고는 확인되지만 제안요청서/RFP 원문은 공개 첨부되어 있지 않습니다. 공고상 접수처 또는 담당자에게 별도 교부 여부를 확인해야 합니다.
