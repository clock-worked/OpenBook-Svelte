"""
Audio generation service for OpenBook.
Handles TTS provider integration and manifest management.
"""

import os
import sys
import json
from pathlib import Path
from typing import Optional, Dict, Any, Tuple
import httpx
from datetime import datetime
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


from vibevoice_local_service import VibeVoiceLocalService


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
        voice_sample_root: Optional[str] = None
    ) -> Dict[str, Any]:
        """
        Generate audio for a single dialogue line.
        
        Returns:
            dict with 'success', 'audio_path', and optionally 'error'
        """
        try:
            # Normalize text to handle smart quotes and other special characters
            normalized_text = self._normalize_text(text)
            
            # Construct output paths
            character_dir = self.audio_root / chapter_title / "audio_lines" / character_name
            character_dir.mkdir(parents=True, exist_ok=True)
            
            audio_ext = self._get_audio_extension(provider)
            audio_filename = f"{line_id}-{character_name}{audio_ext}"
            audio_path = character_dir / audio_filename
            
            # Generate audio based on provider
            provider_lower = provider.lower()
            if provider_lower == "elevenlabs":
                success = await self._generate_elevenlabs(normalized_text, voice_id, audio_path)
            elif provider_lower in ["google_tts", "chirp3", "google", "chirp"]:
                # chirp3 is Google's Chirp 3 model
                success = await self._generate_google_tts(normalized_text, voice_id, audio_path)
            elif provider_lower in ["vibevoice_local", "vibevoice-local", "vibevoice"]:
                success, error_details = self._generate_vibevoice_local(
                    normalized_text,
                    voice_id,
                    audio_path,
                    voice_sample_root,
                )
                if not success:
                     return {
                        "success": False,
                        "error": f"VibeVoice generation failed: {error_details}"
                    }
            else:
                return {
                    "success": False,
                    "error": f"Unsupported TTS provider: {provider}"
                }
            
            if not success:
                # Should be covered by specific provider blocks, but fallback:
                return {
                    "success": False,
                    "error": "Failed to generate audio from TTS provider (Unknown error)"
                }
            
            # Get audio duration (approximate - actual duration would require audio library)
            audio_size = audio_path.stat().st_size
            estimated_duration = self._estimate_duration(audio_size)
            
            # Update manifest
            manifest_path = character_dir / "manifest.json"
            self._update_manifest(
                manifest_path=manifest_path,
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
                duration=estimated_duration
            )
            
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
            sample_path, resolution_error = self._resolve_vibevoice_sample_path(
                voice_id=voice_id,
                voice_sample_root=voice_sample_root,
            )
            if not sample_path:
                return False, resolution_error or "Unable to resolve voice sample path."

            if self.vibevoice_local_service is None:
                self.vibevoice_local_service = VibeVoiceLocalService()

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

    def _resolve_vibevoice_sample_path(
        self,
        voice_id: str,
        voice_sample_root: Optional[str],
    ) -> Tuple[Optional[str], Optional[str]]:
        candidate_paths = []

        if os.path.isabs(voice_id):
            candidate_paths.append(voice_id)
        else:
            if voice_sample_root:
                candidate_paths.append(os.path.join(voice_sample_root, voice_id))

            # Auto-discovery fallbacks for common layouts.
            candidate_roots = [
                self.audio_root / "Audio Samples",
                self.audio_root.parent / "Audio Samples",
                self.audio_root.parent.parent / "Audio Samples",
            ]
            for root in candidate_roots:
                candidate_paths.append(str(root / voice_id))

        expanded_candidates = []
        audio_exts = [".wav", ".mp3", ".flac", ".m4a", ".ogg"]
        for path in candidate_paths:
            expanded_candidates.append(path)
            base, ext = os.path.splitext(path)
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

    def _get_audio_extension(self, provider: str) -> str:
        provider_lower = provider.lower()
        if provider_lower in ["vibevoice_local", "vibevoice-local", "vibevoice"]:
            return ".wav"
        return ".mp3"
    
    def _update_manifest(
        self,
        manifest_path: Path,
        line_id: int,
        character_id: str,
        character_name: str,
        text: str,
        audio_file: str,
        chapter: str,
        source_file: str,
        voice_id: str,
        provider: str,
        emotion: Optional[str],
        duration: float
    ):
        """Update or create manifest.json with new clip information."""
        
        # Load existing manifest or create new one
        if manifest_path.exists():
            with open(manifest_path, 'r', encoding='utf-8') as f:
                manifest = json.load(f)
        else:
            manifest = {
                "formatVersion": "2.0",
                "characterId": character_id,
                "characterName": character_name,
                "metadata": {
                    "totalClips": 0,
                    "chapters": [],
                    "sources": {},
                    "voiceIds": {},
                    "primaryVoiceId": voice_id,
                    "lastUpdated": ""
                },
                "clips": []
            }
        
        # Create new clip entry
        new_clip = {
            "id": line_id,
            "characterId": character_id,
            "characterName": character_name,
            "text": text,
            "audioFile": audio_file,
            "chapter": chapter,
            "sourceFile": source_file,
            "voiceId": voice_id,
            "provider": provider,
            "emotion": emotion,
            "metadata": {
                "generatedAt": datetime.utcnow().isoformat() + "Z",
                "duration": round(duration, 2)
            }
        }
        
        # Remove existing clip with same ID if it exists
        manifest["clips"] = [c for c in manifest["clips"] if c["id"] != line_id]
        
        # Add new clip
        manifest["clips"].append(new_clip)
        
        # Update metadata
        manifest["metadata"]["totalClips"] = len(manifest["clips"])
        
        # Update chapters list
        chapters = set(manifest["metadata"]["chapters"])
        chapters.add(chapter)
        manifest["metadata"]["chapters"] = sorted(list(chapters))
        
        # Update sources
        sources = manifest["metadata"]["sources"]
        sources[source_file] = sources.get(source_file, 0) + 1
        
        # Update voice IDs
        voice_ids = manifest["metadata"]["voiceIds"]
        voice_ids[voice_id] = voice_ids.get(voice_id, 0) + 1
        
        manifest["metadata"]["lastUpdated"] = datetime.utcnow().isoformat() + "Z"
        
        # Save manifest
        with open(manifest_path, 'w', encoding='utf-8') as f:
            json.dump(manifest, f, indent=2, ensure_ascii=False)
    
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
            manifest_path = character_dir / "manifest.json"
            if manifest_path.exists():
                with open(manifest_path, 'r', encoding='utf-8') as f:
                    manifest = json.load(f)
                
                # Remove clip from manifest
                original_count = len(manifest["clips"])
                manifest["clips"] = [c for c in manifest["clips"] if c["id"] != line_id]
                
                if len(manifest["clips"]) < original_count:
                    # Update metadata
                    manifest["metadata"]["totalClips"] = len(manifest["clips"])
                    manifest["metadata"]["lastUpdated"] = datetime.utcnow().isoformat() + "Z"
                    
                    # Save updated manifest
                    with open(manifest_path, 'w', encoding='utf-8') as f:
                        json.dump(manifest, f, indent=2, ensure_ascii=False)
                    print(f"Updated manifest, removed clip {line_id}")
            
            return True
            
        except Exception as e:
            print(f"Error deleting audio line: {e}")
            return False
    
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
            manifest_path = character_dir / "manifest.json"
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

