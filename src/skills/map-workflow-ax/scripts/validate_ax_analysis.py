#!/usr/bin/env python3
"""Validate that an AX analysis is grounded in a confirmed process interview."""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


SOLUTION_TYPES = {
    "human-only",
    "ai-assist",
    "rules-api",
    "rpa",
    "dedicated-app",
    "hybrid",
}
CONFIDENCE_LEVELS = {"low", "medium", "high"}
PRIORITIES = {"now", "next", "later", "do-not-automate"}


@dataclass
class ValidationResult:
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    @property
    def valid(self) -> bool:
        return not self.errors

    def as_dict(self) -> dict[str, Any]:
        return {
            "valid": self.valid,
            "errors": self.errors,
            "warnings": self.warnings,
        }


def read_json(path: Path) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise ValueError(f"file not found: {path}") from exc
    except json.JSONDecodeError as exc:
        raise ValueError(f"invalid JSON in {path}: {exc}") from exc
    if not isinstance(payload, dict):
        raise ValueError(f"top-level JSON must be an object: {path}")
    return payload


def nonempty_text(value: Any) -> bool:
    return isinstance(value, str) and bool(value.strip())


def string_list(value: Any, *, allow_empty: bool = True) -> bool:
    if not isinstance(value, list):
        return False
    if not allow_empty and not value:
        return False
    return all(nonempty_text(item) for item in value)


def process_is_confirmed(process: dict[str, Any]) -> bool:
    return bool(process.get("interview_confirmed")) or process.get("status") in {
        "confirmed",
        "finalized",
        "completed",
    }


def source_texts(step: dict[str, Any]) -> list[str]:
    texts: list[str] = []
    for value in step.get("source_quotes") or []:
        if nonempty_text(value):
            texts.append(value.strip())
    for key in ("name", "action"):
        value = step.get(key)
        if nonempty_text(value):
            texts.append(value.strip())
    for key in ("pain_points", "decision_rules", "approvals", "exceptions"):
        for value in step.get(key) or []:
            if nonempty_text(value):
                texts.append(value.strip())
    return texts


def quote_is_grounded(quote: str, step: dict[str, Any]) -> bool:
    needle = " ".join(quote.split())
    if not needle:
        return False
    for source in source_texts(step):
        haystack = " ".join(source.split())
        if needle in haystack or haystack in needle:
            return True
    return False


def validate_process(process: dict[str, Any], result: ValidationResult) -> dict[str, dict[str, Any]]:
    if not process_is_confirmed(process):
        result.errors.append("process interview is not confirmed")

    raw_steps = process.get("steps")
    if not isinstance(raw_steps, list) or not raw_steps:
        result.errors.append("process must contain at least one step")
        return {}

    steps: dict[str, dict[str, Any]] = {}
    for index, step in enumerate(raw_steps):
        if not isinstance(step, dict):
            result.errors.append(f"process step at index {index} must be an object")
            continue
        step_id = step.get("id") or step.get("step_id")
        if not nonempty_text(step_id):
            result.errors.append(f"process step at index {index} has no id")
            continue
        if step_id in steps:
            result.errors.append(f"duplicate process step id: {step_id}")
            continue
        if step.get("status") != "confirmed":
            result.errors.append(f"process step is not confirmed: {step_id}")
        steps[step_id] = step
    return steps


def validate_opportunity(
    item: Any,
    index: int,
    steps: dict[str, dict[str, Any]],
    result: ValidationResult,
) -> str | None:
    label = f"opportunities[{index}]"
    if not isinstance(item, dict):
        result.errors.append(f"{label} must be an object")
        return None

    opportunity_id = item.get("id")
    if not nonempty_text(opportunity_id):
        result.errors.append(f"{label}.id is required")
        opportunity_id = None
    else:
        label = opportunity_id

    for field_name in (
        "title",
        "problem",
        "rationale",
        "automation_boundary",
        "value_hypothesis",
    ):
        if not nonempty_text(item.get(field_name)):
            result.errors.append(f"{label}.{field_name} is required")

    step_ids = item.get("step_ids")
    if not string_list(step_ids, allow_empty=False):
        result.errors.append(f"{label}.step_ids must be a non-empty string array")
        step_ids = []
    elif len(step_ids) != len(set(step_ids)):
        result.errors.append(f"{label}.step_ids contains duplicates")
    for step_id in step_ids:
        if step_id not in steps:
            result.errors.append(f"{label} references unknown step: {step_id}")

    evidence = item.get("evidence")
    if not isinstance(evidence, list) or not evidence:
        result.errors.append(f"{label}.evidence must be non-empty")
    else:
        for evidence_index, entry in enumerate(evidence):
            evidence_label = f"{label}.evidence[{evidence_index}]"
            if not isinstance(entry, dict):
                result.errors.append(f"{evidence_label} must be an object")
                continue
            step_id = entry.get("step_id")
            quote = entry.get("quote")
            if step_id not in steps:
                result.errors.append(f"{evidence_label} references unknown step: {step_id}")
                continue
            if step_id not in step_ids:
                result.errors.append(f"{evidence_label} step is missing from {label}.step_ids")
            if not nonempty_text(quote):
                result.errors.append(f"{evidence_label}.quote is required")
            elif not quote_is_grounded(quote, steps[step_id]):
                result.errors.append(
                    f"{evidence_label}.quote is not grounded in confirmed step {step_id}"
                )

    solution_type = item.get("solution_type")
    if solution_type not in SOLUTION_TYPES:
        result.errors.append(f"{label}.solution_type is invalid: {solution_type}")

    for field_name in (
        "product_features",
        "integrations",
        "controls",
        "risks",
        "assumptions",
        "unknowns",
        "validation_metrics",
    ):
        allow_empty = field_name not in {"controls", "validation_metrics"}
        if not string_list(item.get(field_name), allow_empty=allow_empty):
            qualifier = "a string array" if allow_empty else "a non-empty string array"
            result.errors.append(f"{label}.{field_name} must be {qualifier}")

    if solution_type in {"dedicated-app", "hybrid"} and not string_list(
        item.get("product_features"), allow_empty=False
    ):
        result.errors.append(f"{label} requires product_features for {solution_type}")

    confidence = item.get("confidence")
    if confidence not in CONFIDENCE_LEVELS:
        result.errors.append(f"{label}.confidence is invalid: {confidence}")

    priority = item.get("priority")
    if priority not in PRIORITIES:
        result.errors.append(f"{label}.priority is invalid: {priority}")
    if solution_type == "human-only" and priority != "do-not-automate":
        result.errors.append(f"{label} human-only work must use do-not-automate priority")
    if priority == "now" and confidence == "low":
        result.warnings.append(f"{label} is priority now despite low confidence")

    return opportunity_id


def validate_analysis(process: dict[str, Any], analysis: dict[str, Any]) -> ValidationResult:
    result = ValidationResult()
    steps = validate_process(process, result)

    if analysis.get("schema_version") != "1.0":
        result.errors.append("analysis.schema_version must be 1.0")
    if not nonempty_text(analysis.get("session_id")):
        result.errors.append("analysis.session_id is required")
    process_session_id = process.get("session_id")
    if process_session_id and analysis.get("session_id") != process_session_id:
        result.errors.append("analysis.session_id does not match process.session_id")

    process_revision = process.get("revision")
    analysis_revision = analysis.get("process_revision")
    if not isinstance(analysis_revision, int) or analysis_revision < 1:
        result.errors.append("analysis.process_revision must be a positive integer")
    elif isinstance(process_revision, int) and analysis_revision != process_revision:
        result.errors.append(
            f"analysis.process_revision {analysis_revision} does not match process revision {process_revision}"
        )

    if not nonempty_text(analysis.get("summary")):
        result.errors.append("analysis.summary is required")

    opportunities = analysis.get("opportunities")
    opportunity_ids: list[str] = []
    if not isinstance(opportunities, list):
        result.errors.append("analysis.opportunities must be an array")
        opportunities = []
    for index, item in enumerate(opportunities):
        opportunity_id = validate_opportunity(item, index, steps, result)
        if opportunity_id:
            opportunity_ids.append(opportunity_id)
    if len(opportunity_ids) != len(set(opportunity_ids)):
        result.errors.append("analysis opportunity ids must be unique")

    mvp = analysis.get("mvp")
    if not isinstance(mvp, dict):
        result.errors.append("analysis.mvp must be an object")
    else:
        selected = mvp.get("selected_opportunity_ids")
        if not string_list(selected):
            result.errors.append("mvp.selected_opportunity_ids must be a string array")
            selected = []
        for opportunity_id in selected:
            if opportunity_id not in opportunity_ids:
                result.errors.append(f"mvp references unknown opportunity: {opportunity_id}")
        for field_name in (
            "scope_in",
            "scope_out",
            "user_stories",
            "success_metrics",
            "human_approval_points",
        ):
            allow_empty = field_name != "success_metrics"
            if not string_list(mvp.get(field_name), allow_empty=allow_empty):
                qualifier = "a string array" if allow_empty else "a non-empty string array"
                result.errors.append(f"mvp.{field_name} must be {qualifier}")

    if not string_list(analysis.get("unresolved_questions")):
        result.errors.append("analysis.unresolved_questions must be a string array")

    return result


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Validate an AX analysis against a confirmed process interview."
    )
    parser.add_argument("process_json", type=Path, help="Confirmed process.json file")
    parser.add_argument("analysis_json", type=Path, help="Generated ax-analysis.json file")
    parser.add_argument(
        "--format",
        choices=("text", "json"),
        default="text",
        help="Validation output format",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        process = read_json(args.process_json)
        analysis = read_json(args.analysis_json)
    except ValueError as exc:
        print(str(exc), file=sys.stderr)
        return 2

    result = validate_analysis(process, analysis)
    if args.format == "json":
        print(json.dumps(result.as_dict(), ensure_ascii=False, indent=2))
    else:
        print("VALID" if result.valid else "INVALID")
        for error in result.errors:
            print(f"ERROR: {error}")
        for warning in result.warnings:
            print(f"WARNING: {warning}")
    return 0 if result.valid else 1


if __name__ == "__main__":
    raise SystemExit(main())
