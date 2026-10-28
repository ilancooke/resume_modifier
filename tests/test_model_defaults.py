from __future__ import annotations

import inspect
import sys
import unittest
from unittest.mock import patch

from approved_tailoring import decide_finbot, request_selection
from match_client import generate_resume_match_report
from openai_client import (
    compress_tailored_resume,
    generate_tailored_resume,
    generate_tailored_resume_with_trace,
    request_feedback_revision,
)
from tailor_resume import parse_args as parse_tailor_args
from validate_resume_match import parse_args as parse_match_args


DEFAULT_MODEL = "gpt-6.1-terra"


class ModelDefaultTests(unittest.TestCase):
    def test_callable_defaults_use_gpt_6_1_terra(self) -> None:
        for function in (
            decide_finbot,
            request_selection,
            generate_tailored_resume,
            generate_tailored_resume_with_trace,
            compress_tailored_resume,
            generate_resume_match_report,
            request_feedback_revision,
        ):
            self.assertEqual(inspect.signature(function).parameters["model"].default, DEFAULT_MODEL)

    def test_cli_defaults_use_gpt_6_1_terra(self) -> None:
        with patch.object(sys, "argv", ["tailor_resume.py"]):
            args = parse_tailor_args()
            self.assertEqual(args.model, DEFAULT_MODEL)
            self.assertFalse(args.skip_feedback_revision)
        with patch.object(sys, "argv", ["validate_resume_match.py", "--resume", "resume.pdf"]):
            self.assertEqual(parse_match_args().model, DEFAULT_MODEL)

    def test_feedback_cycle_can_be_disabled(self) -> None:
        with patch.object(sys, "argv", ["tailor_resume.py", "--skip-feedback-revision"]):
            self.assertTrue(parse_tailor_args().skip_feedback_revision)


if __name__ == "__main__":
    unittest.main()
