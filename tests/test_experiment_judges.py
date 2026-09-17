import json
import unittest
from unittest.mock import MagicMock, patch

from experiment_config import JudgeConfig
from experiment_judges import (
    _judge_with_anthropic,
    _judge_with_openai,
    _score_with_anthropic,
    _score_with_openai,
)
from experiment_prompts import (
    DIMENSIONS,
    AbsoluteEvaluation,
    PairwiseComparison,
    PairwiseComparisonWire,
)


def comparison() -> PairwiseComparison:
    return PairwiseComparison.model_validate(
        {
            "dimension_scores": [
                {
                    "dimension": dimension,
                    "resume_x": 7,
                    "resume_y": 8,
                    "rationale": "Y is stronger.",
                }
                for dimension in DIMENSIONS
            ],
            "winner": "Y",
            "confidence": 80,
            "overall_rationale": "Y is stronger overall.",
            "resume_x_strongest_advantage": "Concise.",
            "resume_y_strongest_advantage": "Specific.",
            "credibility_concerns": [],
        }
    )


def wire_comparison() -> PairwiseComparisonWire:
    canonical = comparison()
    return PairwiseComparisonWire.model_validate(
        {
            "dimension_scores": {
                score.dimension: {
                    "resume_x": score.resume_x,
                    "resume_y": score.resume_y,
                    "rationale": score.rationale,
                }
                for score in canonical.dimension_scores
            },
            "winner": canonical.winner,
            "confidence": canonical.confidence,
            "overall_rationale": canonical.overall_rationale,
            "resume_x_strongest_advantage": canonical.resume_x_strongest_advantage,
            "resume_y_strongest_advantage": canonical.resume_y_strongest_advantage,
            "credibility_concerns": canonical.credibility_concerns,
        }
    )


def absolute_evaluation() -> AbsoluteEvaluation:
    return AbsoluteEvaluation.model_validate(
        {
            "dimension_scores": {
                dimension: {"score": 8, "rationale": "Strong."}
                for dimension in DIMENSIONS
            },
            "confidence": 75,
            "overall_rationale": "Strong overall.",
            "strongest_advantage": "Evidence.",
            "most_important_weakness": "Minor gap.",
            "credibility_concerns": [],
        }
    )


class ExperimentJudgeTests(unittest.TestCase):
    @patch.dict("os.environ", {"OPENAI_API_KEY": "test-key"})
    @patch("experiment_judges.OpenAI")
    def test_openai_adapter_uses_structured_output_and_reasoning(self, client_class) -> None:
        client = client_class.return_value
        response = MagicMock()
        response.output_text = json.dumps(wire_comparison().model_dump())
        response.usage = {"input_tokens": 10}
        client.responses.create.return_value = response
        judge = JudgeConfig(
            id="astra", provider="openai", model="gpt-6-astra", reasoning_effort="low"
        )

        result = _judge_with_openai(judge=judge, prompt="compare")

        self.assertEqual(result.comparison.winner, "Y")
        request = client.responses.create.call_args.kwargs
        self.assertEqual(request["reasoning"], {"effort": "low"})
        self.assertNotIn("temperature", request)
        self.assertTrue(request["text"]["format"]["strict"])

    @patch.dict("os.environ", {"ANTHROPIC_API_KEY": "test-key"})
    @patch("anthropic.Anthropic")
    def test_anthropic_adapter_uses_parsed_structured_output(self, client_class) -> None:
        client = client_class.return_value
        response = MagicMock()
        response.parsed_output = wire_comparison()
        response.usage = {"input_tokens": 10}
        client.messages.parse.return_value = response
        judge = JudgeConfig(
            id="claude", provider="anthropic", model="claude-sonnet-5"
        )

        result = _judge_with_anthropic(judge=judge, prompt="compare")

        self.assertEqual(result.comparison.winner, "Y")
        request = client.messages.parse.call_args.kwargs
        self.assertIs(request["output_format"], PairwiseComparisonWire)
        self.assertEqual(request["thinking"], {"type": "disabled"})
        self.assertNotIn("temperature", request)

    @patch.dict("os.environ", {"OPENAI_API_KEY": "test-key"})
    @patch("experiment_judges.OpenAI")
    def test_openai_absolute_scoring_uses_named_schema(self, client_class) -> None:
        response = MagicMock()
        response.output_text = json.dumps(absolute_evaluation().model_dump())
        response.usage = {"input_tokens": 10}
        client_class.return_value.responses.create.return_value = response
        judge = JudgeConfig(
            id="sol", provider="openai", model="gpt-5.6-sol", reasoning_effort="low"
        )

        result = _score_with_openai(judge=judge, prompt="score")

        self.assertEqual(result.evaluation.flat_scores()["interview_likelihood"], 8)
        schema = client_class.return_value.responses.create.call_args.kwargs["text"]["format"]
        self.assertTrue(schema["strict"])

    @patch.dict("os.environ", {"ANTHROPIC_API_KEY": "test-key"})
    @patch("anthropic.Anthropic")
    def test_anthropic_absolute_scoring_sets_adaptive_medium(self, client_class) -> None:
        response = MagicMock()
        response.parsed_output = absolute_evaluation()
        response.usage = {"input_tokens": 10}
        client_class.return_value.messages.parse.return_value = response
        judge = JudgeConfig(
            id="opus",
            provider="anthropic",
            model="claude-opus-5",
            reasoning_effort="medium",
            anthropic_thinking="adaptive",
        )

        result = _score_with_anthropic(judge=judge, prompt="score")

        self.assertEqual(result.evaluation.confidence, 75)
        request = client_class.return_value.messages.parse.call_args.kwargs
        self.assertIs(request["output_format"], AbsoluteEvaluation)
        self.assertEqual(request["thinking"], {"type": "adaptive"})
        self.assertEqual(request["output_config"], {"effort": "medium"})


if __name__ == "__main__":
    unittest.main()
