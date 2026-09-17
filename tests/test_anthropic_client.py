from __future__ import annotations

import unittest
from unittest.mock import MagicMock, patch

from anthropic_client import _request_parsed
from resume_models import JobMetadata, TailoredResume


class AnthropicGenerationClientTests(unittest.TestCase):
    @patch("anthropic_client.perf_counter", side_effect=[10.0, 10.25])
    def test_parsed_request_records_usage_and_supported_settings(self, _clock) -> None:
        parsed = TailoredResume(
            summary="Summary",
            summary_source_files=["source.md"],
            roles=[],
            metadata=JobMetadata(company_name="Example", job_id="1"),
        )
        response = MagicMock()
        response.parsed_output = parsed
        response.usage = {"input_tokens": 100, "output_tokens": 50}
        client = MagicMock()
        client.messages.parse.return_value = response
        calls = []

        result = _request_parsed(
            client=client,
            model="claude-sonnet-5",
            system_prompt="system",
            user_prompt="user",
            output_format=TailoredResume,
            request_name="resume draft",
            reasoning_effort="medium",
            max_output_tokens=16000,
            anthropic_thinking="adaptive",
            calls=calls,
        )

        self.assertIs(result, parsed)
        request = client.messages.parse.call_args.kwargs
        self.assertNotIn("temperature", request)
        self.assertEqual(request["thinking"], {"type": "adaptive"})
        self.assertEqual(request["output_config"], {"effort": "medium"})
        self.assertEqual(calls[0].input_tokens, 100)
        self.assertEqual(calls[0].duration_seconds, 0.25)


if __name__ == "__main__":
    unittest.main()
