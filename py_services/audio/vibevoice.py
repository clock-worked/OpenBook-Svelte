from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from audio_generation_service import AudioGenerationService


class VibeVoiceLineRequest(BaseModel):
    line_id: int
    text: str
    character_name: str
    character_id: str
    voice_id: str
    provider: str = "vibevoice_local"
    chapter_title: str
    source_file: str
    voice_sample_root: Optional[str] = None
    audio_root: Optional[str] = None


class VibeVoiceLineInput(BaseModel):
    id: int
    text: str
    characterId: Optional[str] = None
    chosenSpeaker: Optional[str] = None


class VibeVoiceCharacterRequest(BaseModel):
    character_name: str
    character_id: str
    lines: List[VibeVoiceLineInput]
    voice_id: str
    provider: str = "vibevoice_local"
    chapter_title: str
    source_file: str
    voice_sample_root: Optional[str] = None
    audio_root: Optional[str] = None


class VibeVoiceAssignment(BaseModel):
    character_id: str
    character_name: str
    voice_id: str
    provider: str = "vibevoice_local"


class VibeVoiceChapterRequest(BaseModel):
    dialogue_path: str
    assignments: List[VibeVoiceAssignment]
    chapter_title: Optional[str] = None
    source_file: Optional[str] = None
    voice_sample_root: Optional[str] = None
    audio_root: Optional[str] = None


def create_vibevoice_router(
    get_audio_root: Callable[[], str],
    get_book_root: Callable[[], str],
    get_audio_service: Callable[[], Optional[AudioGenerationService]],
    set_audio_service: Callable[[str, AudioGenerationService], None],
) -> APIRouter:
    router = APIRouter(prefix="/api/vibevoice", tags=["vibevoice"])

    def _ensure_audio_service(audio_root_override: Optional[str]) -> AudioGenerationService:
        existing = get_audio_service()
        if existing is not None:
            return existing

        selected_root = audio_root_override or get_audio_root() or get_book_root()
        if not selected_root:
            raise HTTPException(
                status_code=400,
                detail="Audio root not configured. Set audio root or provide audio_root in request.",
            )

        normalized_root = os.path.normpath(selected_root)
        service = AudioGenerationService(normalized_root)
        set_audio_service(normalized_root, service)
        return service

    @router.post("/line")
    async def generate_line(request: VibeVoiceLineRequest):
        service = _ensure_audio_service(request.audio_root)

        result = await service.generate_audio_line(
            line_id=request.line_id,
            text=request.text,
            character_name=request.character_name,
            character_id=request.character_id,
            voice_id=request.voice_id,
            provider=request.provider,
            chapter_title=request.chapter_title,
            source_file=request.source_file,
            voice_sample_root=request.voice_sample_root,
        )

        if not result.get("success"):
            error_detail = result.get("error", "Generation failed")
            error_text = str(error_detail).lower()
            status = 500
            if "not found" in error_text or "could not be found" in error_text:
                status = 400
            elif "required" in error_text or "invalid" in error_text:
                status = 400
            raise HTTPException(status_code=status, detail=error_detail)

        return {
            "success": True,
            "generatedCount": 1,
            "audio_path": result.get("audio_path"),
        }

    @router.post("/character")
    async def generate_character(request: VibeVoiceCharacterRequest):
        service = _ensure_audio_service(request.audio_root)

        errors: List[str] = []
        generated_count = 0

        for line in request.lines:
            result = await service.generate_audio_line(
                line_id=line.id,
                text=line.text,
                character_name=request.character_name,
                character_id=request.character_id,
                voice_id=request.voice_id,
                provider=request.provider,
                chapter_title=request.chapter_title,
                source_file=request.source_file,
                voice_sample_root=request.voice_sample_root,
            )
            if result.get("success"):
                generated_count += 1
            else:
                errors.append(f"Line {line.id}: {result.get('error', 'Unknown error')}")

        return {
            "success": len(errors) == 0,
            "generatedCount": generated_count,
            "totalLines": len(request.lines),
            "errors": errors,
        }

    @router.post("/chapter")
    async def generate_chapter(request: VibeVoiceChapterRequest):
        service = _ensure_audio_service(request.audio_root)

        raw_path = request.dialogue_path
        dialogue_file = Path(raw_path)
        if not dialogue_file.is_absolute():
            root = get_book_root()
            dialogue_file = Path(root) / raw_path

        if not dialogue_file.exists() or not dialogue_file.is_file():
            raise HTTPException(status_code=404, detail=f"dialogue.json not found: {dialogue_file}")

        try:
            with open(dialogue_file, "r", encoding="utf-8") as handle:
                dialogue = json.load(handle)
        except Exception as exc:
            raise HTTPException(status_code=400, detail=f"Failed to read dialogue.json: {exc}") from exc

        lines = dialogue.get("lines", [])
        if not isinstance(lines, list):
            raise HTTPException(status_code=400, detail="Invalid dialogue.json format: lines must be a list")

        assignments_by_id: Dict[str, VibeVoiceAssignment] = {
            item.character_id: item for item in request.assignments
        }
        assignments_by_name: Dict[str, VibeVoiceAssignment] = {
            item.character_name.strip().lower(): item
            for item in request.assignments
            if item.character_name
        }

        chapter_title = request.chapter_title or dialogue_file.parent.name
        source_file = request.source_file or str(dialogue_file)

        errors: List[str] = []
        generated_count = 0
        skipped_count = 0

        for line in lines:
            if not isinstance(line, dict):
                continue

            line_id = line.get("id")
            text = (line.get("text") or "").strip()
            if not isinstance(line_id, int) or not text:
                continue

            character_id = line.get("characterId") or "narrator"
            chosen_speaker = line.get("chosenSpeaker")

            assignment = assignments_by_id.get(str(character_id))
            if assignment is None and isinstance(chosen_speaker, str):
                assignment = assignments_by_name.get(chosen_speaker.strip().lower())

            if assignment is None:
                skipped_count += 1
                errors.append(f"Line {line_id}: no selected voice mapping for character '{character_id}'")
                continue

            result = await service.generate_audio_line(
                line_id=line_id,
                text=text,
                character_name=assignment.character_name,
                character_id=assignment.character_id,
                voice_id=assignment.voice_id,
                provider=assignment.provider,
                chapter_title=chapter_title,
                source_file=source_file,
                voice_sample_root=request.voice_sample_root,
            )

            if result.get("success"):
                generated_count += 1
            else:
                errors.append(f"Line {line_id}: {result.get('error', 'Unknown error')}")

        return {
            "success": len(errors) == 0,
            "generatedCount": generated_count,
            "skippedCount": skipped_count,
            "totalLines": len(lines),
            "errors": errors,
            "chapterTitle": chapter_title,
        }

    return router
