import os
from typing import Optional

from audio_generation_service import AudioGenerationService


class ApiRuntimeState:
    def __init__(self) -> None:
        self._book_root_path = ""
        self._audio_root_path = ""
        self._audio_service: Optional[AudioGenerationService] = None

    def get_book_root(self) -> str:
        return self._book_root_path

    def get_audio_root(self) -> str:
        return self._audio_root_path

    def get_audio_service(self) -> Optional[AudioGenerationService]:
        return self._audio_service

    def set_book_root(self, root_path: str) -> str:
        self._book_root_path = os.path.normpath(root_path)
        return self._book_root_path

    def set_audio_service(self, root_path: str, service: AudioGenerationService) -> None:
        self._audio_root_path = os.path.normpath(root_path)
        self._audio_service = service

    def initialize_audio_service(self, root_path: str) -> str:
        normalized_root = os.path.normpath(root_path)
        self._audio_root_path = normalized_root
        self._audio_service = AudioGenerationService(normalized_root)
        return normalized_root

    def reset(self) -> None:
        self._book_root_path = ""
        self._audio_root_path = ""
        self._audio_service = None


runtime_state = ApiRuntimeState()