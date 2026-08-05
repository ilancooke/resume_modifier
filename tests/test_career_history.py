from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from career_history import CareerHistoryError, load_career_history
from resume_models import ROLE_SPECS


class CareerHistoryTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp_dir.cleanup)
        self.root = Path(self.temp_dir.name)
        self._write("Amazon/Data Scientist/project.md", "# DS project\n\nResult: 10%")
        self._write("Amazon/BA-BIE/history.md", "# BA/BIE work")
        self._write("Petsmart/PetSmart - Manager, Business Analytics.md", "# Manager work")
        self._write("Petsmart/PetSmart - Customer Insights Analyst.md", "# Analyst work")
        self._write("Template.md", "# Empty questionnaire template")

    def _write(self, relative_path: str, text: str) -> None:
        path = self.root / relative_path
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")

    def test_assigns_files_by_hierarchy_and_excludes_template(self) -> None:
        history = load_career_history(self.root)

        self.assertEqual(history.excluded_files, ("Template.md",))
        self.assertEqual(len(history.files), 4)
        self.assertEqual(
            {file.role_id for file in history.files},
            {spec.role_id for spec in ROLE_SPECS},
        )
        context = history.model_context()
        self.assertIn("Amazon/Data Scientist/project.md", context)
        self.assertIn("# DS project", context)
        self.assertNotIn("Empty questionnaire", context)

    def test_rejects_unassigned_markdown(self) -> None:
        self._write("Other/unassigned.md", "# Unknown")

        with self.assertRaisesRegex(CareerHistoryError, "Could not assign"):
            load_career_history(self.root)

    def test_requires_evidence_for_every_role(self) -> None:
        (self.root / "Amazon/Data Scientist/project.md").unlink()

        with self.assertRaisesRegex(CareerHistoryError, "amazon_data_scientist"):
            load_career_history(self.root)


if __name__ == "__main__":
    unittest.main()

