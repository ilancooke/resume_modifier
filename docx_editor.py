"""Template-specific DOCX inspection and OOXML editing utilities."""

from __future__ import annotations

import difflib
import os
import tempfile
from copy import deepcopy
from dataclasses import dataclass
from pathlib import Path
from zipfile import ZIP_DEFLATED, ZipFile

from docx.oxml import OxmlElement, parse_xml
from docx.oxml.ns import qn

from resume_models import (
    BULLETS_PLACEHOLDER,
    ROLE_SPECS,
    SUMMARY_PLACEHOLDER,
    TailoredResume,
)

DOCUMENT_XML_PATH = "word/document.xml"
NUMBERING_XML_PATH = "word/numbering.xml"
KEEP_WITH_NEXT_PREFIXES = (
    "WORK EXPERIENCE",
    "AMAZON |",
    "Data Scientist, Marketplace Fraud & Abuse |",
    "Business Intelligence Engineer |",
    "Business Analyst |",
    "Related Accounts Enforcement",
    "PETSMART |",
    "Manager, Business Analytics |",
    "Customer Insights Analyst, Marketing Analytics |",
    "EDUCATION",
    "CERTIFICATIONS & TECHNOLOGIES",
)


class DocxEditingError(RuntimeError):
    """Raised when the resume template cannot be parsed or edited safely."""


@dataclass(frozen=True, slots=True)
class ResumeTemplate:
    """Validated placeholder and paragraph structure from the resume stub."""

    path: Path
    paragraph_texts: tuple[str, ...]
    summary_index: int
    bullet_indices: tuple[int, ...]

    def full_text(self) -> str:
        return "\n".join(text for text in self.paragraph_texts if text)


def load_resume_template(path: str | Path) -> ResumeTemplate:
    """Validate the summary and four role-specific placeholder blocks."""

    template_path = Path(path).expanduser().resolve()
    if not template_path.is_file():
        raise DocxEditingError(f"Resume template not found: {template_path}")
    if template_path.suffix.lower() != ".docx":
        raise DocxEditingError(f"Resume template must be a .docx file: {template_path}")

    root = _read_document_root(template_path)
    paragraphs = _body_paragraphs(root)
    paragraph_texts = tuple(_paragraph_text(paragraph).strip() for paragraph in paragraphs)

    summary_indices = [
        index for index, text in enumerate(paragraph_texts) if text == SUMMARY_PLACEHOLDER
    ]
    if len(summary_indices) != 1:
        raise DocxEditingError(
            f"Expected exactly one {SUMMARY_PLACEHOLDER!r} paragraph, found {len(summary_indices)}."
        )

    bullet_indices = tuple(
        index for index, text in enumerate(paragraph_texts) if text == BULLETS_PLACEHOLDER
    )
    if len(bullet_indices) != len(ROLE_SPECS):
        raise DocxEditingError(
            f"Expected {len(ROLE_SPECS)} {BULLETS_PLACEHOLDER!r} paragraphs, "
            f"found {len(bullet_indices)}."
        )

    previous_index = summary_indices[0]
    for spec, bullet_index in zip(ROLE_SPECS, bullet_indices, strict=True):
        block_text = "\n".join(paragraph_texts[previous_index + 1 : bullet_index])
        missing_anchors = [anchor for anchor in spec.anchor_texts if anchor not in block_text]
        if missing_anchors:
            raise DocxEditingError(
                f"Could not match placeholder for {spec.role_id}; missing preceding role anchor(s): "
                + ", ".join(missing_anchors)
            )
        previous_index = bullet_index

    return ResumeTemplate(
        path=template_path,
        paragraph_texts=paragraph_texts,
        summary_index=summary_indices[0],
        bullet_indices=bullet_indices,
    )


def write_tailored_resume(
    *,
    template: ResumeTemplate,
    tailoring: TailoredResume,
    output_path: str | Path,
) -> Path:
    """Copy the template package and replace only its editable OOXML slots."""

    output_path = Path(output_path).expanduser().resolve()
    if output_path == template.path:
        raise DocxEditingError("Refusing to overwrite the source resume template.")

    role_ids = [role.role_id for role in tailoring.roles]
    expected_role_ids = [spec.role_id for spec in ROLE_SPECS]
    if role_ids != expected_role_ids:
        raise DocxEditingError(
            f"Generated role order must be {expected_role_ids}, got {role_ids}."
        )

    root = _read_document_root(template.path)
    bullet_numbering_id = _find_bullet_numbering_id(template.path)
    paragraphs = _body_paragraphs(root)
    summary_paragraph = paragraphs[template.summary_index]
    bullet_paragraphs = [paragraphs[index] for index in template.bullet_indices]

    _replace_paragraph_text(summary_paragraph, tailoring.summary.strip())
    for paragraph in paragraphs:
        text = _paragraph_text(paragraph).strip()
        if text.startswith(KEEP_WITH_NEXT_PREFIXES):
            _set_keep_with_next(paragraph)

    for spec, placeholder, role in zip(
        ROLE_SPECS, bullet_paragraphs, tailoring.roles, strict=True
    ):
        if len(role.bullets) != spec.bullet_count:
            raise DocxEditingError(
                f"{spec.role_id} requires exactly {spec.bullet_count} bullets, "
                f"got {len(role.bullets)}."
            )
        parent = placeholder.getparent()
        insertion_index = parent.index(placeholder)
        parent.remove(placeholder)
        for offset, bullet in enumerate(role.bullets):
            cloned_paragraph = deepcopy(placeholder)
            _replace_paragraph_text(cloned_paragraph, bullet.text.strip())
            _set_bullet_numbering(cloned_paragraph, bullet_numbering_id)
            parent.insert(insertion_index + offset, cloned_paragraph)

    final_text = "\n".join(_paragraph_text(paragraph) for paragraph in _body_paragraphs(root))
    if SUMMARY_PLACEHOLDER in final_text or BULLETS_PLACEHOLDER in final_text:
        raise DocxEditingError("One or more resume placeholders remained after editing.")

    output_path.parent.mkdir(parents=True, exist_ok=True)
    _write_package_with_document_xml(
        source_path=template.path,
        output_path=output_path,
        document_xml=root.xml.encode("utf-8"),
    )
    return output_path


def build_diff_preview(template: ResumeTemplate, tailoring: TailoredResume) -> str:
    """Generate a unified preview of placeholder-to-content changes."""

    before = [f"Summary: {SUMMARY_PLACEHOLDER}"]
    after = [f"Summary: {tailoring.summary.strip()}"]
    for spec, role in zip(ROLE_SPECS, tailoring.roles, strict=True):
        before.extend([f"{spec.role}: {BULLETS_PLACEHOLDER}"])
        after.extend(f"{spec.role}: {bullet.text.strip()}" for bullet in role.bullets)

    return "\n".join(
        difflib.unified_diff(
            before,
            after,
            fromfile="resume-template",
            tofile="tailored-resume",
            lineterm="",
        )
    )


def extract_candidate_name(template: ResumeTemplate) -> str:
    """Return the first all-uppercase non-placeholder paragraph."""

    for text in template.paragraph_texts:
        if text and text.upper() == text and "[" not in text:
            return text
    return template.path.stem


def _read_document_root(path: Path):
    try:
        with ZipFile(path) as package:
            document_xml = package.read(DOCUMENT_XML_PATH)
    except (OSError, KeyError) as exc:
        raise DocxEditingError(f"Could not read DOCX package {path}: {exc}") from exc
    return parse_xml(document_xml)


def _find_bullet_numbering_id(path: Path) -> int:
    """Return a numId backed by a real level-zero bullet definition."""

    try:
        with ZipFile(path) as package:
            numbering_root = parse_xml(package.read(NUMBERING_XML_PATH))
    except (OSError, KeyError) as exc:
        raise DocxEditingError(
            f"Could not read DOCX numbering definitions from {path}: {exc}"
        ) from exc

    abstract_numbering = {
        element.get(qn("w:abstractNumId")): element
        for element in numbering_root.findall(qn("w:abstractNum"))
    }
    for numbering_instance in numbering_root.findall(qn("w:num")):
        number_id = numbering_instance.get(qn("w:numId"))
        abstract_reference = numbering_instance.find(qn("w:abstractNumId"))
        if number_id is None or abstract_reference is None:
            continue
        abstract_id = abstract_reference.get(qn("w:val"))
        abstract_definition = abstract_numbering.get(abstract_id)
        if abstract_definition is None:
            continue
        for level in abstract_definition.findall(qn("w:lvl")):
            if level.get(qn("w:ilvl"), "0") != "0":
                continue
            number_format = level.find(qn("w:numFmt"))
            if number_format is not None and number_format.get(qn("w:val")) == "bullet":
                return int(number_id)

    raise DocxEditingError(
        "Resume template does not contain a level-zero Word bullet numbering definition."
    )


def _body_paragraphs(root) -> list:
    body = root.find(qn("w:body"))
    if body is None:
        raise DocxEditingError("DOCX document.xml does not contain a body element.")
    return [child for child in body if child.tag == qn("w:p")]


def _paragraph_text(paragraph) -> str:
    return "".join(text_node.text or "" for text_node in paragraph.iter(qn("w:t")))


def _replace_paragraph_text(paragraph, new_text: str) -> None:
    text_nodes = list(paragraph.iter(qn("w:t")))
    if not text_nodes:
        run = paragraph.makeelement(qn("w:r"))
        text_node = paragraph.makeelement(qn("w:t"))
        run.append(text_node)
        paragraph.append(run)
        text_nodes = [text_node]

    text_nodes[0].text = new_text
    for text_node in text_nodes[1:]:
        text_node.text = ""


def _set_keep_with_next(paragraph) -> None:
    paragraph_properties = paragraph.find(qn("w:pPr"))
    if paragraph_properties is None:
        paragraph_properties = OxmlElement("w:pPr")
        paragraph.insert(0, paragraph_properties)
    if paragraph_properties.find(qn("w:keepNext")) is None:
        paragraph_properties.append(OxmlElement("w:keepNext"))


def _set_bullet_numbering(paragraph, numbering_id: int) -> None:
    """Attach a real Word bullet definition to a generated bullet paragraph."""

    paragraph_properties = paragraph.find(qn("w:pPr"))
    if paragraph_properties is None:
        paragraph_properties = OxmlElement("w:pPr")
        paragraph.insert(0, paragraph_properties)

    existing_numbering = paragraph_properties.find(qn("w:numPr"))
    if existing_numbering is not None:
        paragraph_properties.remove(existing_numbering)

    numbering_properties = OxmlElement("w:numPr")
    indentation_level = OxmlElement("w:ilvl")
    indentation_level.set(qn("w:val"), "0")
    number_reference = OxmlElement("w:numId")
    number_reference.set(qn("w:val"), str(numbering_id))
    numbering_properties.extend((indentation_level, number_reference))

    paragraph_style = paragraph_properties.find(qn("w:pStyle"))
    insertion_index = (
        paragraph_properties.index(paragraph_style) + 1
        if paragraph_style is not None
        else 0
    )
    paragraph_properties.insert(insertion_index, numbering_properties)


def _write_package_with_document_xml(
    *, source_path: Path, output_path: Path, document_xml: bytes
) -> None:
    file_descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{output_path.stem}_", suffix=".docx", dir=output_path.parent
    )
    os.close(file_descriptor)
    temporary_path = Path(temporary_name)
    try:
        with ZipFile(source_path) as source, ZipFile(
            temporary_path, "w", compression=ZIP_DEFLATED
        ) as destination:
            for info in source.infolist():
                payload = (
                    document_xml
                    if info.filename == DOCUMENT_XML_PATH
                    else source.read(info.filename)
                )
                destination.writestr(info, payload)
        temporary_path.replace(output_path)
    finally:
        if temporary_path.exists():
            temporary_path.unlink()
