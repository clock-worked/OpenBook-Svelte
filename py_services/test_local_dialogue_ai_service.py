"""Tests for local_dialogue_ai_service.py (the local-LLM assist service).

Covers the run-status lifecycle through the public seam the route uses
(prepare_status -> run -> get_status) with the context builder and the
local model client (httpx) mocked out, the 64-entry status cap inherited
from RunStatusTracker, the env-file-loading helper, the model endpoint
config, and the shared answer/character normalization helpers.
"""

import os
import tempfile
import unittest
from contextlib import ExitStack
from pathlib import Path
from unittest import mock

import dialogue_ai_common
import local_dialogue_ai_service
from api_models import (
    DialogueAiCharacterRef,
    DialogueJson,
    DialogueLine,
    LocalDialogueAiRequest,
)
from dialogue_ai_common import (
    load_local_env_file,
    normalize_character,
    normalize_model_answer,
)
from local_dialogue_ai_service import (
    DEFAULT_ENDPOINT,
    DEFAULT_MODEL_NAME,
    LocalDialogueAiService,
)
from run_status import RunStatusTracker


CHAPTER_TEXT = 'Mara said, "Hello."'
TWO_LINE_TEXT = 'Mara said, "Hello." Kren replied, "Fine."'


class _FakeLocalResponse:
    """Just enough of httpx.Response for local_dialogue_ai_service._call_model."""

    def __init__(self, payload):
        self._payload = payload

    def raise_for_status(self):
        return None

    def json(self):
        return self._payload


def _local_response(text: str) -> _FakeLocalResponse:
    return _FakeLocalResponse({"output": [{"type": "message", "content": text}]})


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


def _make_request(request_id, lines, chapter_text: str) -> LocalDialogueAiRequest:
    dialogue = DialogueJson(
        formatVersion="2.0",
        chapterId="chapter",
        stats={"totalLines": len(lines), "conflicts": 0, "characterBreakdown": {}},
        lines=lines,
    )
    return LocalDialogueAiRequest(
        request_id=request_id,
        chapter_path="chapter",
        chapter_text=chapter_text,
        dialogue=dialogue,
    )


def _make_assist_context(lines) -> dict:
    targets = [
        {
            "line": line,
            "paragraphIndex": 0,
            "currentParagraphText": "Mara said, \"Hello.\"",
            "previousParagraphText": None,
            "nextParagraphText": None,
        }
        for line in lines
    ]
    return {
        "targets": targets,
        "chapterCharacters": [
            DialogueAiCharacterRef(characterId="mara", name="Mara", aliases=["M."]),
            DialogueAiCharacterRef(characterId="kren", name="Kren", aliases=[]),
        ],
        "bookCharacters": [],
        "skippedNarratorLines": 0,
    }


class RunStatusLifecycleTests(unittest.TestCase):
    def setUp(self):
        self._temp_dir = tempfile.TemporaryDirectory(prefix="openbook_local_ai_test_")
        self.root = Path(self._temp_dir.name)

    def tearDown(self):
        self._temp_dir.cleanup()

    def _patched_run(self, lines, post_kwargs) -> ExitStack:
        stack = ExitStack()
        stack.enter_context(
            mock.patch(
                "local_dialogue_ai_service.build_assist_context",
                return_value=_make_assist_context(lines),
            )
        )
        stack.enter_context(mock.patch.object(local_dialogue_ai_service.httpx, "post", **post_kwargs))
        return stack

    def test_run_transitions_status_to_completed(self):
        line = _make_line(1, "mara", "Hello.", 12, 18)
        request = _make_request("local-run", [line], CHAPTER_TEXT)
        service = LocalDialogueAiService(lambda: "")

        service.prepare_status(request)
        queued = service.get_status("local-run")
        self.assertEqual(queued.status, "queued")
        self.assertEqual(queued.totalLines, 1)
        self.assertEqual(queued.chapterPath, "chapter")

        with self._patched_run([line], {"return_value": _local_response("mara")}):
            response = service.run(request)

        self.assertEqual(response.results[0].outcome, "keep_existing")
        self.assertEqual(response.summary.modelCalls, 1)

        status = service.get_status("local-run")
        self.assertEqual(status.status, "completed")
        self.assertEqual(status.processedLines, 1)
        self.assertEqual(status.totalLines, 1)
        self.assertEqual(status.progressRatio, 1.0)
        self.assertIsNotNone(status.startedAt)

    def test_run_records_failed_status_when_model_call_fails(self):
        line = _make_line(1, "mara", "Hello.", 12, 18)
        request = _make_request("local-fail", [line], CHAPTER_TEXT)
        service = LocalDialogueAiService(lambda: "")

        service.prepare_status(request)
        with self._patched_run([line], {"side_effect": RuntimeError("boom")}):
            with self.assertRaises(RuntimeError):
                service.run(request)

        status = service.get_status("local-fail")
        self.assertEqual(status.status, "failed")
        self.assertIn("boom", status.error)
        self.assertEqual(status.processedLines, 0)

    def test_run_reports_suggestion_and_unresolved_outcomes(self):
        lines = [
            _make_line(1, "mara", "Hello.", 12, 18),
            _make_line(2, "mara", "Fine.", 35, 40),
        ]
        request = _make_request("local-mixed", lines, TWO_LINE_TEXT)
        service = LocalDialogueAiService(lambda: "")

        service.prepare_status(request)
        with self._patched_run(
            lines,
            {"side_effect": [_local_response("None"), _local_response("kren")]},
        ):
            response = service.run(request)

        self.assertEqual(response.results[0].outcome, "unresolved")
        self.assertEqual(response.results[1].outcome, "suggestion")
        self.assertEqual(response.results[1].suggestedCharacterId, "kren")
        self.assertEqual(response.summary.unresolvedCount, 1)
        self.assertEqual(response.summary.suggestionCount, 1)

        status = service.get_status("local-mixed")
        self.assertEqual(status.status, "completed")
        self.assertEqual(status.totalLines, 2)
        self.assertEqual(status.progressRatio, 1.0)

    def test_status_cap_evicts_oldest_entry(self):
        service = LocalDialogueAiService(lambda: "")
        line = _make_line(1, "mara", "Hello.", 12, 18)
        for index in range(RunStatusTracker.MAX_ENTRIES + 1):
            service.prepare_status(_make_request(f"req-{index:03d}", [line], CHAPTER_TEXT))
        self.assertIsNone(service.get_status("req-000"))
        newest = f"req-{RunStatusTracker.MAX_ENTRIES:03d}"
        self.assertIsNotNone(service.get_status(newest))

    def test_prepare_status_counts_speaker_lines_clamped_to_max(self):
        lines = [
            _make_line(1, "mara", "Hello.", 12, 18),
            _make_line(2, "kren", "Fine.", 35, 40),
            _make_line(3, "narrator", "Quiet.", 0, 6),
        ]
        request = _make_request("count-run", lines, TWO_LINE_TEXT)
        request.max_lines = 2
        service = LocalDialogueAiService(lambda: "")

        service.prepare_status(request)
        status = service.get_status("count-run")
        self.assertEqual(status.totalLines, 2)


class LoadLocalEnvFileTests(unittest.TestCase):
    """load_local_env_file as used by the local model config (no side effects)."""

    def setUp(self):
        self._temp_dir = tempfile.TemporaryDirectory(prefix="openbook_local_ai_env_test_")
        self.env_path = Path(self._temp_dir.name) / ".env"
        self._env_keys = ["OPENBOOK_TEST_ENV_KEY", "OPENBOOK_TEST_ENV_PLAIN"]

    def tearDown(self):
        for key in self._env_keys:
            os.environ.pop(key, None)
        self._temp_dir.cleanup()

    def test_missing_env_file_is_ignored(self):
        with mock.patch.object(dialogue_ai_common, "ENV_FILE_PATH", self.env_path):
            load_local_env_file()  # must not raise
        for key in self._env_keys:
            self.assertIsNone(os.environ.get(key))

    def test_malformed_lines_are_skipped_and_values_parsed(self):
        self.env_path.write_text(
            "# comment\n"
            "\n"
            "OPENBOOK_TEST_ENV_KEY=\"quoted value\"\n"
            "NO_EQUALS_LINE\n"
            "=value-without-key\n"
            "OPENBOOK_TEST_ENV_PLAIN=plain\n",
            encoding="utf-8",
        )
        with mock.patch.object(dialogue_ai_common, "ENV_FILE_PATH", self.env_path):
            load_local_env_file()
        self.assertEqual(os.environ.get("OPENBOOK_TEST_ENV_KEY"), "quoted value")
        self.assertEqual(os.environ.get("OPENBOOK_TEST_ENV_PLAIN"), "plain")

    def test_existing_environment_values_win(self):
        self.env_path.write_text("OPENBOOK_TEST_ENV_KEY=from-file\n", encoding="utf-8")
        with mock.patch.object(dialogue_ai_common, "ENV_FILE_PATH", self.env_path), \
                mock.patch.dict(os.environ, {"OPENBOOK_TEST_ENV_KEY": "from-env"}):
            load_local_env_file()
            self.assertEqual(os.environ["OPENBOOK_TEST_ENV_KEY"], "from-env")


class LocalModelConfigTests(unittest.TestCase):
    def test_defaults_when_env_blank(self):
        with mock.patch.object(local_dialogue_ai_service, "_load_local_env_file"), \
                mock.patch.dict(
                    os.environ,
                    {"OPENBOOK_LOCAL_AI_ENDPOINT": "  ", "OPENBOOK_LOCAL_AI_MODEL": ""},
                ):
            endpoint, model = local_dialogue_ai_service._local_model_config()
        self.assertEqual(endpoint, DEFAULT_ENDPOINT)
        self.assertEqual(model, DEFAULT_MODEL_NAME)

    def test_env_values_are_used(self):
        with mock.patch.object(local_dialogue_ai_service, "_load_local_env_file"), \
                mock.patch.dict(
                    os.environ,
                    {
                        "OPENBOOK_LOCAL_AI_ENDPOINT": " http://127.0.0.1:1234/api/v1/chat ",
                        "OPENBOOK_LOCAL_AI_MODEL": "my-model",
                    },
                ):
            endpoint, model = local_dialogue_ai_service._local_model_config()
        self.assertEqual(endpoint, "http://127.0.0.1:1234/api/v1/chat")
        self.assertEqual(model, "my-model")


class SharedHelperTests(unittest.TestCase):
    def test_normalize_model_answer_strips_fences(self):
        self.assertEqual(normalize_model_answer("```text\nmara\n```"), "mara")

    def test_normalize_model_answer_strips_label_prefix(self):
        self.assertEqual(normalize_model_answer("Speaker: kren"), "kren")

    def test_normalize_model_answer_keeps_only_first_line(self):
        self.assertEqual(normalize_model_answer("mara\nI am Mara."), "mara")

    def test_normalize_model_answer_blank(self):
        self.assertEqual(normalize_model_answer("   "), "")

    def test_normalize_character_variants(self):
        self.assertEqual(normalize_character("Mara-Ves"), "mara ves")
        self.assertEqual(normalize_character("kren_"), "kren")
        self.assertEqual(normalize_character(None), "")


if __name__ == "__main__":
    unittest.main()
