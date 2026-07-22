from __future__ import annotations

import importlib.util
import json
import sys
import tempfile
import unittest
from argparse import Namespace
from pathlib import Path


SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "session_manager.py"
SPEC = importlib.util.spec_from_file_location("session_manager", SCRIPT)
assert SPEC and SPEC.loader
session_manager = importlib.util.module_from_spec(SPEC)
sys.modules["session_manager"] = session_manager
SPEC.loader.exec_module(session_manager)


def args(**kwargs):
    defaults = {"expected_revision": None}
    defaults.update(kwargs)
    return Namespace(**defaults)


class SessionManagerTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.workspace = Path(self.temp.name) / "demo"
        session_manager.command_init(
            args(workspace=self.workspace, session_id="demo", title="경비 정산")
        )

    def tearDown(self) -> None:
        self.temp.cleanup()

    def set_scope(self) -> dict:
        return session_manager.command_set_scope(
            args(
                workspace=self.workspace,
                purpose="업무 경비를 정산한다.",
                start_trigger="직원이 비용을 지출한다.",
                end_condition="전표와 지급이 완료된다.",
                included=["국내 업무경비"],
                excluded=["해외출장"],
                customer=["신청 직원"],
                source_quote=["출장비 정산 업무를 정리하고 싶어요."],
            )
        )

    def add_step(self, step_id: str = "S01", next_steps=None) -> dict:
        return session_manager.command_add_step(
            args(
                workspace=self.workspace,
                id=step_id,
                name="증빙 입력",
                action="신청자가 영수증 내용을 입력한다.",
                actor="신청자",
                input=["영수증"],
                output=["경비 신청서"],
                system=["그룹웨어"],
                decision_rule=None,
                approval=None,
                exception=None,
                frequency_per_month=None,
                minutes_per_case=None,
                manual_ratio_pct=None,
                pain_point=["반복 입력"],
                data_sensitivity=None,
                financial_or_legal_impact=None,
                final_action_authority=None,
                confidence="high",
                source_quote=["영수증 내용을 그룹웨어에 하나씩 입력해요."],
                next_step=next_steps,
            )
        )

    def test_init_creates_state_and_log(self) -> None:
        process = session_manager.load_process(self.workspace)
        self.assertEqual(process["session_id"], "demo")
        self.assertEqual(process["revision"], 1)
        self.assertTrue((self.workspace / "change-log.jsonl").is_file())

    def test_confirm_and_complete_interview(self) -> None:
        self.set_scope()
        self.add_step()
        session_manager.command_confirm_step(args(workspace=self.workspace, id="S01"))
        process = session_manager.command_complete(args(workspace=self.workspace))
        self.assertTrue(process["interview_confirmed"])
        self.assertEqual(process["analysis"]["status"], "unlocked")

    def test_complete_rejects_draft_step(self) -> None:
        self.set_scope()
        self.add_step()
        with self.assertRaises(session_manager.SessionError) as context:
            session_manager.command_complete(args(workspace=self.workspace))
        self.assertIn("not confirmed", str(context.exception))

    def test_revision_guard_rejects_stale_update(self) -> None:
        process = self.set_scope()
        with self.assertRaises(session_manager.SessionError) as context:
            session_manager.command_set_scope(
                args(
                    workspace=self.workspace,
                    expected_revision=process["revision"] - 1,
                    purpose="다른 목적",
                    start_trigger=None,
                    end_condition=None,
                    included=None,
                    excluded=None,
                    customer=None,
                    source_quote=None,
                )
            )
        self.assertIn("revision conflict", str(context.exception))

    def test_edit_after_confirmation_reopens_and_archives_analysis(self) -> None:
        self.set_scope()
        self.add_step()
        session_manager.command_confirm_step(args(workspace=self.workspace, id="S01"))
        confirmed = session_manager.command_complete(args(workspace=self.workspace))
        (self.workspace / "ax-analysis.json").write_text("{}\n", encoding="utf-8")
        updated = session_manager.command_update_step(
            args(
                workspace=self.workspace,
                id="S01",
                name=None,
                action="신청자가 영수증과 목적을 입력한다.",
                actor=None,
                input=None,
                output=None,
                system=None,
                decision_rule=None,
                approval=None,
                exception=None,
                frequency_per_month=None,
                minutes_per_case=None,
                manual_ratio_pct=None,
                pain_point=None,
                data_sensitivity=None,
                financial_or_legal_impact=None,
                final_action_authority=None,
                confidence=None,
                source_quote=["영수증과 목적을 입력해요."],
                next_step=None,
                expected_revision=confirmed["revision"],
            )
        )
        self.assertFalse(updated["interview_confirmed"])
        self.assertEqual(updated["steps"][0]["status"], "draft")
        self.assertFalse((self.workspace / "ax-analysis.json").exists())
        self.assertTrue(list(self.workspace.glob("ax-analysis.stale-*.json")))

    def test_rejected_edit_does_not_invalidate_confirmed_session(self) -> None:
        self.set_scope()
        self.add_step()
        session_manager.command_confirm_step(args(workspace=self.workspace, id="S01"))
        confirmed = session_manager.command_complete(args(workspace=self.workspace))
        analysis_path = self.workspace / "ax-analysis.json"
        analysis_path.write_text("{}\n", encoding="utf-8")

        with self.assertRaises(session_manager.SessionError):
            session_manager.command_add_branch(
                args(
                    workspace=self.workspace,
                    id="B01",
                    from_step="S01",
                    to_step="S99",
                    condition="누락",
                    actor="신청자",
                    action="보완한다.",
                    kind="exception",
                    source_quote=["누락이면 보완해요."],
                    expected_revision=confirmed["revision"],
                )
            )

        current = session_manager.load_process(self.workspace)
        self.assertTrue(current["interview_confirmed"])
        self.assertTrue(analysis_path.exists())
        self.assertFalse(list(self.workspace.glob("ax-analysis.stale-*.json")))

    def test_branch_requires_existing_destination(self) -> None:
        self.add_step()
        with self.assertRaises(session_manager.SessionError):
            session_manager.command_add_branch(
                args(
                    workspace=self.workspace,
                    id="B01",
                    from_step="S01",
                    to_step="S99",
                    condition="누락",
                    actor="신청자",
                    action="보완한다.",
                    kind="exception",
                    source_quote=["누락이면 보완해요."],
                )
            )

    def test_report_renders_as_is_process(self) -> None:
        self.set_scope()
        self.add_step()
        session_manager.command_confirm_step(args(workspace=self.workspace, id="S01"))
        session_manager.command_complete(args(workspace=self.workspace))
        output = session_manager.command_report(args(workspace=self.workspace))
        report = Path(output["report"]).read_text(encoding="utf-8")
        self.assertIn("경비 정산 Process-to-AX", report)
        self.assertIn("신청자가 영수증 내용을 입력한다", report)

    def test_finalize_valid_analysis(self) -> None:
        self.set_scope()
        self.add_step()
        session_manager.command_confirm_step(args(workspace=self.workspace, id="S01"))
        process = session_manager.command_complete(args(workspace=self.workspace))
        analysis = {
            "schema_version": "1.0",
            "session_id": "demo",
            "process_revision": process["revision"],
            "summary": "반복 입력을 줄이고 승인 판단은 사람에게 남긴다.",
            "opportunities": [
                {
                    "id": "AX01",
                    "title": "증빙 입력 보조",
                    "step_ids": ["S01"],
                    "evidence": [{"step_id": "S01", "quote": "영수증 내용을 그룹웨어에 하나씩 입력해요."}],
                    "problem": "반복 입력",
                    "solution_type": "dedicated-app",
                    "rationale": "입력 결과를 저장하고 검토해야 한다.",
                    "automation_boundary": "초안 입력까지만 자동화하고 제출은 사용자가 확인한다.",
                    "product_features": ["증빙 보관", "입력 검토"],
                    "integrations": ["그룹웨어"],
                    "controls": ["원문 표시", "사용자 제출 승인"],
                    "risks": ["OCR 오류"],
                    "assumptions": [],
                    "unknowns": ["월 처리량"],
                    "value_hypothesis": "작성시간을 줄일 수 있다.",
                    "validation_metrics": ["건당 작성시간"],
                    "confidence": "medium",
                    "priority": "now",
                }
            ],
            "mvp": {
                "selected_opportunity_ids": ["AX01"],
                "scope_in": ["증빙 입력 초안"],
                "scope_out": ["자동 지급"],
                "user_stories": ["신청자로서 반복 입력을 줄이고 싶다."],
                "success_metrics": ["건당 작성시간"],
                "human_approval_points": ["신청서 제출"],
            },
            "unresolved_questions": ["월 처리량은 몇 건인가?"],
        }
        (self.workspace / "ax-analysis.json").write_text(
            json.dumps(analysis, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        output = session_manager.command_finalize(args(workspace=self.workspace))
        self.assertEqual(output["status"], "finalized")
        self.assertTrue((self.workspace / "final-report.md").is_file())


if __name__ == "__main__":
    unittest.main()
