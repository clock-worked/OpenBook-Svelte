"""Tests for dialogue_ai_service.py (the Gemini assist service).

Covers the run-status lifecycle through the public seam the route uses
(prepare_status -> run -> get_status) with the context builder and the
external model client mocked out, the 64-entry status cap inherited from
RunStatusTracker, and the shared normalization helpers this service uses.
The Gemini decision logic itself is out of scope; tracker internals live in
test_run_status.py.
"""

import json
import os
import tempfile
import unittest
from contextlib import ExitStack
from pathlib import Path
from unittest import mock

import dialogue_ai_service
from api_models import (
    DialogueAiAssistRequest,
    DialogueAiCharacterRef,
    DialogueJson,
    DialogueLine,
)
from dialogue_ai_common import count_target_lines, normalize_character
from dialogue_ai_service import DialogueAiAssistService
from run_status import RunStatusTracker


CHAPTER_TEXT = 'Mara said, "Hello."'


class _FakeModelResponse:
    """Just enough of httpx.Response for dialogue_ai_service._call_model."""

    def __init__(self, payload):
        self._payload = payload

    def raise_for_status(self):
        return None

    def json(self):
        return self._payload


def _make_line(line_id: int, character_id: str, text: str, start: int, end: int) -> DialogueLine:
    return DialogueLine(
        id=line_id,
        characterId=character_id,
        text=text,
        span={"start": start, "end": end},
        metadata={"intensity": 0.0, "customTags": {}},
        candidates=[],
        isConflict=False,
    )


def _make_request(request_id, lines, chapter_text: str) -> DialogueAiAssistRequest:
    dialogue = DialogueJson(
        formatVersion="2.0",
        chapterId="chapter",
        stats={"totalLines": len(lines), "conflicts": 0, "characterBreakdown": {}},
        lines=lines,
    )
    return DialogueAiAssistRequest(
        request_id=request_id,
        chapter_path="chapter",
        chapter_text=chapter_text,
        dialogue=dialogue,
    )


def _make_assist_context(line: DialogueLine) -> dict:
    return {
        "targets": [
            {
                "line": line,
                "paragraphIndex": 0,
                "currentParagraphText": CHAPTER_TEXT,
                "previousParagraphText": None,
                "nextParagraphText": None,
            }
        ],
        "chapterCharacters": [
            DialogueAiCharacterRef(characterId="mara", name="Mara", aliases=["M."])
        ],
        "bookCharacters": [],
        "skippedNarratorLines": 0,
    }


def _gemini_response(decision: dict) -> _FakeModelResponse:
    return _FakeModelResponse(
        {
            "candidates": [
                {"content": {"parts": [{"text": json.dumps(decision)}]}}
            ]
        }
    )


class RunStatusLifecycleTests(unittest.TestCase):
    def setUp(self):
        self._temp_dir = tempfile.TemporaryDirectory(prefix="openbook_dialogue_ai_test_")
        self.root = Path(self._temp_dir.name)

    def tearDown(self):
        self._temp_dir.cleanup()

    def _patched_run(self, line, post_kwargs) -> ExitStack:
        stack = ExitStack()
        stack.enter_context(
            mock.patch(
                "dialogue_ai_service.build_assist_context",
                return_value=_make_assist_context(line),
            )
        )
        stack.enter_context(mock.patch.object(dialogue_ai_service, "AI_LOG_DIR", self.root / "logs"))
        stack.enter_context(mock.patch.object(dialogue_ai_service, "AI_CACHE_DIR", self.root / "cache"))
        stack.enter_context(mock.patch.object(dialogue_ai_service.httpx, "post", **post_kwargs))
        return stack

    def test_run_transitions_status_to_completed(self):
        line = _make_line(1, "mara", "Hello.", 12, 18)
        request = _make_request("gemini-run", [line], CHAPTER_TEXT)
        service = DialogueAiAssistService(lambda: "")

        service.prepare_status(request)
        queued = service.get_status("gemini-run")
        self.assertEqual(queued.status, "queued")
        self.assertEqual(queued.totalLines, 1)
        self.assertEqual(queued.chapterPath, "chapter")

        with mock.patch.dict(os.environ, {"GEMINI_API_KEY": "test-key"}), \
                self._patched_run(
                    line,
                    {
                        "return_value": _gemini_response(
                            {
                                "responseType": "final",
                                "action": "keep_existing",
                                "characterId": "mara",
                                "characterName": "Mara",
                                "confidence": 0.99,
                                "reasonCodes": ["explicit_cue"],
                            }
                        )
                    },
                ):
            response = service.run(request)

        self.assertEqual(response.results[0].disposition, "auto_apply")
        self.assertEqual(response.summary.autoApplyCount, 1)
        self.assertEqual(response.summary.modelCalls, 1)

        status = service.get_status("gemini-run")
        self.assertEqual(status.status, "completed")
        self.assertEqual(status.processedLines, 1)
        self.assertEqual(status.totalLines, 1)
        self.assertEqual(status.progressRatio, 1.0)
        self.assertIsNotNone(status.startedAt)
        self.assertTrue(status.logDirectory.startswith(str(self.root / "logs")))

    def test_run_records_failed_status_when_model_call_fails(self):
        line = _make_line(1, "mara", "Hello.", 12, 18)
        request = _make_request("gemini-fail", [line], CHAPTER_TEXT)
        service = DialogueAiAssistService(lambda: "")

        service.prepare_status(request)
        with mock.patch.dict(os.environ, {"GEMINI_API_KEY": "test-key"}), \
                self._patched_run(line, {"side_effect": RuntimeError("boom")}):
            with self.assertRaises(RuntimeError):
                service.run(request)

        status = service.get_status("gemini-fail")
        self.assertEqual(status.status, "failed")
        self.assertIn("boom", status.error)
        self.assertEqual(status.processedLines, 0)

    def test_status_cap_evicts_oldest_entry(self):
        service = DialogueAiAssistService(lambda: "")
        line = _make_line(1, "mara", "Hello.", 12, 18)
        for index in range(RunStatusTracker.MAX_ENTRIES + 1):
            service.prepare_status(_make_request(f"req-{index:03d}", [line], CHAPTER_TEXT))
        self.assertIsNone(service.get_status("req-000"))
        newest = f"req-{RunStatusTracker.MAX_ENTRIES:03d}"
        self.assertIsNotNone(service.get_status(newest))

    def test_prepare_status_counts_only_speaker_lines(self):
        lines = [
            _make_line(1, "mara", "Hello.", 12, 18),
            _make_line(2, "kren", "Fine.", 0, 5),
            _make_line(3, "narrator", "The room was quiet.", 0, 20),
        ]
        request = _make_request("count-run", lines, CHAPTER_TEXT)
        service = DialogueAiAssistService(lambda: "")

        service.prepare_status(request)
        status = service.get_status("count-run")
        self.assertEqual(status.totalLines, 2)

    def test_prepare_status_without_request_id_is_noop(self):
        line = _make_line(1, "mara", "Hello.", 12, 18)
        request = _make_request(None, [line], CHAPTER_TEXT)
        service = DialogueAiAssistService(lambda: "")

        service.prepare_status(request)
        self.assertIsNone(service.get_status(""))


class SharedHelperTests(unittest.TestCase):
    def test_normalize_character_variants(self):
        self.assertEqual(normalize_character("Mara_Ves"), "mara ves")
        self.assertEqual(normalize_character("  the-king  "), "the king")
        self.assertEqual(normalize_character("MARA__VES!"), "mara ves")
        self.assertEqual(normalize_character(None), "")

    def test_count_target_lines_clamps_to_max(self):
        lines = [
            _make_line(1, "mara", "Hello.", 12, 18),
            _make_line(2, "kren", "Fine.", 0, 5),
            _make_line(3, "narrator", "Quiet.", 0, 6),
        ]
        self.assertEqual(count_target_lines(lines), 2)
        self.assertEqual(count_target_lines(lines, max_lines=1), 1)
        self.assertEqual(count_target_lines(lines, max_lines=None), 2)


if __name__ == "__main__":
    unittest.main()
