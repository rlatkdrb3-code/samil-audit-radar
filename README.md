# Samil Listed Audit Radar

AX 해커톤 예선 제출용 Codex 플러그인입니다. 공개 자료로 확인 가능한 문제인 `상장사 감사인 이력 파악과 주기적 지정/선임 이벤트 사전 모니터링`을 바탕으로, 삼일PwC 페르소나의 감사영업 대상 추천 시스템을 구현합니다.

공개 데모: https://samil-audit-radar.onrender.com

## 문제 정의

회계법인의 본질적 업무는 감사용역이며, 감사 고객의 선임/지정 주기 변화는 감사·세무·딜·리스크 자문 기회의 출발점이 됩니다. 상장사는 사업보고서와 OpenDART 주요정보 API의 구조화 수준이 높아, 기업별 감사인, 감사인 연속연차, 감사보수·감사시간, 임원 주요경력, 주기적 지정 가능 시점을 공개 자료로 비교적 안정적으로 확인할 수 있습니다.

이 플러그인은 OpenDART 공개 API를 사용해 상장사별 현재 감사인, 최근 감사인 이력, 동일 감사인 연속연차, 감사용역 보수·시간, 임원 주요경력, 주기적 지정/자유선임 전환 가능성을 추정해 리서치 메모와 웹 UI로 제공합니다. 기본 firm context는 삼일PwC 페르소나이며, 회사별 영업 적합도 점수와 추천등급, 첫 컨택 각도, 제안 서비스, 다음 확인사항을 제시합니다. 실제 서비스에서는 회계법인의 ERP/CRM 내부 데이터로 담당 인력의 업종 감사경험, 도메인 지식, 독립성 제한, 기존 관계, 감사위원회·감사·사외이사 접점 신호를 추가할 수 있습니다. 공개 데모에는 실제 삼일 내부 데이터나 개인정보가 포함되지 않습니다.

## 폴더 구조

```text
samil-audit-radar/
├── src/
│   ├── .codex-plugin/plugin.json
│   ├── skills/audit-radar/SKILL.md
│   ├── scripts/audit_radar.py
│   ├── examples/firm_context.sample.json
│   ├── examples/sample_audit_history.json
│   └── references/public-sources.md
├── README.md
└── logs/
```

`src/`가 Codex 플러그인 루트입니다.

## 실행 방법

먼저 OpenDART API 키를 환경변수로 설정합니다. 채용공고 신호까지 쓰려면 사람인 API access-key도 함께 설정합니다.

```bash
export DART_API_KEY="발급받은_키"
export SARAMIN_ACCESS_KEY="사람인_access_key"
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

삼일PwC 페르소나 기준으로 검색 후보를 랭킹:

```bash
python3 scripts/audit_radar.py recommend 삼성전자 --years 10
```

사람인 채용공고에서 삼일 서비스 수요 신호 추출:

```bash
python3 scripts/audit_radar.py jobs --days 14 --limit 30
python3 scripts/audit_radar.py jobs --company 삼성전자 --seed 내부회계 --seed 이전가격 --format json
```

회사 검색은 OpenDART 고유번호 목록에서 종목코드가 있는 상장사 후보만 남긴 뒤, 완전일치, 고유번호, 종목코드, 앞부분 일치, 회사명 포함 여부 순으로 점수를 매깁니다. 예를 들어 `삼성전자`를 검색하면 `삼성전자`가 먼저 나오고, 이름에 키워드가 포함된 상장사 후보가 이어집니다.

채용공고 검색은 사람인 Job Search API를 사용합니다. 사람인 API 사이트에서 이용 신청 및 승인 후 발급받은 `access-key`를 `SARAMIN_ACCESS_KEY`로 설정합니다. 기본 seed는 `내부회계`, `연결결산`, `K-IFRS`, `DART`, `XBRL`, `이전가격`, `국제조세`, `세무조사`, `M&A`, `Valuation`, `FDD`, `IPO`, `SAP`, `ERP`입니다. 사람인 API의 `keywords` 검색은 기업명, 공고명, 업직종, 직무내용을 대상으로 하므로, 이 플러그인은 seed별 검색 결과를 합쳐 Assurance/Risk, Tax, Deals, Capital Markets, Finance Transformation 수요 신호로 점수화합니다.

Firm context를 바꾸려면 `src/examples/firm_context.sample.json`을 참고해 루트에 `firm_context.local.json`을 만들거나, `AUDIT_FIRM_CONTEXT` 환경변수에 JSON 경로를 지정합니다. 기본값은 삼일PwC 페르소나입니다. 이 파일에는 회계법인명, 감사인 alias, 산업 포커스, 서비스 라인, ERP/CRM상 우선 계정·제한 계정·warm introduction 신호, 내부 인력의 업종 전문성, 감사대상 회사 의사결정 후보군과의 관계 edge를 넣을 수 있습니다. `firm_context.local.json`은 커밋되지 않도록 `.gitignore`에 포함되어 있습니다.

웹서비스 실행:

```bash
python3 scripts/audit_radar.py serve --port 8765
```

API 키 없이 로직만 확인하려면:

```bash
python3 scripts/audit_radar.py demo
```

## 공개 배포

현재 Render에 배포되어 있습니다.

- Public URL: https://samil-audit-radar.onrender.com
- Health check: https://samil-audit-radar.onrender.com/healthz

GitHub Pages처럼 정적 호스팅만 제공하는 곳에는 실제 OpenDART 검색 기능을 안전하게 배포하기 어렵습니다. API 키가 브라우저에 노출될 수 있기 때문입니다. 이 프로젝트는 Python 서버가 `DART_API_KEY`를 서버 환경변수로 읽도록 되어 있으므로 Render, Railway, Fly.io, Cloud Run 같은 서버 실행형 플랫폼에 배포하는 방식을 권장합니다.

Render 배포 예시:

1. 이 폴더를 GitHub 저장소로 push합니다.
2. Render에서 New Web Service를 만들고 저장소를 연결합니다.
3. Start command는 `python3 src/scripts/audit_radar.py serve --host 0.0.0.0`를 사용합니다.
4. Environment Variables에 `DART_API_KEY`와, 채용공고 기능을 쓰려면 `SARAMIN_ACCESS_KEY`를 추가합니다.
5. Health check path는 `/healthz`를 사용합니다.

배포 서버에는 다음 보호장치가 들어 있습니다.

- API 키는 HTML/JavaScript로 내려가지 않고 서버에서만 사용됩니다.
- `/api/search`, `/api/report`에 1시간 응답 캐시가 적용됩니다.
- IP별 1분 30회 간단한 rate limit이 적용됩니다.
- 검색 연도 범위는 4~12년으로 제한됩니다.

## 데이터 범위와 한계

- 현재 공개 배포 버전은 코스피·코스닥·코넥스 상장사로 범위를 제한합니다.
- 상장사는 OpenDART 정기보고서 주요정보 API에서 감사인, 감사의견, 핵심감사사항, 감사용역 보수·시간, 임원 현황의 주요경력을 구조화해 조회합니다.
- `감사전재무제표미제출신고서`, 제출 지연/연장, 정정 공시처럼 미제출·지연제출 가능성을 시사하는 항목은 별도 특이사항으로 표시합니다.
- 임원 학력은 OpenDART 임원 현황의 별도 구조화 필드가 아닙니다. 학력이나 경력 단서는 회사가 `주요경력` 문자열에 기재한 범위에서만 확인하며, 실제 영업 활용 전 원문과 개인정보 처리 기준을 재검토해야 합니다.
- 영업 케이스 분류는 OpenDART 법인구분, 정기보고서 API 존재 여부, 회사명·업종코드 신호를 조합한 추정입니다. 금융회사 여부, 지정감사 사유, 유예·분산지정 여부는 원문 및 추가 자료로 확인해야 합니다.
- 추천등급은 설정된 firm context를 기준으로 타이밍, 세그먼트 적합도, 공개 검증성, 부가자문 확장성, ERP/CRM 맥락, 인력·관계 커버리지, 제약 조정을 점수화한 영업 리서치 신호입니다. 감사 수임 가능성, 독립성, 이해상충, 품질관리 판단은 별도 내부 검토가 필요합니다.
- 개인 인적사항, 학력, 이력, 네트워크 정보는 합법적으로 보유했거나 공개·동의된 업무 관련 태그로만 사용해야 하며, 공개 데모와 제출물에는 실제 개인정보나 삼일 내부 데이터를 포함하지 않습니다.
- 공시 미확인 연도는 미제출, 제출 지연, 외감 비대상, 명칭 불일치, API 조회 한계가 모두 가능하므로 법적 미제출로 단정하지 않습니다.

## 제출 메모

- `logs/`에는 실제 AI 대화 로그 원본을 넣어야 합니다. 사후 편집하거나 요약본을 넣으면 안 됩니다.
- 루트에 해커톤 로그 훅(`.codex/hooks.json`, `.claude/settings.json`, `tools/save_log.py`)을 설치했습니다. 이후 이 폴더에서 Codex CLI 또는 Claude Code를 실행하면 새 대화 로그가 `logs/` 아래에 저장됩니다.
- Codex 앱에서 진행한 대화는 이 훅으로 자동 저장되지 않으므로, 제출 시 앱 대화 전체를 편집 없이 텍스트 형식으로 `logs/`에 추가해야 합니다.
