import os
import tempfile
from pathlib import Path
from typing import Callable, List

from fastapi import APIRouter, HTTPException

from api_models import CorefTestRequest, ParseRequest, SaveRequest
from chapter_service import get_chapter_stats, list_chapters as list_chapters_service
from openbook_parser.booknlp_parser_service import BookNLPParserService
from openbook_parser.dialogue_parser_service import DialogueParserService, script_to_dict_list
from update_character_stats import update_character_stats


FORCED_BLOCKED_SPEAKERS = ["he", "she", "as"]


def create_parser_router(get_book_root: Callable[[], str]) -> APIRouter:
    router = APIRouter(tags=["parser"])

    @router.get("/api/list-chapters")
    async def list_chapters():
        try:
            chapters = list_chapters_service(get_book_root())
            stats = get_chapter_stats(chapters)
            chapters_dict = [chapter.to_dict() for chapter in chapters]

            print(f"Found {stats['total']} chapters in '{get_book_root()}' ({stats['parsed']} parsed)")
            return {
                "chapters": chapters_dict,
                "count": stats["total"],
                "stats": stats,
            }
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        except Exception as exc:
            print(f"Error listing chapters: {exc}")
            raise HTTPException(status_code=500, detail=f"Failed to list chapters: {str(exc)}") from exc

    @router.post("/api/save")
    async def save_file(request: SaveRequest):
        try:
            book_root = get_book_root()
            if not book_root:
                raise HTTPException(status_code=400, detail="Book root path not set. Please select a book directory first.")

            full_path = os.path.join(book_root, request.file_path)
            normalized_path = os.path.normpath(full_path)
            print(f"Received save request for relative path: '{request.file_path}'")
            print(f"Full path: '{full_path}'")
            print(f"Normalized path: '{normalized_path}'")

            if ".." in normalized_path:
                raise HTTPException(status_code=400, detail="Invalid file path")

            content_str = request.content.model_dump_json(indent=2)
            dir_name = os.path.dirname(normalized_path)
            if not os.path.exists(dir_name):
                os.makedirs(dir_name)
                print(f"Created directory: {dir_name}")

            with open(normalized_path, "w", encoding="utf-8") as handle:
                handle.write(content_str)

            print(f"Successfully wrote {len(content_str)} bytes to {normalized_path}")

            if "dialogue.json" in request.file_path:
                try:
                    print("Updating character stats after dialogue save...")
                    stats_result = update_character_stats(Path(book_root))
                    if stats_result.get("success"):
                        print(f"  ✓ Updated {stats_result['updatedCharacters']} characters")
                    else:
                        print(f"  ⚠️  Character stats update failed: {stats_result.get('error')}")
                except Exception as stats_err:
                    print(f"  ⚠️  Error updating character stats: {stats_err}")

            return {"message": "File saved successfully."}
        except HTTPException as http_exc:
            raise http_exc
        except Exception as exc:
            print(f"Error saving file '{normalized_path}': {exc}")
            import traceback

            traceback.print_exc()
            raise HTTPException(status_code=500, detail=str(exc)) from exc

    @router.post("/api/update-character-stats")
    async def update_stats():
        try:
            book_root = get_book_root()
            if not book_root:
                raise HTTPException(status_code=400, detail="Book root path not set.")

            print(f"Manual character stats update requested for: {book_root}")
            result = update_character_stats(Path(book_root))

            if result.get("success"):
                return {
                    "message": "Character stats updated successfully",
                    "stats": result,
                }

            raise HTTPException(
                status_code=500,
                detail=f"Failed to update character stats: {result.get('error')}",
            )
        except HTTPException as http_exc:
            raise http_exc
        except Exception as exc:
            print(f"Error updating character stats: {exc}")
            import traceback

            traceback.print_exc()
            raise HTTPException(status_code=500, detail=str(exc)) from exc

    @router.post("/api/parse")
    async def parse_text(request: ParseRequest):
        try:
            source_path = request.filename
            book_root = get_book_root()
            if source_path and not os.path.isabs(source_path) and book_root:
                source_path = os.path.normpath(os.path.join(book_root, source_path))

            with tempfile.NamedTemporaryFile(
                mode="w+",
                delete=False,
                encoding="utf-8",
                newline="",
                suffix=".txt",
            ) as temp_file:
                temp_file.write(request.text)
                temp_file_path = temp_file.name

            parser_service = BookNLPParserService()
            manual_blocklist: List[str] = list({*FORCED_BLOCKED_SPEAKERS, *(request.manual_blocklist or [])})
            script_lines, character_names, parse_meta = parser_service.parse_file(
                temp_file_path,
                options=request.parser_options or {},
                manual_blocklist=manual_blocklist,
                source_path=source_path,
            )

            return {
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
                },
            }
        except Exception as exc:
            return {"error": str(exc)}, 500
        finally:
            if "temp_file_path" in locals() and os.path.exists(temp_file_path):
                os.unlink(temp_file_path)

    @router.post("/api/coref-health")
    async def coref_health(request: CorefTestRequest):
        parser_service = DialogueParserService()
        return parser_service.coref_health(sample_text=request.text)

    return router