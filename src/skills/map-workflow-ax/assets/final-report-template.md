# {{PROCESS_NAME}} Process-to-AX 결과 보고서

| 항목 | 값 |
|---|---|
| 세션 | {{SESSION_ID}} |
| 기준 프로세스 리비전 | {{PROCESS_REVISION}} |
| 전체 확정 시각 | {{PROCESS_CONFIRMED_AT}} |
| 보고서 생성 시각 | {{GENERATED_AT}} |
| 분석 상태 | {{ANALYSIS_STATUS}} |

> 이 보고서는 사용자에게 확정받은 업무 흐름을 기준으로 작성했습니다. `미확인`과 `가정`은 확인된 사실이 아닙니다.

## 1. 경영진 요약

{{EXECUTIVE_SUMMARY}}

### 추천 순서

{{PRIORITY_SUMMARY}}

### 핵심 전제와 미확인 사항

{{CRITICAL_UNKNOWNS}}

## 2. 확정된 현재 업무

### 목적과 범위

- 목적: {{PROCESS_PURPOSE}}
- 시작: {{START_TRIGGER}}
- 종료: {{END_CONDITION}}
- 포함 범위: {{SCOPE_IN}}
- 제외 범위: {{SCOPE_OUT}}
- 결과 사용자: {{PROCESS_CUSTOMERS}}

### 참여자·시스템·자료

| 구분 | 내용 |
|---|---|
| 참여 역할 | {{ACTORS}} |
| 사용 시스템 | {{SYSTEMS}} |
| 주요 입력 | {{INPUTS}} |
| 주요 산출물 | {{OUTPUTS}} |

### 기본 흐름

| 단계 | 담당자 | 행동 | 입력 → 출력 | 시스템 | 다음 단계 | 근거 상태 |
|---|---|---|---|---|---|---|
{{AS_IS_STEP_ROWS}}

### 판단·승인·분기·예외

| 출발 단계 | 조건·사건 | 처리 | 목적지·합류점 | 빈도·영향 |
|---|---|---|---|---|
{{BRANCH_ROWS}}

### 확인되지 않은 업무 정보

{{PROCESS_UNKNOWNS}}

## 3. AX 기회 포트폴리오

| 우선순위 | ID | 대상 단계 | 문제 | 해결 유형 | 기대 가치 | 신뢰도 |
|---|---|---|---|---|---|---|
{{OPPORTUNITY_ROWS}}

해결 유형: `human-only` · `ai-assist` · `rules-api` · `rpa` · `dedicated-app` · `hybrid`

## 4. AX 기회 상세

{{#OPPORTUNITIES}}
### {{ID}}. {{TITLE}}

- 우선순위 / 신뢰도: `{{PRIORITY}}` / `{{CONFIDENCE}}`
- 관련 단계: {{STEP_IDS}}
- 확인된 문제: {{PROBLEM}}
- 해결 유형: `{{SOLUTION_TYPE}}`
- 선택 근거: {{RATIONALE}}
- 가치 가설: {{VALUE_HYPOTHESIS}}

#### 근거

{{EVIDENCE}}

#### 자동화 경계

{{AUTOMATION_BOUNDARY}}

#### 제품화 기능

{{PRODUCT_FEATURES}}

#### 필요한 연동

{{INTEGRATIONS}}

#### 위험과 통제

| 위험 | 대응 통제 |
|---|---|
{{RISK_CONTROL_ROWS}}

#### 가정·미확인·검증

- 가정: {{ASSUMPTIONS}}
- 미확인: {{UNKNOWNS}}
- 검증 지표: {{VALIDATION_METRICS}}

{{/OPPORTUNITIES}}

## 5. 권장 To-Be 흐름

{{TO_BE_FLOW}}

### 사람에게 남기는 판단과 승인

{{HUMAN_BOUNDARIES}}

## 6. MVP 제안

- 선택 기회: {{MVP_SELECTED_IDS}}
- 대상 사용자와 문제: {{MVP_TARGET}}

### 포함 범위

{{MVP_SCOPE_IN}}

### 제외 범위

{{MVP_SCOPE_OUT}}

### 사용자 스토리

{{MVP_USER_STORIES}}

### 사람 승인 지점

{{MVP_APPROVAL_POINTS}}

### 성공 지표와 검증 계획

{{MVP_SUCCESS_METRICS}}

## 7. 실행 전 확인사항

{{UNRESOLVED_QUESTIONS}}

## 8. 별도 구현 작업으로 넘기기

- 구현할 기회 하나를 사용자가 명시적으로 선택한다.
- 메인 Process-to-AX 작업에서 `구현 작업 열기 {{OPPORTUNITY_ID}}`라고 요청한다.
- 플러그인은 확정 근거, 자동화 경계, 통제와 완료 조건을 고정한 인계서와 새 Codex 작업 링크를 제공한다.
- 구현 결과는 메인 작업에서 다시 검토하며, 구현 완료가 자동 배포나 효과 입증을 뜻하지는 않는다.

## 부록 A. 근거 추적표

| AX 기회 | 단계 | 사용자 원문·확정 근거 |
|---|---|---|
{{TRACEABILITY_ROWS}}

## 부록 B. 변경 이력

{{CHANGE_LOG}}
