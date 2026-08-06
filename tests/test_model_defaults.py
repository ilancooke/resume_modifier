from __future__ import annotations

import inspect
import sys
import unittest
from unittest.mock import patch

from match_client import generate_resume_match_report
from openai_client import compress_tailored_resume, generate_tailored_resume
from tailor_resume import parse_args as parse_tailor_args
from validate_resume_match import parse_args as parse_match_args


DEFAULT_MODEL = "gpt-5.6-sol"


class ModelDefaultTests(unittest.TestCase):
    def test_callable_defaults_use_gpt_5_6_sol(self) -> None:
        for function in (
            generate_tailored_resume,
            compress_tailored_resume,
            generate_resume_match_report,
        ):
            self.assertEqual(inspect.signature(function).parameters["model"].default, DEFAULT_MODEL)

    def test_cli_defaults_use_gpt_5_6_sol(self) -> None:
        with patch.object(sys, "argv", ["tailor_resume.py"]):
            self.assertEqual(parse_tailor_args().model, DEFAULT_MODEL)
        with patch.object(sys, "argv", ["validate_resume_match.py", "--resume", "resume.pdf"]):
            self.assertEqual(parse_match_args().model, DEFAULT_MODEL)


if __name__ == "__main__":
    unittest.main()
