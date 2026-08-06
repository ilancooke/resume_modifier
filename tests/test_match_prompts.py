from __future__ import annotations

import unittest

from match_prompts import SYSTEM_PROMPT, build_user_prompt


class MatchPromptTests(unittest.TestCase):
    def test_logistical_preferences_do_not_affect_matching(self) -> None:
        self.assertIn(
            "must not affect any dimension or the overall score",
            SYSTEM_PROMPT,
        )
        prompt = build_user_prompt(
            resume_text="Candidate resume text",
            job_description="Role requires travel and a preferred hub.",
        )
        self.assertIn(
            "from all criteria, hard blockers, missing or weak signals, recommendations, and scoring",
            prompt,
        )


if __name__ == "__main__":
    unittest.main()
