#!/usr/bin/env python3
import argparse
import json
import re
import subprocess
import sys
from pathlib import Path
from typing import Any


REPO_ROOT_DEFAULT = Path(__file__).resolve().parents[2]
AUDIT_DEFAULT = Path("C:/temp/book14_asr_audit_00_72/book_asr_audit.json")
HITS_DEFAULT = REPO_ROOT_DEFAULT / "scripts" / "tmp" / "book14_short_dialogue_then_narration_hits.json"


def safe_slug(value: str) -> str:
    cleaned = re.sub(r"[^a-zA-Z0-9]+", "-", value.strip()).strip("-").lower()
    return cleaned or "character"


def run_one(
    *,
    python_exe: Path,
    repo_root: Path,
    chapter_dir: Path,
    output_dir: Path,
    segments: list[dict[str, Any]],
    main_character: str,
    device: str,
) -> tuple[bool, str]:
    output_dir.mkdir(parents=True, exist_ok=True)
    segments_path = output_dir / "segments.json"
    segments_path.write_text(json.dumps(segments, ensure_ascii=False, indent=2), encoding="utf-8")

    command = [
        str(python_exe),
        str((repo_root / "py_services" / "asr_experiment_runner.py").resolve()),
        "multi-heuristic-split",
        "--output-dir",
        str(output_dir),
        "--segments-json",
        str(segments_path),
        "--main-character-name",
        main_character,
        "--chapter-dir",
        str(chapter_dir),
        "--pause-snap",
        "--vibevoice-device",
        device,
    ]

    log_path = output_dir / "_run.log"
    with log_path.open("w", encoding="utf-8", errors="replace") as log_handle:
        log_handle.write("COMMAND:\n")
        log_handle.write(" ".join(command) + "\n\n")
        log_handle.flush()

        process = subprocess.Popen(
            command,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            bufsize=1,
        )

        live_lines: list[str] = []
        assert process.stdout is not None
        for line in process.stdout:
            print(line, end="")
            log_handle.write(line)
            if len(live_lines) < 500:
                live_lines.append(line.rstrip("\n"))
            log_handle.flush()

        return_code = process.wait()

    if return_code == 0:
        return True, "\n".join(live_lines[-30:])

    return False, "\n".join(live_lines[-120:])


def build_segments_from_hit(match: dict[str, Any]) -> list[dict[str, Any]]:
    first = match.get("firstDialogue") or {}
    narration = match.get("followingNarration") or {}
    followup = match.get("sameSpeakerFollowup")

    segments: list[dict[str, Any]] = [
        {
            "lineId": first.get("lineId"),
            "character": first.get("characterId"),
            "text": first.get("text"),
        },
        {
            "lineId": narration.get("lineId"),
            "character": narration.get("characterId"),
            "text": narration.get("text"),
        },
    ]

    if isinstance(followup, dict):
        segments.append(
            {
                "lineId": followup.get("lineId"),
                "character": followup.get("characterId"),
                "text": followup.get("text"),
            }
        )

    clean_segments = []
    for segment in segments:
        line_id = segment.get("lineId")
        character = segment.get("character")
        text = segment.get("text")
        if not isinstance(character, str) or not character.strip():
            continue
        if not isinstance(text, str) or not text.strip():
            continue
        clean_segments.append(
            {
                "lineId": line_id if isinstance(line_id, int) else None,
                "character": character.strip(),
                "text": text.strip(),
            }
        )

    return clean_segments


def merge_segments_by_character(
    segments: list[dict[str, Any]],
    delimiter: str,
) -> list[dict[str, Any]]:
    grouped: dict[str, dict[str, Any]] = {}
    order: list[str] = []

    for segment in segments:
        character = segment["character"]
        line_id = segment.get("lineId")
        text = segment["text"]

        if character not in grouped:
            grouped[character] = {
                "lineId": line_id if isinstance(line_id, int) else None,
                "character": character,
                "text_parts": [text],
                "sourceLineIds": [line_id] if isinstance(line_id, int) else [],
            }
            order.append(character)
            continue

        grouped[character]["text_parts"].append(text)
        if isinstance(line_id, int):
            grouped[character]["sourceLineIds"].append(line_id)

    merged: list[dict[str, Any]] = []
    for character in order:
        payload = grouped[character]
        merged.append(
            {
                "lineId": payload["lineId"],
                "character": payload["character"],
                "text": delimiter.join(payload["text_parts"]),
                "sourceLineIds": payload["sourceLineIds"],
            }
        )

    return merged


def collect_jobs_from_hits(payload: dict[str, Any], delimiter: str) -> list[dict[str, Any]]:
    matches = payload.get("matches", [])
    if not isinstance(matches, list):
        raise ValueError("hits file must contain a matches array")

    jobs: list[dict[str, Any]] = []
    for match in matches:
        if not isinstance(match, dict):
            continue

        dialogue_path_value = match.get("dialoguePath")
        if not isinstance(dialogue_path_value, str):
            continue

        chapter_dir = Path(dialogue_path_value).resolve().parent
        raw_segments = build_segments_from_hit(match)
        if len(raw_segments) < 1:
            continue

        segments = merge_segments_by_character(raw_segments, delimiter=delimiter)
        if len(segments) < 1:
            continue

        line_ids = [segment.get("lineId") for segment in raw_segments if isinstance(segment.get("lineId"), int)]
        min_line = min(line_ids) if line_ids else 0
        max_line = max(line_ids) if line_ids else 0
        folder_name = f"hits_{min_line}_{max_line}_{len(raw_segments)}"

        jobs.append(
            {
                "chapterDir": chapter_dir,
                "folderName": folder_name,
                "segments": segments,
                "mainCharacter": str(segments[0]["character"]),
                "source": "hits",
            }
        )

    return jobs


def extract_regen_lines_for_chapter(chapter: dict[str, Any]) -> list[dict[str, Any]]:
    clips = chapter.get("clips", [])
    regenerate = chapter.get("regenerate", [])
    if not isinstance(clips, list) or not isinstance(regenerate, list):
        return []

    clip_by_line_id: dict[int, dict[str, Any]] = {}
    for clip in clips:
        if not isinstance(clip, dict):
            continue
        line_id = clip.get("lineIdFromFile")
        if not isinstance(line_id, int):
            continue
        expected = clip.get("expected") if isinstance(clip.get("expected"), dict) else {}
        character_id = expected.get("characterId")
        text = expected.get("text")
        if not isinstance(character_id, str) or not character_id.strip():
            continue
        if not isinstance(text, str) or not text.strip():
            continue
        clip_by_line_id[line_id] = {
            "lineId": line_id,
            "character": character_id.strip(),
            "text": text.strip(),
        }

    lines: list[dict[str, Any]] = []
    seen: set[int] = set()

    for item in regenerate:
        if not isinstance(item, dict):
            continue
        line_id = item.get("lineId")
        if not isinstance(line_id, int) or line_id in seen:
            continue

        from_clip = clip_by_line_id.get(line_id)
        if from_clip is not None:
            lines.append(from_clip)
            seen.add(line_id)
            continue

        character_id = item.get("characterId")
        text = item.get("expectedText")
        if isinstance(character_id, str) and character_id.strip() and isinstance(text, str) and text.strip():
            lines.append(
                {
                    "lineId": line_id,
                    "character": character_id.strip(),
                    "text": text.strip(),
                }
            )
            seen.add(line_id)

    lines.sort(key=lambda entry: int(entry["lineId"]))
    return lines


def collect_jobs_from_audit(
    payload: dict[str, Any],
    line_tail: str,
) -> list[dict[str, Any]]:
    chapters = payload.get("chapters", [])
    if not isinstance(chapters, list):
        raise ValueError("audit file must contain a chapters array")

    jobs: list[dict[str, Any]] = []

    for chapter in chapters:
        if not isinstance(chapter, dict):
            continue

        chapter_dir_raw = chapter.get("chapterDir")
        if not isinstance(chapter_dir_raw, str):
            continue

        chapter_dir = Path(chapter_dir_raw).resolve()
        lines = extract_regen_lines_for_chapter(chapter)
        if not lines:
            continue

        for line in lines:
            line_id = int(line["lineId"])
            character = str(line["character"])
            base_text = str(line["text"]).strip()
            if not base_text:
                continue

            tail = str(line_tail)
            text_with_tail = f"{base_text} {tail.strip()}".strip() if tail.strip() else base_text
            folder_name = f"regen_{safe_slug(character)}_{line_id}_tail"

            jobs.append(
                {
                    "chapterDir": chapter_dir,
                    "folderName": folder_name,
                    "segments": [
                        {
                            "lineId": line_id,
                            "character": character,
                            "text": text_with_tail,
                            "sourceLineIds": [line_id],
                            "baseText": base_text,
                            "lineTail": tail,
                        }
                    ],
                    "mainCharacter": character,
                    "source": "audit-line-tail",
                    "groupCount": 1,
                }
            )

    return jobs


def main() -> None:
    parser = argparse.ArgumentParser(
        description=(
            "Generate review regeneration audio. "
            "Default mode reads book_asr_audit.json and regenerates each flagged line with an appended tail."
        )
    )
    parser.add_argument(
        "--mode",
        choices=["audit-grouped-character", "audit-line-tail", "hits-merge"],
        default="audit-line-tail",
    )
    parser.add_argument("--audit-json", type=Path, default=AUDIT_DEFAULT)
    parser.add_argument("--hits", type=Path, default=HITS_DEFAULT)
    parser.add_argument("--repo-root", type=Path, default=REPO_ROOT_DEFAULT)
    parser.add_argument("--python-exe", type=Path, default=Path(sys.executable))
    parser.add_argument("--device", default="cuda")
    parser.add_argument("--delimiter", default=" ... ")
    parser.add_argument(
        "--line-tail",
        default="... he said. Keeping his voice neutaral and looking toward the horizon as he though about it.",
    )
    parser.add_argument("--output-root-name", default="review")
    parser.add_argument("--force", action="store_true")
    parser.add_argument("--limit", type=int, default=0)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    repo_root = args.repo_root.resolve()
    python_exe = args.python_exe.resolve()

    if args.mode in {"audit-grouped-character", "audit-line-tail"}:
        payload = json.loads(args.audit_json.read_text(encoding="utf-8"))
        jobs = collect_jobs_from_audit(
            payload,
            line_tail=args.line_tail,
        )
    else:
        payload = json.loads(args.hits.read_text(encoding="utf-8"))
        jobs = collect_jobs_from_hits(payload, delimiter=args.delimiter)

    total = len(jobs)
    done = 0
    skipped = 0
    failed = 0

    for index, job in enumerate(jobs, start=1):
        if args.limit and done + skipped + failed >= args.limit:
            break

        chapter_dir = Path(job["chapterDir"]).resolve()
        folder_name = str(job["folderName"])
        output_dir = chapter_dir / args.output_root_name / folder_name
        report_path = output_dir / "heuristic_multi_split_experiment.json"

        if report_path.exists() and not args.force:
            skipped += 1
            print(f"[{index}/{total}] SKIP {chapter_dir.name}/{args.output_root_name}/{folder_name}")
            continue

        segments = job["segments"]
        if not isinstance(segments, list) or not segments:
            failed += 1
            print(f"[{index}/{total}] FAIL invalid segments: {chapter_dir.name}/{folder_name}")
            continue

        main_character = str(job["mainCharacter"])
        group_count = int(job.get("groupCount", len(segments)))
        print(
            f"[{index}/{total}] RUN {chapter_dir.name}/{args.output_root_name}/{folder_name} "
            f"(source={job.get('source')}, lines={group_count}, character={main_character})"
        )

        if args.dry_run:
            output_dir.mkdir(parents=True, exist_ok=True)
            (output_dir / "segments.json").write_text(
                json.dumps(segments, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
            done += 1
            print(f"[{index}/{total}] DRY-OK {chapter_dir.name}/{args.output_root_name}/{folder_name}")
            continue

        ok, log = run_one(
            python_exe=python_exe,
            repo_root=repo_root,
            chapter_dir=chapter_dir,
            output_dir=output_dir,
            segments=segments,
            main_character=main_character,
            device=args.device,
        )

        if ok:
            done += 1
            print(f"[{index}/{total}] OK {chapter_dir.name}/{args.output_root_name}/{folder_name}")
            continue

        failed += 1
        print(f"[{index}/{total}] FAIL {chapter_dir.name}/{args.output_root_name}/{folder_name}")
        output_dir.mkdir(parents=True, exist_ok=True)
        (output_dir / "_error.log").write_text(log, encoding="utf-8", errors="replace")

    print("\n=== Batch Summary ===")
    print(f"Mode: {args.mode}")
    print(f"Discovered jobs: {total}")
    print(f"Processed: {done + skipped + failed}")
    print(f"Done: {done}")
    print(f"Skipped: {skipped}")
    print(f"Failed: {failed}")


if __name__ == "__main__":
    main()
