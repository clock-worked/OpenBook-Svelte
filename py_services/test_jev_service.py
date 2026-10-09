import os
import unittest
from unittest import mock

from api_models import LocalDialogueAiRequest
from jev_service import JevService


class JevServiceTests(unittest.TestCase):
    def test_malformed_answer_is_a_line_error(self):
        with mock.patch.dict(os.environ, {"VERCEL_JEV_API_KEY": "test-key"}):
            service = JevService(lambda: "")
        request = LocalDialogueAiRequest(
            request_id="malformed-answer",
            chapter_path="chapter",
            chapter_text="Hello",
            dialogue={
                "formatVersion": "2.0",
                "chapterId": "chapter",
                "stats": {"totalLines": 1, "conflicts": 0, "characterBreakdown": {}},
                "lines": [{
                    "id": 1,
                    "text": "Hello",
                    "characterId": "unknown",
                    "span": {"start": 0, "end": 5},
                    "metadata": {"intensity": 0, "customTags": {}},
                    "candidates": [],
                    "isConflict": False,
                }],
            },
        )
        context = {
            "targets": [{"line": request.dialogue.lines[0], "paragraphIndex": 0, "currentParagraphText": "Hello"}],
            "chapterCharacters": [],
            "bookCharacters": [],
            "skippedNarratorLines": 0,
        }
        with mock.patch("jev_service.build_assist_context", return_value=context), mock.patch.object(
            service._client, "ask", return_value={"answers": {"speaker": {"choice": {"unknown": 0.9}}}}
        ):
            response = service.run(request)
        self.assertEqual(response.results[0].outcome, "error")
        status = service.get_status("malformed-answer")
        self.assertIsNotNone(status)
        self.assertEqual(status.status, "completed")