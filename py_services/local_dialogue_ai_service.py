import os
import re
import threading
from datetime import datetime, timezone
from pathlib import Path
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
from dialogue_ai_context import build_assist_context


DEFAULT_ENDPOINT = "http://127.0.0.1:1234/api/v1/chat"
DEFAULT_MODEL_NAME = "primal-hunter-llama-3-8b-instruct"
ENV_FILE_PATH = Path(__file__).with_name(".env")
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


def _load_local_env_file() -> None:
    if not ENV_FILE_PATH.exists():
        return

    for raw_line in ENV_FILE_PATH.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in {'"', "'"}:
            value = value[1:-1]
        if key:
            os.environ.setdefault(key, value)


def _local_model_config() -> tuple[str, str]:
    _load_local_env_file()
    endpoint = os.getenv("OPENBOOK_LOCAL_AI_ENDPOINT", DEFAULT_ENDPOINT).strip() or DEFAULT_ENDPOINT
    model = os.getenv("OPENBOOK_LOCAL_AI_MODEL", DEFAULT_MODEL_NAME).strip() or DEFAULT_MODEL_NAME
    return endpoint, model


def _normalize_character(value: Any) -> str:
    text = str(value or "").strip().lower().replace("_", " ").replace("-", " ")
    text = re.sub(r"[^a-z0-9 ]+", " ", text)
    return re.sub(r"\s+", " ", text).strip()


def _character_name(character_id: Optional[str], characters: List[DialogueAiCharacterRef]) -> Optional[str]:
    normalized = _normalize_character(character_id)
    if not normalized:
        return None
    for character in characters:
        if normalized in {
            _normalize_character(character.characterId),
            _normalize_character(character.name),
            *(_normalize_character(alias) for alias in character.aliases),
        }:
            return character.name
    return str(character_id).replace("_", " ").replace("-", " ").title()


def _character_lookup(
    chapter_characters: List[DialogueAiCharacterRef],
    book_characters: List[DialogueAiCharacterRef],
) -> Dict[str, DialogueAiCharacterRef]:
    lookup: Dict[str, DialogueAiCharacterRef] = {}
    for character in [*book_characters, *chapter_characters]:
        for value in [character.characterId, character.name, *character.aliases]:
            normalized = _normalize_character(value)
            if normalized:
                lookup[normalized] = character
    return lookup


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


def _normalize_model_answer(raw_output: str) -> str:
    answer = str(raw_output or "").strip()
    answer = re.sub(r"^```(?:text)?\s*", "", answer, flags=re.IGNORECASE)
    answer = re.sub(r"\s*```$", "", answer)
    answer = answer.splitlines()[0].strip() if answer else ""
    answer = re.sub(r"^(?:character(?:id)?|speaker)\s*:\s*", "", answer, flags=re.IGNORECASE)
    return answer.strip(" `\"'.,;:")


class LocalDialogueAiService:
    def __init__(self, get_book_root: Callable[[], str]):
        self._get_book_root = get_book_root
        self._status_lock = threading.Lock()
        self._run_statuses: Dict[str, Dict[str, Any]] = {}

    def prepare_status(self, request: LocalDialogueAiRequest) -> None:
        request_id = str(request.request_id or "").strip()
        if not request_id:
            return
        total_lines = sum(
            1
            for line in request.dialogue.lines
            if line.characterId not in (None, "", "narrator")
        )
        if request.max_lines is not None and request.max_lines >= 0:
            total_lines = min(total_lines, request.max_lines)
        self._update_status(
            request_id,
            status="queued",
            processed_lines=0,
            total_lines=total_lines,
            chapter_path=request.chapter_path,
        )

    def get_status(self, request_id: str) -> Optional[DialogueAiAssistStatus]:
        with self._status_lock:
            payload = self._run_statuses.get(str(request_id or "").strip())
            return DialogueAiAssistStatus(**payload) if payload else None

    def _update_status(
        self,
        request_id: Optional[str],
        *,
        status: str,
        processed_lines: int,
        total_lines: int,
        chapter_path: Optional[str] = None,
        error: Optional[str] = None,
        started_at: Optional[str] = None,
    ) -> None:
        request_id = str(request_id or "").strip()
        if not request_id:
            return

        now = datetime.now(timezone.utc).isoformat()
        total = max(0, int(total_lines or 0))
        processed = min(max(0, int(processed_lines or 0)), total) if total else 0
        ratio = processed / total if total else (1.0 if status == "completed" else 0.0)
        with self._status_lock:
            previous = self._run_statuses.get(request_id, {})
            self._run_statuses[request_id] = {
                "requestId": request_id,
                "status": status,
                "processedLines": processed,
                "totalLines": total,
                "progressRatio": ratio,
                "startedAt": started_at or previous.get("startedAt") or now,
                "updatedAt": now,
                "chapterPath": chapter_path or previous.get("chapterPath"),
                "error": error,
                "logDirectory": None,
            }
            while len(self._run_statuses) > 64:
                self._run_statuses.pop(next(iter(self._run_statuses)), None)

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
        self._update_status(
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
                self._update_status(
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
            self._update_status(
                request_id,
                status="completed",
                processed_lines=total_lines,
                total_lines=total_lines,
                chapter_path=request.chapter_path,
                started_at=started_at,
            )
            return LocalDialogueAiResponse(summary=summary, results=results, errors=errors)
        except Exception as exc:
            self._update_status(
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
