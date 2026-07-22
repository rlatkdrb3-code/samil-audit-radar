# Audit & AX Intelligence

감사인 교체·선임 레이더, 감사시장 점유율 분석, 대화형 Process-to-AX 업무설계를 결합한 Codex 플러그인 및 웹서비스입니다.

공개 데모: https://samil-audit-radar.onrender.com

## 문제 정의

회계법인은 상장사의 외부감사인 선임, 재선임, 교체, 입찰 공고를 제때 확인해야 하지만, 필요한 정보가 OpenDART 정기보고서, 외부감사관련 공시, 회사 IR/공지 페이지에 흩어져 있습니다. 담당자는 회사별 최근 완료 사업연도 공시 감사인, 감사인 이력, 현재 선임기간 원문, 감사용역 보수와 감사시간, 임원 현황, 외부감사인 입찰 또는 선임 공고 여부를 반복적으로 확인해야 합니다.

이 플러그인은 OpenDART 공개 API를 기반으로 상장사별 감사인 관련 정보를 한 화면에 정리합니다. 최신 사업보고서가 존재하지만 구조화 감사인 API가 비어 있으면 최근 3개년만 원문 XML을 확인하고, 최대 기수의 감사의견 표와 감사용역 표가 같은 감사인을 가리킬 때만 공백을 보완합니다. 감사인명이 연속된 사실만으로 3년 선임계약이나 재선임을 추정하지 않으며, 회사 공식 선임공고에서 대상기간이 확인되지 않으면 날짜와 D-day 대신 `현재 선임기간 원문 확인` 상태를 표시합니다. 주기적 지정도 동일 감사인 재임기간이 아니라 자유선임 사업연도를 확인해야 판단합니다. 또한 OpenDART 외부감사관련 공시에서 현재 확인 가능한 외부감사인 입찰, 제안요청, 선임 공고 신호를 함께 보여줍니다.

## 이번 제출 범위

- 상장사 검색
- 최근 완료 사업연도 공시 감사인 확인
- 최근 감사인 이력과 감사의견 확인
- 공개이력상 동일 감사인 연속연차 참고 표시
- 최신 완료연도 감사인 데이터의 신선도와 현재 선임기간 원문 확인 상태 표시
- 주기적 지정 자유선임·지정 구분의 원문 확인 필요성 표시
- 감사용역 보수, 감사시간, 시간당 보수 표시
- 임원 현황과 감사위원회 관련 가능 신호 표시
- OpenDART 외부감사관련 공시 기준 입찰/제안요청/선임 공고 확인
- 확인된 공고의 원문 URL 제공
- 2026-07-23 재검증 스냅샷(2023년 3,234개사·2024년 3,323개사·2025년 3,343개사)의 회계법인별 점유율 비교
- 감사계약 보수·실제수행 보수·피감사회사 공시 매출·수익 기준 보조 분석과 지표별 커버리지 표시
- 연도별 추이, 선택 연도 구성, CSV 업로드와 JSON 내보내기 대시보드
- 한 번에 한 질문씩 진행하는 업무 범위·기본 흐름·예외·통제 인터뷰
- 브라우저 로컬 저장 기반 인터뷰 재개와 사용자 기준본 확정
- 확정 단계와 원문 근거를 연결한 전체 AX 후보·사람 승인 경계 및 MVP 선정 전 4축 평가 대상 초안
- Process-to-AX 보고서 Markdown, 세션 JSON과 별도 Codex 구현 프롬프트 제공

## 제외 범위

- 비공개 제안요청서/RFP 분석
- 회계법인 내부 인력풀 기반 팀 구성 추천
- 내부 CRM, 독립성, 수임 가능성, 품질관리 판단
- 제안서 자동 작성
- 회사 홈페이지 전체 웹 크롤링 기반의 완전한 공고 탐색

공개 공고가 확인되더라도 제안요청서/RFP 원문이 첨부되지 않은 경우가 많습니다. 이 경우 플러그인은 `공고는 확인되지만 RFP는 공개 첨부 없음`으로 표시하고, 공고상 담당 이메일 또는 연락처 확인이 필요하다고 안내합니다.

## 폴더 구조

```text
samil-audit-radar/
├── src/
│   ├── .codex-plugin/plugin.json
│   ├── skills/audit-radar/SKILL.md
│   ├── skills/audit-market-share/SKILL.md
│   ├── skills/map-workflow-ax/SKILL.md
│   ├── skills/build-ax-tool/SKILL.md
│   ├── scripts/audit_radar.py
│   ├── scripts/reconcile_opendart.py
│   ├── web/market_share.html
│   ├── web/process_to_ax.html
│   ├── examples/
│   └── references/
├── README.md
├── requirements.txt
├── Procfile
└── render.yaml
```

`src/`가 Codex 플러그인 루트입니다.

## 실행 방법

OpenDART API 키를 환경변수로 설정합니다.

```bash
export DART_API_KEY="발급받은_키"
```

회사 검색:

```bash
cd src
python3 scripts/audit_radar.py search 삼성전자
```

리포트 생성:

```bash
python3 scripts/audit_radar.py report 삼성전자 --years 10 --output audit-radar-report.md
```

웹서비스 실행:

```bash
python3 scripts/audit_radar.py serve --port 8765
```

실행 후 감사인 교체 레이더는 `/`, 감사시장 점유율 대시보드는 `/market-share`, 대화형 업무 인터뷰는 `/process-to-ax`에서 확인합니다. 점유율 화면의 기본 CSV는 같은 서버의 `/data/audit-market-share.csv`에서 제공합니다.

API 키 없이 화면과 로직을 확인:

```bash
python3 scripts/audit_radar.py demo
```

## 공개 배포

현재 Render에 배포되어 있습니다.

- Public URL: https://samil-audit-radar.onrender.com
- Market share dashboard: https://samil-audit-radar.onrender.com/market-share
- Process-to-AX Architect: https://samil-audit-radar.onrender.com/process-to-ax
- Health check: https://samil-audit-radar.onrender.com/healthz

API 키가 브라우저에 노출되지 않도록 Python 서버가 `DART_API_KEY`를 서버 환경변수로 읽습니다. 정적 호스팅만 제공하는 플랫폼보다 Render, Railway, Fly.io, Cloud Run 같은 서버 실행형 플랫폼에 배포하는 방식을 권장합니다.

Render 배포 예시:

1. 이 폴더를 GitHub 저장소로 push합니다.
2. Render에서 New Web Service를 만들고 저장소를 연결합니다.
3. Start command는 `python3 src/scripts/audit_radar.py serve --host 0.0.0.0`를 사용합니다.
4. Environment Variables에 `DART_API_KEY`를 추가합니다.
5. Health check path는 `/healthz`를 사용합니다.

배포 서버에는 다음 보호장치가 들어 있습니다.

- API 키는 HTML/JavaScript로 내려가지 않고 서버에서만 사용됩니다.
- `/api/search`, `/api/report`에 1시간 응답 캐시가 적용됩니다.
- IP별 1분 30회 간단한 rate limit이 적용됩니다.
- 검색 연도 범위는 4~12년으로 제한됩니다.

## 데이터 범위와 한계

- 현재 공개 배포 버전은 코스피, 코스닥, 코넥스 상장사로 범위를 제한합니다.
- 감사인 이력만으로 현재 3년 선임기간, 재선임, 교체, 지정 여부를 확정하지 않습니다. 공식 선임공고·선임보고의 대상기간을 수집하지 못한 회사에는 미래 날짜와 D-day를 표시하지 않습니다.
- 원문 XML 보완은 표 헤더, 최대 기수, 감사의견·감사용역 표 합의를 모두 통과한 경우에만 사용하며 평문 키워드 일치는 감사인 근거로 사용하지 않습니다.
- 지정감사, 자유선임, 유예, 분산지정 등 세부 사유는 OpenDART 구조화 데이터만으로 확정하기 어렵기 때문에 주기적 지정 D-day를 자동 산출하지 않습니다.
- 추천점수는 확률이나 시장통계가 아닌 공개 신호와 샘플 firm context에 기반한 휴리스틱 참고점수입니다.
- 점유율은 전체 외부감사시장 공식 통계가 아니라 OpenDART 사업보고서 제출회사 스냅샷입니다. 2025년은 현재 비교 가능한 최신 공통 사업연도이며, 일부 비12월 결산회사가 제출한 2026 사업보고서는 시장 전체가 완성되지 않아 점유율 연도로 섞지 않습니다.
- 보수의 외화 표시 또는 공시 자체의 단위·오탈자 충돌이 원문과 후속 비교공시로도 해소되지 않으면 값을 추정하지 않고 해당 보수 지표의 분모에서 제외합니다. 화면과 CSV는 지표별 커버리지, 검증 상태, 근거 접수번호와 제외 사유를 함께 제공합니다.
- 피감사회사 매출·수익은 확보 가능한 공시 계정을 사용하므로 연결/별도와 매출액/이자수익 등이 혼재하는 보조 지표입니다. 연도별 커버리지를 함께 확인해야 합니다.
- 회사 홈페이지 또는 구매/입찰 게시판에만 올라온 공고는 OpenDART만으로 확인되지 않을 수 있습니다.
- 공시 미확인 연도는 미제출, 제출 지연, 외감 비대상, 명칭 불일치, API 조회 한계가 모두 가능하므로 법적 미제출로 단정하지 않습니다.
- 임원 학력은 OpenDART 구조화 필드가 아니며, 주요경력 문자열에 적힌 범위에서만 확인할 수 있습니다.

## 제출 메모

- `logs/`에는 실제 AI 대화 로그 원본을 넣어야 합니다. 사후 편집하거나 요약본을 넣으면 안 됩니다.
- 루트에 해커톤 로그 훅(`.codex/hooks.json`, `.claude/settings.json`, `tools/save_log.py`)을 설치했습니다.
- Codex 앱에서 진행한 대화는 이 훅으로 자동 저장되지 않으므로, 제출 시 앱 대화 전체를 편집 없이 텍스트 형식으로 `logs/`에 추가해야 합니다.
