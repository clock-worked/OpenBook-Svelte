import os

from fastapi import APIRouter, HTTPException

from api_models import SetAudioRootRequest, SetRootRequest
from api_runtime_state import ApiRuntimeState


def create_root_config_router(runtime_state: ApiRuntimeState) -> APIRouter:
    router = APIRouter(tags=["root-config"])

    @router.post("/api/set-book-root")
    async def set_book_root(request: SetRootRequest):
        if not os.path.isdir(request.root_path):
            raise HTTPException(status_code=400, detail="Provided path is not a valid directory.")

        normalized_book_root = runtime_state.set_book_root(request.root_path)
        print(f"Book root path set to: '{normalized_book_root}'")

        if not runtime_state.get_audio_root():
            normalized_audio_root = runtime_state.initialize_audio_service(normalized_book_root)
            print(f"Audio root path defaulted to book root: '{normalized_audio_root}'")

        return {"message": "Book root path set successfully.", "path": normalized_book_root}

    @router.post("/api/set-audio-root")
    async def set_audio_root(request: SetAudioRootRequest):
        if not os.path.isdir(request.audio_root):
            raise HTTPException(status_code=400, detail="Provided audio root path is not a valid directory.")

        normalized_audio_root = runtime_state.initialize_audio_service(request.audio_root)
        print(f"Audio root path set to: '{normalized_audio_root}'")

        return {"message": "Audio root path set successfully.", "path": normalized_audio_root}

    return router