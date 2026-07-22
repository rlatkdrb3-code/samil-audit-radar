#!/usr/bin/env python3
"""Persist and render a conversational Process-to-AX interview session."""

from __future__ import annotations

import argparse
import json
import os
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable


PROCESS_FILE = "process.json"
CHANGE_LOG_FILE = "change-log.jsonl"
ANALYSIS_FILE = "ax-analysis.json"
REPORT_FILE = "final-report.md"
CONFIDENCE_VALUES = ("unknown", "low", "medium", "high")


class SessionError(RuntimeError):
    pass


def now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def read_json(path: Path) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise SessionError(f"session file not found: {path}") from exc
    except json.JSONDecodeError as exc:
        raise SessionError(f"invalid JSON in {path}: {exc}") from exc
    if not isinstance(payload, dict):
        raise SessionError(f"top-level JSON must be an object: {path}")
    return payload


def atomic_write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temp_name = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, ensure_ascii=False, indent=2)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temp_name, path)
    except Exception:
        try:
            os.unlink(temp_name)
        except OSError:
            pass
        raise


def atomic_write_text(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temp_name = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temp_name, path)
    except Exception:
        try:
            os.unlink(temp_name)
        except OSError:
            pass
        raise


def append_event(workspace: Path, process: dict[str, Any], event: str, details: dict[str, Any]) -> None:
    record = {
        "timestamp": now_iso(),
        "session_id": process.get("session_id"),
        "revision": process.get("revision"),
        "event": event,
        "details": details,
    }
    path = workspace / CHANGE_LOG_FILE
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(record, ensure_ascii=False) + "\n")
        handle.flush()
        os.fsync(handle.fileno())


def new_process(session_id: str, title: str) -> dict[str, Any]:
    timestamp = now_iso()
    return {
        "schema_version": "1.0",
        "session_id": session_id,
        "title": title,
        "status": "interviewing",
        "stage": "scope",
        "revision": 1,
        "created_at": timestamp,
        "updated_at": timestamp,
        "interview_confirmed": False,
        "confirmed_at": None,
        "scope": {
            "purpose": None,
            "start_trigger": None,
            "end_condition": None,
            "included": [],
            "excluded": [],
            "customers": [],
            "source_quotes": [],
        },
        "actors": [],
        "systems": [],
        "steps": [],
        "branches": [],
        "open_questions": [],
        "focus": {"kind": "scope", "id": None},
        "analysis": {"status": "locked", "process_revision": None},
    }


def default_step(step_id: str, name: str, action: str) -> dict[str, Any]:
    return {
        "id": step_id,
        "name": name,
        "actor": None,
        "action": action,
        "inputs": [],
        "outputs": [],
        "systems": [],
        "decision_rules": [],
        "approvals": [],
        "exceptions": [],
        "frequency_per_month": None,
        "minutes_per_case": None,
        "manual_ratio_pct": None,
        "pain_points": [],
        "data_sensitivity": None,
        "financial_or_legal_impact": None,
        "final_action_authority": None,
        "status": "draft",
        "confidence": "unknown",
        "source_quotes": [],
        "next_steps": [],
    }


def process_path(workspace: Path) -> Path:
    return workspace / PROCESS_FILE


def load_process(workspace: Path) -> dict[str, Any]:
    return read_json(process_path(workspace))


def step_by_id(process: dict[str, Any], step_id: str) -> dict[str, Any]:
    for step in process.get("steps", []):
        if step.get("id") == step_id:
            return step
    raise SessionError(f"unknown step id: {step_id}")


def ensure_revision(process: dict[str, Any], expected_revision: int | None) -> None:
    if expected_revision is None:
        return
    actual = process.get("revision")
    if actual != expected_revision:
        raise SessionError(f"revision conflict: expected {expected_revision}, found {actual}")


def archive_stale_analysis(workspace: Path, process: dict[str, Any]) -> None:
    analysis_path = workspace / ANALYSIS_FILE
    if not analysis_path.exists():
        return
    stale = workspace / f"ax-analysis.stale-r{process.get('revision', 'unknown')}.json"
    counter = 1
    while stale.exists():
        stale = workspace / f"ax-analysis.stale-r{process.get('revision', 'unknown')}-{counter}.json"
        counter += 1
    os.replace(analysis_path, stale)


def invalidate_confirmation(workspace: Path, process: dict[str, Any]) -> None:
    if process.get("interview_confirmed") or process.get("status") in {"confirmed", "finalized"}:
        archive_stale_analysis(workspace, process)
        process["interview_confirmed"] = False
        process["confirmed_at"] = None
        process["status"] = "interviewing"
        process["stage"] = "review"
        process["analysis"] = {"status": "locked", "process_revision": None}


def rebuild_indexes(process: dict[str, Any]) -> None:
    actors: list[str] = []
    systems: list[str] = []
    for step in process.get("steps", []):
        actor = step.get("actor")
        if isinstance(actor, str) and actor.strip() and actor not in actors:
            actors.append(actor)
        for system in step.get("systems") or []:
            if isinstance(system, str) and system.strip() and system not in systems:
                systems.append(system)
    for branch in process.get("branches", []):
        actor = branch.get("actor")
        if isinstance(actor, str) and actor.strip() and actor not in actors:
            actors.append(actor)
    process["actors"] = actors
    process["systems"] = systems


def mutate(
    workspace: Path,
    expected_revision: int | None,
    event: str,
    details: dict[str, Any],
    operation: Callable[[dict[str, Any]], None],
) -> dict[str, Any]:
    process = load_process(workspace)
    ensure_revision(process, expected_revision)
    operation(process)
    # Invalidate only after the requested mutation succeeds. A rejected edit must
    # not archive a still-valid analysis or reopen the confirmed interview.
    invalidate_confirmation(workspace, process)
    rebuild_indexes(process)
    process["revision"] = int(process.get("revision", 0)) + 1
    process["updated_at"] = now_iso()
    atomic_write_json(process_path(workspace), process)
    append_event(workspace, process, event, details)
    return process


def clean_list(values: list[str] | None) -> list[str] | None:
    if values is None:
        return None
    cleaned: list[str] = []
    for value in values:
        text = value.strip()
        if text and text not in cleaned:
            cleaned.append(text)
    return cleaned


def parse_optional_bool(value: str | None) -> bool | None:
    if value is None:
        return None
    return value == "yes"


def add_step_fields(parser: argparse.ArgumentParser, *, require_identity: bool) -> None:
    if require_identity:
        parser.add_argument("--id", required=True)
        parser.add_argument("--name", required=True)
        parser.add_argument("--action", required=True)
    else:
        parser.add_argument("id")
        parser.add_argument("--name")
        parser.add_argument("--action")
    parser.add_argument("--actor")
    parser.add_argument("--input", action="append", default=None)
    parser.add_argument("--output", action="append", default=None)
    parser.add_argument("--system", action="append", default=None)
    parser.add_argument("--decision-rule", action="append", default=None)
    parser.add_argument("--approval", action="append", default=None)
    parser.add_argument("--exception", action="append", default=None)
    parser.add_argument("--frequency-per-month", type=float)
    parser.add_argument("--minutes-per-case", type=float)
    parser.add_argument("--manual-ratio-pct", type=float)
    parser.add_argument("--pain-point", action="append", default=None)
    parser.add_argument("--data-sensitivity")
    parser.add_argument("--financial-or-legal-impact")
    parser.add_argument("--final-action-authority", choices=("yes", "no"))
    parser.add_argument("--confidence", choices=CONFIDENCE_VALUES)
    parser.add_argument("--source-quote", action="append", default=None)
    parser.add_argument("--next-step", action="append", default=None)


def step_updates(args: argparse.Namespace) -> dict[str, Any]:
    updates: dict[str, Any] = {}
    scalar_names = (
        "name",
        "actor",
        "action",
        "frequency_per_month",
        "minutes_per_case",
        "manual_ratio_pct",
        "data_sensitivity",
        "financial_or_legal_impact",
        "confidence",
    )
    for name in scalar_names:
        value = getattr(args, name, None)
        if value is not None:
            updates[name] = value.strip() if isinstance(value, str) else value
    authority = parse_optional_bool(getattr(args, "final_action_authority", None))
    if authority is not None:
        updates["final_action_authority"] = authority
    list_map = {
        "input": "inputs",
        "output": "outputs",
        "system": "systems",
        "decision_rule": "decision_rules",
        "approval": "approvals",
        "exception": "exceptions",
        "pain_point": "pain_points",
        "source_quote": "source_quotes",
        "next_step": "next_steps",
    }
    for argument_name, field_name in list_map.items():
        values = clean_list(getattr(args, argument_name, None))
        if values is not None:
            updates[field_name] = values
    for field_name in ("frequency_per_month", "minutes_per_case", "manual_ratio_pct"):
        value = updates.get(field_name)
        if value is not None and value < 0:
            raise SessionError(f"{field_name} cannot be negative")
    if updates.get("manual_ratio_pct") is not None and updates["manual_ratio_pct"] > 100:
        raise SessionError("manual_ratio_pct cannot exceed 100")
    return updates


def validation_errors(process: dict[str, Any]) -> list[str]:
    errors: list[str] = []
    scope = process.get("scope") or {}
    for key in ("purpose", "start_trigger", "end_condition"):
        if not isinstance(scope.get(key), str) or not scope[key].strip():
            errors.append(f"scope.{key} is required")
    steps = process.get("steps") or []
    if not steps:
        errors.append("at least one step is required")
        return errors
    ids = {step.get("id") for step in steps}
    if len(ids) != len(steps) or None in ids:
        errors.append("step ids must be present and unique")
    for step in steps:
        step_id = step.get("id") or "<unknown>"
        if step.get("status") != "confirmed":
            errors.append(f"step {step_id} is not confirmed")
        for key in ("name", "action"):
            if not isinstance(step.get(key), str) or not step[key].strip():
                errors.append(f"step {step_id}.{key} is required")
        if not step.get("source_quotes"):
            errors.append(f"step {step_id} requires at least one source quote")
        for next_step in step.get("next_steps") or []:
            if next_step != "END" and next_step not in ids:
                errors.append(f"step {step_id} points to unknown next step {next_step}")
    if not any(
        not (step.get("next_steps") or []) or "END" in (step.get("next_steps") or [])
        for step in steps
    ):
        errors.append("process has no terminal step or END connection")
    for branch in process.get("branches") or []:
        if branch.get("from_step") not in ids:
            errors.append(f"branch {branch.get('id')} has unknown from_step")
        destination = branch.get("to_step")
        if destination != "END" and destination not in ids:
            errors.append(f"branch {branch.get('id')} has unknown to_step")
    reachable = {steps[0].get("id")}
    changed = True
    while changed:
        changed = False
        for step in steps:
            if step.get("id") not in reachable:
                continue
            for target in step.get("next_steps") or []:
                if target != "END" and target not in reachable:
                    reachable.add(target)
                    changed = True
        for branch in process.get("branches") or []:
            if branch.get("from_step") in reachable:
                target = branch.get("to_step")
                if target != "END" and target not in reachable:
                    reachable.add(target)
                    changed = True
    disconnected = sorted(step_id for step_id in ids if step_id not in reachable)
    if disconnected:
        errors.append(f"disconnected steps: {', '.join(disconnected)}")
    return errors


def markdown_cell(value: Any) -> str:
    if value is None:
        return "미확인"
    if isinstance(value, list):
        text = " · ".join(str(item) for item in value) or "-"
    else:
        text = str(value)
    return text.replace("|", "\\|").replace("\n", " ")


def bullet_lines(values: list[str] | None, empty: str = "- 없음") -> list[str]:
    if not values:
        return [empty]
    return [f"- {value}" for value in values]


def render_report(process: dict[str, Any], analysis: dict[str, Any] | None = None) -> str:
    scope = process.get("scope") or {}
    confirmed_label = "예" if process.get("interview_confirmed") else "아니오"
    lines = [
        f"# {process.get('title') or '업무 프로세스'} Process-to-AX 결과 보고서",
        "",
        f"- 세션: `{process.get('session_id')}`",
        f"- 기준 프로세스 리비전: `{process.get('revision')}`",
        f"- 인터뷰 확정: `{confirmed_label}`",
        f"- 보고서 생성: `{now_iso()}`",
        "",
        "> 이 보고서는 사용자에게 확정받은 업무 흐름을 기준으로 작성했습니다. 미확인과 가정은 확인된 사실이 아닙니다.",
        "",
        "## 1. 확정된 현재 업무",
        "",
        f"- 목적: {markdown_cell(scope.get('purpose'))}",
        f"- 시작: {markdown_cell(scope.get('start_trigger'))}",
        f"- 종료: {markdown_cell(scope.get('end_condition'))}",
        f"- 포함 범위: {markdown_cell(scope.get('included'))}",
        f"- 제외 범위: {markdown_cell(scope.get('excluded'))}",
        f"- 결과 사용자: {markdown_cell(scope.get('customers'))}",
        "",
        "### 기본 흐름",
        "",
        "| 단계 | 담당자 | 행동 | 입력 | 출력 | 시스템 | 다음 단계 |",
        "|---|---|---|---|---|---|---|",
    ]
    for step in process.get("steps") or []:
        lines.append(
            "| "
            + " | ".join(
                markdown_cell(value)
                for value in (
                    step.get("id"),
                    step.get("actor"),
                    step.get("action"),
                    step.get("inputs"),
                    step.get("outputs"),
                    step.get("systems"),
                    step.get("next_steps") or ["END"],
                )
            )
            + " |"
        )
    lines.extend(["", "### 분기와 예외", ""])
    branches = process.get("branches") or []
    if branches:
        lines.extend(
            [
                "| ID | 출발 | 조건 | 처리 | 목적지 |",
                "|---|---|---|---|---|",
            ]
        )
        for branch in branches:
            lines.append(
                "| "
                + " | ".join(
                    markdown_cell(branch.get(key))
                    for key in ("id", "from_step", "condition", "action", "to_step")
                )
                + " |"
            )
    else:
        lines.append("- 기록된 분기 없음")
    lines.extend(["", "### 미확인 사항", ""])
    questions = [item.get("text") for item in process.get("open_questions") or [] if item.get("status") != "resolved"]
    lines.extend(bullet_lines(questions))

    if analysis is None:
        lines.extend(["", "## 2. AX 분석", "", "- 아직 생성되지 않았습니다."])
        return "\n".join(lines) + "\n"

    lines.extend(
        [
            "",
            "## 2. AX 분석 요약",
            "",
            analysis.get("summary", ""),
            "",
            "### 기회 포트폴리오",
            "",
            "| 우선순위 | ID | 대상 단계 | 해결 유형 | 제안 | 신뢰도 |",
            "|---|---|---|---|---|---|",
        ]
    )
    for opportunity in analysis.get("opportunities") or []:
        lines.append(
            "| "
            + " | ".join(
                markdown_cell(value)
                for value in (
                    opportunity.get("priority"),
                    opportunity.get("id"),
                    opportunity.get("step_ids"),
                    opportunity.get("solution_type"),
                    opportunity.get("title"),
                    opportunity.get("confidence"),
                )
            )
            + " |"
        )
    for opportunity in analysis.get("opportunities") or []:
        lines.extend(
            [
                "",
                f"### {opportunity.get('id')}. {opportunity.get('title')}",
                "",
                f"- 관련 단계: {markdown_cell(opportunity.get('step_ids'))}",
                f"- 문제: {opportunity.get('problem')}",
                f"- 해결 유형: `{opportunity.get('solution_type')}`",
                f"- 근거: {opportunity.get('rationale')}",
                f"- 자동화 경계: {opportunity.get('automation_boundary')}",
                f"- 가치 가설: {opportunity.get('value_hypothesis')}",
                "- 제품 기능: " + markdown_cell(opportunity.get("product_features")),
                "- 연동: " + markdown_cell(opportunity.get("integrations")),
                "- 통제: " + markdown_cell(opportunity.get("controls")),
                "- 위험: " + markdown_cell(opportunity.get("risks")),
                "- 가정: " + markdown_cell(opportunity.get("assumptions")),
                "- 미확인: " + markdown_cell(opportunity.get("unknowns")),
                "- 검증 지표: " + markdown_cell(opportunity.get("validation_metrics")),
                "",
                "근거 문장:",
            ]
        )
        for evidence in opportunity.get("evidence") or []:
            lines.append(f"- `{evidence.get('step_id')}` — {evidence.get('quote')}")
    mvp = analysis.get("mvp") or {}
    lines.extend(
        [
            "",
            "## 3. MVP 제안",
            "",
            "- 선택 기회: " + markdown_cell(mvp.get("selected_opportunity_ids")),
            "- 포함 범위: " + markdown_cell(mvp.get("scope_in")),
            "- 제외 범위: " + markdown_cell(mvp.get("scope_out")),
            "- 사용자 스토리: " + markdown_cell(mvp.get("user_stories")),
            "- 사람 승인 지점: " + markdown_cell(mvp.get("human_approval_points")),
            "- 성공 지표: " + markdown_cell(mvp.get("success_metrics")),
            "",
            "## 4. 실행 전 확인사항",
            "",
        ]
    )
    lines.extend(bullet_lines(analysis.get("unresolved_questions")))
    buildable_ids = [
        item.get("id")
        for item in analysis.get("opportunities") or []
        if item.get("priority") != "do-not-automate" and item.get("solution_type") != "human-only"
    ]
    lines.extend(
        [
            "",
            "## 5. 별도 구현 작업으로 넘기기",
            "",
            "- 구현 가능 기회: " + markdown_cell(buildable_ids),
            "- 메인 Process-to-AX 작업에서 `구현 작업 열기 AXnn`이라고 요청합니다.",
            "- 플러그인은 검증된 인계서를 만들고 새 Codex 작업 또는 검토형 새 작업 링크를 제공합니다.",
            "- 구현 완료는 자동 병합·배포 또는 효과 입증을 의미하지 않습니다.",
            "",
            "## 6. 변경 이력",
            "",
            f"- `{CHANGE_LOG_FILE}` 참조",
        ]
    )
    return "\n".join(lines) + "\n"


def load_analysis_validator() -> Any:
    scripts_dir = str(Path(__file__).resolve().parent)
    if scripts_dir not in sys.path:
        sys.path.insert(0, scripts_dir)
    import validate_ax_analysis

    return validate_ax_analysis


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Manage a Process-to-AX interview session.")
    parser.add_argument("--workspace", type=Path, required=True, help="Session directory")
    parser.add_argument("--expected-revision", type=int, help="Optimistic revision guard")
    subparsers = parser.add_subparsers(dest="command", required=True)

    init = subparsers.add_parser("init", help="Create a new session")
    init.add_argument("--session-id", required=True)
    init.add_argument("--title", required=True)

    subparsers.add_parser("show", help="Print the current process JSON")

    scope = subparsers.add_parser("set-scope", help="Update scope fields")
    scope.add_argument("--purpose")
    scope.add_argument("--start-trigger")
    scope.add_argument("--end-condition")
    scope.add_argument("--included", action="append", default=None)
    scope.add_argument("--excluded", action="append", default=None)
    scope.add_argument("--customer", action="append", default=None)
    scope.add_argument("--source-quote", action="append", default=None)

    add_step = subparsers.add_parser("add-step", help="Add a draft process step")
    add_step_fields(add_step, require_identity=True)

    update_step = subparsers.add_parser("update-step", help="Replace supplied fields on a step")
    add_step_fields(update_step, require_identity=False)

    confirm_step = subparsers.add_parser("confirm-step", help="Confirm a process step")
    confirm_step.add_argument("id")

    branch = subparsers.add_parser("add-branch", help="Add a decision or exception branch")
    branch.add_argument("--id", required=True)
    branch.add_argument("--from-step", required=True)
    branch.add_argument("--to-step", required=True, help="Existing step id or END")
    branch.add_argument("--condition", required=True)
    branch.add_argument("--actor")
    branch.add_argument("--action", required=True)
    branch.add_argument("--kind", choices=("decision", "rejection", "exception", "error"), default="exception")
    branch.add_argument("--source-quote", action="append", default=None)

    question = subparsers.add_parser("add-open-question", help="Record an unresolved question")
    question.add_argument("text")
    question.add_argument("--step-id")

    resolve = subparsers.add_parser("resolve-question", help="Resolve an open question by its id")
    resolve.add_argument("id")

    subparsers.add_parser("complete-interview", help="Validate and confirm the whole process")
    subparsers.add_parser("report", help="Render final-report.md from available artifacts")
    subparsers.add_parser("finalize", help="Validate AX analysis and finalize the report")
    return parser


def command_init(args: argparse.Namespace) -> dict[str, Any]:
    workspace = args.workspace
    path = process_path(workspace)
    if path.exists():
        raise SessionError(f"session already exists: {path}")
    process = new_process(args.session_id.strip(), args.title.strip())
    if not process["session_id"] or not process["title"]:
        raise SessionError("session id and title must not be blank")
    atomic_write_json(path, process)
    append_event(workspace, process, "session_initialized", {"title": process["title"]})
    return process


def command_set_scope(args: argparse.Namespace) -> dict[str, Any]:
    scalar = {
        "purpose": args.purpose,
        "start_trigger": args.start_trigger,
        "end_condition": args.end_condition,
    }
    lists = {
        "included": clean_list(args.included),
        "excluded": clean_list(args.excluded),
        "customers": clean_list(args.customer),
        "source_quotes": clean_list(args.source_quote),
    }
    if not any(value is not None for value in [*scalar.values(), *lists.values()]):
        raise SessionError("set-scope requires at least one field")

    def operation(process: dict[str, Any]) -> None:
        scope = process["scope"]
        for key, value in scalar.items():
            if value is not None:
                scope[key] = value.strip()
        for key, value in lists.items():
            if value is not None:
                scope[key] = value
        process["focus"] = {"kind": "scope", "id": None}

    return mutate(args.workspace, args.expected_revision, "scope_updated", {**scalar, **lists}, operation)


def command_add_step(args: argparse.Namespace) -> dict[str, Any]:
    updates = step_updates(args)
    step_id = args.id.strip()
    if not step_id or not args.name.strip() or not args.action.strip():
        raise SessionError("step id, name, and action must not be blank")

    def operation(process: dict[str, Any]) -> None:
        if any(step.get("id") == step_id for step in process["steps"]):
            raise SessionError(f"step already exists: {step_id}")
        step = default_step(step_id, args.name.strip(), args.action.strip())
        step.update(updates)
        process["steps"].append(step)
        process["stage"] = "main-path"
        process["focus"] = {"kind": "step", "id": step_id}

    return mutate(args.workspace, args.expected_revision, "step_added", {"step_id": step_id}, operation)


def command_update_step(args: argparse.Namespace) -> dict[str, Any]:
    updates = step_updates(args)
    if not updates:
        raise SessionError("update-step requires at least one field")

    def operation(process: dict[str, Any]) -> None:
        step = step_by_id(process, args.id)
        step.update(updates)
        step["status"] = "draft"
        process["focus"] = {"kind": "step", "id": args.id}

    return mutate(args.workspace, args.expected_revision, "step_updated", {"step_id": args.id, "fields": sorted(updates)}, operation)


def command_confirm_step(args: argparse.Namespace) -> dict[str, Any]:
    def operation(process: dict[str, Any]) -> None:
        step = step_by_id(process, args.id)
        if not str(step.get("name") or "").strip() or not str(step.get("action") or "").strip():
            raise SessionError("cannot confirm a step without name and action")
        if not step.get("source_quotes"):
            raise SessionError("cannot confirm a step without a user source quote")
        step["status"] = "confirmed"
        process["focus"] = {"kind": "step", "id": args.id}

    return mutate(args.workspace, args.expected_revision, "step_confirmed", {"step_id": args.id}, operation)


def command_add_branch(args: argparse.Namespace) -> dict[str, Any]:
    def operation(process: dict[str, Any]) -> None:
        if any(branch.get("id") == args.id for branch in process["branches"]):
            raise SessionError(f"branch already exists: {args.id}")
        step_by_id(process, args.from_step)
        if args.to_step != "END":
            step_by_id(process, args.to_step)
        branch = {
            "id": args.id,
            "from_step": args.from_step,
            "to_step": args.to_step,
            "condition": args.condition.strip(),
            "actor": args.actor.strip() if args.actor else None,
            "action": args.action.strip(),
            "kind": args.kind,
            "source_quotes": clean_list(args.source_quote) or [],
        }
        process["branches"].append(branch)
        process["stage"] = "branches"
        process["focus"] = {"kind": "branch", "id": args.id}

    return mutate(args.workspace, args.expected_revision, "branch_added", {"branch_id": args.id}, operation)


def command_add_question(args: argparse.Namespace) -> dict[str, Any]:
    def operation(process: dict[str, Any]) -> None:
        if args.step_id:
            step_by_id(process, args.step_id)
        next_number = len(process["open_questions"]) + 1
        process["open_questions"].append(
            {
                "id": f"Q{next_number:02d}",
                "text": args.text.strip(),
                "step_id": args.step_id,
                "status": "open",
            }
        )

    return mutate(args.workspace, args.expected_revision, "open_question_added", {"text": args.text, "step_id": args.step_id}, operation)


def command_resolve_question(args: argparse.Namespace) -> dict[str, Any]:
    def operation(process: dict[str, Any]) -> None:
        for question in process["open_questions"]:
            if question.get("id") == args.id:
                question["status"] = "resolved"
                return
        raise SessionError(f"unknown question id: {args.id}")

    return mutate(args.workspace, args.expected_revision, "open_question_resolved", {"question_id": args.id}, operation)


def command_complete(args: argparse.Namespace) -> dict[str, Any]:
    process = load_process(args.workspace)
    ensure_revision(process, args.expected_revision)
    errors = validation_errors(process)
    if errors:
        raise SessionError("cannot complete interview:\n- " + "\n- ".join(errors))
    process["revision"] = int(process.get("revision", 0)) + 1
    process["interview_confirmed"] = True
    process["confirmed_at"] = now_iso()
    process["status"] = "confirmed"
    process["stage"] = "analysis"
    process["analysis"] = {"status": "unlocked", "process_revision": process["revision"]}
    process["updated_at"] = now_iso()
    atomic_write_json(process_path(args.workspace), process)
    append_event(args.workspace, process, "interview_completed", {"step_count": len(process["steps"])})
    return process


def command_report(args: argparse.Namespace) -> dict[str, Any]:
    process = load_process(args.workspace)
    analysis_path = args.workspace / ANALYSIS_FILE
    analysis = read_json(analysis_path) if analysis_path.exists() else None
    report_path = args.workspace / REPORT_FILE
    atomic_write_text(report_path, render_report(process, analysis))
    return {"report": str(report_path), "analysis_included": analysis is not None, "revision": process.get("revision")}


def command_finalize(args: argparse.Namespace) -> dict[str, Any]:
    process = load_process(args.workspace)
    ensure_revision(process, args.expected_revision)
    if not process.get("interview_confirmed"):
        raise SessionError("cannot finalize before whole-process confirmation")
    analysis_path = args.workspace / ANALYSIS_FILE
    analysis = read_json(analysis_path)
    validator = load_analysis_validator()
    result = validator.validate_analysis(process, analysis)
    if not result.valid:
        raise SessionError("AX analysis validation failed:\n- " + "\n- ".join(result.errors))
    report_path = args.workspace / REPORT_FILE
    atomic_write_text(report_path, render_report(process, analysis))
    process["status"] = "finalized"
    process["stage"] = "complete"
    process["analysis"] = {"status": "validated", "process_revision": process["revision"]}
    process["updated_at"] = now_iso()
    atomic_write_json(process_path(args.workspace), process)
    append_event(args.workspace, process, "session_finalized", {"report": REPORT_FILE, "warnings": result.warnings})
    return {
        "session_id": process["session_id"],
        "revision": process["revision"],
        "status": process["status"],
        "report": str(report_path),
        "validation_warnings": result.warnings,
    }


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        if args.command == "init":
            result = command_init(args)
        elif args.command == "show":
            result = load_process(args.workspace)
        elif args.command == "set-scope":
            result = command_set_scope(args)
        elif args.command == "add-step":
            result = command_add_step(args)
        elif args.command == "update-step":
            result = command_update_step(args)
        elif args.command == "confirm-step":
            result = command_confirm_step(args)
        elif args.command == "add-branch":
            result = command_add_branch(args)
        elif args.command == "add-open-question":
            result = command_add_question(args)
        elif args.command == "resolve-question":
            result = command_resolve_question(args)
        elif args.command == "complete-interview":
            result = command_complete(args)
        elif args.command == "report":
            result = command_report(args)
        elif args.command == "finalize":
            result = command_finalize(args)
        else:
            raise SessionError(f"unsupported command: {args.command}")
    except SessionError as exc:
        print(str(exc), file=sys.stderr)
        return 2
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
