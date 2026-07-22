---
name: build-ax-tool
description: Implement or plan one user-selected AX opportunity in a separate Codex app task from a validated Process-to-AX implementation handoff. Use when a new Codex task receives a handoff.json or TASK.md created by map-workflow-ax, or when the user asks to continue, test, or review that isolated AX tool build without reopening the business-process interview.
---

# Build an AX Tool from a Handoff

Treat this task as an implementation branch. Keep the original Process-to-AX task as the only place where business facts, the confirmed workflow, and the AX decision are changed.

## Verify the handoff first

1. Locate the supplied `TASK.md` and adjacent `handoff.json`.
2. Read both completely. Resolve all paths from the handoff rather than guessing them.
3. Run the verification command printed in `TASK.md` before inspecting or changing implementation files.
4. Stop if verification reports tampering, a stale process or analysis, a non-finalized baseline, or a `human-only` / `do-not-automate` opportunity.
5. Never edit the source `process.json`, `ax-analysis.json`, `change-log.jsonl`, or `final-report.md` from this task.

If the handoff is invalid, state the exact failure and tell the user to return to the main Process-to-AX task to issue a new handoff. Do not repair the source-of-truth files here.

## Start the separate implementation conversation

Lead with a compact receipt:

```text
[구현 인계] 검증 ✓ · 기회 AX01 · 기준본 r15 · 별도 구현 작업

확정된 구현 목표
- ...

지켜야 할 경계
- ...
```

Do not repeat the workflow interview. Use the handoff's confirmed steps, exact evidence, automation boundary, controls, protected human actions, MVP scope, assumptions, and unknowns.

If one blocking implementation choice is missing, ask exactly one question and wait. Prefer questions about the target environment, available integration, representative test data, or acceptance criterion. Do not turn an unknown into a fact. If no blocking choice is missing and the user authorized implementation, begin work.

## Implement inside the delegated scope

- Work only in the handoff's target project and allowed output area unless the user explicitly expands scope.
- Inspect existing files and preserve unrelated user changes.
- Build the thinnest useful slice that satisfies the handoff objective and acceptance criteria.
- Implement every automation stop condition and human approval boundary as an actual control or testable constraint.
- Prefer deterministic rules or APIs for deterministic work. Use AI only where the handoff identifies probabilistic or unstructured work.
- Keep secrets out of the handoff and source. Use environment-variable or connector references when credentials are needed.
- Use synthetic or approved test data. Do not copy sensitive interview evidence into fixtures unless the handoff explicitly permits it.
- Test in proportion to risk. Include negative tests for actions the tool must never take.
- Do not deploy, merge, submit, email, pay, approve, or trigger another irreversible action without explicit authority in this task.

If implementation reveals that a confirmed business fact is wrong, record a process change request with the related step ID and evidence. Do not modify the baseline.

## Return a review packet

Before declaring completion:

1. Re-run the handoff verification command to detect a changed baseline.
2. Run the relevant implementation tests and record their exact results.
3. Write `RESULT.md` beside `handoff.json`. Include:
   - handoff ID and source revision;
   - status: `ready_for_review`, `needs_input`, `needs_rebase`, or `failed`;
   - implemented outcome and changed files;
   - acceptance criteria with pass/fail evidence;
   - commands run and test results;
   - preserved human boundaries and controls;
   - deviations, remaining risks, assumptions, and unknowns;
   - any process change requests;
   - recommended next action.
4. Link the implementation artifacts and `RESULT.md` with absolute paths.
5. Tell the user to return to the main Process-to-AX task and say `구현 결과 검토 <RESULT.md 절대경로>`.

Do not claim that a prototype proves business value. Keep measured results separate from hypotheses.
