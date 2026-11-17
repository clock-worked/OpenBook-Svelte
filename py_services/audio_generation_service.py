"""
Audio generation service for OpenBook.
Handles TTS provider integration; manifest writes go through
audio/manifest_store.py.
"""

import os
import sys
import asyncio
import threading
from pathlib import Path
from typing import Optional, Dict, Any, Tuple
import httpx
import base64
from google.oauth2 import service_account
from google.auth.transport.requests import Request

# Set UTF-8 encoding for Windows console
if sys.platform == 'win32':
    if hasattr(sys.stdout, 'reconfigure'):
        sys.stdout.reconfigure(encoding='utf-8')
    if hasattr(sys.stderr, 'reconfigure'):
        sys.stderr.reconfigure(encoding='utf-8')
    os.environ['PYTHONIOENCODING'] = 'utf-8'


from chatterbox_local_service import ChatterboxLocalService
from vibevoice_local_service import VibeVoiceLocalService

try:
    from .audio.provider_profile import (
        ADAPTER_CHATTERBOX,
        ADAPTER_ELEVENLABS,
        ADAPTER_GOOGLE_TTS,
        ADAPTER_VIBEVOICE,
        ProviderProfile,
        UnknownProviderError,
        is_chatterbox_provider,
        resolve_provider_profile,
    )
    from .audio.manifest_store import (
        MANIFEST_FILENAME,
        build_clip as build_manifest_clip,
        reconcile as reconcile_manifest,
        remove_clip as remove_manifest_clip,
        upsert_clip as upsert_manifest_clip,
    )
except ImportError:
    from audio.provider_profile import (
        ADAPTER_CHATTERBOX,
        ADAPTER_ELEVENLABS,
        ADAPTER_GOOGLE_TTS,
        ADAPTER_VIBEVOICE,
        ProviderProfile,
        UnknownProviderError,
        is_chatterbox_provider,
        resolve_provider_profile,
    )
    from audio.manifest_store import (
        MANIFEST_FILENAME,
        build_clip as build_manifest_clip,
        reconcile as reconcile_manifest,
        remove_clip as remove_manifest_clip,
        upsert_clip as upsert_manifest_clip,
    )


DEFAULT_CHATTERBOX_VARIANT = "base"


def build_chatterbox_generator_info(
    provider: Optional[str],
    settings: Optional[Dict[str, Any]],
) -> Dict[str, Any]:
    """Build the per-clip manifest record for which generator produced a clip.

    Chatterbox clips record the effective variant plus the explicit sampling
    settings (``None`` when the caller left a knob at its default). Non-Chatterbox
    providers record nulls so every clip entry has a uniform schema.
    """
    if not is_chatterbox_provider(provider):
        return {
            "variant": None,
            "temperature": None,
            "cfgWeight": None,
            "exaggeration": None,
        }
    settings = settings or {}
    variant = str(settings.get("variant") or DEFAULT_CHATTERBOX_VARIANT).lower().strip()
    return {
        "variant": variant,
        "temperature": settings.get("temperature"),
        "cfgWeight": settings.get("cfg_weight"),
        "exaggeration": settings.get("exaggeration"),
    }


class AudioGenerationService:
    """Service for generating audio clips using various TTS providers."""
    
    def __init__(self, audio_root: str):
        """
        Initialize the audio generation service.
        
        Args:
            audio_root: Root directory for storing generated audio files
        """
        self.audio_root = Path(audio_root)
        self.google_credentials = None
        self.google_access_token = None
        self.google_token_expiry = None
        self.vibevoice_local_service: Optional[VibeVoiceLocalService] = None
        self._vibevoice_lock = threading.Lock()
        # One cached worker per Chatterbox variant (model class is fixed at
        # load time). Max two models in GPU memory: "turbo" and "base".
        self._chatterbox_services: Dict[str, ChatterboxLocalService] = {}
        self._chatterbox_lock = threading.Lock()
        
        # Load Google credentials
        self._load_google_credentials()
    
    async def generate_audio_line(
        self,
        line_id: int,
        text: str,
        character_name: str,
        character_id: str,
        voice_id: str,
        provider: str,
        chapter_title: str,
        source_file: str,
        emotion: Optional[str] = None,
        voice_sample_root: Optional[str] = None,
        chatterbox_settings: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        """
        Generate audio for a single dialogue line.
        
        Returns:
            dict with 'success', 'audio_path', and optionally 'error'
        """
        try:
            # Which engine, which extension: one registry lookup.
            try:
                profile = resolve_provider_profile(provider)
            except UnknownProviderError as exc:
                return {
                    "success": False,
                    "error": str(exc)
                }

            # Normalize text to handle smart quotes and other special characters
            normalized_text = self._normalize_text(text)

            # Construct output paths
            character_dir = self.audio_root / chapter_title / "audio_lines" / character_name
            character_dir.mkdir(parents=True, exist_ok=True)

            audio_ext = profile.file_extension
            audio_filename = f"{line_id}-{character_name}{audio_ext}"
            audio_path = character_dir / audio_filename

            # Generate audio with the profile's engine adapter
            line_adapter = self._line_adapter(profile)
            success, error_details = await line_adapter(
                text=normalized_text,
                voice_id=voice_id,
                output_path=audio_path,
                voice_sample_root=voice_sample_root,
                chatterbox_settings=chatterbox_settings,
            )

            if not success:
                if error_details is not None:
                    return {
                        "success": False,
                        "error": f"{profile.display_name} generation failed: {error_details}"
                    }
                return {
                    "success": False,
                    "error": "Failed to generate audio from TTS provider (Unknown error)"
                }
            
            # Get audio duration (approximate - actual duration would require audio library)
            audio_size = audio_path.stat().st_size
            estimated_duration = self._estimate_duration(audio_size)
            
            # Record the clip in the character manifest
            manifest_path = character_dir / MANIFEST_FILENAME
            clip = build_manifest_clip(
                line_id=line_id,
                character_id=character_id,
                character_name=character_name,
                text=text,
                audio_file=audio_filename,
                chapter=chapter_title,
                source_file=source_file,
                voice_id=voice_id,
                provider=provider,
                emotion=emotion,
                duration=round(estimated_duration, 2),
                generator_info=build_chatterbox_generator_info(provider, chatterbox_settings),
            )
            await asyncio.to_thread(upsert_manifest_clip, manifest_path, clip)
            
            return {
                "success": True,
                "audio_path": str(audio_path)
            }
            
        except Exception as e:
            error_msg = str(e).encode('utf-8', errors='replace').decode('utf-8', errors='replace')
            return {
                "success": False,
                "error": error_msg
            }

    # --- Engine adapters for the single-line path -------------------------
    # Each takes the same keyword arguments and returns (success, error).
    # ``error`` is None when the engine gave no details (cloud APIs log
    # theirs); a string (possibly empty) for local engines.
    # Keyed by ProviderProfile.adapter (audio/provider_profile.py).

    def _line_adapter(self, profile: ProviderProfile):
        return getattr(self, self._LINE_ADAPTER_METHODS[profile.adapter])

    async def _line_elevenlabs(self, *, text, voice_id, output_path, voice_sample_root, chatterbox_settings):
        return await self._generate_elevenlabs(text, voice_id, output_path), None

    async def _line_google_tts(self, *, text, voice_id, output_path, voice_sample_root, chatterbox_settings):
        return await self._generate_google_tts(text, voice_id, output_path), None

    async def _line_vibevoice(self, *, text, voice_id, output_path, voice_sample_root, chatterbox_settings):
        return await asyncio.to_thread(
            self._generate_vibevoice_local,
            text,
            voice_id,
            output_path,
            voice_sample_root,
        )

    async def _line_chatterbox(self, *, text, voice_id, output_path, voice_sample_root, chatterbox_settings):
        return await asyncio.to_thread(
            self._generate_chatterbox_local,
            text,
            voice_id,
            output_path,
            voice_sample_root,
            chatterbox_settings,
        )

    _LINE_ADAPTER_METHODS: Dict[str, str] = {
        ADAPTER_ELEVENLABS: "_line_elevenlabs",
        ADAPTER_GOOGLE_TTS: "_line_google_tts",
        ADAPTER_VIBEVOICE: "_line_vibevoice",
        ADAPTER_CHATTERBOX: "_line_chatterbox",
    }

    async def _generate_elevenlabs(
        self,
        text: str,
        voice_id: str,
        output_path: Path
    ) -> bool:
        """Generate audio using ElevenLabs API."""
        # Get API key from environment
        api_key = os.getenv("ELEVENLABS_API_KEY")
        if not api_key:
            print("Warning: ELEVENLABS_API_KEY not set in environment")
            return False
        
        url = f"https://api.elevenlabs.io/v1/text-to-speech/{voice_id}"
        
        headers = {
            "Accept": "audio/mpeg",
            "Content-Type": "application/json",
            "xi-api-key": api_key
        }
        
        data = {
            "text": text,
            "model_id": "eleven_monolingual_v1",
            "voice_settings": {
                "stability": 0.5,
                "similarity_boost": 0.75
            }
        }
        
        async with httpx.AsyncClient(timeout=30.0) as client:
            response = await client.post(url, json=data, headers=headers)
            
            if response.status_code == 200:
                with open(output_path, "wb") as f:
                    f.write(response.content)
                return True
            else:
                error_text = response.text.encode('utf-8', errors='replace').decode('utf-8', errors='replace')
                print(f"ElevenLabs API error: {response.status_code} - {error_text}")
                return False
    
    def _load_google_credentials(self):
        """Load Google service account credentials from the credentials file."""
        try:
            # Look for credentials file in py_services directory
            credentials_path = Path(__file__).parent / "gen-lang-client-0693332105-2c542594902a.json"
            
            if not credentials_path.exists():
                print(f"Warning: Google credentials file not found at {credentials_path}")
                return
            
            self.google_credentials = service_account.Credentials.from_service_account_file(
                str(credentials_path),
                scopes=['https://www.googleapis.com/auth/cloud-platform']
            )
            print("Google TTS credentials loaded successfully")
            
        except Exception as e:
            print(f"Warning: Failed to load Google credentials: {e}")
    
    async def _get_google_access_token(self) -> Optional[str]:
        """Get a valid Google access token, refreshing if necessary."""
        if not self.google_credentials:
            return None
        
        # Check if token is still valid
        if self.google_access_token and self.google_token_expiry:
            import datetime
            if datetime.datetime.now() < self.google_token_expiry:
                return self.google_access_token
        
        # Refresh the token
        try:
            self.google_credentials.refresh(Request())
            self.google_access_token = self.google_credentials.token
            self.google_token_expiry = self.google_credentials.expiry
            return self.google_access_token
        except Exception as e:
            print(f"Error refreshing Google access token: {e}")
            return None
    
    async def _generate_google_tts(
        self,
        text: str,
        voice_name: str,
        output_path: Path
    ) -> bool:
        """
        Generate audio using Google Cloud TTS API.
        voice_name should be in format: accent-VoiceName (e.g., en_US-Charon)
        """
        if not self.google_credentials:
            print("Error: Google credentials not loaded")
            return False
        
        try:
            # Get access token
            token = await self._get_google_access_token()
            if not token:
                print("Error: Could not get Google access token")
                return False
            
            # Parse voice name - handle multiple formats:
            # 1. "en-GB-Chirp3-HD-Enceladus" (already formatted for Google API)
            # 2. "Chirp3_VoiceName_accent" (e.g., "Chirp3_Charon_en_US")
            # 3. "accent-VoiceName" (e.g., "en_US-Charon")
            # 4. Just "VoiceName" (default to en-US)
            
            # Check if it's already in Google's format (contains language code + Chirp3)
            if voice_name.startswith(('en-', 'es-', 'fr-', 'de-', 'it-', 'pt-', 'ja-', 'ko-', 'zh-')) and 'Chirp3' in voice_name:
                # Already formatted correctly for Google API
                full_voice_name = voice_name
                # Extract accent for the request
                accent = voice_name.split('-')[0] + '-' + voice_name.split('-')[1]  # e.g., en-GB
            elif voice_name.startswith('Chirp3_'):
                # Format: Chirp3_VoiceName_accent (e.g., Chirp3_Charon_en_US)
                parts = voice_name.split('_')
                if len(parts) >= 3:
                    voice_base = parts[1]
                    # Last two parts are accent (e.g., en_US)
                    accent = '_'.join(parts[2:]).replace('_', '-')
                    full_voice_name = f"{accent}-Chirp3-{voice_base}"
                else:
                    # Fallback
                    full_voice_name = voice_name
                    accent = 'en-US'
            elif '-' in voice_name and 'Chirp3' not in voice_name:
                # Format: accent-VoiceName (legacy format without Chirp3)
                parts = voice_name.split('-', 1)
                accent = parts[0].replace('_', '-')  # en_US -> en-US
                voice_base = parts[1]
                full_voice_name = f"{accent}-{voice_base}"
            elif '_' in voice_name:
                # Format: accent_VoiceName
                parts = voice_name.split('_', 1)
                accent = parts[0].replace('_', '-')
                voice_base = parts[1]
                full_voice_name = f"{accent}-{voice_base}"
            else:
                # Default to en-US if no accent specified
                accent = 'en-US'
                full_voice_name = f"{accent}-{voice_name}"
            
            print(f"  Input voice_name: {voice_name}")
            print(f"  Using Google TTS voice: {full_voice_name} (language: {accent})")
            
            # Prepare request body
            request_body = {
                "input": {"text": text},
                "voice": {
                    "languageCode": accent,
                    "name": full_voice_name
                },
                "audioConfig": {
                    "audioEncoding": "MP3",
                    "sampleRateHertz": 24000
                }
            }
            
            # Make API request
            url = "https://texttospeech.googleapis.com/v1beta1/text:synthesize"
            headers = {
                "Authorization": f"Bearer {token}",
                "Content-Type": "application/json"
            }
            
            async with httpx.AsyncClient(timeout=30.0) as client:
                response = await client.post(url, json=request_body, headers=headers)
                
                if response.status_code == 200:
                    data = response.json()
                    audio_content = data.get('audioContent')
                    
                    if audio_content:
                        # Decode base64 audio content
                        audio_bytes = base64.b64decode(audio_content)
                        
                        # Write to file
                        with open(output_path, "wb") as f:
                            f.write(audio_bytes)
                        
                        print("  [OK] Google TTS audio generated successfully")
                        return True
                    else:
                        print("Error: No audio content in Google TTS response")
                        print(f"Response data: {data}")
                        return False
                else:
                    error_text = response.text.encode('utf-8', errors='replace').decode('utf-8', errors='replace')
                    print(f"Google TTS API error: {response.status_code}")
                    print(f"Error details: {error_text}")
                    print(f"Request body was: {request_body}")
                    return False
                    
        except Exception as e:
            error_msg = str(e).encode('utf-8', errors='replace').decode('utf-8', errors='replace')
            print(f"Error generating Google TTS audio: {error_msg}")
            import traceback
            try:
                traceback.print_exc()
            except UnicodeEncodeError:
                print("Error details could not be printed due to encoding issue")
            return False

    def _generate_vibevoice_local(
        self,
        text: str,
        voice_id: str,
        output_path: Path,
        voice_sample_root: Optional[str]
    ) -> Tuple[bool, Optional[str]]:
        try:
            sample_path, resolution_error = self._resolve_local_voice_sample_path(
                voice_id=voice_id,
                voice_sample_root=voice_sample_root,
            )
            if not sample_path:
                return False, resolution_error or "Unable to resolve voice sample path."

            if self.vibevoice_local_service is None:
                self.vibevoice_local_service = VibeVoiceLocalService()

            with self._vibevoice_lock:
                self.vibevoice_local_service.generate_audio(
                    text=text,
                    sample_path=sample_path,
                    output_path=str(output_path),
                )
            return True, None
        except Exception as exc:
            error_msg = str(exc).encode('utf-8', errors='replace').decode('utf-8', errors='replace')
            print(f"Error generating VibeVoice audio: {error_msg}")
            import traceback
            traceback.print_exc()
            return False, error_msg

    def generate_vibevoice_text_to_path(
        self,
        *,
        text: str,
        voice_id: str,
        output_path: Path,
        voice_sample_root: Optional[str],
    ) -> Tuple[bool, Optional[str]]:
        return self._generate_vibevoice_local(
            text=text,
            voice_id=voice_id,
            output_path=output_path,
            voice_sample_root=voice_sample_root,
        )

    def _get_chatterbox_service(self, variant: Optional[str]) -> ChatterboxLocalService:
        """Return the cached worker for a Chatterbox variant, loading it if needed.

        Must be called with ``self._chatterbox_lock`` held.
        """
        normalized_variant = str(variant or DEFAULT_CHATTERBOX_VARIANT).lower().strip()
        service = self._chatterbox_services.get(normalized_variant)
        if service is None:
            service = ChatterboxLocalService(variant=normalized_variant)
            self._chatterbox_services[normalized_variant] = service
        return service

    def _generate_chatterbox_local(
        self,
        text: str,
        voice_id: str,
        output_path: Path,
        voice_sample_root: Optional[str],
        settings: Optional[Dict[str, Any]] = None,
    ) -> Tuple[bool, Optional[str]]:
        try:
            sample_path, resolution_error = self._resolve_local_voice_sample_path(
                voice_id=voice_id,
                voice_sample_root=voice_sample_root,
            )
            if not sample_path:
                return False, resolution_error or "Unable to resolve voice sample path."

            settings = settings or {}
            variant = str(settings.get("variant") or DEFAULT_CHATTERBOX_VARIANT).lower().strip()

            with self._chatterbox_lock:
                service = self._get_chatterbox_service(variant)
                service.generate_audio(
                    text=text,
                    sample_path=sample_path,
                    output_path=str(output_path),
                    temperature=settings.get("temperature"),
                    cfg_weight=settings.get("cfg_weight"),
                    exaggeration=settings.get("exaggeration"),
                )
            return True, None
        except Exception as exc:
            error_msg = str(exc).encode('utf-8', errors='replace').decode('utf-8', errors='replace')
            print(f"Error generating Chatterbox audio: {error_msg}")
            import traceback
            traceback.print_exc()
            return False, error_msg

    def generate_chatterbox_text_to_path(
        self,
        *,
        text: str,
        voice_id: str,
        output_path: Path,
        voice_sample_root: Optional[str],
        settings: Optional[Dict[str, Any]] = None,
    ) -> Tuple[bool, Optional[str]]:
        return self._generate_chatterbox_local(
            text=text,
            voice_id=voice_id,
            output_path=output_path,
            voice_sample_root=voice_sample_root,
            settings=settings,
        )

    def _resolve_local_voice_sample_path(
        self,
        voice_id: str,
        voice_sample_root: Optional[str],
    ) -> Tuple[Optional[str], Optional[str]]:
        candidate_paths = []

        def _append_candidate(path_value: str) -> None:
            if not path_value:
                return
            candidate_paths.append(path_value)

            # Support Git Bash style absolute paths on Windows (e.g. /c/Users/...)
            if os.name == "nt" and len(path_value) > 3 and path_value[0] == "/" and path_value[1].isalpha() and path_value[2] == "/":
                drive_style = f"{path_value[1]}:{path_value[2:]}"
                candidate_paths.append(drive_style)

        if os.path.isabs(voice_id):
            _append_candidate(voice_id)
        else:
            if voice_sample_root:
                _append_candidate(os.path.join(voice_sample_root, voice_id))

                # If caller provides Audio Samples root, also try its common previews subdir.
                _append_candidate(os.path.join(voice_sample_root, "voice-previews", voice_id))

            # Auto-discovery fallbacks for common layouts.
            candidate_roots = [
                self.audio_root / "Audio Samples",
                self.audio_root.parent / "Audio Samples",
                self.audio_root.parent.parent / "Audio Samples",
            ]
            for root in candidate_roots:
                _append_candidate(str(root / voice_id))
                _append_candidate(str(root / "voice-previews" / voice_id))

        expanded_candidates = []
        audio_exts = [".wav", ".mp3", ".flac", ".m4a", ".ogg"]
        for path in candidate_paths:
            expanded_candidates.append(path)
            _, ext = os.path.splitext(path)
            if not ext:
                for audio_ext in audio_exts:
                    expanded_candidates.append(path + audio_ext)

        seen = set()
        deduped = []
        for path in expanded_candidates:
            norm = os.path.normpath(path)
            if norm in seen:
                continue
            seen.add(norm)
            deduped.append(norm)

        for path in deduped:
            if os.path.exists(path):
                return path, None

        searched = "\n - " + "\n - ".join(deduped) if deduped else " (no candidate paths)"
        return None, (
            "Voice sample file could not be found for voice_id "
            f"'{voice_id}'. Searched paths:{searched}"
        )
    
    def _estimate_duration(self, file_size_bytes: int) -> float:
        """
        Estimate audio duration based on file size.
        Assumes MP3 at ~128kbps = ~16KB/s
        """
        bytes_per_second = 16000
        return file_size_bytes / bytes_per_second

    def delete_audio_line(
        self,
        chapter_title: str,
        character_name: str,
        line_id: int
    ) -> bool:
        """Delete audio file for a single line."""
        try:
            character_dir = self.audio_root / chapter_title / "audio_lines" / character_name
            
            if not character_dir.exists():
                return True  # Already deleted
            
            # Delete known audio files
            for ext in (".mp3", ".wav"):
                audio_filename = f"{line_id}-{character_name}{ext}"
                audio_path = character_dir / audio_filename
                if audio_path.exists():
                    audio_path.unlink()
                    print(f"Deleted audio file: {audio_path}")
            
            # Update manifest to remove this clip
            manifest_path = character_dir / MANIFEST_FILENAME
            if remove_manifest_clip(manifest_path, line_id):
                print(f"Updated manifest, removed clip {line_id}")
            
            return True
            
        except Exception as e:
            print(f"Error deleting audio line: {e}")
            return False

    def reconcile_character_manifest(
        self,
        chapter_title: str,
        character_name: str,
    ) -> Dict[str, Any]:
        """Reconcile a character manifest against files on disk.

        If the manifest is missing but audio files exist, bootstrap a minimal
        manifest from those files so the UI can reflect generated audio.
        """
        return reconcile_manifest(self.audio_root, chapter_title, character_name)
    
    def delete_character_audio(
        self,
        chapter_title: str,
        character_name: str
    ) -> bool:
        """Delete all audio files for a character in a specific chapter."""
        try:
            character_dir = self.audio_root / chapter_title / "audio_lines" / character_name
            
            if not character_dir.exists():
                return True  # Already deleted
            
            # Delete all audio files
            for audio_file in character_dir.glob("*.mp3"):
                audio_file.unlink()
            for audio_file in character_dir.glob("*.wav"):
                audio_file.unlink()
            
            # Delete manifest
            manifest_path = character_dir / MANIFEST_FILENAME
            if manifest_path.exists():
                manifest_path.unlink()
            
            # Remove directory if empty
            if not any(character_dir.iterdir()):
                character_dir.rmdir()
            
            return True
            
        except Exception as e:
            print(f"Error deleting character audio: {e}")
            return False

    def _normalize_text(self, text: str) -> str:
        """
        Normalize text by replacing smart/curly quotes with straight quotes
        and handling other problematic Unicode characters.
        """
        # Replace smart quotes with straight quotes
        text = text.replace('’', "'")  # Right single quotation mark (U+2019)
        text = text.replace('‘', "'")  # Left single quotation mark (U+2018)
        text = text.replace('”', '"')  # Right double quotation mark (U+201D)
        text = text.replace('“', '"')  # Left double quotation mark (U+201C)
        text = text.replace('–', '-')  # En dash (U+2013)
        text = text.replace('—', '-')  # Em dash (U+2014)
        text = text.replace('…', '...')  # Ellipsis (U+2026)
        
        # Handle replacement characters (U+FFFD) by converting to regular apostrophe if context suggests it
        # This handles cases where curly quotes were already corrupted
        if '\ufffd' in text:
            text = text.replace('\ufffd', "'")
        
        return text

