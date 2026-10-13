import os
import unittest
from unittest import mock

from api_models import DialogueAiCharacterRef, LocalDialogueAiRequest
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

    def _make_request(self, request_id):
        return LocalDialogueAiRequest(
            request_id=request_id,
            chapter_path="chapter",
            chapter_text="Hello",
            dialogue={
                "formatVersion": "2.0",
                "chapterId": "chapter",
                "stats": {"totalLines": 1, "conflicts": 0, "characterBreakdown": {}},
                "lines": [{
                    "id": 1,
                    "text": "Hello",
                    "characterId": "mara",
                    "span": {"start": 0, "end": 5},
                    "metadata": {"intensity": 0, "customTags": {}},
                    "candidates": [],
                    "isConflict": False,
                }],
            },
        )

    def _make_context(self):
        request = self._make_request("ctx")
        return {
            "targets": [{"line": request.dialogue.lines[0], "paragraphIndex": 0, "currentParagraphText": "Hello"}],
            "chapterCharacters": [DialogueAiCharacterRef(characterId="mara", name="Mara", aliases=[])],
            "bookCharacters": [],
            "skippedNarratorLines": 0,
        }

    def test_run_transitions_status_to_completed(self):
        with mock.patch.dict(os.environ, {"VERCEL_JEV_API_KEY": "test-key"}):
            service = JevService(lambda: "")
        request = self._make_request("complete-run")
        service.prepare_status(request)
        status = service.get_status("complete-run")
        self.assertEqual(status.status, "queued")
        self.assertEqual(status.totalLines, 1)
        with mock.patch("jev_service.build_assist_context", return_value=self._make_context()), mock.patch.object(
            service._client, "ask", return_value={"answers": {"speaker": {"choice": "mara", "confidence": 0.9}}}
        ):
            response = service.run(request)
        self.assertEqual(response.results[0].outcome, "keep_existing")
        status = service.get_status("complete-run")
        self.assertEqual(status.status, "completed")
        self.assertEqual(status.processedLines, 1)
        self.assertEqual(status.totalLines, 1)

    def test_run_records_failed_status_when_model_call_fails(self):
        with mock.patch.dict(os.environ, {"VERCEL_JEV_API_KEY": "test-key"}):
            service = JevService(lambda: "")
        request = self._make_request("failed-run")
        service.prepare_status(request)
        with mock.patch("jev_service.build_assist_context", return_value=self._make_context()), mock.patch.object(
            service._client, "ask", side_effect=RuntimeError("boom")
        ):
            with self.assertRaises(RuntimeError):
                service.run(request)
        status = service.get_status("failed-run")
        self.assertIsNotNone(status)
        self.assertEqual(status.status, "failed")
        self.assertIn("boom", status.error)