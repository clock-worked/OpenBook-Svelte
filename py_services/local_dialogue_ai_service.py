import os
from datetime import datetime, timezone
from typing import Any, Callable, Dict, List, Optional

import httpx

from api_models import (
    DialogueAiAssistStatus,
    DialogueAiCharacterRef,
    LocalDialogueAiLineResult,
    LocalDialogueAiRequest,
    LocalDialogueAiResponse,
    LocalDialogueAiSummary,
)
from dialogue_ai_common import (
    character_lookup as _character_lookup,
    character_name as _character_name,
    count_target_lines,
    load_local_env_file as _load_local_env_file,
    normalize_character as _normalize_character,
    normalize_model_answer as _normalize_model_answer,
)
from dialogue_ai_context import build_assist_context
from run_status import RunStatusTracker


DEFAULT_ENDPOINT = "http://127.0.0.1:1234/api/v1/chat"
DEFAULT_MODEL_NAME = "primal-hunter-llama-3-8b-instruct"
SYSTEM_PROMPT = (
    "You are an expert dialogue attribution AI. Read the metadata and the text context. "
    "The target dialogue is wrapped in <quote> tags. Output ONLY the characterId of the speaker, or 'None'. "
    "Do not include any other text.\n\n"
    "Rules:\n"
    "1. Output 'None' if the text inside the tags is pure narration or if the speaker is completely ambiguous.\n"
    "2. Explicit tags (e.g., 'Alice said') immediately before or after the quote take highest priority.\n"
    "3. Tag continuation: If the pattern is dialogue-narration-dialogue, the speaker usually remains the explicitly tagged character.\n"
    "4. Contiguous dialogue: Adjacent quotes in the same paragraph usually inherit the previous speaker.\n"
    "5. Do not confuse the person being addressed with the speaker."
)


def _local_model_config() -> tuple[str, str]:
    _load_local_env_file()
    endpoint = os.getenv("OPENBOOK_LOCAL_AI_ENDPOINT", DEFAULT_ENDPOINT).strip() or DEFAULT_ENDPOINT
    model = os.getenv("OPENBOOK_LOCAL_AI_MODEL", DEFAULT_MODEL_NAME).strip() or DEFAULT_MODEL_NAME
    return endpoint, model


def _extract_content(value: Any) -> str:
    if isinstance(value, str):
        return value.strip()
    if isinstance(value, list):
        parts = [_extract_content(item) for item in value]
        return "\n".join(part for part in parts if part).strip()
    if not isinstance(value, dict):
        return ""

    for key in ("content", "text", "message"):
        content = _extract_content(value.get(key))
        if content:
            return content
    return ""


def _extract_model_output(payload: Any) -> str:
    if not isinstance(payload, dict):
        return _extract_content(payload)

    output = payload.get("output")
    if isinstance(output, list):
        message_parts = [
            _extract_content(item)
            for item in output
            if isinstance(item, dict) and str(item.get("type") or "").lower() in {"message", "output_text", "text"}
        ]
        message_text = "\n".join(part for part in message_parts if part).strip()
        if message_text:
            return message_text
    output_text = _extract_content(output)
    if output_text:
        return output_text

    choices = payload.get("choices")
    if isinstance(choices, list) and choices:
        choice_text = _extract_content(choices[0])
        if choice_text:
            return choice_text

    for key in ("message", "content", "response"):
        text = _extract_content(payload.get(key))
        if text:
            return text
    return ""


class LocalDialogueAiService:
    def __init__(self, get_book_root: Callable[[], str]):
        self._get_book_root = get_book_root
        self._status = RunStatusTracker()

    def prepare_status(self, request: LocalDialogueAiRequest) -> None:
        request_id = str(request.request_id or "").strip()
        if not request_id:
            return
        total_lines = count_target_lines(request.dialogue.lines, request.max_lines)
        self._status.prepare(
            request_id,
            total_lines=total_lines,
            chapter_path=request.chapter_path,
        )

    def get_status(self, request_id: str) -> Optional[DialogueAiAssistStatus]:
        return self._status.get(request_id)

    def run(self, request: LocalDialogueAiRequest) -> LocalDialogueAiResponse:
        assist_context = build_assist_context(
            book_root=self._get_book_root(),
            chapter_text=request.chapter_text,
            dialogue=request.dialogue,
        )
        targets = assist_context["targets"]
        if request.max_lines is not None and request.max_lines >= 0:
            targets = targets[:request.max_lines]

        request_id = str(request.request_id or "").strip() or None
        started_at = datetime.now(timezone.utc).isoformat()
        total_lines = len(targets)
        self._status.update(
            request_id,
            status="running",
            processed_lines=0,
            total_lines=total_lines,
            chapter_path=request.chapter_path,
            started_at=started_at,
        )

        all_lines = list(request.dialogue.lines)
        line_indexes = {line.id: index for index, line in enumerate(all_lines)}
        lookup = _character_lookup(
            assist_context["chapterCharacters"],
            assist_context["bookCharacters"],
        )
        results: List[LocalDialogueAiLineResult] = []
        errors: List[str] = []

        try:
            for processed, target in enumerate(targets, start=1):
                result = self._run_line(
                    request=request,
                    target=target,
                    all_lines=all_lines,
                    line_index=line_indexes.get(target["line"].id, 0),
                    character_lookup=lookup,
                    chapter_characters=assist_context["chapterCharacters"],
                )
                results.append(result)
                if result.outcome == "error" and result.error:
                    errors.append(f"line {result.lineId}: {result.error}")
                self._status.update(
                    request_id,
                    status="running",
                    processed_lines=processed,
                    total_lines=total_lines,
                    chapter_path=request.chapter_path,
                    started_at=started_at,
                )

            summary = LocalDialogueAiSummary(
                scannedLines=len(results),
                unchangedCount=sum(result.outcome == "keep_existing" for result in results),
                suggestionCount=sum(result.outcome == "suggestion" for result in results),
                unresolvedCount=sum(result.outcome == "unresolved" for result in results),
                errorCount=sum(result.outcome == "error" for result in results),
                skippedNarratorLines=assist_context["skippedNarratorLines"],
                modelCalls=len(results),
            )
            self._status.update(
                request_id,
                status="completed",
                processed_lines=total_lines,
                total_lines=total_lines,
                chapter_path=request.chapter_path,
                started_at=started_at,
            )
            return LocalDialogueAiResponse(summary=summary, results=results, errors=errors)
        except Exception as exc:
            self._status.update(
                request_id,
                status="failed",
                processed_lines=len(results),
                total_lines=total_lines,
                chapter_path=request.chapter_path,
                error=str(exc),
                started_at=started_at,
            )
            raise

    def _run_line(
        self,
        *,
        request: LocalDialogueAiRequest,
        target: Dict[str, Any],
        all_lines: List[Any],
        line_index: int,
        character_lookup: Dict[str, DialogueAiCharacterRef],
        chapter_characters: List[DialogueAiCharacterRef],
    ) -> LocalDialogueAiLineResult:
        line = target["line"]
        prompt = self._build_user_prompt(request, line, all_lines, line_index)
        raw_output = self._call_model(prompt)
        answer = _normalize_model_answer(raw_output)

        current_ref = character_lookup.get(_normalize_character(line.characterId))
        current_character_id = current_ref.characterId if current_ref else line.characterId
        current_character_name = current_ref.name if current_ref else _character_name(line.characterId, chapter_characters)
        common = {
            "lineId": line.id,
            "paragraphIndex": target["paragraphIndex"],
            "currentCharacterId": current_character_id,
            "currentCharacterName": current_character_name,
            "currentParagraphText": target["currentParagraphText"],
            "rawOutput": raw_output,
        }

        if not answer or answer.lower() == "none":
            return LocalDialogueAiLineResult(**common, outcome="unresolved")

        suggested_ref = character_lookup.get(_normalize_character(answer))
        if suggested_ref is None:
            return LocalDialogueAiLineResult(**common, outcome="unresolved")

        if current_ref is not None:
            is_same_character = suggested_ref.characterId == current_ref.characterId
        else:
            is_same_character = _normalize_character(suggested_ref.characterId) == _normalize_character(line.characterId)

        return LocalDialogueAiLineResult(
            **common,
            suggestedCharacterId=suggested_ref.characterId,
            suggestedCharacterName=suggested_ref.name,
            outcome="keep_existing" if is_same_character else "suggestion",
        )

    def _build_user_prompt(
        self,
        request: LocalDialogueAiRequest,
        line: Any,
        all_lines: List[Any],
        line_index: int,
    ) -> str:
        start = int(line.span.start)
        end = int(line.span.end)
        if start < 0 or end < start or start >= len(request.chapter_text) or end > len(request.chapter_text):
            raise ValueError(f"Invalid source span for line {line.id}: {start}-{end}")

        context_start = max(0, start - request.context_window)
        context_end = min(len(request.chapter_text), end + request.context_window)
        context = (
            request.chapter_text[context_start:start]
            + "<quote>"
            + request.chapter_text[start:end]
            + "</quote>"
            + request.chapter_text[end:context_end]
        ).strip()

        metadata: List[str] = []
        if request.include_speaker_context:
            nearby = all_lines[max(0, line_index - 5):min(len(all_lines), line_index + 6)]
            scene_characters = sorted({
                str(candidate.characterId)
                for candidate in nearby
                if candidate.characterId not in (None, "", "narrator", "None")
            })
            metadata.append(f"Scene Characters: {', '.join(scene_characters) if scene_characters else 'None'}")

        if request.include_previous_speaker:
            last_speaker = "None"
            for candidate in reversed(all_lines[:line_index]):
                if candidate.characterId not in (None, "", "narrator", "None"):
                    last_speaker = str(candidate.characterId)
                    break
            metadata.append(f"Last Speaker: {last_speaker}")

        if metadata:
            return "\n".join(metadata) + f"\n\nContext:\n{context}"
        return f"Context:\n{context}"

    def _call_model(self, user_prompt: str) -> str:
        endpoint, model = _local_model_config()
        response = httpx.post(
            endpoint,
            headers={"Content-Type": "application/json"},
            json={
                "model": model,
                "system_prompt": SYSTEM_PROMPT,
                "input": user_prompt,
            },
            timeout=httpx.Timeout(120.0, connect=5.0),
        )
        response.raise_for_status()
        raw_output = _extract_model_output(response.json())
        if not raw_output:
            raise ValueError("LM Studio returned an empty or unsupported response payload.")
        return raw_output
