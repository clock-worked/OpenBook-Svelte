"""The character audio manifest: one schema, one write path.

A manifest lives at ``<audio_root>/<chapter>/audio_lines/<character>/manifest.json``
and lists the generated clips for that character in that chapter (format
v2.0)::

    {
      "formatVersion": "2.0",
      "characterId": ..., "characterName": ...,
      "metadata": {"totalClips", "chapters", "sources", "voiceIds",
                   "primaryVoiceId", "lastUpdated"},
      "clips": [ <clip>, ... ]          # sorted by (chapter, id)
    }

Every writer goes through this module and keeps only its own *trigger*:

- ``upsert_clips`` / ``upsert_clip`` -- after generation (the single-line
  service path and the VibeVoice chapter pipeline);
- ``remove_clip`` -- after one clip's audio is deleted;
- ``reconcile`` -- align the manifest with the audio files on disk,
  bootstrapping one from orphan files when it is missing or unreadable.

Metadata is always recomputed from the clip list (``recompute_metadata``), so
the counts can never drift from the clips. ``read_manifest`` is tolerant: a
missing, unreadable or non-object file reads as ``None``; an upsert over such
a file starts a fresh manifest (``reconcile`` re-adds any other clips whose
audio is still on disk).

Pure module: no fastapi / soundfile imports, so it is unit-testable anywhere.
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List, Mapping, Optional, Union

logger = logging.getLogger(__name__)

FORMAT_VERSION = "2.0"
MANIFEST_FILENAME = "manifest.json"
AUDIO_LINES_DIRNAME = "audio_lines"
# Clip audio the reconciler recognises, in preference order when one line id
# has more than one file.
CLIP_AUDIO_EXTENSIONS = (".wav", ".mp3")
# Provider recorded on clips bootstrapped from orphan audio files.
BOOTSTRAP_PROVIDER = "vibevoice_local"

PathLike = Union[str, Path]


def utc_timestamp() -> str:
    """``lastUpdated`` / ``generatedAt`` format: ISO-8601 UTC with a ``Z``."""
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


class ManifestPathError(ValueError):
    """A chapter title or character name that would leave the audio root."""


def character_audio_dir(audio_root: PathLike, chapter_title: str, character_name: str) -> Path:
    """``<audio_root>/<chapter>/audio_lines/<character>``, kept inside the root.

    Chapter titles and character names arrive from HTTP requests; one that
    is absolute or climbs out with ``..`` raises :class:`ManifestPathError`
    instead of addressing a file elsewhere on disk.
    """
    root = Path(audio_root)
    character_dir = root / chapter_title / AUDIO_LINES_DIRNAME / character_name
    resolved_root = root.resolve()
    resolved_dir = character_dir.resolve()
    if resolved_root not in resolved_dir.parents:
        raise ManifestPathError(
            f"Chapter {chapter_title!r} / character {character_name!r} is outside the audio root"
        )
    return character_dir


def manifest_path_for(audio_root: PathLike, chapter_title: str, character_name: str) -> Path:
    return character_audio_dir(audio_root, chapter_title, character_name) / MANIFEST_FILENAME


def load(audio_root: PathLike, chapter_title: str, character_name: str) -> Optional[Dict[str, Any]]:
    """The stored manifest for one character in one chapter, or ``None``. Never writes."""
    return read_manifest(manifest_path_for(audio_root, chapter_title, character_name))


def fallback_character_id(character_name: str) -> str:
    """The id recorded when only the character folder name is known."""
    return character_name.lower().replace(" ", "_")


# --- schema ----------------------------------------------------------------


def build_clip(
    *,
    line_id: int,
    character_id: str,
    character_name: str,
    text: str,
    audio_file: str,
    chapter: str,
    source_file: str,
    voice_id: str,
    provider: str,
    duration: float,
    generated_at: Optional[str] = None,
    emotion: Optional[str] = None,
    generator_info: Optional[Mapping[str, Any]] = None,
    timing: Optional[Mapping[str, float]] = None,
) -> Dict[str, Any]:
    """One clip entry. ``generator_info`` is ``build_chatterbox_generator_info``'s
    record (``None`` -> all-null generator fields); ``timing`` adds extra clip
    metadata such as ``startSec`` / ``endSec`` from the chapter pipeline."""
    generator_info = generator_info or {}
    metadata: Dict[str, Any] = {
        "generatedAt": generated_at or utc_timestamp(),
        "duration": duration,
    }
    if timing:
        metadata.update(timing)
    return {
        "id": int(line_id),
        "characterId": character_id,
        "characterName": character_name,
        "text": text,
        "audioFile": audio_file,
        "chapter": chapter,
        "sourceFile": source_file,
        "voiceId": voice_id,
        "provider": provider,
        "variant": generator_info.get("variant"),
        "temperature": generator_info.get("temperature"),
        "cfgWeight": generator_info.get("cfgWeight"),
        "exaggeration": generator_info.get("exaggeration"),
        "emotion": emotion,
        "metadata": metadata,
    }


def new_manifest(
    *,
    character_id: str,
    character_name: str,
    primary_voice_id: Optional[str] = None,
) -> Dict[str, Any]:
    return {
        "formatVersion": FORMAT_VERSION,
        "characterId": character_id,
        "characterName": character_name,
        "metadata": {
            "totalClips": 0,
            "chapters": [],
            "sources": {},
            "voiceIds": {},
            "primaryVoiceId": primary_voice_id,
            "lastUpdated": "",
        },
        "clips": [],
    }


def _clip_sort_key(clip: Mapping[str, Any]):
    clip_id = clip.get("id")
    return (str(clip.get("chapter", "")), clip_id if isinstance(clip_id, int) else 0)


def recompute_metadata(
    manifest: Dict[str, Any],
    *,
    fallback_primary_voice_id: Optional[str] = None,
) -> Dict[str, Any]:
    """Rebuild ``metadata`` counts from ``clips`` (in place; returns metadata).

    ``primaryVoiceId`` keeps its current value while some clip still uses it,
    else becomes the most-used voice, else ``fallback_primary_voice_id``.
    ``lastUpdated`` is left to the caller.
    """
    clips = manifest.get("clips")
    if not isinstance(clips, list):
        clips = []
        manifest["clips"] = clips

    metadata = manifest.get("metadata")
    if not isinstance(metadata, dict):
        metadata = {}
        manifest["metadata"] = metadata

    chapters: set = set()
    source_counts: Dict[str, int] = {}
    voice_counts: Dict[str, int] = {}
    for clip in clips:
        chapter = clip.get("chapter")
        if isinstance(chapter, str) and chapter:
            chapters.add(chapter)
        source_file = clip.get("sourceFile")
        if isinstance(source_file, str) and source_file:
            source_counts[source_file] = source_counts.get(source_file, 0) + 1
        voice_id = clip.get("voiceId")
        if isinstance(voice_id, str) and voice_id:
            voice_counts[voice_id] = voice_counts.get(voice_id, 0) + 1

    existing_primary = metadata.get("primaryVoiceId")
    if isinstance(existing_primary, str) and existing_primary in voice_counts:
        primary = existing_primary
    elif voice_counts:
        primary = max(voice_counts.items(), key=lambda item: item[1])[0]
    else:
        primary = fallback_primary_voice_id

    metadata["totalClips"] = len(clips)
    metadata["chapters"] = sorted(chapters)
    metadata["sources"] = source_counts
    metadata["voiceIds"] = voice_counts
    metadata["primaryVoiceId"] = primary
    metadata.setdefault("lastUpdated", "")
    return metadata


# --- read / write ----------------------------------------------------------


def read_manifest(path: PathLike) -> Optional[Dict[str, Any]]:
    """The manifest at ``path``, or ``None`` if missing, unreadable or not an object."""
    manifest_path = Path(path)
    if not manifest_path.is_file():
        return None
    try:
        with open(manifest_path, "r", encoding="utf-8") as handle:
            manifest = json.load(handle)
    except (OSError, ValueError) as exc:
        logger.warning("Unreadable audio manifest %s: %s", manifest_path, exc)
        return None
    return manifest if isinstance(manifest, dict) else None


def write_manifest(path: PathLike, manifest: Mapping[str, Any]) -> None:
    manifest_path = Path(path)
    manifest_path.parent.mkdir(parents=True, exist_ok=True)
    with open(manifest_path, "w", encoding="utf-8") as handle:
        json.dump(manifest, handle, indent=2, ensure_ascii=False)


# --- writes ----------------------------------------------------------------


def upsert_clips(
    path: PathLike,
    clips: Iterable[Dict[str, Any]],
    *,
    character_id: str,
    character_name: str,
    voice_id: str,
) -> Dict[str, Any]:
    """Insert or replace ``clips`` (by ``id``) and write the manifest.

    Creates the manifest if missing (or unreadable). The header identity is
    set to ``character_id`` / ``character_name``; ``voice_id`` is the primary
    voice when no clip carries one.
    """
    new_clips = list(clips)
    manifest = read_manifest(path) or new_manifest(
        character_id=character_id,
        character_name=character_name,
        primary_voice_id=voice_id,
    )

    existing = manifest.get("clips")
    if not isinstance(existing, list):
        existing = []
    replacing_ids = {int(clip["id"]) for clip in new_clips}
    kept = [
        clip
        for clip in existing
        if isinstance(clip, dict)
        and not (isinstance(clip.get("id"), int) and clip["id"] in replacing_ids)
    ]
    all_clips = kept + new_clips
    all_clips.sort(key=_clip_sort_key)

    manifest["formatVersion"] = FORMAT_VERSION
    manifest["characterId"] = character_id
    manifest["characterName"] = character_name
    manifest["clips"] = all_clips
    metadata = recompute_metadata(manifest, fallback_primary_voice_id=voice_id)
    metadata["lastUpdated"] = utc_timestamp()

    write_manifest(path, manifest)
    return manifest


def upsert_clip(
    path: PathLike,
    clip: Dict[str, Any],
    *,
    voice_id: Optional[str] = None,
) -> Dict[str, Any]:
    """``upsert_clips`` for one clip; identity and voice come from the clip."""
    return upsert_clips(
        path,
        [clip],
        character_id=clip["characterId"],
        character_name=clip["characterName"],
        voice_id=voice_id if voice_id is not None else clip.get("voiceId", ""),
    )


def remove_clip(path: PathLike, line_id: int) -> bool:
    """Drop the clip with ``line_id``; write only if one was removed.

    Returns whether the manifest changed. A missing or unreadable manifest is
    left alone.
    """
    manifest = read_manifest(path)
    if manifest is None:
        return False
    clips = manifest.get("clips")
    if not isinstance(clips, list):
        return False
    kept = [clip for clip in clips if not (isinstance(clip, dict) and clip.get("id") == line_id)]
    if len(kept) == len(clips):
        return False
    manifest["clips"] = kept
    metadata = recompute_metadata(manifest)
    metadata["lastUpdated"] = utc_timestamp()
    write_manifest(path, manifest)
    return True


def discover_clip_audio(character_dir: PathLike) -> Dict[int, str]:
    """``{line_id: file name}`` for ``<line_id>-*.wav|mp3`` files in ``character_dir``.

    When a line id has both, the ``.wav`` wins.
    """
    directory = Path(character_dir)
    found: Dict[int, str] = {}
    candidates = sorted(
        (p for p in directory.iterdir() if p.is_file() and p.suffix.lower() in CLIP_AUDIO_EXTENSIONS),
        key=lambda p: p.name.lower(),
    )
    for audio_file in candidates:
        line_prefix = audio_file.stem.split("-", 1)[0]
        if not line_prefix.isdigit():
            continue
        line_id = int(line_prefix)
        existing = found.get(line_id)
        if existing is None or (
            existing.lower().endswith(".mp3") and audio_file.suffix.lower() == ".wav"
        ):
            found[line_id] = audio_file.name
    return found


def _bootstrap_clip(line_id: int, file_name: str, *, chapter_title: str, character_name: str, generated_at: str) -> Dict[str, Any]:
    return build_clip(
        line_id=line_id,
        character_id=fallback_character_id(character_name),
        character_name=character_name,
        text="",
        audio_file=file_name,
        chapter=chapter_title,
        source_file=chapter_title,
        voice_id="",
        provider=BOOTSTRAP_PROVIDER,
        duration=0.0,
        generated_at=generated_at,
    )


def _reconcile_result(updated: bool, added: int, removed: int, manifest: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    return {
        "updated": updated,
        "added_count": added,
        "removed_count": removed,
        "clip_count": len(manifest["clips"]) if manifest else 0,
        "manifest": manifest,
    }


def reconcile(audio_root: PathLike, chapter_title: str, character_name: str) -> Dict[str, Any]:
    """Align a character manifest with the audio files on disk.

    Drops clips whose audio file is gone, adds a placeholder clip for every
    ``<line_id>-*.wav|mp3`` file the manifest doesn't list, and follows a
    clip's file when only its extension changed. A missing or unreadable
    manifest is bootstrapped from the files alone. Writes only when clips were
    added or removed. Returns ``{updated, added_count, removed_count,
    clip_count, manifest}`` (``manifest`` is ``None`` when there is nothing
    on disk).
    """
    character_dir = character_audio_dir(audio_root, chapter_title, character_name)
    path = character_dir / MANIFEST_FILENAME

    if not character_dir.is_dir():
        return _reconcile_result(False, 0, 0, None)

    discovered = discover_clip_audio(character_dir)
    generated_at = utc_timestamp()
    manifest = read_manifest(path)

    if manifest is None:
        if not discovered:
            return _reconcile_result(False, 0, 0, None)
        manifest = new_manifest(
            character_id=fallback_character_id(character_name),
            character_name=character_name,
        )
        manifest["clips"] = [
            _bootstrap_clip(
                line_id,
                file_name,
                chapter_title=chapter_title,
                character_name=character_name,
                generated_at=generated_at,
            )
            for line_id, file_name in sorted(discovered.items())
        ]
        metadata = recompute_metadata(manifest)
        metadata["lastUpdated"] = generated_at
        write_manifest(path, manifest)
        return _reconcile_result(True, len(manifest["clips"]), 0, manifest)

    clips = manifest.get("clips")
    if not isinstance(clips, list):
        clips = []

    kept_by_id: Dict[int, Dict[str, Any]] = {}
    removed = 0
    for clip in clips:
        line_id = clip.get("id") if isinstance(clip, dict) else None
        audio_file = clip.get("audioFile") if isinstance(clip, dict) else None
        if (
            not isinstance(line_id, int)
            or not isinstance(audio_file, str)
            or not audio_file.strip()
            or not (character_dir / audio_file).is_file()
        ):
            removed += 1
            continue
        kept_by_id[line_id] = clip

    added = 0
    for line_id, file_name in discovered.items():
        existing = kept_by_id.get(line_id)
        if existing is None:
            kept_by_id[line_id] = _bootstrap_clip(
                line_id,
                file_name,
                chapter_title=chapter_title,
                character_name=character_name,
                generated_at=generated_at,
            )
            added += 1
        elif existing.get("audioFile") != file_name:
            existing["audioFile"] = file_name

    manifest["clips"] = [kept_by_id[line_id] for line_id in sorted(kept_by_id)]
    metadata = recompute_metadata(manifest)
    updated = removed > 0 or added > 0
    if updated:
        metadata["lastUpdated"] = utc_timestamp()
        write_manifest(path, manifest)
    return _reconcile_result(updated, added, removed, manifest)
