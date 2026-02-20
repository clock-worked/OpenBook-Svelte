#!/usr/bin/env python3
"""
FastAPI server for OpenBook parser service.
"""

import os
import sys
import tempfile
import json
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from pydantic import BaseModel
from typing import List, Optional, Dict

# Set UTF-8 encoding for Windows console
if sys.platform == 'win32':
    if hasattr(sys.stdout, 'reconfigure'):
        sys.stdout.reconfigure(encoding='utf-8')
    if hasattr(sys.stderr, 'reconfigure'):
        sys.stderr.reconfigure(encoding='utf-8')
    os.environ['PYTHONIOENCODING'] = 'utf-8'

# Ensure local package import works
CURRENT_DIR = os.path.dirname(os.path.abspath(__file__))
if CURRENT_DIR not in sys.path:
    sys.path.insert(0, CURRENT_DIR)

from openbook_parser.dialogue_parser_service import DialogueParserService, script_to_dict_list
from openbook_parser.booknlp_parser_service import BookNLPParserService
from chapter_service import list_chapters as list_chapters_service, get_chapter_stats
from pathlib import Path
from update_character_stats import update_character_stats
from audio_generation_service import AudioGenerationService
from audio.vibevoice import create_vibevoice_router

app = FastAPI()

# Global variables to store paths
BOOK_ROOT_PATH = ""
AUDIO_ROOT_PATH = ""
audio_service: Optional[AudioGenerationService] = None

# CORS configuration
origins = [
    "http://localhost:5173",  # SvelteKit dev server
    "http://127.0.0.1:5173",  # SvelteKit dev server (loopback)
    "http://localhost:4173",  # SvelteKit preview server
    "http://127.0.0.1:4173",  # SvelteKit preview server (loopback)
]

app.add_middleware(
    CORSMiddleware,
    allow_origins=origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


def _get_audio_root() -> str:
    return AUDIO_ROOT_PATH


def _get_book_root() -> str:
    return BOOK_ROOT_PATH


def _get_audio_service() -> Optional[AudioGenerationService]:
    return audio_service


def _set_audio_service(root_path: str, service: AudioGenerationService) -> None:
    global AUDIO_ROOT_PATH, audio_service
    AUDIO_ROOT_PATH = os.path.normpath(root_path)
    audio_service = service


app.include_router(
    create_vibevoice_router(
        get_audio_root=_get_audio_root,
        get_book_root=_get_book_root,
        get_audio_service=_get_audio_service,
        set_audio_service=_set_audio_service,
    )
)

class SetRootRequest(BaseModel):
    root_path: str


class SetAudioRootRequest(BaseModel):
    audio_root: str


@app.post("/api/set-book-root")
async def set_book_root(request: SetRootRequest):
    global BOOK_ROOT_PATH
    
    # Basic validation
    if not os.path.isdir(request.root_path):
        raise HTTPException(status_code=400, detail="Provided path is not a valid directory.")
        
    BOOK_ROOT_PATH = os.path.normpath(request.root_path)
    print(f"Book root path set to: '{BOOK_ROOT_PATH}'")

    # Default audio root to book root if not already configured.
    global AUDIO_ROOT_PATH, audio_service
    if not AUDIO_ROOT_PATH:
        AUDIO_ROOT_PATH = BOOK_ROOT_PATH
        audio_service = AudioGenerationService(AUDIO_ROOT_PATH)
        print(f"Audio root path defaulted to book root: '{AUDIO_ROOT_PATH}'")

    return {"message": "Book root path set successfully.", "path": BOOK_ROOT_PATH}


@app.post("/api/set-audio-root")
async def set_audio_root(request: SetAudioRootRequest):
    global AUDIO_ROOT_PATH, audio_service
    
    # Basic validation
    audio_root_path = request.audio_root
    if not os.path.isdir(audio_root_path):
        raise HTTPException(status_code=400, detail="Provided audio root path is not a valid directory.")
    
    AUDIO_ROOT_PATH = os.path.normpath(audio_root_path)
    audio_service = AudioGenerationService(AUDIO_ROOT_PATH)
    print(f"Audio root path set to: '{AUDIO_ROOT_PATH}'")
    
    return {"message": "Audio root path set successfully.", "path": AUDIO_ROOT_PATH}

@app.get("/api/list-chapters")
async def list_chapters():
    """
    Lists all .txt files in the book root directory as chapters.
    Returns a list of chapter objects with name, path, and parsed status.
    """
    try:
        chapters = list_chapters_service(BOOK_ROOT_PATH)
        stats = get_chapter_stats(chapters)
        
        # Convert to dictionaries for JSON response
        chapters_dict = [ch.to_dict() for ch in chapters]
        
        print(f"Found {stats['total']} chapters in '{BOOK_ROOT_PATH}' ({stats['parsed']} parsed)")
        return {
            "chapters": chapters_dict,
            "count": stats['total'],
            "stats": stats
        }
    
    except ValueError as ve:
        raise HTTPException(status_code=400, detail=str(ve))
    except Exception as e:
        print(f"Error listing chapters: {e}")
        raise HTTPException(status_code=500, detail=f"Failed to list chapters: {str(e)}")

class ParseRequest(BaseModel):
    text: str
    filename: str
    manual_blocklist: Optional[List[str]] = None
    parser_options: Optional[Dict[str, object]] = None


FORCED_BLOCKED_SPEAKERS = ["he", "she", "as"]

class CorefTestRequest(BaseModel):
    text: Optional[str] = None

# Pydantic models for saving dialogue.json
class Span(BaseModel):
    start: int
    end: int

class Metadata(BaseModel):
    emotion: Optional[str] = None
    intensity: float
    pacing: Optional[str] = None
    prefix: Optional[str] = None
    customTags: dict

class Candidate(BaseModel):
    characterId: str
    confidence: float

class DialogueLine(BaseModel):
    id: int
    characterId: Optional[str]
    text: str
    span: Span
    metadata: Metadata
    candidates: List[Candidate]
    isConflict: bool

class Stats(BaseModel):
    totalLines: int
    conflicts: int
    characterBreakdown: Dict[str, int]

class DialogueJson(BaseModel):
    formatVersion: str
    chapterId: str
    lines: List[DialogueLine]
    stats: Stats

class SaveRequest(BaseModel):
    file_path: str
    content: DialogueJson

@app.post("/api/save")
async def save_file(request: SaveRequest):
    """
    Accepts a file path and dialogue content and saves it to disk.
    """
    try:
        # If no book root is set, we can't save relative paths reliably
        if not BOOK_ROOT_PATH:
            raise HTTPException(status_code=400, detail="Book root path not set. Please select a book directory first.")

        # Join the book root with the relative path from the request
        full_path = os.path.join(BOOK_ROOT_PATH, request.file_path)
        
        # Normalize the path to handle mixed separators and other OS-specific quirks
        normalized_path = os.path.normpath(full_path)
        print(f"Received save request for relative path: '{request.file_path}'")
        print(f"Full path: '{full_path}'")
        print(f"Normalized path: '{normalized_path}'")

        # Basic security check to prevent path traversal attacks.
        # This should be made more robust in a production environment.
        if ".." in normalized_path:
            raise HTTPException(status_code=400, detail="Invalid file path")

        # The Pydantic model is automatically converted to a dictionary.
        # We need to convert it back to a JSON string with indentation.
        content_str = request.content.model_dump_json(indent=2)
        
        # Ensure the directory exists
        dir_name = os.path.dirname(normalized_path)
        if not os.path.exists(dir_name):
            os.makedirs(dir_name)
            print(f"Created directory: {dir_name}")

        with open(normalized_path, 'w', encoding='utf-8') as f:
            f.write(content_str)
        
        print(f"Successfully wrote {len(content_str)} bytes to {normalized_path}")
        
        # If this is a dialogue.json file, update character stats
        if 'dialogue.json' in request.file_path:
            try:
                print("Updating character stats after dialogue save...")
                stats_result = update_character_stats(Path(BOOK_ROOT_PATH))
                if stats_result.get('success'):
                    print(f"  ✓ Updated {stats_result['updatedCharacters']} characters")
                else:
                    print(f"  ⚠️  Character stats update failed: {stats_result.get('error')}")
            except Exception as stats_err:
                print(f"  ⚠️  Error updating character stats: {stats_err}")
                # Don't fail the whole save if stats update fails
            
        return {"message": "File saved successfully."}
    except HTTPException as http_exc:
        raise http_exc
    except Exception as e:
        # Log the error for debugging purposes
        print(f"Error saving file '{normalized_path}': {e}")
        import traceback
        traceback.print_exc()
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/api/update-character-stats")
async def update_stats():
    """
    Manually trigger an update of character statistics in characters.json
    by scanning all dialogue.json files.
    """
    try:
        if not BOOK_ROOT_PATH:
            raise HTTPException(status_code=400, detail="Book root path not set.")
        
        print(f"Manual character stats update requested for: {BOOK_ROOT_PATH}")
        result = update_character_stats(Path(BOOK_ROOT_PATH))
        
        if result.get('success'):
            return {
                "message": "Character stats updated successfully",
                "stats": result
            }
        else:
            raise HTTPException(
                status_code=500, 
                detail=f"Failed to update character stats: {result.get('error')}"
            )
    
    except HTTPException as http_exc:
        raise http_exc
    except Exception as e:
        print(f"Error updating character stats: {e}")
        import traceback
        traceback.print_exc()
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/api/parse")
async def parse_text(request: ParseRequest):
    """
    Accepts raw text, saves it to a temporary file, and runs the parser on it.
    """
    try:
        source_path = request.filename
        if source_path and not os.path.isabs(source_path) and BOOK_ROOT_PATH:
            source_path = os.path.normpath(os.path.join(BOOK_ROOT_PATH, source_path))

        # Preserve newlines exactly as sent by the client to keep span offsets aligned.
        with tempfile.NamedTemporaryFile(mode='w+', delete=False, encoding='utf-8', newline='', suffix=".txt") as temp_file:
            temp_file.write(request.text)
            temp_file_path = temp_file.name

        parser_service = BookNLPParserService()
        manual_blocklist = list(
            {
                *FORCED_BLOCKED_SPEAKERS,
                *(request.manual_blocklist or []),
            }
        )
        script_lines, character_names, parse_meta = parser_service.parse_file(
            temp_file_path,
            options=request.parser_options or {},
            manual_blocklist=manual_blocklist,
            source_path=source_path,
        )
        
        result = {
            "script": script_to_dict_list(script_lines),
            "characters": character_names,
            "meta": {
                "version": "1.0.0",
                "parserBackend": parse_meta.get("backend", "legacy"),
                "fallbackReason": parse_meta.get("fallback_reason"),
                "quotes": parse_meta.get("quotes"),
                "alignedQuotes": parse_meta.get("aligned_quotes"),
                "exactAlignedQuotes": parse_meta.get("exact_aligned_quotes"),
                "fallbackAlignedQuotes": parse_meta.get("fallback_aligned_quotes"),
                "agreementHits": parse_meta.get("agreement_hits"),
                "uncertainLines": parse_meta.get("uncertain_lines"),
            }
        }
        
        return result

    except Exception as e:
        return {"error": str(e)}, 500
    finally:
        if 'temp_file_path' in locals() and os.path.exists(temp_file_path):
            os.unlink(temp_file_path)


@app.post("/api/coref-health")
async def coref_health(request: CorefTestRequest):
    """
    Tests coreference model availability and returns a small sample result.
    """
    parser_service = DialogueParserService()
    return parser_service.coref_health(sample_text=request.text)


class ScanManifestsRequest(BaseModel):
    audioRoot: str


class ListVoiceSamplesRequest(BaseModel):
    samples_root: str


class ReadFileAbsoluteRequest(BaseModel):
    file_path: str


class GenerateAudioLineRequest(BaseModel):
    line_id: int
    text: str
    character_name: str
    character_id: str
    voice_id: str
    provider: str
    chapter_title: str
    source_file: str
    voice_sample_root: Optional[str] = None
    audio_root: Optional[str] = None


class DeleteCharacterAudioRequest(BaseModel):
    chapter_title: str
    character_name: str


class DeleteAudioLineRequest(BaseModel):
    chapter_title: str
    character_name: str
    line_id: int


@app.post("/api/read_file_absolute")
async def read_file_absolute(request: ReadFileAbsoluteRequest):
    """
    Reads a file from an absolute path and returns its JSON content.
    Used primarily for reading manifest.json files.
    """
    try:
        file_path = Path(request.file_path)
        
        # Security check: ensure the file path is absolute and points to a real file
        if not file_path.is_absolute():
            raise HTTPException(status_code=400, detail="File path must be absolute")
        
        if not file_path.exists():
            raise HTTPException(status_code=404, detail=f"File not found: {file_path}")
        
        if not file_path.is_file():
            raise HTTPException(status_code=400, detail=f"Path is not a file: {file_path}")
        
        # Read and parse the JSON file
        with open(file_path, 'r', encoding='utf-8') as f:
            data = json.load(f)
        
        return data
        
    except HTTPException as http_exc:
        raise http_exc
    except json.JSONDecodeError as e:
        raise HTTPException(status_code=400, detail=f"Invalid JSON file: {str(e)}")
    except Exception as e:
        print(f"Error reading file {request.file_path}: {e}")
        import traceback
        traceback.print_exc()
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/api/list-voice-samples")
async def list_voice_samples(request: ListVoiceSamplesRequest):
    """
    Lists local voice sample files from a directory.
    Returns a list of filenames for supported audio extensions.
    """
    try:
        root = Path(request.samples_root)
        if not root.exists():
            raise HTTPException(status_code=404, detail=f"Samples root not found: {root}")
        if not root.is_dir():
            raise HTTPException(status_code=400, detail=f"Samples root is not a directory: {root}")

        allowed_exts = {".wav", ".mp3", ".flac", ".ogg"}
        samples = sorted(
            [p.name for p in root.iterdir() if p.is_file() and p.suffix.lower() in allowed_exts]
        )

        return {"samples": samples}
    except HTTPException as http_exc:
        raise http_exc
    except Exception as exc:
        print(f"Error listing voice samples: {exc}")
        raise HTTPException(status_code=500, detail=str(exc))


@app.post("/api/generate_audio_line")
async def generate_audio_line(request: GenerateAudioLineRequest):
    """
    Generates audio for a single dialogue line using the configured TTS provider.
    """
    global AUDIO_ROOT_PATH, audio_service
    try:
        if not audio_service:
            fallback_root = request.audio_root or BOOK_ROOT_PATH
            if fallback_root:
                AUDIO_ROOT_PATH = os.path.normpath(fallback_root)
                audio_service = AudioGenerationService(AUDIO_ROOT_PATH)
                print(f"Audio root path defaulted to: '{AUDIO_ROOT_PATH}'")
            else:
                raise HTTPException(
                    status_code=400, 
                    detail="Audio root not configured. Please set audio root path first."
                )
        
        # Safely print text preview (handle Unicode)
        text_preview = request.text[:50].encode('utf-8', errors='replace').decode('utf-8', errors='replace')
        print(f"Generating audio for line {request.line_id} - {request.character_name}")
        print(f"  Text: {text_preview}...")
        print(f"  Provider: {request.provider}, Voice: {request.voice_id}")
        
        result = await audio_service.generate_audio_line(
            line_id=request.line_id,
            text=request.text,
            character_name=request.character_name,
            character_id=request.character_id,
            voice_id=request.voice_id,
            provider=request.provider,
            chapter_title=request.chapter_title,
            source_file=request.source_file,
            voice_sample_root=request.voice_sample_root
        )
        
        if result["success"]:
            print(f"  [OK] Audio generated: {result['audio_path']}")
            return {"audio_path": result["audio_path"]}
        else:
            error_msg = result.get("error", "Unknown error")
            print(f"  [FAILED] Generation failed: {error_msg}")
            raise HTTPException(status_code=500, detail=error_msg)
        
    except HTTPException as http_exc:
        raise http_exc
    except Exception as e:
        error_msg = str(e).encode('utf-8', errors='replace').decode('utf-8', errors='replace')
        print(f"Error generating audio: {error_msg}")
        import traceback
        try:
            traceback.print_exc()
        except UnicodeEncodeError:
            # Fallback for traceback if encoding fails
            print("Error details could not be printed due to encoding issue")
        raise HTTPException(status_code=500, detail=error_msg)


@app.post("/api/delete_character_audio")
async def delete_character_audio(request: DeleteCharacterAudioRequest):
    """
    Deletes all audio files for a character in a specific chapter.
    """
    global AUDIO_ROOT_PATH, audio_service
    try:
        if not audio_service:
            if BOOK_ROOT_PATH:
                AUDIO_ROOT_PATH = BOOK_ROOT_PATH
                audio_service = AudioGenerationService(AUDIO_ROOT_PATH)
                print(f"Audio root path defaulted to book root: '{AUDIO_ROOT_PATH}'")
            else:
                raise HTTPException(
                    status_code=400, 
                    detail="Audio root not configured. Please set audio root path first."
                )
        
        print(f"Deleting audio for {request.character_name} in {request.chapter_title}")
        
        success = audio_service.delete_character_audio(
            chapter_title=request.chapter_title,
            character_name=request.character_name
        )
        
        if success:
            print("  [OK] Audio deleted successfully")
            return {"message": "Audio deleted successfully"}
        else:
            raise HTTPException(status_code=500, detail="Failed to delete audio")
        
    except HTTPException as http_exc:
        raise http_exc
    except Exception as e:
        print(f"Error deleting audio: {e}")
        import traceback
        traceback.print_exc()
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/api/delete_audio_line")
async def delete_audio_line(request: DeleteAudioLineRequest):
    """
    Deletes audio file for a single dialogue line.
    """
    global AUDIO_ROOT_PATH, audio_service
    try:
        if not audio_service:
            if BOOK_ROOT_PATH:
                AUDIO_ROOT_PATH = BOOK_ROOT_PATH
                audio_service = AudioGenerationService(AUDIO_ROOT_PATH)
                print(f"Audio root path defaulted to book root: '{AUDIO_ROOT_PATH}'")
            else:
                raise HTTPException(
                    status_code=400, 
                    detail="Audio root not configured. Please set audio root path first."
                )
        
        print(f"Deleting audio for line {request.line_id} - {request.character_name} in {request.chapter_title}")
        
        success = audio_service.delete_audio_line(
            chapter_title=request.chapter_title,
            character_name=request.character_name,
            line_id=request.line_id
        )
        
        if success:
            print("  [OK] Audio line deleted successfully")
            return {"message": "Audio line deleted successfully"}
        else:
            raise HTTPException(status_code=500, detail="Failed to delete audio line")
        
    except HTTPException as http_exc:
        raise http_exc
    except Exception as e:
        print(f"Error deleting audio line: {e}")
        import traceback
        traceback.print_exc()
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/api/audio/{chapter_title}/{character_name}/{line_id}")
async def serve_audio_file(chapter_title: str, character_name: str, line_id: int):
    """
    Serves an audio file from the audio root directory.
    Path format: {audioRoot}/{chapter_title}/{character_name}/{lineId}-{characterName}.mp3
    """
    try:
        if not AUDIO_ROOT_PATH:
            raise HTTPException(
                status_code=400, 
                detail="Audio root not configured. Please set audio root path first."
            )
        
        # Construct the file path (prefer mp3, fallback to wav)
        audio_dir = Path(AUDIO_ROOT_PATH) / chapter_title / "audio_lines" / character_name
        audio_filename = f"{line_id}-{character_name}.mp3"
        audio_path = audio_dir / audio_filename
        if not audio_path.exists():
            audio_filename = f"{line_id}-{character_name}.wav"
            audio_path = audio_dir / audio_filename
        
        # Security check: ensure the path is within the audio root
        try:
            audio_path = audio_path.resolve()
            audio_root_resolved = Path(AUDIO_ROOT_PATH).resolve()
            if not str(audio_path).startswith(str(audio_root_resolved)):
                raise HTTPException(status_code=403, detail="Access denied")
        except Exception as e:
            raise HTTPException(status_code=400, detail="Invalid file path")
        
        # Check if file exists
        if not audio_path.exists():
            raise HTTPException(status_code=404, detail=f"Audio file not found: {audio_filename}")
        
        if not audio_path.is_file():
            raise HTTPException(status_code=400, detail=f"Path is not a file: {audio_filename}")
        
        # Serve the audio file
        # Allow caching for performance - cache-busting is handled on frontend when regenerating
        media_type = "audio/mpeg" if audio_path.suffix.lower() == ".mp3" else "audio/wav"
        return FileResponse(
            path=str(audio_path),
            media_type=media_type,
            filename=audio_filename
        )
        
    except HTTPException as http_exc:
        raise http_exc
    except Exception as e:
        print(f"Error serving audio file: {e}")
        import traceback
        traceback.print_exc()
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/api/scan-voice-manifests")
async def scan_voice_manifests(request: ScanManifestsRequest):
    """
    Scans all manifest.json files in the audio directory and returns voice information.
    Used to auto-discover voices from generated audio clips.
    """
    try:
        audio_root = Path(request.audioRoot)
        
        if not audio_root.exists():
            raise HTTPException(status_code=404, detail=f"Audio root path does not exist: {audio_root}")
        
        if not audio_root.is_dir():
            raise HTTPException(status_code=400, detail=f"Audio root path is not a directory: {audio_root}")
        
        print(f"Scanning for manifest files in: {audio_root}")
        
        # Find all manifest.json files recursively
        manifest_files = list(audio_root.rglob("manifest.json"))
        print(f"Found {len(manifest_files)} manifest files")
        
        manifests = []
        
        for manifest_path in manifest_files:
            try:
                with open(manifest_path, 'r', encoding='utf-8') as f:
                    manifest_data = json.load(f)
                    
                    # Only include manifests with format version 2.0
                    if manifest_data.get('formatVersion') == '2.0':
                        manifests.append(manifest_data)
                        
            except Exception as e:
                print(f"Error reading manifest {manifest_path}: {e}")
                continue
        
        print(f"Successfully loaded {len(manifests)} v2.0 manifests")
        
        return {
            "manifests": manifests,
            "count": len(manifests),
            "audioRoot": str(audio_root)
        }
        
    except HTTPException as http_exc:
        raise http_exc
    except Exception as e:
        print(f"Error scanning manifests: {e}")
        import traceback
        traceback.print_exc()
        raise HTTPException(status_code=500, detail=str(e))


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
