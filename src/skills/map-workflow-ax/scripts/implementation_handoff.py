#!/usr/bin/env python3
"""Create and verify a bounded handoff for a separate Codex implementation task."""

from __future__ import annotations

import argparse
import copy
import hashlib
import json
import os
import re
import shlex
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import quote


PROCESS_FILE = "process.json"
ANALYSIS_FILE = "ax-analysis.json"
HANDOFF_FILE = "handoff.json"
TASK_FILE = "TASK.md"
REGISTRY_FILE = "handoff-registry.jsonl"
TOP_LEVEL_KEYS = {
    "schema_version",
    "created_at",
    "source",
    "selection",
    "target",
    "task",
    "context",
    "result_contract",
    "packet_sha256",
    "handoff_id",
}


class HandoffError(RuntimeError):
    """Raised when a handoff cannot be created or verified."""


def now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def read_json(path: Path) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise HandoffError(f"file not found: {path}") from exc
    except json.JSONDecodeError as exc:
        raise HandoffError(f"invalid JSON in {path}: {exc}") from exc
    if not isinstance(payload, dict):
        raise HandoffError(f"top-level JSON must be an object: {path}")
    return payload


def atomic_write_json(path: Path, payload: dict[str, Any]) -> None:
    atomic_write_text(path, json.dumps(payload, ensure_ascii=False, indent=2) + "\n")


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


def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    try:
        with path.open("rb") as handle:
            for chunk in iter(lambda: handle.read(65536), b""):
                digest.update(chunk)
    except FileNotFoundError as exc:
        raise HandoffError(f"file not found: {path}") from exc
    return digest.hexdigest()


def canonical_sha256(payload: dict[str, Any]) -> str:
    encoded = json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def nonempty_text(value: Any) -> bool:
    return isinstance(value, str) and bool(value.strip())


def clean_strings(values: list[str] | None) -> list[str]:
    cleaned: list[str] = []
    for value in values or []:
        text = value.strip()
        if text and text not in cleaned:
            cleaned.append(text)
    return cleaned


def safe_id(value: str) -> str:
    normalized = re.sub(r"[^A-Za-z0-9_-]+", "-", value.strip()).strip("-")
    return normalized or "session"


def inside(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root)
        return True
    except ValueError:
        return False


def ensure_exact_keys(payload: dict[str, Any], expected: set[str], label: str) -> None:
    actual = set(payload)
    if actual != expected:
        missing = sorted(expected - actual)
        extra = sorted(actual - expected)
        details: list[str] = []
        if missing:
            details.append("missing " + ", ".join(missing))
        if extra:
            details.append("unexpected " + ", ".join(extra))
        raise HandoffError(f"{label} fields are invalid: {'; '.join(details)}")


def selection_is_explicit(evidence: str, opportunity_id: str) -> bool:
    normalized = " ".join(evidence.lower().split())
    if opportunity_id.lower() not in normalized:
        return False
    negative_markers = (
        "하지 마",
        "하지마",
        "취소",
        "금지",
        "do not",
        "don't",
        "cancel",
        "not implement",
    )
    if any(marker in normalized for marker in negative_markers):
        return False
    intent_markers = (
        "구현",
        "개발",
        "만들",
        "프로토타입",
        "작업 열",
        "진행",
        "implement",
        "build",
        "prototype",
        "create",
        "start",
    )
    return any(marker in normalized for marker in intent_markers)


def append_registry_record(workspace: Path, packet: dict[str, Any]) -> Path:
    path = workspace / REGISTRY_FILE
    record = {
        "issued_at": packet["created_at"],
        "handoff_id": packet["handoff_id"],
        "packet_sha256": packet["packet_sha256"],
        "session_id": packet["source"]["session_id"],
        "process_revision": packet["source"]["process_revision"],
        "opportunity_id": packet["selection"]["opportunity_id"],
        "confirmation_evidence": packet["selection"]["confirmation_evidence"],
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(record, ensure_ascii=False, sort_keys=True) + "\n")
        handle.flush()
        os.fsync(handle.fileno())
    return path


def registry_has_packet(workspace: Path, packet: dict[str, Any]) -> bool:
    path = workspace / REGISTRY_FILE
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except FileNotFoundError:
        return False
    expected = {
        "issued_at": packet["created_at"],
        "handoff_id": packet["handoff_id"],
        "packet_sha256": packet["packet_sha256"],
        "session_id": packet["source"]["session_id"],
        "process_revision": packet["source"]["process_revision"],
        "opportunity_id": packet["selection"]["opportunity_id"],
        "confirmation_evidence": packet["selection"]["confirmation_evidence"],
    }
    for line in lines:
        try:
            record = json.loads(line)
        except json.JSONDecodeError:
            continue
        if record == expected:
            return True
    return False


def load_analysis_validator() -> Any:
    scripts_dir = str(Path(__file__).resolve().parent)
    if scripts_dir not in sys.path:
        sys.path.insert(0, scripts_dir)
    import validate_ax_analysis

    return validate_ax_analysis


def opportunity_by_id(analysis: dict[str, Any], opportunity_id: str) -> dict[str, Any]:
    matches = [
        item
        for item in analysis.get("opportunities") or []
        if isinstance(item, dict) and item.get("id") == opportunity_id
    ]
    if len(matches) != 1:
        raise HandoffError(f"expected exactly one opportunity {opportunity_id}, found {len(matches)}")
    return matches[0]


def validate_source(
    process: dict[str, Any],
    analysis: dict[str, Any],
    opportunity_id: str,
) -> dict[str, Any]:
    if not process.get("interview_confirmed") or process.get("status") != "finalized":
        raise HandoffError("implementation handoff requires a finalized, confirmed process")
    process_analysis = process.get("analysis") or {}
    if process_analysis.get("status") != "validated":
        raise HandoffError("implementation handoff requires process.analysis.status validated")

    validator = load_analysis_validator()
    result = validator.validate_analysis(process, analysis)
    if not result.valid:
        raise HandoffError("AX analysis validation failed:\n- " + "\n- ".join(result.errors))
    if analysis.get("process_revision") != process.get("revision"):
        raise HandoffError("analysis revision does not match the finalized process")

    opportunity = opportunity_by_id(analysis, opportunity_id)
    selected = (analysis.get("mvp") or {}).get("selected_opportunity_ids") or []
    if opportunity_id not in selected:
        raise HandoffError(f"opportunity {opportunity_id} is not in the validated MVP selection")
    if opportunity.get("solution_type") == "human-only":
        raise HandoffError(f"opportunity {opportunity_id} is human-only and cannot be implemented")
    if opportunity.get("priority") == "do-not-automate":
        raise HandoffError(f"opportunity {opportunity_id} is marked do-not-automate")
    if opportunity.get("confidence") == "low":
        raise HandoffError(f"opportunity {opportunity_id} has low confidence; validate it before implementation")

    steps = {
        step.get("id"): step
        for step in process.get("steps") or []
        if isinstance(step, dict) and nonempty_text(step.get("id"))
    }
    for step_id in opportunity.get("step_ids") or []:
        step = steps.get(step_id)
        if step is None:
            raise HandoffError(f"opportunity references unknown step {step_id}")
        if step.get("status") != "confirmed":
            raise HandoffError(f"opportunity references unconfirmed step {step_id}")
    return opportunity


def make_deep_link(target_root: Path, task_path: Path) -> tuple[str, str]:
    prompt = (
        f"Use $build-ax-tool. Read {task_path} and its adjacent handoff.json. "
        "Run the verification command before any edits, then implement only the selected AX opportunity."
    )
    link = (
        "codex://threads/new?prompt="
        + quote(prompt, safe="")
        + "&path="
        + quote(str(target_root), safe="")
    )
    return prompt, link


def packet_without_integrity(packet: dict[str, Any]) -> dict[str, Any]:
    stable = copy.deepcopy(packet)
    stable.pop("handoff_id", None)
    stable.pop("packet_sha256", None)
    return stable


def expected_handoff_id(packet: dict[str, Any], digest: str) -> str:
    source = packet.get("source") or {}
    selection = packet.get("selection") or {}
    return (
        f"{safe_id(str(source.get('session_id') or 'session'))}-"
        f"{safe_id(str(selection.get('opportunity_id') or 'AX'))}-"
        f"r{source.get('process_revision')}-{digest[:12]}"
    )


def render_task(
    packet: dict[str, Any],
    handoff_path: Path,
    task_path: Path,
) -> tuple[str, str, str]:
    source = packet["source"]
    opportunity = packet["context"]["opportunity"]
    task = packet["task"]
    target_root = Path(packet["target"]["project_root"])
    prompt, deep_link = make_deep_link(target_root, task_path)
    verify_command = " ".join(
        [
            "python3",
            shlex.quote(str(Path(__file__).resolve())),
            "verify",
            shlex.quote(str(handoff_path)),
            "--workspace",
            shlex.quote(str(Path(source["workspace"]))),
        ]
    )

    def bullets(values: list[str]) -> str:
        return "\n".join(f"- {value}" for value in values) if values else "- 없음"

    lines = [
        "# Process-to-AX 구현 인계",
        "",
        f"- 인계 ID: `{packet['handoff_id']}`",
        f"- 기준본: `{source['session_id']}` / revision `{source['process_revision']}`",
        f"- 선택 기회: `{opportunity['id']}` — {opportunity['title']}",
        f"- 사용자 선택 원문: {packet['selection']['confirmation_evidence']}",
        "",
        "## 구현 목표",
        "",
        task["objective"],
        "",
        "## 먼저 검증",
        "",
        "아래 명령이 성공하기 전에는 구현 파일을 수정하지 않습니다.",
        "",
        "```bash",
        verify_command,
        "```",
        "",
        "## 확정된 대상과 경계",
        "",
        f"- 확인된 문제: {opportunity['problem']}",
        f"- 해결 유형: `{opportunity['solution_type']}`",
        f"- 자동화 경계: {opportunity['automation_boundary']}",
        "",
        "### 사람 승인 지점",
        "",
        bullets(packet["context"]["mvp"].get("human_approval_points") or []),
        "",
        "### 통제",
        "",
        bullets(opportunity.get("controls") or []),
        "",
        "### 보호된 사람 행위",
        "",
        bullets(
            [
                f"{item['step_id']}: {item['action']}"
                for item in packet["context"].get("protected_actions") or []
            ]
        ),
        "",
        "## 완료 조건",
        "",
        bullets(task["acceptance_criteria"]),
        "",
        "## 가정과 미확인 사항",
        "",
        "### 가정",
        "",
        bullets(opportunity.get("assumptions") or []),
        "",
        "### 미확인",
        "",
        bullets(opportunity.get("unknowns") or []),
        "",
        "## 기준 파일",
        "",
        f"- `{source['process_file']}`",
        f"- `{source['analysis_file']}`",
        f"- `{handoff_path}`",
        "",
        "## 새 Codex 작업",
        "",
        f"[별도 Codex 구현 작업 열기]({deep_link})",
        "",
        "링크는 새 작업의 작성란만 채웁니다. 내용을 검토한 뒤 사용자가 전송합니다.",
        "",
        "복사 가능한 시작 프롬프트:",
        "",
        "```text",
        prompt,
        "```",
        "",
        "구현 작업은 `$build-ax-tool`을 사용하고, 완료 시 이 폴더에 `RESULT.md`를 작성합니다.",
    ]
    return "\n".join(lines) + "\n", verify_command, deep_link


def create_handoff(
    *,
    workspace: Path,
    opportunity_id: str,
    target_root: Path,
    task_objective: str,
    selection_evidence: str,
    acceptance_criteria: list[str],
    constraints: list[str] | None = None,
    test_commands: list[str] | None = None,
) -> dict[str, Any]:
    workspace = workspace.expanduser().resolve()
    target_root = target_root.expanduser().resolve()
    if not workspace.is_dir():
        raise HandoffError(f"workspace directory not found: {workspace}")
    if not target_root.is_dir():
        raise HandoffError(f"target root directory not found: {target_root}")
    objective = task_objective.strip()
    evidence = selection_evidence.strip()
    criteria = clean_strings(acceptance_criteria)
    if not objective:
        raise HandoffError("task objective is required")
    if not evidence:
        raise HandoffError("user selection evidence is required")
    if not selection_is_explicit(evidence, opportunity_id):
        raise HandoffError(
            "selection evidence must explicitly name the opportunity and request implementation"
        )
    if not criteria:
        raise HandoffError("at least one acceptance criterion is required")

    process_path = workspace / PROCESS_FILE
    analysis_path = workspace / ANALYSIS_FILE
    process = read_json(process_path)
    analysis = read_json(analysis_path)
    opportunity = validate_source(process, analysis, opportunity_id)
    referenced_ids = set(opportunity.get("step_ids") or [])
    referenced_steps = [
        copy.deepcopy(step)
        for step in process.get("steps") or []
        if step.get("id") in referenced_ids
    ]
    touching_branches = [
        copy.deepcopy(branch)
        for branch in process.get("branches") or []
        if branch.get("from_step") in referenced_ids or branch.get("to_step") in referenced_ids
    ]
    protected_actions = [
        {
            "step_id": step.get("id"),
            "action": step.get("action"),
            "approvals": copy.deepcopy(step.get("approvals") or []),
            "final_action_authority": bool(step.get("final_action_authority")),
        }
        for step in referenced_steps
        if step.get("final_action_authority") or step.get("approvals")
    ]
    if any(item["final_action_authority"] for item in protected_actions) and not (
        (analysis.get("mvp") or {}).get("human_approval_points") or []
    ):
        raise HandoffError(
            "a final-action step requires an explicit human approval point before implementation"
        )
    if target_root == Path(target_root.anchor) or target_root == Path.home().resolve():
        raise HandoffError("target root is too broad; use the current saved project directory")
    implementation_output = (
        target_root / "ax-implementations" / safe_id(str(process["session_id"])) / opportunity_id
    ).resolve()
    if not inside(implementation_output, target_root):
        raise HandoffError("implementation output must stay inside target root")

    packet: dict[str, Any] = {
        "schema_version": "1.0",
        "created_at": now_iso(),
        "source": {
            "session_id": process["session_id"],
            "process_revision": process["revision"],
            "workspace": str(workspace),
            "process_file": str(process_path.resolve()),
            "analysis_file": str(analysis_path.resolve()),
            "process_sha256": file_sha256(process_path),
            "analysis_sha256": file_sha256(analysis_path),
            "process_status": process["status"],
            "analysis_status": (process.get("analysis") or {}).get("status"),
        },
        "selection": {
            "opportunity_id": opportunity_id,
            "confirmed_by_user": True,
            "confirmation_evidence": evidence,
        },
        "target": {
            "project_root": str(target_root),
            "implementation_output": str(implementation_output),
        },
        "task": {
            "objective": objective,
            "acceptance_criteria": criteria,
            "constraints": clean_strings(constraints),
            "test_commands": clean_strings(test_commands),
            "open_item_policy": "ask-before-assuming",
        },
        "context": {
            "process_title": process.get("title"),
            "scope": copy.deepcopy(process.get("scope") or {}),
            "confirmed_steps": referenced_steps,
            "touching_branches": touching_branches,
            "protected_actions": protected_actions,
            "opportunity": copy.deepcopy(opportunity),
            "mvp": copy.deepcopy(analysis.get("mvp") or {}),
            "unresolved_questions": copy.deepcopy(analysis.get("unresolved_questions") or []),
        },
        "result_contract": {
            "file": "RESULT.md",
            "allowed_statuses": ["ready_for_review", "needs_input", "needs_rebase", "failed"],
            "baseline_is_read_only": True,
        },
    }
    digest = canonical_sha256(packet_without_integrity(packet))
    packet["packet_sha256"] = digest
    packet["handoff_id"] = expected_handoff_id(packet, digest)

    output_dir = (workspace / "handoffs" / packet["handoff_id"]).resolve()
    if not inside(output_dir, workspace):
        raise HandoffError("handoff output must stay inside the session workspace")
    handoff_path = output_dir / HANDOFF_FILE
    task_path = output_dir / TASK_FILE
    task_markdown, verify_command, deep_link = render_task(packet, handoff_path, task_path)
    atomic_write_json(handoff_path, packet)
    atomic_write_text(task_path, task_markdown)
    registry_path = append_registry_record(workspace, packet)

    verification = verify_handoff(handoff_path=handoff_path, workspace=workspace)
    return {
        "handoff_id": packet["handoff_id"],
        "handoff": str(handoff_path),
        "task": str(task_path),
        "registry": str(registry_path),
        "verification_command": verify_command,
        "codex_deep_link": deep_link,
        "verified": verification["valid"],
    }


def require_mapping(payload: dict[str, Any], key: str) -> dict[str, Any]:
    value = payload.get(key)
    if not isinstance(value, dict):
        raise HandoffError(f"handoff.{key} must be an object")
    return value


def verify_handoff(*, handoff_path: Path, workspace: Path) -> dict[str, Any]:
    handoff_path = handoff_path.expanduser().resolve()
    workspace = workspace.expanduser().resolve()
    packet = read_json(handoff_path)
    ensure_exact_keys(packet, TOP_LEVEL_KEYS, "handoff")
    if packet.get("schema_version") != "1.0":
        raise HandoffError("handoff.schema_version must be 1.0")
    for key in ("source", "selection", "target", "task", "context", "result_contract"):
        require_mapping(packet, key)
    ensure_exact_keys(
        packet["source"],
        {
            "session_id",
            "process_revision",
            "workspace",
            "process_file",
            "analysis_file",
            "process_sha256",
            "analysis_sha256",
            "process_status",
            "analysis_status",
        },
        "handoff.source",
    )
    ensure_exact_keys(
        packet["selection"],
        {"opportunity_id", "confirmed_by_user", "confirmation_evidence"},
        "handoff.selection",
    )
    ensure_exact_keys(
        packet["target"],
        {"project_root", "implementation_output"},
        "handoff.target",
    )
    ensure_exact_keys(
        packet["task"],
        {"objective", "acceptance_criteria", "constraints", "test_commands", "open_item_policy"},
        "handoff.task",
    )
    ensure_exact_keys(
        packet["context"],
        {
            "process_title",
            "scope",
            "confirmed_steps",
            "touching_branches",
            "protected_actions",
            "opportunity",
            "mvp",
            "unresolved_questions",
        },
        "handoff.context",
    )
    ensure_exact_keys(
        packet["result_contract"],
        {"file", "allowed_statuses", "baseline_is_read_only"},
        "handoff.result_contract",
    )
    digest = canonical_sha256(packet_without_integrity(packet))
    if packet.get("packet_sha256") != digest:
        raise HandoffError("handoff packet digest mismatch; the embedded packet was modified")
    expected_id = expected_handoff_id(packet, digest)
    if packet.get("handoff_id") != expected_id:
        raise HandoffError("handoff id does not match packet content")
    expected_path = (workspace / "handoffs" / expected_id / HANDOFF_FILE).resolve()
    if handoff_path != expected_path:
        raise HandoffError("handoff file is not in its canonical session path")
    if not registry_has_packet(workspace, packet):
        raise HandoffError("handoff is not recorded in the session issuance registry")

    source = packet["source"]
    selection = packet["selection"]
    target = packet["target"]
    task = packet["task"]
    context = packet["context"]
    if Path(str(source.get("workspace"))).expanduser().resolve() != workspace:
        raise HandoffError("handoff workspace does not match the verification workspace")
    process_path = workspace / PROCESS_FILE
    analysis_path = workspace / ANALYSIS_FILE
    if Path(str(source.get("process_file"))).resolve() != process_path.resolve():
        raise HandoffError("handoff process path does not match the workspace")
    if Path(str(source.get("analysis_file"))).resolve() != analysis_path.resolve():
        raise HandoffError("handoff analysis path does not match the workspace")
    if file_sha256(process_path) != source.get("process_sha256"):
        raise HandoffError("process.json hash changed; issue a new implementation handoff")
    if file_sha256(analysis_path) != source.get("analysis_sha256"):
        raise HandoffError("ax-analysis.json hash changed; issue a new implementation handoff")

    process = read_json(process_path)
    analysis = read_json(analysis_path)
    opportunity_id = selection.get("opportunity_id")
    if not nonempty_text(opportunity_id):
        raise HandoffError("handoff selection opportunity id is required")
    if selection.get("confirmed_by_user") is not True or not nonempty_text(
        selection.get("confirmation_evidence")
    ):
        raise HandoffError("handoff requires explicit user selection evidence")
    if not selection_is_explicit(selection["confirmation_evidence"], opportunity_id):
        raise HandoffError("handoff selection evidence is not an explicit implementation request")
    opportunity = validate_source(process, analysis, opportunity_id)
    if source.get("session_id") != process.get("session_id"):
        raise HandoffError("handoff session id does not match process.json")
    if source.get("process_revision") != process.get("revision"):
        raise HandoffError("handoff process revision is stale")
    if source.get("process_status") != process.get("status"):
        raise HandoffError("handoff process status does not match process.json")
    if source.get("analysis_status") != (process.get("analysis") or {}).get("status"):
        raise HandoffError("handoff analysis status does not match process.json")

    expected_steps = [
        step
        for step in process.get("steps") or []
        if step.get("id") in set(opportunity.get("step_ids") or [])
    ]
    expected_branches = [
        branch
        for branch in process.get("branches") or []
        if branch.get("from_step") in set(opportunity.get("step_ids") or [])
        or branch.get("to_step") in set(opportunity.get("step_ids") or [])
    ]
    expected_protected = [
        {
            "step_id": step.get("id"),
            "action": step.get("action"),
            "approvals": copy.deepcopy(step.get("approvals") or []),
            "final_action_authority": bool(step.get("final_action_authority")),
        }
        for step in expected_steps
        if step.get("final_action_authority") or step.get("approvals")
    ]
    exact_checks = {
        "context.scope": (context.get("scope"), process.get("scope") or {}),
        "context.confirmed_steps": (context.get("confirmed_steps"), expected_steps),
        "context.touching_branches": (context.get("touching_branches"), expected_branches),
        "context.protected_actions": (context.get("protected_actions"), expected_protected),
        "context.opportunity": (context.get("opportunity"), opportunity),
        "context.mvp": (context.get("mvp"), analysis.get("mvp") or {}),
        "context.unresolved_questions": (
            context.get("unresolved_questions"),
            analysis.get("unresolved_questions") or [],
        ),
    }
    for label, (actual, expected) in exact_checks.items():
        if actual != expected:
            raise HandoffError(f"{label} does not match the validated source")

    if not nonempty_text(task.get("objective")):
        raise HandoffError("handoff task objective is required")
    if not isinstance(task.get("acceptance_criteria"), list) or not all(
        nonempty_text(item) for item in task["acceptance_criteria"]
    ) or not task["acceptance_criteria"]:
        raise HandoffError("handoff requires at least one acceptance criterion")
    if task.get("open_item_policy") != "ask-before-assuming":
        raise HandoffError("handoff open item policy must be ask-before-assuming")

    target_root = Path(str(target.get("project_root"))).expanduser().resolve()
    implementation_output = Path(str(target.get("implementation_output"))).expanduser().resolve()
    if not target_root.is_dir():
        raise HandoffError(f"target project root no longer exists: {target_root}")
    if target_root == Path(target_root.anchor) or target_root == Path.home().resolve():
        raise HandoffError("target root is too broad; use the current saved project directory")
    if not inside(implementation_output, target_root):
        raise HandoffError("implementation output escapes the target project root")
    if not inside(handoff_path, workspace):
        raise HandoffError("handoff file is outside the session workspace")
    task_path = handoff_path.parent / TASK_FILE
    try:
        actual_task = task_path.read_text(encoding="utf-8")
    except FileNotFoundError as exc:
        raise HandoffError(f"TASK.md is missing beside the handoff: {task_path}") from exc
    expected_task, _, _ = render_task(packet, handoff_path, task_path)
    if actual_task != expected_task:
        raise HandoffError("TASK.md does not match the validated handoff packet")

    return {
        "valid": True,
        "handoff_id": packet["handoff_id"],
        "session_id": process["session_id"],
        "process_revision": process["revision"],
        "opportunity_id": opportunity_id,
        "target_root": str(target_root),
        "implementation_output": str(implementation_output),
    }


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Create or verify a Process-to-AX implementation handoff."
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    create = subparsers.add_parser("create", help="Create a validated implementation handoff")
    create.add_argument("--workspace", type=Path, required=True)
    create.add_argument("--opportunity-id", required=True)
    create.add_argument("--target-root", type=Path, required=True)
    create.add_argument("--task-objective", required=True)
    create.add_argument("--selection-evidence", required=True)
    create.add_argument("--acceptance-criterion", action="append", required=True)
    create.add_argument("--constraint", action="append", default=None)
    create.add_argument("--test-command", action="append", default=None)

    verify = subparsers.add_parser("verify", help="Verify a handoff against its live source")
    verify.add_argument("handoff_json", type=Path)
    verify.add_argument("--workspace", type=Path, required=True)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        if args.command == "create":
            result = create_handoff(
                workspace=args.workspace,
                opportunity_id=args.opportunity_id.strip(),
                target_root=args.target_root,
                task_objective=args.task_objective,
                selection_evidence=args.selection_evidence,
                acceptance_criteria=args.acceptance_criterion,
                constraints=args.constraint,
                test_commands=args.test_command,
            )
        else:
            result = verify_handoff(
                handoff_path=args.handoff_json,
                workspace=args.workspace,
            )
    except HandoffError as exc:
        print(f"INVALID: {exc}", file=sys.stderr)
        return 1
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
