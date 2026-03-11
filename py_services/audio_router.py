import json
import os
from pathlib import Path
from typing import Callable, Optional

from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse

from api_models import (
    DeleteAudioLineRequest,
    DeleteCharacterAudioRequest,
    GenerateAudioLineRequest,
    ListVoiceSamplesRequest,
    ReconcileAudioManifestRequest,
    ReadFileAbsoluteRequest,
    ScanManifestsRequest,
)
from audio_generation_service import AudioGenerationService


def create_audio_router(
    get_audio_root: Callable[[], str],
    get_book_root: Callable[[], str],
    get_audio_service: Callable[[], Optional[AudioGenerationService]],
    set_audio_service: Callable[[str, AudioGenerationService], None],
) -> APIRouter:
    router = APIRouter(tags=["audio"])

    def _ensure_audio_service(audio_root_override: Optional[str] = None) -> AudioGenerationService:
        service = get_audio_service()
        if service is not None:
            return service

        fallback_root = audio_root_override or get_audio_root() or get_book_root()
        if not fallback_root:
            raise HTTPException(status_code=400, detail="Audio root not configured. Please set audio root path first.")

        normalized_root = os.path.normpath(fallback_root)
        new_service = AudioGenerationService(normalized_root)
        set_audio_service(normalized_root, new_service)
        print(f"Audio root path defaulted to: '{normalized_root}'")
        return new_service

    @router.post("/api/read_file_absolute")
    async def read_file_absolute(request: ReadFileAbsoluteRequest):
        try:
            file_path = Path(request.file_path)

            if not file_path.is_absolute():
                raise HTTPException(status_code=400, detail="File path must be absolute")
            if not file_path.exists():
                raise HTTPException(status_code=404, detail=f"File not found: {file_path}")
            if not file_path.is_file():
                raise HTTPException(status_code=400, detail=f"Path is not a file: {file_path}")

            with open(file_path, "r", encoding="utf-8") as handle:
                return json.load(handle)
        except HTTPException as http_exc:
            raise http_exc
        except json.JSONDecodeError as exc:
            raise HTTPException(status_code=400, detail=f"Invalid JSON file: {str(exc)}") from exc
        except Exception as exc:
            print(f"Error reading file {request.file_path}: {exc}")
            import traceback

            traceback.print_exc()
            raise HTTPException(status_code=500, detail=str(exc)) from exc

    @router.post("/api/list-voice-samples")
    async def list_voice_samples(request: ListVoiceSamplesRequest):
        try:
            root = Path(request.samples_root)
            if not root.exists():
                raise HTTPException(status_code=404, detail=f"Samples root not found: {root}")
            if not root.is_dir():
                raise HTTPException(status_code=400, detail=f"Samples root is not a directory: {root}")

            allowed_exts = {".wav", ".mp3", ".flac", ".ogg"}
            samples = sorted([path.name for path in root.iterdir() if path.is_file() and path.suffix.lower() in allowed_exts])
            return {"samples": samples}
        except HTTPException as http_exc:
            raise http_exc
        except Exception as exc:
            print(f"Error listing voice samples: {exc}")
            raise HTTPException(status_code=500, detail=str(exc)) from exc

    @router.get("/api/voice-sample")
    async def serve_voice_sample(filename: str, samples_root: str):
        try:
            root = Path(samples_root)
            if not root.exists():
                raise HTTPException(status_code=404, detail=f"Samples root not found: {root}")
            if not root.is_dir():
                raise HTTPException(status_code=400, detail=f"Samples root is not a directory: {root}")

            requested_name = Path(filename).name
            if requested_name != filename:
                raise HTTPException(status_code=400, detail="Invalid sample filename")

            allowed_exts = {".wav", ".mp3", ".flac", ".ogg"}
            suffix = Path(requested_name).suffix.lower()
            if suffix not in allowed_exts:
                raise HTTPException(status_code=400, detail="Unsupported sample format")

            root_resolved = root.resolve()
            sample_path = (root_resolved / requested_name).resolve()

            try:
                sample_path.relative_to(root_resolved)
            except ValueError as exc:
                raise HTTPException(status_code=403, detail="Access denied") from exc

            if not sample_path.exists() or not sample_path.is_file():
                raise HTTPException(status_code=404, detail=f"Sample not found: {requested_name}")

            if suffix == ".mp3":
                media_type = "audio/mpeg"
            elif suffix == ".flac":
                media_type = "audio/flac"
            elif suffix == ".ogg":
                media_type = "audio/ogg"
            else:
                media_type = "audio/wav"

            return FileResponse(path=str(sample_path), media_type=media_type, filename=requested_name)
        except HTTPException as http_exc:
            raise http_exc
        except Exception as exc:
            print(f"Error serving voice sample '{filename}': {exc}")
            raise HTTPException(status_code=500, detail=str(exc)) from exc

    @router.post("/api/generate_audio_line")
    async def generate_audio_line(request: GenerateAudioLineRequest):
        try:
            service = _ensure_audio_service(request.audio_root)

            text_preview = request.text[:50].encode("utf-8", errors="replace").decode("utf-8", errors="replace")
            print(f"Generating audio for line {request.line_id} - {request.character_name}")
            print(f"  Text: {text_preview}...")
            print(f"  Provider: {request.provider}, Voice: {request.voice_id}")

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

            if result.get("success"):
                print(f"  [OK] Audio generated: {result['audio_path']}")
                return {"audio_path": result["audio_path"]}

            error_msg = result.get("error", "Unknown error")
            print(f"  [FAILED] Generation failed: {error_msg}")
            raise HTTPException(status_code=500, detail=error_msg)
        except HTTPException as http_exc:
            raise http_exc
        except Exception as exc:
            error_msg = str(exc).encode("utf-8", errors="replace").decode("utf-8", errors="replace")
            print(f"Error generating audio: {error_msg}")
            import traceback

            try:
                traceback.print_exc()
            except UnicodeEncodeError:
                print("Error details could not be printed due to encoding issue")
            raise HTTPException(status_code=500, detail=error_msg) from exc

    @router.post("/api/delete_character_audio")
    async def delete_character_audio(request: DeleteCharacterAudioRequest):
        try:
            service = _ensure_audio_service()
            print(f"Deleting audio for {request.character_name} in {request.chapter_title}")

            success = service.delete_character_audio(
                chapter_title=request.chapter_title,
                character_name=request.character_name,
            )

            if success:
                print("  [OK] Audio deleted successfully")
                return {"message": "Audio deleted successfully"}

            raise HTTPException(status_code=500, detail="Failed to delete audio")
        except HTTPException as http_exc:
            raise http_exc
        except Exception as exc:
            print(f"Error deleting audio: {exc}")
            import traceback

            traceback.print_exc()
            raise HTTPException(status_code=500, detail=str(exc)) from exc

    @router.post("/api/delete_audio_line")
    async def delete_audio_line(request: DeleteAudioLineRequest):
        try:
            service = _ensure_audio_service()
            print(f"Deleting audio for line {request.line_id} - {request.character_name} in {request.chapter_title}")

            success = service.delete_audio_line(
                chapter_title=request.chapter_title,
                character_name=request.character_name,
                line_id=request.line_id,
            )

            if success:
                print("  [OK] Audio line deleted successfully")
                return {"message": "Audio line deleted successfully"}

            raise HTTPException(status_code=500, detail="Failed to delete audio line")
        except HTTPException as http_exc:
            raise http_exc
        except Exception as exc:
            print(f"Error deleting audio line: {exc}")
            import traceback

            traceback.print_exc()
            raise HTTPException(status_code=500, detail=str(exc)) from exc

    @router.post("/api/reconcile-audio-manifest")
    async def reconcile_audio_manifest(request: ReconcileAudioManifestRequest):
        service = _ensure_audio_service(request.audio_root)
        return service.reconcile_character_manifest(
            chapter_title=request.chapter_title,
            character_name=request.character_name,
        )

    @router.get("/api/audio/{chapter_title}/{character_name}/{line_id}")
    async def serve_audio_file(
        chapter_title: str,
        character_name: str,
        line_id: int,
        root: str | None = None,
    ):
        try:
            audio_root = get_audio_root() or get_book_root() or root
            if not audio_root:
                raise HTTPException(status_code=400, detail="Audio root not configured. Please set audio root path first.")

            try:
                resolved_audio_root = Path(audio_root).resolve()
            except HTTPException:
                raise
            except Exception as exc:
                raise HTTPException(status_code=400, detail="Invalid audio root path") from exc

            audio_dir = resolved_audio_root / chapter_title / "audio_lines" / character_name
            candidate_filenames = [f"{line_id}-{character_name}.mp3", f"{line_id}-{character_name}.wav"]

            resolved_audio_path = None
            audio_filename = candidate_filenames[0]
            for candidate_name in candidate_filenames:
                candidate_path = audio_dir / candidate_name
                try:
                    resolved_candidate = candidate_path.resolve()
                    resolved_candidate.relative_to(resolved_audio_root)
                except ValueError as exc:
                    raise HTTPException(status_code=403, detail="Access denied") from exc
                except Exception as exc:
                    raise HTTPException(status_code=400, detail="Invalid file path") from exc

                if resolved_candidate.exists():
                    if not resolved_candidate.is_file():
                        raise HTTPException(status_code=400, detail=f"Path is not a file: {candidate_name}")
                    resolved_audio_path = resolved_candidate
                    audio_filename = candidate_name
                    break

            if resolved_audio_path is None:
                raise HTTPException(status_code=404, detail=f"Audio file not found: {audio_filename}")

            media_type = "audio/mpeg" if resolved_audio_path.suffix.lower() == ".mp3" else "audio/wav"
            return FileResponse(
                path=str(resolved_audio_path),
                media_type=media_type,
                filename=audio_filename,
                headers={
                    "Cache-Control": "no-store, no-cache, must-revalidate, max-age=0",
                    "Pragma": "no-cache",
                    "Expires": "0",
                },
            )
        except HTTPException as http_exc:
            raise http_exc
        except Exception as exc:
            print(f"Error serving audio file: {exc}")
            import traceback

            traceback.print_exc()
            raise HTTPException(status_code=500, detail=str(exc)) from exc

    @router.post("/api/scan-voice-manifests")
    async def scan_voice_manifests(request: ScanManifestsRequest):
        try:
            audio_root = Path(request.audioRoot)
            if not audio_root.exists():
                raise HTTPException(status_code=404, detail=f"Audio root path does not exist: {audio_root}")
            if not audio_root.is_dir():
                raise HTTPException(status_code=400, detail=f"Audio root path is not a directory: {audio_root}")

            print(f"Scanning for manifest files in: {audio_root}")
            manifest_files = list(audio_root.rglob("manifest.json"))
            print(f"Found {len(manifest_files)} manifest files")

            manifests = []
            for manifest_path in manifest_files:
                try:
                    with open(manifest_path, "r", encoding="utf-8") as handle:
                        manifest_data = json.load(handle)
                        if manifest_data.get("formatVersion") == "2.0":
                            manifests.append(manifest_data)
                except Exception as exc:
                    print(f"Error reading manifest {manifest_path}: {exc}")
                    continue

            print(f"Successfully loaded {len(manifests)} v2.0 manifests")
            return {
                "manifests": manifests,
                "count": len(manifests),
                "audioRoot": str(audio_root),
            }
        except HTTPException as http_exc:
            raise http_exc
        except Exception as exc:
            print(f"Error scanning manifests: {exc}")
            import traceback

            traceback.print_exc()
            raise HTTPException(status_code=500, detail=str(exc)) from exc

    return router