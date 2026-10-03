import asyncio
import os
import tempfile
from pathlib import Path
from typing import Callable, Dict, List

from fastapi import APIRouter, HTTPException

from api_models import (
    CorefTestRequest,
    DialogueAiAssistRequest,
    LocalDialogueAiRequest,
    ParseRequest,
    RelativeFileRequest,
    SaveRequest,
)
from chapter_service import get_chapter_stats, list_chapters as list_chapters_service
from dialogue_ai_service import DialogueAiAssistService
from local_dialogue_ai_service import LocalDialogueAiService
from jev_client import JevClient
from jev_service import JevService
from openbook_parser.booknlp_parser_service import BookNLPParserService
from openbook_parser.dialogue_parser_service import (
    DialogueLine,
    DialogueParserService,
    script_to_dict_list,
)
from openbook_parser.jev_verify_service import (
    JevVerifyCache,
    VerifyPolicy,
    load_character_lookup,
    verify_chapter,
)
from update_character_stats import update_character_stats


FORCED_BLOCKED_SPEAKERS = ["he", "she", "as"]


def create_parser_router(get_book_root: Callable[[], str]) -> APIRouter:
    router = APIRouter(tags=["parser"])
    dialogue_ai_service = DialogueAiAssistService(get_book_root=get_book_root)
    local_dialogue_ai_service = LocalDialogueAiService(get_book_root=get_book_root)
    jev_service = JevService(get_book_root=get_book_root)

    def run_jev_verify(request: ParseRequest, script_lines: List[DialogueLine]) -> dict:
        """JEV cross-verification stage over a freshly parsed chapter.

        On by default when VERCEL_JEV_API_KEY is present (the .env is
        already loaded at router-creation time). The live policy always
        keeps downgrade_only and noul_gate on: the live path never
        promotes a suggestion to an assert. Fail-open: any problem here
        must be handled by the caller, never fail the parse.
        """
        jev_options = (request.parser_options or {}).get("jev_verify")
        if not isinstance(jev_options, dict):
            jev_options = {}
        api_key = os.getenv("VERCEL_JEV_API_KEY")
        if not api_key:
            return {"enabled": False, "skipped": "no_api_key"}
        if jev_options.get("enabled") is False:
            return {"enabled": False, "skipped": "disabled"}

        policy = VerifyPolicy(downgrade_only=True, noul_gate=True)
        if jev_options.get("gate") in ("carryover", "full"):
            policy.gate = str(jev_options["gate"])
        for field in ("downgrade_conf", "promote_conf", "budget_seconds"):
            value = jev_options.get(field)
            if isinstance(value, (int, float)) and not isinstance(value, bool):
                setattr(policy, field, float(value))

        book_root = get_book_root()
        name_lookup: Dict[str, str] = {"narrator": "narrator"}
        if book_root:
            try:
                name_lookup = load_character_lookup(book_root)
            except Exception:  # noqa: BLE001 - a bad roster must not fail the parse
                pass

        client = JevClient(api_key=api_key)
        summary = verify_chapter(
            script_lines,
            request.text,
            name_lookup,
            policy=policy,
            client=client,
            cache=JevVerifyCache(),
            log=lambda message: print(message),
        )
        summary["enabled"] = True
        return summary

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

    @router.post("/api/read-text")
    async def read_text_file(request: RelativeFileRequest):
        book_root = Path(get_book_root()).resolve()
        requested_path = (book_root / request.file_path).resolve()
        if requested_path != book_root and book_root not in requested_path.parents:
            raise HTTPException(status_code=400, detail="Invalid file path")
        if not requested_path.is_file():
            raise HTTPException(status_code=404, detail=f"File not found: {request.file_path}")
        try:
            raw_bytes = requested_path.read_bytes()
            try:
                content = raw_bytes.decode("utf-8")
            except UnicodeDecodeError:
                content = raw_bytes.decode("cp1252", errors="replace")
            return {"content": content}
        except OSError as exc:
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

            # JEV cross-verification (offline experiment, now live). Runs in a
            # worker thread (the router's established pattern for long work)
            # and fails open: the parse result is returned either way.
            try:
                jev_verify_meta = await asyncio.to_thread(run_jev_verify, request, script_lines)
            except Exception as exc:  # noqa: BLE001 - verify must never fail the parse
                print(f"JEV verify stage failed: {exc}")
                jev_verify_meta = {"enabled": True, "error": str(exc)}

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
                    "jevVerify": jev_verify_meta,
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
        coref_health_fn = getattr(parser_service, "coref_health", None)
        if not callable(coref_health_fn):
            raise HTTPException(status_code=501, detail="Coreference health check is unavailable.")
        return coref_health_fn(sample_text=request.text)

    @router.post("/api/dialogue-ai-assist")
    async def dialogue_ai_assist(request: DialogueAiAssistRequest):
        try:
            dialogue_ai_service.prepare_status(request)
            response = await asyncio.to_thread(dialogue_ai_service.run, request)
            return response.model_dump()
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @router.get("/api/dialogue-ai-assist-status/{request_id}")
    async def dialogue_ai_assist_status(request_id: str):
        status = dialogue_ai_service.get_status(request_id)
        if status is None:
            raise HTTPException(status_code=404, detail="Dialogue AI assist request not found.")
        return status.model_dump()

    @router.post("/api/local-dialogue-ai")
    async def local_dialogue_ai(request: LocalDialogueAiRequest):
        try:
            local_dialogue_ai_service.prepare_status(request)
            response = await asyncio.to_thread(local_dialogue_ai_service.run, request)
            return response.model_dump()
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @router.get("/api/local-dialogue-ai-status/{request_id}")
    async def local_dialogue_ai_status(request_id: str):
        status = local_dialogue_ai_service.get_status(request_id)
        if status is None:
            raise HTTPException(status_code=404, detail="Local dialogue AI request not found.")
        return status.model_dump()

    @router.post("/api/jev-dialogue-ai")
    async def jev_dialogue_ai(request: LocalDialogueAiRequest):
        try:
            jev_service.prepare_status(request)
            response = await asyncio.to_thread(jev_service.run, request)
            return response.model_dump()
        except (ValueError, ConnectionError) as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @router.get("/api/jev-dialogue-ai-status/{request_id}")
    async def jev_dialogue_ai_status(request_id: str):
        status = jev_service.get_status(request_id)
        if status is None:
            raise HTTPException(status_code=404, detail="Jev dialogue AI request not found.")
        return status.model_dump()

    return router
