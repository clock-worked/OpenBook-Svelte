#!/usr/bin/env python3
"""
FastAPI server for OpenBook parser/audio services.
Composition root that wires routers and shared runtime state.
"""

import os
import sys

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from api_runtime_state import runtime_state
from audio.vibevoice import create_vibevoice_router
from audio_router import create_audio_router
from parser_router import create_parser_router
from root_config_router import create_root_config_router


if sys.platform == "win32":
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    if hasattr(sys.stderr, "reconfigure"):
        sys.stderr.reconfigure(encoding="utf-8")
    os.environ["PYTHONIOENCODING"] = "utf-8"

CURRENT_DIR = os.path.dirname(os.path.abspath(__file__))
if CURRENT_DIR not in sys.path:
    sys.path.insert(0, CURRENT_DIR)


app = FastAPI()

origins = [
    "http://localhost:5173",
    "http://127.0.0.1:5173",
    "http://localhost:4173",
    "http://127.0.0.1:4173",
]

app.add_middleware(
    CORSMiddleware,
    allow_origins=origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


app.include_router(create_root_config_router(runtime_state=runtime_state))
app.include_router(create_parser_router(get_book_root=runtime_state.get_book_root))
app.include_router(
    create_audio_router(
        get_audio_root=runtime_state.get_audio_root,
        get_book_root=runtime_state.get_book_root,
        get_audio_service=runtime_state.get_audio_service,
        set_audio_service=runtime_state.set_audio_service,
    )
)
app.include_router(
    create_vibevoice_router(
        get_audio_root=runtime_state.get_audio_root,
        get_book_root=runtime_state.get_book_root,
        get_audio_service=runtime_state.get_audio_service,
        set_audio_service=runtime_state.set_audio_service,
    )
)


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="0.0.0.0", port=8000)
