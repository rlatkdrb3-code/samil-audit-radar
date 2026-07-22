# Audit Market Intelligence

감사인 교체·선임 레이더와 감사시장 점유율 분석을 결합한 Codex 플러그인 및 웹서비스입니다.

공개 데모: https://samil-audit-radar.onrender.com

## 문제 정의

회계법인은 상장사의 외부감사인 선임, 재선임, 교체, 입찰 공고를 제때 확인해야 하지만, 필요한 정보가 OpenDART 정기보고서, 외부감사관련 공시, 회사 IR/공지 페이지에 흩어져 있습니다. 담당자는 회사별 현재 감사인, 감사인 이력, 동일 감사인 연속연차, 감사용역 보수와 감사시간, 임원 현황, 외부감사인 입찰 또는 선임 공고 여부를 반복적으로 확인해야 합니다.

이 플러그인은 OpenDART 공개 API를 기반으로 상장사별 감사인 관련 정보를 한 화면에 정리하고, 감사인 교체 또는 재선임 검토 시점이 얼마나 남았는지 추정합니다. 또한 OpenDART 외부감사관련 공시에서 현재 확인 가능한 외부감사인 입찰, 제안요청, 선임 공고 신호를 함께 보여줍니다.

## 이번 제출 범위

- 상장사 검색
- 현재 감사인 확인
- 최근 감사인 이력과 감사의견 확인
- 동일 감사인 연속연차 계산
- 감사인 교체/재선임 검토 예상시기와 남은 기간 표시
- 감사용역 보수, 감사시간, 시간당 보수 표시
- 임원 현황과 감사위원회 관련 가능 신호 표시
- OpenDART 외부감사관련 공시 기준 입찰/제안요청/선임 공고 확인
- 확인된 공고의 원문 URL 제공
- 2023~2024 회계법인별 피감회사 수 기준 시장점유율 비교
- 감사계약 보수·실제수행 보수·피감사회사 매출 기준 보조 분석
- 연도별 추이, 선택 연도 구성, CSV 업로드와 JSON 내보내기 대시보드

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
│   ├── scripts/audit_radar.py
│   ├── scripts/reconcile_opendart.py
│   ├── web/market_share.html
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

실행 후 감사인 교체 레이더는 `/`, 감사시장 점유율 대시보드는 `/market-share`에서 확인합니다. 점유율 화면의 기본 CSV는 같은 서버의 `/data/audit-market-share.csv`에서 제공합니다.

API 키 없이 화면과 로직을 확인:

```bash
python3 scripts/audit_radar.py demo
```

## 공개 배포

현재 Render에 배포되어 있습니다.

- Public URL: https://samil-audit-radar.onrender.com
- Market share dashboard (v0.2.0 배포 후): https://samil-audit-radar.onrender.com/market-share
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
- 감사인 교체/재선임 일정은 공개 공시와 결산월 기준으로 계산한 추정치입니다.
- 지정감사, 자유선임, 유예, 분산지정 등 세부 사유는 OpenDART 구조화 데이터만으로 확정하기 어렵습니다.
- 회사 홈페이지 또는 구매/입찰 게시판에만 올라온 공고는 OpenDART만으로 확인되지 않을 수 있습니다.
- 공시 미확인 연도는 미제출, 제출 지연, 외감 비대상, 명칭 불일치, API 조회 한계가 모두 가능하므로 법적 미제출로 단정하지 않습니다.
- 임원 학력은 OpenDART 구조화 필드가 아니며, 주요경력 문자열에 적힌 범위에서만 확인할 수 있습니다.

## 제출 메모

- `logs/`에는 실제 AI 대화 로그 원본을 넣어야 합니다. 사후 편집하거나 요약본을 넣으면 안 됩니다.
- 루트에 해커톤 로그 훅(`.codex/hooks.json`, `.claude/settings.json`, `tools/save_log.py`)을 설치했습니다.
- Codex 앱에서 진행한 대화는 이 훅으로 자동 저장되지 않으므로, 제출 시 앱 대화 전체를 편집 없이 텍스트 형식으로 `logs/`에 추가해야 합니다.
