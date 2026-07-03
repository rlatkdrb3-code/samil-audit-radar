# Public Sources

This plugin is grounded in public audit disclosure and auditor appointment rules.

## Sample Firm Context

- The public demo uses `src/examples/firm_context.sample.json`.
  - This is a Samil PwC persona context built from public positioning assumptions, not a claim of access to Samil PwC's internal data.
  - The schema is designed to accept real firm-provided fields such as auditor aliases, service lines, industry focus, restricted accounts, priority accounts, warm-introduction signals, firm-side personnel expertise, target-company decision-maker role tags, and relationship edges.
  - Education, career, and network fields are extension slots only. The public demo does not include real personal data or internal relationship data.
- Publicly verifiable signals remain grounded in OpenDART filings and external-auditor appointment rules.

## OpenDART APIs

- OpenDART introduction: https://opendart.fss.or.kr/intro/main.do
  - OpenDART provides DART disclosures through APIs and data files for public reuse.
- Corporation code API: https://opendart.fss.or.kr/guide/detail.do?apiGrpCd=DS001&apiId=2019018
  - Provides the DART corporation code list used for company search.
- Auditor name and audit opinion API: https://opendart.fss.or.kr/guide/detail.do?apiGrpCd=DS002&apiId=2020009
  - Provides auditor name, audit opinion, emphasis matters, key audit matters, and settlement date from periodic reports.
- Audit service contract status API: https://opendart.fss.or.kr/guide/detail.do?apiGrpCd=DS002&apiId=2020010
  - Provides audit service contract fee/time and actual fee/time fields from periodic reports.
- Executive status API: https://opendart.fss.or.kr/guide/detail.do?apiGrpCd=DS002&apiId=2019010
  - Provides executive name, position, registered executive status, full-time status, duty, major career, maximum-shareholder relationship, tenure, and tenure-end fields from periodic reports.
  - It does not provide a stable separate education field; education can only be treated as a text clue when a company includes it in major career text.
- Non-audit service contract status API: https://opendart.fss.or.kr/guide/detail.do?apiGrpCd=DS002&apiId=2020011
  - Provides non-audit service contracts with the statutory auditor from periodic reports.
- OpenDART disclosure search API: https://opendart.fss.or.kr/guide/detail.do?apiGrpCd=DS001&apiId=2019001
  - Supports `pblntf_ty=F` for external-audit-related filings, including audit reports, consolidated audit reports, combined audit reports, accounting-firm business reports, and pre-audit financial statement non-submission notices.
- DART company-by-company search: https://dart.fss.or.kr/dsab001/main.do
  - Public DART search exposes `외부감사관련` categories such as `감사보고서`, `연결감사보고서`, `결합감사보고서`, and `감사전재무제표미제출신고서`.
- External Audit Act Enforcement Decree, audit report submission and public inspection: https://www.law.go.kr/LSW/lumLsLinkPop.do?lspttninfSeq=149589
  - Requires submitted audit reports to be made available for public inspection on internet websites for the statutory period.

## Hiring Signal API

- Saramin API introduction: https://oapi.saramin.co.kr/
  - Saramin provides a developer API for job posting data after API application and approval.
- Saramin Job Search API: https://oapi.saramin.co.kr/guide/job-search
  - Provides `GET https://oapi.saramin.co.kr/job-search` with `access-key`, `keywords`, `stock`, date filters, sorting, and count parameters.
  - The `keywords` parameter searches company name, posting title, job/industry fields, and job description text.
  - The plugin uses this API as an early signal source, not as proof of confirmed advisory demand.

## Periodic Designation and Appointment Rules

- FSC press release on periodic designation: https://www.fsc.go.kr/no010101/84372
  - Explains the periodic designation system as six years of free appointment followed by three years of regulator-designated external audit for listed companies and similar entities.
- External Audit Act Article 10, auditor appointment: https://www.law.go.kr/LSW/lsLawLinkInfo.do?chrClsCd=010202&lsId=001701&lsJoLnkSeq=900643588&print=print
  - Sets appointment deadlines and the rule that listed companies, large non-listed companies, and financial companies appoint the same auditor for three consecutive business years.
  - It also distinguishes appointment bodies such as audit committee, statutory auditor with auditor-selection committee approval, and related governance rules depending on company type.
- FSS/FSC annual guidance summarized by KDI: https://eiec.kdi.re.kr/policy/materialView.do?num=273968
  - Summarizes auditor appointment deadlines, reporting duties, and company-type differences.
- External Audit Act Enforcement Decree, external audit scope: https://www.law.go.kr/lumLsLinkPop.do?chrClsCd=010202&lspttninfSeq=149542
  - Defines external audit target thresholds for companies, including asset, revenue, liability, employee, and member thresholds.
- External Audit Act Enforcement Decree, large non-listed company: https://www.law.go.kr/LSW/lsInfoP.do?lsiSeq=286317
  - Defines the large non-listed company asset threshold, including the separate threshold for business-report filers and public-disclosure-group companies.
