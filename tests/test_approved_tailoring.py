from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from pydantic import ValidationError

from approved_fixtures import make_inputs, make_plan, make_selection
from approved_tailoring import (
    FeedbackDecision, ProjectException, ReserveBullet, Selection, add_reserve, assemble_resume,
    decide_finbot, drop_lowest_priority, request_selection, validate_selection,
)
from openai_client import ResumeTailoringError
from resume_models import FeedbackSignal


class ApprovedTailoringTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.library, self.template = make_inputs(Path(self.temp.name))
        self.plan = make_plan()
        self.selection = make_selection()

    def validate(self, selection, **kwargs):
        validate_selection(selection, library=self.library, plan=self.plan, **kwargs)

    def test_assembles_verbatim_text_in_canonical_role_order(self):
        selection = self.selection.model_copy(update={"roles": tuple(reversed(self.selection.roles))})
        self.validate(selection)
        result = assemble_resume(selection, library=self.library, plan=self.plan)
        for role, chosen in zip(result.roles, self.selection.roles):
            self.assertEqual(role.role_id, chosen.role_id)
            self.assertEqual([b.text for b in role.bullets], [self.library.by_id()[key].text for key in chosen.bullet_ids])

    def test_excluded_finbot_cannot_be_summary_evidence(self):
        selection = self.selection.model_copy(update={"summary_sources": ("finbot",)})
        with self.assertRaisesRegex(ResumeTailoringError, "summary evidence"):
            validate_selection(selection, library=self.library, plan=make_plan(False))

    def test_plan_is_immutable_and_later_schema_has_no_finbot_field(self):
        with self.assertRaises(ValidationError):
            self.plan.keep_finbot = False
        payload = self.selection.model_dump()
        for field in ("keep_finbot", "skills", "bullet_text"):
            with self.subTest(field=field):
                with self.assertRaises(ValidationError):
                    Selection.model_validate({**payload, field: False})

    def test_selection_ids_roles_and_ranking_are_checked(self):
        for ids in (("UNKNOWN",), ("PM-01",), ("ADS-M0-01", "ADS-M0-01")):
            roles = (self.selection.roles[0].model_copy(update={"bullet_ids": ids}), *self.selection.roles[1:])
            with self.subTest(ids=ids), self.assertRaises(ResumeTailoringError):
                self.validate(self.selection.model_copy(update={"roles": roles}))
        with self.assertRaisesRegex(ResumeTailoringError, "Priority"):
            self.validate(self.selection.model_copy(update={"priority_order": ()}))

    def test_second_project_bullet_requires_rationale(self):
        role = self.selection.roles[0].model_copy(update={"bullet_ids": (*self.selection.roles[0].bullet_ids, "ADS-M0-02")})
        selection = self.selection.model_copy(update={"roles": (role, *self.selection.roles[1:]), "priority_order": (*self.selection.priority_order, "ADS-M0-02")})
        with self.assertRaisesRegex(ResumeTailoringError, "justify"):
            self.validate(selection)
        project = self.library.by_id()["ADS-M0-02"].project_key
        selection = selection.model_copy(update={"project_exceptions": (ProjectException(project=project, rationale="Distinct threshold-calibration responsibility."),)})
        self.validate(selection)

    def test_cross_project_group_can_supply_distinct_bullets(self):
        role = self.selection.roles[0].model_copy(update={"bullet_ids": ("ADS-OPS-01", "ADS-OPS-02")})
        selection = self.selection.model_copy(update={"roles": (role, *self.selection.roles[1:]), "priority_order": ("ADS-OPS-01", "ADS-OPS-02", "ABA-BA-01", "PM-01", "PC-01")})
        self.validate(selection)

    def test_removing_finbot_can_allow_more_than_eight_ds_bullets(self):
        validate_selection(make_selection(10), library=self.library, plan=make_plan(False))

    def test_overflow_removes_lowest_priority_without_changing_summary_or_text(self):
        candidate = drop_lowest_priority(self.selection, library=self.library, plan=self.plan)
        self.assertEqual(candidate.priority_order, self.selection.priority_order[:-1])
        self.assertEqual(candidate.summary, self.selection.summary)
        self.validate(candidate)

    def test_adds_reserve_with_project_exception_and_exact_text(self):
        reserve = ReserveBullet(bullet_id="ADS-M0-02", rationale="Adds threshold calibration detail.",
                                second_bullet_rationale="Distinct operational threshold responsibility.")
        initial = self.selection.model_copy(update={"reserve_bullets": (reserve,)})
        self.validate(initial)
        candidate = add_reserve(initial, reserve, library=self.library, plan=self.plan)
        self.validate(candidate)
        self.assertEqual(candidate.roles[0].bullet_ids[:2], ("ADS-M0-01", "ADS-M0-02"))
        self.assertFalse(candidate.reserve_bullets)
        self.assertEqual(candidate.project_exceptions[0].project, self.library.by_id()[reserve.bullet_id].project_key)
        self.assertEqual(assemble_resume(candidate, library=self.library, plan=self.plan).roles[0].bullets[1].text,
                         self.library.by_id()[reserve.bullet_id].text)

    def test_reserve_does_not_bypass_second_project_justification(self):
        reserve = ReserveBullet(bullet_id="ADS-M0-02", rationale="Threshold detail.", second_bullet_rationale="")
        with self.assertRaisesRegex(ResumeTailoringError, "justification"):
            add_reserve(self.selection, reserve, library=self.library, plan=self.plan)

    def test_unknown_duplicate_and_selected_reserve_ids_rejected(self):
        for ids in (("UNKNOWN",), ("ADS-M0-01",), ("ADS-M9-01", "ADS-M9-01")):
            reserves = tuple(ReserveBullet(bullet_id=key, rationale="Relevant additional evidence.", second_bullet_rationale="") for key in ids)
            with self.subTest(ids=ids), self.assertRaises(ResumeTailoringError):
                self.validate(self.selection.model_copy(update={"reserve_bullets": reserves}))

    def test_feedback_requires_complete_evidenced_decisions_and_real_changes(self):
        signals = (FeedbackSignal(signal_id="s1", text="Highlight production reliability"),)
        with self.assertRaisesRegex(ResumeTailoringError, "signals"):
            self.validate(self.selection, signals=signals)
        decision = FeedbackDecision(signal_id="s1", status="addressed", rationale="Added evidence.", evidence_refs=("ADS-M0-01",))
        unchanged = self.selection.model_copy(update={"feedback_decisions": (decision,)})
        with self.assertRaisesRegex(ResumeTailoringError, "unchanged"):
            self.validate(unchanged, signals=signals, original=self.selection)

    @patch("approved_tailoring._create_client")
    @patch("approved_tailoring._request_json")
    def test_decision_uses_real_finbot_and_subsequent_calls_get_locked_plan(self, request, _client):
        request.return_value = self.plan.model_dump_json()
        plan = decide_finbot(template=self.template, library=self.library, job_description="Build LLM evaluation systems")
        self.assertIn("Developed LLM earnings extraction", request.call_args.kwargs["user_prompt"])
        self.assertIn("approved_professional_experience", request.call_args.kwargs["user_prompt"])
        request.return_value = self.selection.model_dump_json()
        request_selection(library=self.library, template=self.template, plan=plan, job_description="Role")
        prompt = request.call_args.kwargs["user_prompt"]
        self.assertIn('"keep_finbot": true', prompt)
        schema = request.call_args.kwargs["schema"]
        self.assertNotIn("keep_finbot", schema["properties"])
        for definition in (schema, *schema.get("$defs", {}).values()):
            if definition.get("type") == "object":
                self.assertFalse(definition["additionalProperties"])
                self.assertEqual(set(definition["required"]), set(definition["properties"]))

    @patch("approved_tailoring._create_client")
    @patch("approved_tailoring._request_json", return_value="not json")
    def test_invalid_response_fails_closed(self, _request, _client):
        with self.assertRaises(ResumeTailoringError):
            decide_finbot(template=self.template, library=self.library, job_description="Role")

    @patch("approved_tailoring._create_client")
    @patch("approved_tailoring._request_json")
    def test_feedback_citation_error_gets_one_strict_correction(self, request, _client):
        signals = (FeedbackSignal(signal_id="s1", text="Highlight model monitoring"),)
        role = self.selection.roles[0].model_copy(update={"bullet_ids": (*self.selection.roles[0].bullet_ids, "ADS-OPS-01")})
        decision = FeedbackDecision(signal_id="s1", status="addressed", rationale="Added monitoring evidence.", evidence_refs=("ADS-M9-01",))
        invalid = self.selection.model_copy(update={"roles": (role, *self.selection.roles[1:]),
            "priority_order": (*self.selection.priority_order, "ADS-OPS-01"), "feedback_decisions": (decision,)})
        valid = invalid.model_copy(update={"feedback_decisions": (decision.model_copy(update={"evidence_refs": ("ADS-OPS-01",)}),)})
        request.side_effect = [invalid.model_dump_json(), valid.model_dump_json()]
        attempts = []
        result = request_selection(library=self.library, template=self.template, plan=self.plan, job_description="Role",
                                   previous=self.selection, signals=signals, review=invalid, validation_attempts=attempts)
        self.assertEqual(result, valid)
        self.assertEqual(request.call_count, 2)
        self.assertEqual(len(attempts), 1)
        correction = request.call_args.kwargs["user_prompt"]
        self.assertIn("s1", correction)
        self.assertIn("ADS-M9-01", correction)
        self.assertIn('"keep_finbot": true', correction)

    @patch("approved_tailoring._create_client")
    @patch("approved_tailoring._request_json")
    def test_invalid_selection_correction_is_bounded(self, request, _client):
        invalid = self.selection.model_copy(update={"priority_order": ()})
        request.return_value = invalid.model_dump_json()
        with self.assertRaisesRegex(ResumeTailoringError, "Priority"):
            request_selection(library=self.library, template=self.template, plan=self.plan, job_description="Role")
        self.assertEqual(request.call_count, 2)


if __name__ == "__main__":
    unittest.main()
