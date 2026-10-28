import json
from pathlib import Path
import tempfile
import unittest

from config import ConfigError, load_config


class ConfigTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name) / "config.json"
        self.payload = {
            "resume_template_path": "/tmp/stub.docx", "bullet_library_dir": "/tmp/approved",
            "job_description_path": "/tmp/job.txt", "tailored_resumes_dir": "/tmp/output",
            "resume_diagnostics_dir": "/tmp/diagnostics",
        }

    def test_standard_config_requires_no_narrative_history(self):
        self.path.write_text(json.dumps(self.payload))
        config = load_config(self.path)
        self.assertIsNone(config.career_history_dir)
        self.assertEqual(config.bullet_library_dir, Path("/tmp/approved").resolve())

    def test_missing_or_empty_bullet_library_is_error(self):
        for value in (None, "", 123):
            self.payload["bullet_library_dir"] = value
            self.path.write_text(json.dumps(self.payload))
            with self.subTest(value=value), self.assertRaisesRegex(ConfigError, "bullet_library_dir"):
                load_config(self.path)


if __name__ == "__main__":
    unittest.main()
