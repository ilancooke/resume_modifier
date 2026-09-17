import unittest

from pydantic import ValidationError

from experiment_prompts import (
    DIMENSIONS,
    DimensionScore,
    PairwiseComparison,
    PairwiseComparisonWire,
)


def comparison_with_dimensions(dimensions: tuple[str, ...]) -> PairwiseComparison:
    return PairwiseComparison(
        dimension_scores=[
            DimensionScore(
                dimension=dimension,
                resume_x=7,
                resume_y=8,
                rationale="Y is stronger.",
            )
            for dimension in dimensions
        ],
        winner="Y",
        confidence=80,
        overall_rationale="Y is stronger overall.",
        resume_x_strongest_advantage="Concise.",
        resume_y_strongest_advantage="Specific.",
        credibility_concerns=[],
    )


class ExperimentPromptTests(unittest.TestCase):
    def test_accepts_exact_dimension_order(self) -> None:
        result = comparison_with_dimensions(DIMENSIONS)
        self.assertEqual(result.winner, "Y")

    def test_rejects_missing_or_reordered_dimensions(self) -> None:
        with self.assertRaises(ValidationError):
            comparison_with_dimensions(tuple(reversed(DIMENSIONS)))

    def test_score_schema_enforces_one_through_ten_as_enum(self) -> None:
        schema = PairwiseComparisonWire.model_json_schema()
        score_schema = schema["$defs"]["WireDimensionScore"]["properties"]["resume_x"]
        self.assertEqual(score_schema["enum"], list(range(1, 11)))

        payload = comparison_with_dimensions(DIMENSIONS).model_dump()
        payload["dimension_scores"][0]["resume_x"] = 0
        with self.assertRaises(ValidationError):
            PairwiseComparison.model_validate(payload)

    def test_wire_schema_requires_each_named_dimension_and_converts_in_order(self) -> None:
        canonical = comparison_with_dimensions(DIMENSIONS)
        payload = {
            "dimension_scores": {
                score.dimension: {
                    "resume_x": score.resume_x,
                    "resume_y": score.resume_y,
                    "rationale": score.rationale,
                }
                for score in reversed(canonical.dimension_scores)
            },
            "winner": canonical.winner,
            "confidence": canonical.confidence,
            "overall_rationale": canonical.overall_rationale,
            "resume_x_strongest_advantage": canonical.resume_x_strongest_advantage,
            "resume_y_strongest_advantage": canonical.resume_y_strongest_advantage,
            "credibility_concerns": canonical.credibility_concerns,
        }

        wire = PairwiseComparisonWire.model_validate(payload)

        self.assertEqual(
            [score.dimension for score in wire.to_comparison().dimension_scores],
            list(DIMENSIONS),
        )
        del payload["dimension_scores"]["ownership_clarity"]
        with self.assertRaises(ValidationError):
            PairwiseComparisonWire.model_validate(payload)


if __name__ == "__main__":
    unittest.main()
