# Samil Audit Radar

AX 해커톤 예선 제출용 Codex 플러그인입니다. 대상 기업은 삼일PwC이며, 공개 자료로 확인 가능한 문제인 `감사인 이력 파악과 주기적 지정/선임 이벤트 사전 모니터링`을 다룹니다.

## 문제 정의

회계법인의 본질적 업무는 감사용역이며, 감사 고객의 선임/지정 주기 변화는 감사·세무·딜·리스크 자문 기회의 출발점이 됩니다. 하지만 기업별 감사인, 감사인 연속연차, 주기적 지정 가능 시점을 확인하려면 DART 공시와 외부감사 제도 기준을 함께 봐야 합니다.

이 플러그인은 OpenDART 공개 API를 사용해 기업별 현재 감사인, 최근 감사인 이력, 동일 감사인 연속연차, 주기적 지정/자유선임 전환 가능성을 추정해 리서치 메모와 로컬 웹 UI로 제공합니다.

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

웹서비스 실행:

```bash
python3 scripts/audit_radar.py serve --port 8765
```

API 키 없이 로직만 확인하려면:

```bash
python3 scripts/audit_radar.py demo
```

## 공개 배포

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

## 제출 메모

- `logs/`에는 실제 AI 대화 로그 원본을 넣어야 합니다. 사후 편집하거나 요약본을 넣으면 안 됩니다.
- 루트에 해커톤 로그 훅(`.codex/hooks.json`, `.claude/settings.json`, `tools/save_log.py`)을 설치했습니다. 이후 이 폴더에서 Codex CLI 또는 Claude Code를 실행하면 새 대화 로그가 `logs/` 아래에 저장됩니다.
- Codex 앱에서 진행한 대화는 이 훅으로 자동 저장되지 않으므로, 제출 시 앱 대화 전체를 편집 없이 텍스트 형식으로 `logs/`에 추가해야 합니다.
