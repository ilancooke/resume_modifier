from pathlib import Path
import tempfile
import unittest

from approved_fixtures import make_inputs
from bullet_library import BulletLibraryError, load_bullet_library


class BulletLibraryTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.library, _ = make_inputs(self.root)

    def test_reads_ids_exact_text_roles_and_cross_project_metadata(self):
        self.assertEqual(len(self.library.bullets), 16)
        bullet = self.library.by_id()["PC-01"]
        self.assertEqual(bullet.text, "Automated recurring brand reporting with PL/SQL.")
        self.assertEqual(bullet.role_id, "petsmart_analytics")
        self.assertEqual(bullet.project, "Customer Insights Analyst")
        self.assertTrue(self.library.by_id()["ADS-OPS-01"].cross_project)
        self.assertGreater(self.library.role_limits()["amazon_data_scientist"], 8)

    def test_duplicate_ids_rejected(self):
        path = self.library.root / "Amazon/Data Scientist/duplicate.md"
        path.write_text("# DS\n## Another\n- **ADS-M0-01:** Duplicate accomplishment.\n")
        with self.assertRaisesRegex(BulletLibraryError, "Duplicate"):
            load_bullet_library(self.library.root)

    def test_unknown_role_path_rejected(self):
        (self.library.root / "unassigned.md").write_text("Evidence")
        with self.assertRaisesRegex(BulletLibraryError, "Unrecognized"):
            load_bullet_library(self.library.root)

    def test_missing_role_rejected(self):
        (self.library.root / "Petsmart/PetSmart_Bullets.md").unlink()
        with self.assertRaisesRegex(BulletLibraryError, "Missing"):
            load_bullet_library(self.library.root)

    def test_malformed_bullet_and_wrong_role_id_rejected(self):
        path = self.library.root / "Amazon/Data Scientist/bad.md"
        for text in ("- Plain bullet", "- **PM-02:** Wrong role.", "- **ADS-EMPTY-01:** "):
            with self.subTest(text=text):
                path.write_text("# DS\n## Project\n" + text)
                with self.assertRaises(BulletLibraryError):
                    load_bullet_library(self.library.root)

    def test_empty_file_rejected(self):
        (self.library.root / "Amazon/Data Scientist/empty.md").write_text("# Empty")
        with self.assertRaisesRegex(BulletLibraryError, "No approved"):
            load_bullet_library(self.library.root)


if __name__ == "__main__":
    unittest.main()
