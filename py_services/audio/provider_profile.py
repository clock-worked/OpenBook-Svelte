"""Provider profile registry: the one BE answer to "which engine, which endpoint".

A TTS *provider string* (what the FE sends as ``provider`` and what manifests
record) resolves to exactly one :class:`ProviderProfile`::

    canonical name + aliases -> { engine, endpoint, file extension, adapter }

- ``engine`` -- :data:`ENGINE_LOCAL_CHUNKED` (a local model behind the chunked
  ``/api/vibevoice/*`` pipeline: VibeVoice, Chatterbox) or
  :data:`ENGINE_CLOUD` (a remote API behind ``/api/generate_audio_line``).
- ``endpoint`` -- the single-line generation route for that engine kind.
- ``file_extension`` -- what the generated clip is written as.
- ``adapter`` -- the engine adapter name the audio service dispatches to
  (one of :data:`ADAPTERS`). Candidate 6 hangs its ``AudioEngine`` adapters
  off these names.

Lookup is normalized (``strip().lower()``) and *exact*: an unknown provider
raises :class:`UnknownProviderError` -- there is no silent fall-through to a
default engine or extension.

The FE mirror is ``apps/desktop/src/lib/services/audioGenerator.ts``
(``classifyProvider``). The two sides share no code; parity is pinned by
``tests/provider_profile_vectors.json`` (py: ``test_provider_profile_parity.py``;
TS: ``__tests__/providerProfile.test.ts``). Adding a provider = one entry
here, one entry in the FE map, one vector.

Pure module: no fastapi / model imports, safe to import from tests.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, Optional, Tuple

ENGINE_LOCAL_CHUNKED = "local_chunked"
ENGINE_CLOUD = "cloud"
ENGINE_KINDS = (ENGINE_LOCAL_CHUNKED, ENGINE_CLOUD)

# Single-line generation route per engine kind. Both routes call the same
# AudioGenerationService.generate_audio_line; they differ in response shape
# (consolidating them is a deliberate non-goal of candidate 3).
ENDPOINT_BY_ENGINE: Dict[str, str] = {
    ENGINE_LOCAL_CHUNKED: "/api/vibevoice/line",
    ENGINE_CLOUD: "/api/generate_audio_line",
}

ADAPTER_VIBEVOICE = "vibevoice"
ADAPTER_CHATTERBOX = "chatterbox"
ADAPTER_GOOGLE_TTS = "google_tts"
ADAPTER_ELEVENLABS = "elevenlabs"
ADAPTERS = (ADAPTER_VIBEVOICE, ADAPTER_CHATTERBOX, ADAPTER_GOOGLE_TTS, ADAPTER_ELEVENLABS)


class UnknownProviderError(ValueError):
    """Raised for a provider string with no profile. ``str(exc)`` is the
    user-facing message ("Unsupported TTS provider: <provider>")."""

    def __init__(self, provider: Optional[str]) -> None:
        self.provider = provider
        super().__init__(f"Unsupported TTS provider: {provider}")


@dataclass(frozen=True)
class ProviderProfile:
    name: str
    """Canonical provider string (what the FE sends by default)."""
    aliases: Tuple[str, ...]
    """Other accepted spellings (normalized: lowercase, stripped)."""
    engine: str
    """:data:`ENGINE_LOCAL_CHUNKED` or :data:`ENGINE_CLOUD`."""
    file_extension: str
    """Extension of the generated clip, with the dot (``.wav`` / ``.mp3``)."""
    adapter: str
    """Engine adapter the audio service dispatches to (one of :data:`ADAPTERS`)."""
    display_name: str
    """Human name used in error messages ("VibeVoice generation failed: ...")."""

    @property
    def endpoint(self) -> str:
        return ENDPOINT_BY_ENGINE[self.engine]

    @property
    def is_local_chunked(self) -> bool:
        """True when the provider runs on the local chunked pipeline."""
        return self.engine == ENGINE_LOCAL_CHUNKED


PROVIDER_PROFILES: Tuple[ProviderProfile, ...] = (
    ProviderProfile(
        name="vibevoice_local",
        aliases=("vibevoice-local", "vibevoice"),
        engine=ENGINE_LOCAL_CHUNKED,
        file_extension=".wav",
        adapter=ADAPTER_VIBEVOICE,
        display_name="VibeVoice",
    ),
    ProviderProfile(
        name="chatterbox",
        aliases=("chatterbox_local", "chatterbox-local"),
        engine=ENGINE_LOCAL_CHUNKED,
        file_extension=".wav",
        adapter=ADAPTER_CHATTERBOX,
        display_name="Chatterbox",
    ),
    ProviderProfile(
        name="google_tts",
        # chirp3 is Google's Chirp 3 model
        aliases=("chirp3", "google", "chirp"),
        engine=ENGINE_CLOUD,
        file_extension=".mp3",
        adapter=ADAPTER_GOOGLE_TTS,
        display_name="Google TTS",
    ),
    ProviderProfile(
        name="elevenlabs",
        aliases=(),
        engine=ENGINE_CLOUD,
        file_extension=".mp3",
        adapter=ADAPTER_ELEVENLABS,
        display_name="ElevenLabs",
    ),
)


def _build_index(profiles: Tuple[ProviderProfile, ...]) -> Dict[str, ProviderProfile]:
    index: Dict[str, ProviderProfile] = {}
    for profile in profiles:
        if profile.engine not in ENGINE_KINDS:
            raise ValueError(f"Profile {profile.name!r}: unknown engine {profile.engine!r}")
        if profile.adapter not in ADAPTERS:
            raise ValueError(f"Profile {profile.name!r}: unknown adapter {profile.adapter!r}")
        for key in (profile.name, *profile.aliases):
            normalized = normalize_provider(key)
            if normalized != key:
                raise ValueError(f"Profile {profile.name!r}: key {key!r} is not normalized")
            if normalized in index:
                raise ValueError(f"Provider key {normalized!r} is registered twice")
            index[normalized] = profile
    return index


def normalize_provider(provider: Optional[str]) -> str:
    """Lookup key for a provider string: stripped and lowercased."""
    return str(provider or "").strip().lower()


_PROFILE_INDEX: Dict[str, ProviderProfile] = _build_index(PROVIDER_PROFILES)


def find_provider_profile(provider: Optional[str]) -> Optional[ProviderProfile]:
    """The profile for ``provider`` (name or alias), or ``None`` if unknown."""
    return _PROFILE_INDEX.get(normalize_provider(provider))


def resolve_provider_profile(provider: Optional[str]) -> ProviderProfile:
    """The profile for ``provider`` (name or alias).

    Raises :class:`UnknownProviderError` for an unknown provider.
    """
    profile = find_provider_profile(provider)
    if profile is None:
        raise UnknownProviderError(provider)
    return profile


def is_local_chunked_provider(provider: Optional[str]) -> bool:
    """True for a known provider on the local chunked pipeline (VibeVoice,
    Chatterbox); False for cloud *and* unknown providers."""
    profile = find_provider_profile(provider)
    return profile is not None and profile.is_local_chunked


def is_chatterbox_provider(provider: Optional[str]) -> bool:
    """True when ``provider`` routes to the Chatterbox adapter."""
    profile = find_provider_profile(provider)
    return profile is not None and profile.adapter == ADAPTER_CHATTERBOX


def known_provider_keys() -> Tuple[str, ...]:
    """Every accepted provider string (names + aliases), sorted."""
    return tuple(sorted(_PROFILE_INDEX))


__all__ = [
    "ADAPTERS",
    "ADAPTER_CHATTERBOX",
    "ADAPTER_ELEVENLABS",
    "ADAPTER_GOOGLE_TTS",
    "ADAPTER_VIBEVOICE",
    "ENDPOINT_BY_ENGINE",
    "ENGINE_CLOUD",
    "ENGINE_KINDS",
    "ENGINE_LOCAL_CHUNKED",
    "PROVIDER_PROFILES",
    "ProviderProfile",
    "UnknownProviderError",
    "find_provider_profile",
    "is_chatterbox_provider",
    "is_local_chunked_provider",
    "known_provider_keys",
    "normalize_provider",
    "resolve_provider_profile",
]
