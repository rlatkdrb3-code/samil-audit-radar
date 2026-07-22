---
name: map-workflow-ax
description: Conduct a stateful, one-question-at-a-time interview to turn an informal work description into a user-confirmed process map, identify evidence-grounded AI, API, RPA, dedicated-app, human-only, and hybrid AX opportunities, and hand a selected opportunity to a separate Codex app implementation task. Use when a user wants to document or resume a workflow, discover automation or productization opportunities, produce a Process-to-AX report, open an isolated AX tool-building task, or review its returned result.
---

# Map Workflow to AX

Build the current process with the user before proposing its future. Persist every turn, keep facts distinct from inference, and make every AX opportunity traceable to a confirmed workflow step.

## Set up

1. Resolve this skill's directory as `SKILL_ROOT` and the user's current project as `PROJECT_ROOT`.
2. Read [references/interview-protocol.md](references/interview-protocol.md) completely before starting or resuming an interview.
3. Use `PROJECT_ROOT/.ax-workspace/` as the default session root. Do not place session data inside the installed skill.
4. Use `scripts/session_manager.py` for all session creation, process mutation, confirmation, status, analysis finalization, and report rendering. Do not hand-edit its managed `process.json`, `change-log.jsonl`, or `final-report.md`.
5. Treat a supplied document as interview evidence, not as complete truth. Extract what it actually states and ask about missing connections.

If the user says `재개`, find session directories containing `process.json` and inspect each with the `show` command. If exactly one active session exists, resume it. If several exist and the user did not name one, show a compact list and ask one question: which session to resume.

For a new session, generate a filesystem-safe session ID. If the user supplied no workflow description, ask only `함께 정리할 업무를 한 문장으로 알려주세요.` If they already described it, record every explicit fact and ask the first unresolved scope question instead of making them repeat it.

## Enforce the conversation contract

- Ask exactly one question per assistant turn until the interview is complete. Do not hide several questions in bullets or a compound sentence.
- Show a short progress line on every interview turn. Use counts such as `기본 흐름 3/5` only when the denominator is known; otherwise use `3/?`.
- Reflect what changed before asking the next question.
- Accept normal prose as well as `확정`, `수정`, `이전`, `요약`, `완료`, `재개`, `구현 작업 열기 AX01`, and `구현 결과 검토 <RESULT.md>`.
- Record every user answer before composing the next question. Confirm that persistence succeeded.
- Never infer an actor, rule, system, number, branch, or exception as fact.
- Never reveal AX suggestions before the user confirms the whole current process.

Use this response shape:

```text
[진행] 범위 ✓ · 기본 흐름 3/? · 분기 1/? · 전체 확인 ○ · AX 분석 잠금

이번에 반영한 내용
- ...

다음 질문
...?

명령: 확정 · 수정 · 이전 · 요약 · 완료 · 재개
```

Keep the reflection brief. A terminal report handoff does not need another question.

## Persist state

Set `SESSION_DIR="$PROJECT_ROOT/.ax-workspace/$SESSION_ID"`. Run the manager from `SKILL_ROOT`; use its `--help` output for optional fields.

```bash
python3 "$SKILL_ROOT/scripts/session_manager.py" \
  --workspace "$SESSION_DIR" init --session-id "$SESSION_ID" --title "$PROCESS_TITLE"

python3 "$SKILL_ROOT/scripts/session_manager.py" \
  --workspace "$SESSION_DIR" show
```

Maintain one directory per session. Apply interview facts with the matching subcommand:

```bash
# Scope; repeat list flags such as --included and --source-quote as needed.
python3 "$SKILL_ROOT/scripts/session_manager.py" --workspace "$SESSION_DIR" \
  --expected-revision "$REVISION" set-scope --purpose "..." \
  --start-trigger "..." --end-condition "..." --included "..." \
  --excluded "..." --customer "..." --source-quote "..."

# Create, revise, and confirm a step. Preserve the user's wording in --source-quote.
python3 "$SKILL_ROOT/scripts/session_manager.py" --workspace "$SESSION_DIR" \
  --expected-revision "$REVISION" add-step --id S01 --name "..." \
  --actor "..." --action "..." \
  --input "..." --output "..." --system "..." --source-quote "..."
python3 "$SKILL_ROOT/scripts/session_manager.py" --workspace "$SESSION_DIR" \
  --expected-revision "$REVISION" update-step S01 --actor "..."
python3 "$SKILL_ROOT/scripts/session_manager.py" --workspace "$SESSION_DIR" \
  --expected-revision "$REVISION" confirm-step S01

# Connect confirmed steps and preserve the branch condition as evidence.
python3 "$SKILL_ROOT/scripts/session_manager.py" --workspace "$SESSION_DIR" \
  --expected-revision "$REVISION" add-branch --id B01 --from-step S01 \
  --to-step S02 --condition "..." --action "..." --kind exception \
  --source-quote "..."

# Keep missing facts explicit.
python3 "$SKILL_ROOT/scripts/session_manager.py" --workspace "$SESSION_DIR" \
  --expected-revision "$REVISION" add-open-question "..." --step-id S01
python3 "$SKILL_ROOT/scripts/session_manager.py" --workspace "$SESSION_DIR" \
  --expected-revision "$REVISION" resolve-question Q01
```

Use repeatable `add-step` or `update-step` flags for inputs, outputs, systems, decision rules, approvals, exceptions, pain points, source quotes, next steps, known metrics, and risk flags. Supplied lists replace existing lists during `update-step`; include every value to retain. Mark an answered open question with `resolve-question`. Shell-quote each user value as one argument; never interpolate user text into executable shell syntax.

Load `show` before every mutation and after every save. Pass the returned revision through the global `--expected-revision` option on every mutation to prevent lost updates. The session manager owns `process.json`, the append-only `change-log.jsonl`, and the combined `final-report.md`.

If the stored revision changes unexpectedly, reload rather than overwriting. If a save fails, say so and retry; do not claim that the answer was recorded.

## Interview the current process

Follow these phases and skip only questions already answered.

1. **Scope** — establish purpose, start event, end condition, included boundary, excluded boundary, and recipient of the outcome. Summarize and confirm the scope.
2. **Main path** — walk forward from the start. Capture one stable step at a time: actor, action, inputs, outputs, systems, and normal next step. Give it a stable `Sxx` ID and confirm it before moving on.
3. **Decisions and handoffs** — capture decision rules, approvals, rejections, storage, and transfers between roles or systems.
4. **Branches and exceptions** — capture the condition, handling step, destination or rejoin point, and known impact. Resolve dangling branches and loops without an exit condition.
5. **Operating context** — capture known frequency, volume, time, wait, error or rework, data format, integration access, security, regulatory, audit, and retention constraints. Leave unavailable values unknown.
6. **Whole-process review** — show the connected workflow, known branches, and unknowns. Ask only: `위 업무 흐름을 AX 분석의 기준본으로 확정할까요?` On explicit confirmation, run `complete-interview`; never set confirmed status manually.

Store three evidence states explicitly:

- **Fact**: directly stated or explicitly confirmed by the user; preserve a supporting `source_quote` when possible.
- **Inference**: an AI proposal awaiting confirmation; keep it outside confirmed facts with its rationale and confidence.
- **Unknown**: missing information; store it as null/empty plus an open question, never as a guessed default.

Promote an inference to fact only after the user confirms it. Keep the earlier state in the change log.

## Handle commands

Apply command semantics from the interview protocol.

- `확정`: run `confirm-step` for the current step; at whole-process review, run `complete-interview`.
- `수정 [내용]`: change only the named item and log it. Ask one disambiguating question if needed.
- `이전`: move interview focus back without deleting saved facts.
- `요약`: reload with `show`, display scope, confirmed steps, branches, unknowns, and progress; then continue with one next question.
- `완료`: check whether the current phase can close. If a required connection is missing, ask the single highest-value missing question. Never use it as implicit whole-process confirmation.
- `재개`: reload saved state and continue at the first unresolved point.

When a user mixes a command with new facts, save the facts first and then execute the command. If the user edits a fully confirmed process, reopen it, invalidate or archive the stale analysis, and require whole-process confirmation again.

## Gate AX analysis

Do not analyze while the progress line says `AX 분석 잠금`. If asked early, explain in one sentence that a proposal based on an unconfirmed flow could automate the wrong work, then continue with one interview question.

Unlock analysis only when all of these are true:

- the process has an explicit whole-process confirmation;
- every referenced step has `status: confirmed`;
- main-path connections resolve from the start to an end;
- important known approvals, rejection paths, and exceptions are represented;
- remaining unknowns are visible rather than fabricated.

After `complete-interview`, reload the now-confirmed baseline:

```bash
python3 "$SKILL_ROOT/scripts/session_manager.py" \
  --workspace "$SESSION_DIR" show
```

Use the confirmed revision returned by `show` in `ax-analysis.json`. Do not mutate the process while creating the analysis. Any later process edit automatically locks analysis and archives the stale analysis file; reconfirm before regenerating it.

## Generate the AX analysis

1. Read [references/ax-evaluation-rubric.md](references/ax-evaluation-rubric.md) completely.
2. Read [references/ax-analysis-schema.json](references/ax-analysis-schema.json) completely.
3. Evaluate every confirmed step and useful adjacent-step bundle. Do not limit analysis to tasks containing the word "AI."
4. Choose exactly one primary solution type per opportunity:
   - `human-only`
   - `ai-assist`
   - `rules-api`
   - `rpa`
   - `dedicated-app`
   - `hybrid`
5. Ground every opportunity in confirmed step IDs and supporting quotes. Put unsupported implementation claims in `assumptions` or `unknowns`.
6. Define the automation boundary: what the system may do, what a person must do, where execution stops, and what triggers manual handling.
7. For `dedicated-app` and `hybrid`, identify supported productization features such as state, history, search, assignment, approval, exception handling, reuse, feedback, alerts, and audit trails. Explain the workflow need; do not list generic features.
8. Pair risks with concrete controls: actor, trigger, evidence, threshold, failure path, and audit record where relevant.
9. Select a thin MVP from one to three `now` opportunities. Include scope in/out, user stories, human approval points, and measurable success criteria. Mark unknown baselines as a measurement task.
10. Write only fields allowed by the schema to the session's `ax-analysis.json`. Match `session_id` and `process_revision` to the confirmed `process.json`.

Prefer `rules-api` over UI automation when stable integration is available. Use `rpa` only when UI operation is unavoidable. Classify non-delegable final judgment as `human-only` with `do-not-automate` priority. Do not give a low-confidence opportunity `now` priority.

## Validate and render

Validate the analysis against the confirmed process before showing any final recommendations:

```bash
python3 "$SKILL_ROOT/scripts/validate_ax_analysis.py" \
  "$SESSION_DIR/process.json" \
  "$SESSION_DIR/ax-analysis.json"
```

If validation fails, fix `ax-analysis.json` rather than weakening the process evidence or validator. Re-run until it prints `VALID`. Treat warnings as review prompts.

After standalone validation succeeds, run finalization with the same process revision. It validates again, marks the session finalized, and renders the combined `final-report.md` from the confirmed process and analysis:

```bash
python3 "$SKILL_ROOT/scripts/session_manager.py" \
  --workspace "$SESSION_DIR" --expected-revision "$REVISION" finalize
```

Use [assets/final-report-template.md](assets/final-report-template.md) as the completeness and presentation reference. The rendered report must include:

- confirmed As-Is scope, steps, branches, exceptions, and unknowns;
- a prioritized AX opportunity portfolio;
- evidence, automation boundary, productization features, integrations, controls, risks, assumptions, and metrics for each opportunity;
- the To-Be division of work between people and systems;
- a focused MVP and unresolved validation questions;
- traceability from every recommendation back to confirmed steps.

Revalidate and finalize again whenever the analysis or process revision changes. Do not declare completion unless finalized `process.json`, valid `ax-analysis.json`, and the combined `final-report.md` exist for the same revision. Use `report` only to rerender from current artifacts; never use it to bypass `finalize` validation.

## Open implementation as a separate Codex task

Keep this task as the process architect and source of truth. Never build the proposed tool in this interview task. Offer implementation only after finalization, and require a fresh explicit selection before each implementation task is issued.

When the user says `구현 작업 열기 AX01` or clearly selects one opportunity for implementation:

1. Treat that exact message as selection evidence and authorization to create one separate Codex task. Do not infer consent from an opportunity's `now` priority or its inclusion in the AI-generated MVP.
2. Ask one question only if the implementation objective or a minimum acceptance criterion is genuinely missing. Reuse the validated MVP, product features, controls, metrics, and human approval points instead of re-interviewing the workflow.
3. Run `scripts/implementation_handoff.py create` using the finalized session directory, the selected opportunity ID, the current project root, the user's exact selection evidence, the objective, and at least one acceptance criterion. Do not hand-edit the generated `handoff.json` or `TASK.md`.
4. Read the command output and verify the new packet with its printed verification command. A handoff must pin the process and analysis hashes, record its digest in the session issuance registry, and preserve protected human actions.
5. In the Codex app, use the surfaced task-creation capability when available. First resolve the saved project matching `PROJECT_ROOT`, then create a local task in that same project without overriding the user's configured model. Start it with a focused prompt that names `$build-ax-tool`, links the absolute `TASK.md` path, and requires verification before edits.
6. Create a new task rather than copying the entire interview transcript. The minimized, validated handoff is its context boundary.
7. If the app cannot create a task, link `TASK.md` and provide its ready-to-paste opening prompt. Do not silently continue implementation here.
8. Return the created task link or ID and keep this task available for further process work. Do not wait for or merge implementation results automatically.

Reject handoff creation for a stale or unvalidated analysis, an opportunity outside the confirmed MVP, `human-only`, or `do-not-automate`. If the process changes later, the handoff becomes stale and the implementation task must request a new packet.

Read [references/implementation-handoff-schema.json](references/implementation-handoff-schema.json) only when inspecting or debugging the packet contract. Normal creation and verification must use the script.

When the user says `구현 결과 검토 <RESULT.md>`:

1. Read the result and adjacent handoff.
2. Re-run the handoff verification against the current finalized session.
3. Compare every acceptance criterion, test result, automation boundary, and human approval point with the returned implementation.
4. Route a discovered business-fact change back into this one-question interview. Never let the builder edit the process baseline.
5. Ask one decision question: accept for the next controlled validation step, send back for implementation revision, or reopen the process baseline. Acceptance is not deployment, merge, or proof of value.

## Hand off

Lead with the outcome. Summarize the top opportunity, the human boundary, the MVP, and the most important unresolved assumption. Link `process.json`, `ax-analysis.json`, and `final-report.md` using absolute paths. Mention validation success and explain that `구현 작업 열기 AX01` prepares and, when the runtime supports it, opens a separate Codex task. Do not imply that estimated value has been proven.
