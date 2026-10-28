"""Small deterministic inputs for approved-selection integration tests."""

from pathlib import Path

from docx import Document

from approved_tailoring import LockedPlan, RoleSelection, Selection
from bullet_library import ROLE_IDS, load_bullet_library
from docx_editor import load_resume_template


def make_inputs(root: Path):
    paths = (
        root / "library/Amazon/Data Scientist/bullets.md",
        root / "library/Amazon/BA-BIE/bullets.md",
        root / "library/Petsmart/PetSmart_Bullets.md",
    )
    for path in paths:
        path.parent.mkdir(parents=True, exist_ok=True)
    ds = "# Amazon — Data Scientist\n\nCandidate resume bullets.\n"
    for index in range(10):
        ds += (f"\n## Model {index}\n\n- **ADS-M{index}-01:** Developed and deployed production risk models "
               "using Python, feature engineering, and monitored decision workflows across global marketplaces.\n")
    ds += "\n## Model 0\n- **ADS-M0-02:** Calibrated production thresholds to balance investigation capacity, fraud detection, and seller friction across marketplaces.\n"
    ds += "\n## MLOps and Operational Excellence\n- **ADS-OPS-01:** Maintained production model pipelines and automated monitoring across a portfolio of independently deployed Python services.\n"
    ds += "- **ADS-OPS-02:** Standardized incident response and safe manual rollback for production model deployments across data science teams.\n"
    paths[0].write_text(ds)
    paths[1].write_text("# Amazon BA/BIE\n\n## Reporting\n- **ABA-BA-01:** Automated executive reporting with SQL.\n")
    paths[2].write_text("# PetSmart\n\n## Manager, Business Analytics\n- **PM-01:** Evaluated marketing campaigns using holdout groups.\n\n## Customer Insights Analyst\n- **PC-01:** Automated recurring brand reporting with PL/SQL.\n")
    document = Document()
    for text in (
        "CANDIDATE", "[SUMMARY GOES HERE]", "SKILLS", "Languages: Python, SQL",
        "LLM & AI Engineering: structured outputs, evaluation", "SELECTED PROJECTS",
        "Finbot: Event-Driven Financial Intelligence Platform", "Built event-driven AWS services.",
        "Developed LLM earnings extraction.", "Evaluated AI extraction against human-reviewed data.",
        "WORK EXPERIENCE", "Amazon.com | Tempe, AZ",
        "Data Scientist, Marketplace Fraud & Abuse | dates", "[BULLET POINTS GO HERE]",
        "Business Analyst / BI Engineer, Related Accounts Enforcement | dates", "[BULLET POINTS GO HERE]",
        "PetSmart | Phoenix, AZ", "Manager, Business Analytics | dates",
        "Customer Insights Analyst | dates", "[BULLET POINTS GO HERE]", "EDUCATION", "MBA; BS",
        "CERTIFICATIONS", "AWS certification",
    ):
        document.add_paragraph(text, style="List Bullet" if text == "[BULLET POINTS GO HERE]" else None)
    template_path = root / "template.docx"
    document.save(template_path)
    return load_bullet_library(root / "library"), load_resume_template(template_path)


def make_plan(keep=True):
    return LockedPlan(role_family="hybrid", requirements=("Production ML", "Python", "Model evaluation"),
                      keep_finbot=keep, finbot_rationale="Relevant AI evaluation evidence." if keep else "Professional ML evidence is stronger.",
                      company_name="Example", job_id="123")


def make_selection(ds_count=3):
    groups = (tuple(f"ADS-M{i}-01" for i in range(ds_count)), ("ABA-BA-01",), ("PM-01", "PC-01"))
    return Selection(
        summary="Data scientist building production risk models and reliable Python workflows.",
        summary_sources=("ADS-M0-01",), roles=tuple(RoleSelection(role_id=role, bullet_ids=ids) for role, ids in zip(ROLE_IDS, groups)),
        priority_order=tuple(key for group in groups for key in group), project_exceptions=(),
        reserve_bullets=(),
        unsupported_requirements=("No documented Kubernetes experience.",), feedback_decisions=(),
    )
