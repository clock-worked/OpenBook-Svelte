import os
import threading
from datetime import datetime, timezone
from typing import Any, Callable, Dict, List, Optional

from api_models import (
    DialogueAiAssistStatus,
    DialogueAiCharacterRef,
    LocalDialogueAiLineResult,
    LocalDialogueAiRequest,
    LocalDialogueAiResponse,
    LocalDialogueAiSummary,
)
from dialogue_ai_context import build_assist_context
from jev_client import JevClient
from local_dialogue_ai_service import _load_local_env_file, _normalize_model_answer, _character_lookup, _character_name, _normalize_character


class JevService:
    def __init__(self, get_book_root: Callable[[], str]):
        self._get_book_root = get_book_root
        self._status_lock = threading.Lock()
        self._run_statuses: Dict[str, Dict[str, Any]] = {}
        _load_local_env_file()
        api_key = os.getenv("VERCEL_JEV_API_KEY")
        if not api_key:
            raise ValueError("VERCEL_JEV_API_KEY not found in .env file or environment.")
        self._client = JevClient(api_key=api_key)

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
                    character_lookup=lookup,
                    assist_context=assist_context,
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
        character_lookup: Dict[str, DialogueAiCharacterRef],
        assist_context: Dict[str, Any],
    ) -> LocalDialogueAiLineResult:
        line = target["line"]
        try:
            state, questions = self._build_jev_request(request, line, target, assist_context)
            jev_response = self._client.ask(state=state, questions=questions)

            if not isinstance(jev_response, dict) or not isinstance(jev_response.get("answers"), dict):
                raise ValueError("JEV returned an invalid answers object.")
            answer_payload = jev_response["answers"].get("speaker")
            if not isinstance(answer_payload, dict):
                raise ValueError("JEV returned no valid speaker answer.")
            suggested_id = answer_payload.get("choice")
            if not isinstance(suggested_id, str):
                raise ValueError("JEV returned an invalid speaker choice; expected a string.")
            jev_confidence = answer_payload.get("confidence")
            if jev_confidence is not None and (
                isinstance(jev_confidence, bool)
                or not isinstance(jev_confidence, (int, float))
                or not 0 <= jev_confidence <= 1
            ):
                raise ValueError("JEV returned an invalid confidence; expected a number between 0 and 1.")
            raw_output = str(jev_response)

        except (ValueError, ConnectionError) as e:
            return self._create_error_result(target, str(e), character_lookup, assist_context)

        current_ref = character_lookup.get(_normalize_character(line.characterId))
        current_character_id = current_ref.characterId if current_ref else line.characterId
        current_character_name = current_ref.name if current_ref else _character_name(
            line.characterId, assist_context["chapterCharacters"]
        )
        common = {
            "lineId": line.id,
            "paragraphIndex": target["paragraphIndex"],
            "currentCharacterId": current_character_id,
            "currentCharacterName": current_character_name,
            "currentParagraphText": target["currentParagraphText"],
            "rawOutput": raw_output,
            "confidence": jev_confidence,
        }

        if not suggested_id or suggested_id.lower() == "none":
            return LocalDialogueAiLineResult(**common, outcome="unresolved")

        suggested_ref = character_lookup.get(_normalize_character(suggested_id))
        if suggested_ref is None:
            return LocalDialogueAiLineResult(**common, outcome="unresolved")

        is_same_character = _normalize_character(suggested_ref.characterId) == _normalize_character(current_character_id)

        return LocalDialogueAiLineResult(
            **common,
            suggestedCharacterId=suggested_ref.characterId,
            suggestedCharacterName=suggested_ref.name,
            outcome="keep_existing" if is_same_character else "suggestion",
        )

    def _create_error_result(
        self,
        target: Dict[str, Any],
        error: str,
        character_lookup: Dict[str, DialogueAiCharacterRef],
        assist_context: Dict[str, Any],
    ) -> LocalDialogueAiLineResult:
        line = target["line"]
        current_ref = character_lookup.get(_normalize_character(line.characterId))
        return LocalDialogueAiLineResult(
            lineId=line.id,
            paragraphIndex=target["paragraphIndex"],
            currentCharacterId=current_ref.characterId if current_ref else line.characterId,
            currentCharacterName=(
                current_ref.name
                if current_ref
                else _character_name(line.characterId, assist_context["chapterCharacters"])
            ),
            currentParagraphText=target["currentParagraphText"],
            outcome="error",
            error=error,
        )

    def _build_jev_request(self, request, line, target, assist_context) -> tuple[str, dict]:
        start = int(line.span.start)
        end = int(line.span.end)
        if start < 0 or end < start or start >= len(request.chapter_text) or end > len(request.chapter_text):
            raise ValueError(f"Invalid source span for line {line.id}: {start}-{end}")

        # --- compact preamble ---
        preamble_parts: List[str] = []

        # 1. Last speaker + recent turn pattern
        recent = target.get("recentTurnHistory", [])
        if recent:
            last_name = None
            turn_names = []
            for r in recent:
                name = r.get("characterName", r.get("characterId", "?"))
                if r.get("relativeIndex", 0) < 0:
                    turn_names.append(name)
                    if last_name is None:
                        last_name = name
            if last_name:
                preamble_parts.append(f"[Last speaker: {last_name}]")
            if turn_names:
                preamble_parts.append(f"[Recent: {' → '.join(reversed(turn_names))}]")

        # 3. Build state text
        context_start = max(0, start - 1500)
        context_end = min(len(request.chapter_text), end + 1500)
        raw_context = (
            request.chapter_text[context_start:start]
            + "<quote>"
            + request.chapter_text[start:end]
            + "</quote>"
            + request.chapter_text[end:context_end]
        ).strip()

        if preamble_parts:
            state = " ".join(preamble_parts) + "\n" + raw_context
        else:
            state = raw_context

        # --- enriched criteria ---
        # Build a lookup for character metadata
        char_meta: Dict[str, DialogueAiCharacterRef] = {}
        for c in assist_context.get("chapterCharacters", []):
            char_meta[c.characterId] = c

        scene_roster = target.get("sceneRoster", {}).get("presentCharacters", [])
        if len(scene_roster) > 51:
            scene_roster = scene_roster[:51]

        criteria: Dict[str, str] = {}
        for char in scene_roster:
            cid = char["characterId"]
            name = char.get("characterName", cid)
            meta = char_meta.get(cid)
            if meta and meta.aliases:
                alias_hint = ", ".join(meta.aliases[:2])
                criteria[cid] = f"{name} ({alias_hint})"
            else:
                criteria[cid] = name
        criteria["None"] = "Narration / speaker not in list"

        questions = {
            "speaker": {
                "type": "choice",
                "instructions": "Who is the speaker of the dialogue in the <quote> tags?",
                "criteria": criteria,
            }
        }
        return state, questions
