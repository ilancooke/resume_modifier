from __future__ import annotations

import unittest

from prompts import BASE_RESUME_RULES


class ResumePromptTests(unittest.TestCase):
    def test_requires_supported_outcomes_in_bullets(self) -> None:
        self.assertIn("OUTCOME REQUIREMENT", BASE_RESUME_RULES)
        self.assertIn(
            "include the single strongest one in the bullet",
            BASE_RESUME_RULES,
        )
        self.assertIn(
            "Omit an outcome only when the evidence contains no supported outcome.",
            BASE_RESUME_RULES,
        )


if __name__ == "__main__":
    unittest.main()
