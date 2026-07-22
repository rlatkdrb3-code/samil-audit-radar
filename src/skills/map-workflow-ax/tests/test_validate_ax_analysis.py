from __future__ import annotations

import importlib.util
import sys
import unittest
from pathlib import Path


SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "validate_ax_analysis.py"
SPEC = importlib.util.spec_from_file_location("validate_ax_analysis", SCRIPT)
assert SPEC and SPEC.loader
validate_ax_analysis = importlib.util.module_from_spec(SPEC)
sys.modules["validate_ax_analysis"] = validate_ax_analysis
SPEC.loader.exec_module(validate_ax_analysis)


def sample_process() -> dict:
    return {
        "session_id": "expense-demo",
        "revision": 3,
        "interview_confirmed": True,
        "steps": [
            {
                "id": "S01",
                "name": "증빙 대조",
                "action": "담당자가 신청서와 영수증을 눈으로 대조한다.",
                "status": "confirmed",
                "source_quotes": ["영수증 내용을 신청서랑 사람이 하나씩 대조해요."],
                "pain_points": ["반복적인 수작업 대조"],
                "decision_rules": [],
                "approvals": [],
                "exceptions": ["영수증 화질이 낮으면 다시 요청한다."],
            }
        ],
    }


def sample_analysis() -> dict:
    return {
        "schema_version": "1.0",
        "session_id": "expense-demo",
        "process_revision": 3,
        "summary": "증빙 대조를 보조하되 최종 판단은 사람이 유지한다.",
        "opportunities": [
            {
                "id": "AX01",
                "title": "증빙 대조 보조",
                "step_ids": ["S01"],
                "evidence": [
                    {
                        "step_id": "S01",
                        "quote": "영수증 내용을 신청서랑 사람이 하나씩 대조해요.",
                    }
                ],
                "problem": "수작업 대조가 반복된다.",
                "solution_type": "dedicated-app",
                "rationale": "OCR 결과와 신청정보를 비교하고 상태를 저장해야 한다.",
                "automation_boundary": "불일치 표시까지만 자동화하고 승인 판단은 사람이 한다.",
                "product_features": ["증빙 보관", "불일치 검토함"],
                "integrations": ["그룹웨어"],
                "controls": ["원문 증빙 표시", "사람 최종 승인"],
                "risks": ["OCR 오인식"],
                "assumptions": [],
                "unknowns": ["월 처리량"],
                "value_hypothesis": "대조 시간을 줄일 수 있다.",
                "validation_metrics": ["건당 검토시간", "누락 탐지율"],
                "confidence": "medium",
                "priority": "now",
            }
        ],
        "mvp": {
            "selected_opportunity_ids": ["AX01"],
            "scope_in": ["증빙 업로드와 불일치 표시"],
            "scope_out": ["자동 승인과 지급"],
            "user_stories": ["담당자로서 불일치 건만 보고 싶다."],
            "success_metrics": ["건당 검토시간"],
            "human_approval_points": ["비용 승인"],
        },
        "unresolved_questions": ["월 처리량은 몇 건인가?"],
    }


class ValidateAxAnalysisTests(unittest.TestCase):
    def test_valid_grounded_analysis(self) -> None:
        result = validate_ax_analysis.validate_analysis(sample_process(), sample_analysis())
        self.assertTrue(result.valid, result.errors)

    def test_rejects_unconfirmed_process(self) -> None:
        process = sample_process()
        process["interview_confirmed"] = False
        result = validate_ax_analysis.validate_analysis(process, sample_analysis())
        self.assertFalse(result.valid)
        self.assertIn("process interview is not confirmed", result.errors)

    def test_rejects_ungrounded_quote(self) -> None:
        analysis = sample_analysis()
        analysis["opportunities"][0]["evidence"][0]["quote"] = "AI가 필요하다고 확정했다."
        result = validate_ax_analysis.validate_analysis(sample_process(), analysis)
        self.assertFalse(result.valid)
        self.assertTrue(any("not grounded" in error for error in result.errors))

    def test_rejects_unknown_step(self) -> None:
        analysis = sample_analysis()
        analysis["opportunities"][0]["step_ids"] = ["S99"]
        result = validate_ax_analysis.validate_analysis(sample_process(), analysis)
        self.assertFalse(result.valid)
        self.assertTrue(any("unknown step: S99" in error for error in result.errors))

    def test_dedicated_app_requires_product_features(self) -> None:
        analysis = sample_analysis()
        analysis["opportunities"][0]["product_features"] = []
        result = validate_ax_analysis.validate_analysis(sample_process(), analysis)
        self.assertFalse(result.valid)
        self.assertTrue(any("requires product_features" in error for error in result.errors))

    def test_human_only_must_not_be_prioritized_for_automation(self) -> None:
        analysis = sample_analysis()
        opportunity = analysis["opportunities"][0]
        opportunity["solution_type"] = "human-only"
        opportunity["priority"] = "now"
        result = validate_ax_analysis.validate_analysis(sample_process(), analysis)
        self.assertFalse(result.valid)
        self.assertTrue(any("human-only" in error for error in result.errors))


if __name__ == "__main__":
    unittest.main()
