from pathlib import Path
import tempfile
import unittest

from docx import Document
from docx.shared import Pt
import pymupdf

from approved_fixtures import make_inputs
from resume_layout import estimate_resume_space


class ResumeLayoutTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        _, self.template = make_inputs(self.root)

    def make_pdf(self, *, pages=2, last_y=400, footer=True):
        path = self.root / "render.pdf"
        with pymupdf.open() as pdf:
            for i in range(pages):
                page = pdf.new_page(width=612, height=792)
                y = last_y if i == pages - 1 else 650
                page.insert_text((90, y), "Production risk models and approved experience.", fontname="tiro", fontsize=11)
                if footer:
                    page.insert_text((300, 775), str(i + 1), fontsize=9)
            pdf.save(path)
        return path

    def test_measures_body_space_and_ignores_footer(self):
        pdf = self.make_pdf(last_y=400)
        space = estimate_resume_space(pdf, self.template)
        # Default test-template bottom margin is 72 pt; body ends just below y=400.
        self.assertGreater(space.remaining_height_pt, 300)
        self.assertLess(space.remaining_height_pt, 330)
        self.assertEqual(space.overflow_height_pt, 0)

    def test_one_page_resume_includes_available_second_page(self):
        one = estimate_resume_space(self.make_pdf(pages=1), self.template)
        two = estimate_resume_space(self.make_pdf(pages=2), self.template)
        self.assertAlmostEqual(one.remaining_height_pt - two.remaining_height_pt, 648, delta=1)

    def test_measures_real_overflow_height_for_batched_backoff(self):
        space = estimate_resume_space(self.make_pdf(pages=3, last_y=200), self.template)
        self.assertGreater(space.overflow_height_pt, 120)
        self.assertLess(space.overflow_height_pt, 150)

    def test_estimate_uses_template_font_spacing_and_wrapping(self):
        document = Document(self.template.path)
        paragraph = document.paragraphs[self.template.bullet_indices[0]]
        paragraph.runs[0].font.name = "Times New Roman"
        paragraph.runs[0].font.size = Pt(14)
        paragraph.paragraph_format.line_spacing = 1.5
        paragraph.paragraph_format.space_after = Pt(8)
        document.save(self.template.path)
        space = estimate_resume_space(self.make_pdf(), self.template)
        metrics = space.role_layouts["amazon_data_scientist"]
        self.assertEqual(metrics.font_size_pt, 14)
        self.assertEqual(metrics.line_height_pt, 21)
        self.assertGreaterEqual(metrics.paragraph_spacing_pt, 8)
        self.assertGreater(metrics.estimate_height("Production modeling and deployment. " * 15),
                           metrics.estimate_height("Production modeling."))


if __name__ == "__main__":
    unittest.main()
