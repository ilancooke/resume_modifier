"""Conservative space estimates for one batched resume expansion, verified by Word."""

from __future__ import annotations

from dataclasses import dataclass
from math import ceil
from pathlib import Path
from statistics import median

from docx import Document
from docx.oxml.ns import qn

from docx_editor import ResumeTemplate, _find_bullet_numbering_id
from resume_models import ROLE_SPECS


@dataclass(frozen=True, slots=True)
class BulletLayout:
    font_size_pt: float
    line_height_pt: float
    paragraph_spacing_pt: float
    text_width_pt: float
    font_code: str
    width_scale: float

    def estimate_height(self, text: str) -> float:
        """Wrap words with calibrated font metrics, leaving some horizontal slack."""
        import pymupdf

        font = pymupdf.Font(self.font_code)
        width = self.text_width_pt * 0.94
        space = font.text_length(" ", fontsize=self.font_size_pt) * self.width_scale
        lines, used = 1, 0.0
        for word in text.split():
            length = font.text_length(word, fontsize=self.font_size_pt) * self.width_scale
            if used and used + space + length > width:
                lines += 1
                used = 0.0
            if length > width:
                extra = ceil(length / width) - 1
                lines += extra
                used = length - extra * width
            else:
                used += (space if used else 0) + length
        return lines * self.line_height_pt + self.paragraph_spacing_pt


@dataclass(frozen=True, slots=True)
class ResumeSpace:
    remaining_height_pt: float
    overflow_height_pt: float
    role_layouts: dict[str, BulletLayout]


def _paragraph_property(paragraph, name):
    value = getattr(paragraph.paragraph_format, name)
    style = paragraph.style
    while value is None and style is not None:
        value = getattr(style.paragraph_format, name)
        style = style.base_style
    return value


def _font_property(paragraph, name):
    for run in paragraph.runs:
        value = getattr(run.font, name)
        if value is not None:
            return value
    style = paragraph.style
    while style is not None:
        value = getattr(style.font, name)
        if value is not None:
            return value
        style = style.base_style
    return None


def _numbering_indent(document, template: ResumeTemplate) -> float:
    numbering = document.part.numbering_part.element
    number_id = str(_find_bullet_numbering_id(template.path))
    for number in numbering.findall(qn("w:num")):
        if number.get(qn("w:numId")) != number_id:
            continue
        abstract_id = number.find(qn("w:abstractNumId")).get(qn("w:val"))
        for abstract in numbering.findall(qn("w:abstractNum")):
            if abstract.get(qn("w:abstractNumId")) != abstract_id:
                continue
            for level in abstract.findall(qn("w:lvl")):
                if level.get(qn("w:ilvl"), "0") != "0":
                    continue
                indent = level.find(qn("w:pPr") + "/" + qn("w:ind"))
                if indent is not None:
                    return float(indent.get(qn("w:left"), indent.get(qn("w:start"), "360"))) / 20
    return 18.0


def _body_bottom(page, top: float, bottom: float) -> float:
    # Blank paragraph markers and footer/page-number text must not consume body space.
    ends = [block[3] for block in page.get_text("blocks")
            if block[4].strip() and block[1] < bottom and block[3] > top]
    return max(ends, default=top)


def estimate_resume_space(pdf_path: Path, template: ResumeTemplate) -> ResumeSpace:
    """Measure the rendered body and infer wrapping from unchanged template formatting.

    Font widths are calibrated against real PDF spans. This is an estimate only: a single
    batch render, plus at most one trimmed-batch verification, remains authoritative.
    """
    import pymupdf

    document = Document(template.path)
    section = document.sections[0]
    top, bottom_margin = section.top_margin.pt, section.bottom_margin.pt
    body_width = section.page_width.pt - section.left_margin.pt - section.right_margin.pt
    indent = _numbering_indent(document, template)
    with pymupdf.open(pdf_path) as pdf:
        if not pdf.page_count:
            raise ValueError("Cannot estimate space in an empty PDF.")
        last = pdf[-1]
        bottom = last.rect.height - bottom_margin
        remaining = max(0.0, bottom - _body_bottom(last, top, bottom))
        remaining += max(0, 2 - pdf.page_count) * (last.rect.height - top - bottom_margin)
        overflow = sum(max(0.0, _body_bottom(page, top, page.rect.height - bottom_margin) - top)
                       for page in list(pdf)[2:])
        spans = [span for page in pdf for block in page.get_text("dict")["blocks"]
                 for line in block.get("lines", []) for span in line["spans"]
                 if len(span.get("text", "").strip()) >= 15
                 and "bold" not in span.get("font", "").lower()]
        layouts = {}
        for spec, index in zip(ROLE_SPECS, template.bullet_indices, strict=True):
            paragraph = document.paragraphs[index]
            font_name = _font_property(paragraph, "name") or "Calibri"
            size = _font_property(paragraph, "size")
            font_size = size.pt if size is not None else 11.0
            # Base-14 metrics are portable; calibration accounts for the actual Word font.
            serif = any(name in font_name.lower() for name in ("garamond", "cambria", "times", "georgia"))
            font_code = "tiro" if serif else "helv"
            font = pymupdf.Font(font_code)
            matching = [span for span in spans if font_name.lower().replace(" ", "") in span["font"].lower().replace(" ", "")]
            ratios = []
            for span in matching or spans:
                if abs(span["size"] - font_size) > 0.5:
                    continue
                estimated = font.text_length(span["text"], fontsize=font_size)
                if estimated > 0:
                    ratios.append((span["bbox"][2] - span["bbox"][0]) / estimated)
            width_scale = min(1.5, max(0.6, median(ratios))) if ratios else 1.0
            spacing = _paragraph_property(paragraph, "line_spacing")
            if spacing is None:
                line_height = font_size * 1.15
            elif isinstance(spacing, float):
                line_height = font_size * spacing
            else:
                line_height = max(font_size, spacing.pt)
            before = _paragraph_property(paragraph, "space_before")
            after = _paragraph_property(paragraph, "space_after")
            left = _paragraph_property(paragraph, "left_indent")
            right = _paragraph_property(paragraph, "right_indent")
            text_width = body_width - (left.pt if left is not None else indent) - (right.pt if right is not None else 0)
            layouts[spec.role_id] = BulletLayout(
                font_size, line_height, (before.pt if before is not None else 0) + (after.pt if after is not None else 0),
                max(font_size, text_width), font_code, width_scale,
            )
    return ResumeSpace(remaining, overflow, layouts)
