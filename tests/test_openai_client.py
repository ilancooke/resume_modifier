from __future__ import annotations

import unittest

from openai_client import ResumeTailoringError, validate_and_normalize_tailoring
from resume_models import EvidenceBullet, JobMetadata, ROLE_SPECS, TailoredResume, TailoredRole


def build_tailoring() -> tuple[TailoredResume, dict[str, set[str]]]:
    roles = []
    allowed: dict[str, set[str]] = {}
    all_sources = []
    for spec in reversed(ROLE_SPECS):
        source = f"{spec.source_prefix}evidence.md" if spec.source_prefix.endswith("/") else spec.source_prefix
        allowed[spec.role_id] = {source}
        all_sources.append(source)
        roles.append(
            TailoredRole(
                role_id=spec.role_id,
                company=spec.company,
                role=spec.role,
                bullets=[
                    EvidenceBullet(text=f"Supported bullet {index}.", source_files=[source])
                    for index in range(spec.preferred_bullets)
                ],
            )
        )
    return (
        TailoredResume(
            summary="Supported summary.",
            summary_source_files=[all_sources[0]],
            roles=roles,
            metadata=JobMetadata(company_name="Example", job_id="123"),
        ),
        allowed,
    )


class TailoringValidationTests(unittest.TestCase):
    def test_normalizes_roles_to_template_order(self) -> None:
        tailoring, allowed = build_tailoring()

        normalized = validate_and_normalize_tailoring(
            tailoring=tailoring, allowed_sources_by_role=allowed
        )

        self.assertEqual(
            [role.role_id for role in normalized.roles],
            [spec.role_id for spec in ROLE_SPECS],
        )

    def test_canonicalizes_fixed_role_metadata(self) -> None:
        tailoring, allowed = build_tailoring()
        role = tailoring.roles[0]
        tailoring.roles[0] = role.model_copy(
            update={"company": "Pet Smart", "role": "Rewritten title"}
        )

        normalized = validate_and_normalize_tailoring(
            tailoring=tailoring, allowed_sources_by_role=allowed
        )

        spec = next(spec for spec in ROLE_SPECS if spec.role_id == role.role_id)
        normalized_role = normalized.role_by_id(role.role_id)
        self.assertEqual(normalized_role.company, spec.company)
        self.assertEqual(normalized_role.role, spec.role)

    def test_rejects_cross_role_source(self) -> None:
        tailoring, allowed = build_tailoring()
        first_role = tailoring.roles[0]
        bad_bullet = first_role.bullets[0].model_copy(
            update={"source_files": [next(iter(allowed[ROLE_SPECS[0].role_id]))]}
        )
        tailoring.roles[0] = first_role.model_copy(
            update={"bullets": [bad_bullet, *first_role.bullets[1:]]}
        )

        with self.assertRaisesRegex(ResumeTailoringError, "assigned to another role"):
            validate_and_normalize_tailoring(
                tailoring=tailoring, allowed_sources_by_role=allowed
            )

    def test_normalizes_case_only_source_path_differences(self) -> None:
        tailoring, allowed = build_tailoring()
        expected_summary_source = tailoring.summary_source_files[0]
        tailoring.summary_source_files = [expected_summary_source.swapcase()]
        first_role = tailoring.roles[0]
        expected_bullet_source = first_role.bullets[0].source_files[0]
        first_role.bullets[0] = first_role.bullets[0].model_copy(
            update={"source_files": [expected_bullet_source.swapcase()]}
        )

        normalized = validate_and_normalize_tailoring(
            tailoring=tailoring, allowed_sources_by_role=allowed
        )

        self.assertEqual(normalized.summary_source_files, [expected_summary_source])
        normalized_role = normalized.role_by_id(first_role.role_id)
        self.assertEqual(
            normalized_role.bullets[0].source_files,
            [expected_bullet_source],
        )

    def test_allows_fewer_than_preferred_bullets(self) -> None:
        tailoring, allowed = build_tailoring()
        role_index = next(
            index
            for index, role in enumerate(tailoring.roles)
            if role.role_id == ROLE_SPECS[0].role_id
        )
        role = tailoring.roles[role_index]
        tailoring.roles[role_index] = role.model_copy(update={"bullets": role.bullets[:1]})

        normalized = validate_and_normalize_tailoring(
            tailoring=tailoring, allowed_sources_by_role=allowed
        )

        self.assertEqual(len(normalized.roles[0].bullets), 1)

    def test_rejects_bullet_count_above_maximum(self) -> None:
        tailoring, allowed = build_tailoring()
        role = tailoring.roles[0]
        over_limit = [*role.bullets]
        while len(over_limit) <= ROLE_SPECS[0].max_bullets:
            over_limit.append(role.bullets[0])
        tailoring.roles[0] = role.model_copy(update={"bullets": over_limit})

        with self.assertRaisesRegex(ResumeTailoringError, "allows at most"):
            validate_and_normalize_tailoring(
                tailoring=tailoring, allowed_sources_by_role=allowed
            )


if __name__ == "__main__":
    unittest.main()
