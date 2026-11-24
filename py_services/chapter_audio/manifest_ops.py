from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from .text_utils import (
    normalize_chapter_text,
    safe_name,
    to_float_or_none,
    utc_now_iso,
)


def load_manifest_lines(manifest_path: Path) -> list[dict[str, Any]]:
    payload = json.loads(manifest_path.read_text(encoding="utf-8"))
    lines = payload.get("lines")
    if not isinstance(lines, list):
        raise ValueError(f"Invalid manifest format in {manifest_path}: missing lines[]")

    output: list[dict[str, Any]] = []
    for entry in lines:
        if not isinstance(entry, dict):
            continue
        line_id = entry.get("id")
        text = str(entry.get("text", "")).strip()
        if not isinstance(line_id, int) or not text:
            continue

        output_hint = str(entry.get("output", "")).strip()
        character_folder = None
        if "/" in output_hint:
            character_folder = output_hint.split("/", 1)[0].strip()
        character_id = str(entry.get("characterId", "unknown")).strip() or "unknown"
        character_folder = character_folder or character_id.title()

        output.append(
            {
                "id": line_id,
                "characterId": character_id,
                "characterFolder": character_folder,
                "text": text,
                "sourceOutput": output_hint,
            }
        )

    if not output:
        raise ValueError(f"No usable lines found in manifest: {manifest_path}")

    return output


def load_existing_split_state(
    *,
    result_manifest_path: Path,
    expected_lines: list[dict[str, Any]],
    output_dir: Path,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], dict[str, Any], int]:
    if not result_manifest_path.exists():
        raise FileNotFoundError(
            "Readjust mode requires an existing split result manifest: "
            f"{result_manifest_path}"
        )

    payload = json.loads(result_manifest_path.read_text(encoding="utf-8"))
    raw_lines = payload.get("lines")
    if not isinstance(raw_lines, list):
        raise ValueError(
            "Existing split result manifest is missing lines[]: "
            f"{result_manifest_path}"
        )

    by_line_id: dict[int, dict[str, Any]] = {}
    for entry in raw_lines:
        if not isinstance(entry, dict):
            continue
        line_id = entry.get("id")
        if isinstance(line_id, int):
            by_line_id[line_id] = entry

    line_timestamps: list[dict[str, Any]] = []
    split_lines: list[dict[str, Any]] = []
    missing_ids: list[int] = []
    text_mismatch_count = 0

    for expected in expected_lines:
        line_id = int(expected["id"])
        stored = by_line_id.get(line_id)
        if not isinstance(stored, dict):
            missing_ids.append(line_id)
            continue

        start_sec = to_float_or_none(stored.get("startSec"))
        end_sec = to_float_or_none(stored.get("endSec"))
        if start_sec is None or end_sec is None:
            raise ValueError(
                "Existing split result manifest has invalid start/end timestamps "
                f"for line {line_id}: {result_manifest_path}"
            )

        start_sec = round(max(0.0, float(start_sec)), 4)
        end_sec = round(max(start_sec, float(end_sec)), 4)

        expected_text = normalize_chapter_text(str(expected.get("text", "")))
        stored_text = normalize_chapter_text(str(stored.get("text", "")))
        if stored_text and expected_text and stored_text != expected_text:
            text_mismatch_count += 1

        character_folder = str(
            stored.get("characterFolder") or expected.get("characterFolder") or "unknown"
        )
        safe_folder = safe_name(character_folder)

        audio_file = str(stored.get("audioFile", "")).strip()
        audio_path_str = str(stored.get("audioPath", "")).strip()

        if audio_path_str:
            audio_path = Path(audio_path_str)
            if not audio_path.is_absolute():
                audio_path = (output_dir / audio_path).resolve()
        else:
            if not audio_file:
                audio_file = f"{line_id}-{safe_folder}.wav"
            audio_path = (output_dir / "splits" / safe_folder / audio_file).resolve()

        if not audio_file:
            audio_file = audio_path.name

        timestamp_line = {
            **expected,
            "characterFolder": character_folder,
            "startSec": start_sec,
            "endSec": end_sec,
            "durationSec": round(end_sec - start_sec, 4),
        }
        for field_name in (
            "tokenCount",
            "matchedTokenCount",
            "startWordIndex",
            "endWordIndex",
        ):
            field_value = stored.get(field_name)
            if isinstance(field_value, (int, float)):
                timestamp_line[field_name] = int(field_value)

        split_line = {
            **timestamp_line,
            "audioFile": audio_file,
            "audioPath": str(audio_path),
        }

        line_timestamps.append(timestamp_line)
        split_lines.append(split_line)

    if missing_ids:
        sample = ", ".join(str(value) for value in missing_ids[:12])
        suffix = "" if len(missing_ids) <= 12 else ", ..."
        raise ValueError(
            "Existing split result manifest is missing line IDs required by "
            f"{result_manifest_path.name}: {sample}{suffix}"
        )

    return line_timestamps, split_lines, payload, text_mismatch_count


def build_character_manifests(
    chapter_id: str,
    split_lines: list[dict[str, Any]],
) -> dict[str, dict[str, Any]]:
    per_character: dict[str, dict[str, Any]] = {}

    for line in split_lines:
        character_id = str(line.get("characterId", "unknown"))
        character_folder = safe_name(str(line.get("characterFolder", "unknown")))

        bucket = per_character.get(character_folder)
        if bucket is None:
            bucket = {
                "formatVersion": "2.0",
                "characterId": character_id,
                "characterName": str(line.get("characterFolder", character_id.title())),
                "metadata": {
                    "totalClips": 0,
                    "chapters": [chapter_id],
                    "sources": {"audio_test": 0},
                    "voiceIds": {},
                    "primaryVoiceId": "Stephen_Fry_HP1-30s.mp3",
                    "lastUpdated": utc_now_iso(),
                },
                "clips": [],
            }
            per_character[character_folder] = bucket

        clip = {
            "id": int(line["id"]),
            "characterId": character_id,
            "characterName": bucket["characterName"],
            "text": str(line["text"]),
            "audioFile": str(line["audioFile"]),
            "chapter": chapter_id,
            "sourceFile": f"{chapter_id}/chapter.txt",
            "voiceId": "Stephen_Fry_HP1-30s.mp3",
            "provider": "vibevoice_local",
            "emotion": None,
            "metadata": {
                "generatedAt": utc_now_iso(),
                "duration": float(line["durationSec"]),
                "startSec": float(line["startSec"]),
                "endSec": float(line["endSec"]),
            },
        }
        bucket["clips"].append(clip)

    for manifest in per_character.values():
        manifest["clips"] = sorted(manifest["clips"], key=lambda item: item["id"])
        manifest["metadata"]["totalClips"] = len(manifest["clips"])
        manifest["metadata"]["sources"] = {"audio_test": len(manifest["clips"])}
        manifest["metadata"]["voiceIds"] = {"Stephen_Fry_HP1-30s.mp3": len(manifest["clips"])}
        manifest["metadata"]["lastUpdated"] = utc_now_iso()

    return per_character
