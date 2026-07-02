# Samil Audit Radar

AX 해커톤 예선 제출용 Codex 플러그인입니다. 대상 기업은 삼일PwC이며, 공개 자료로 확인 가능한 문제인 `감사인 이력 파악과 주기적 지정/선임 이벤트 사전 모니터링`을 다룹니다.

공개 데모: https://samil-audit-radar.onrender.com

## 문제 정의

회계법인의 본질적 업무는 감사용역이며, 감사 고객의 선임/지정 주기 변화는 감사·세무·딜·리스크 자문 기회의 출발점이 됩니다. 하지만 기업별 감사인, 감사인 연속연차, 주기적 지정 가능 시점을 확인하려면 DART 공시와 외부감사 제도 기준을 함께 봐야 합니다.

이 플러그인은 OpenDART 공개 API를 사용해 기업별 현재 감사인, 최근 감사인 이력, 동일 감사인 연속연차, 주기적 지정/자유선임 전환 가능성을 추정해 리서치 메모와 로컬 웹 UI로 제공합니다. 정기보고서 주요정보 API를 우선 사용하고, 사업보고서 제출대상이 아닌 비상장 외감대상 회사까지 보완하기 위해 `외부감사관련 > 감사보고서/연결감사보고서` 공시목록도 함께 검색합니다. 또한 상장사, 대형비상장/사업보고서 제출대상 후보, 비상장 외감대상, 금융회사 추정, 유한회사 추정, 외감요건 후보처럼 영업 검토용 세그먼트를 나누고 다음 액션을 제안합니다.

## 폴더 구조

```text
samil-audit-radar/
├── src/
│   ├── .codex-plugin/plugin.json
│   ├── skills/audit-radar/SKILL.md
│   ├── scripts/audit_radar.py
│   ├── examples/sample_audit_history.json
│   └── references/public-sources.md
├── README.md
└── logs/
```

`src/`가 Codex 플러그인 루트입니다.

## 실행 방법

먼저 OpenDART API 키를 환경변수로 설정합니다.

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

회사 검색은 OpenDART 고유번호 목록에서 완전일치, 고유번호, 종목코드, 앞부분 일치, 회사명 포함 여부 순으로 점수를 매깁니다. 예를 들어 `삼성전자`를 검색하면 `삼성전자`가 먼저 나오고, `삼성전자서비스`, `삼성전자판매`처럼 이름에 키워드가 포함된 회사가 후보로 이어집니다.

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
4. Environment Variables에 `DART_API_KEY`를 추가합니다.
5. Health check path는 `/healthz`를 사용합니다.

배포 서버에는 다음 보호장치가 들어 있습니다.

- API 키는 HTML/JavaScript로 내려가지 않고 서버에서만 사용됩니다.
- `/api/search`, `/api/report`에 1시간 응답 캐시가 적용됩니다.
- IP별 1분 30회 간단한 rate limit이 적용됩니다.
- 검색 연도 범위는 4~12년으로 제한됩니다.

## 데이터 범위와 한계

- 상장사와 사업보고서 제출대상 회사는 OpenDART 정기보고서 주요정보 API에서 감사인, 감사의견, 핵심감사사항을 구조화해 조회합니다.
- 그 외 비상장 외감대상 회사는 OpenDART 공시검색의 `외부감사관련` 감사보고서 목록을 보조 소스로 사용합니다. 이 경우 감사인은 공시 제출인 기준으로 추정하며, 감사의견과 세부 내용은 원문 확인이 필요합니다.
- `감사전재무제표미제출신고서`, 제출 지연/연장, 정정 공시처럼 미제출·지연제출 가능성을 시사하는 항목은 별도 특이사항으로 표시합니다.
- 영업 케이스 분류는 OpenDART 법인구분, 정기보고서 API 존재 여부, 외부감사관련 감사보고서 공시, 회사명·업종코드 신호를 조합한 추정입니다. 대형비상장 여부, 금융회사 여부, 유한회사 외감요건, 자산·매출·부채·종업원 기준 충족 여부는 원문 및 추가 자료로 확인해야 합니다.
- 공시 미확인 연도는 미제출, 제출 지연, 외감 비대상, 명칭 불일치, API 조회 한계가 모두 가능하므로 법적 미제출로 단정하지 않습니다.

## 제출 메모

- `logs/`에는 실제 AI 대화 로그 원본을 넣어야 합니다. 사후 편집하거나 요약본을 넣으면 안 됩니다.
- 루트에 해커톤 로그 훅(`.codex/hooks.json`, `.claude/settings.json`, `tools/save_log.py`)을 설치했습니다. 이후 이 폴더에서 Codex CLI 또는 Claude Code를 실행하면 새 대화 로그가 `logs/` 아래에 저장됩니다.
- Codex 앱에서 진행한 대화는 이 훅으로 자동 저장되지 않으므로, 제출 시 앱 대화 전체를 편집 없이 텍스트 형식으로 `logs/`에 추가해야 합니다.
