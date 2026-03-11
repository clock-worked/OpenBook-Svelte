"""Apply line-id filename corrections based on chapter ASR audit output."""

from __future__ import annotations

import argparse
import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any


LINE_PREFIX_RE = re.compile(r"^(?P<line_id>\d+)-(?P<rest>.+)$")


@dataclass
class RenamePlan:
    """Represents one source-to-destination audio file rename."""

    source: Path
    destination: Path
    expected_line_id: int
    target_line_id: int
    best_score: float


def parse_line_id_from_name(file_name: str) -> tuple[int | None, str | None]:
    """Extract numeric line id and suffix from a `<id>-<name>.wav` stem."""

    match = LINE_PREFIX_RE.match(file_name)
    if not match:
        return None, None
    return int(match.group("line_id")), match.group("rest")


def build_destination_path(source: Path, target_line_id: int) -> Path:
    """Build destination path by replacing only the numeric line-id prefix."""

    _line_id, suffix = parse_line_id_from_name(source.name)
    if suffix is None:
        raise ValueError(f"Cannot parse line id from file name: {source.name}")
    return source.with_name(f"{target_line_id}-{suffix}")


def load_audit(audit_path: Path) -> dict[str, Any]:
    """Load and validate a chapter ASR audit JSON payload."""

    payload = json.loads(audit_path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError("Audit payload must be an object")
    clips = payload.get("clips")
    if not isinstance(clips, list):
        raise ValueError("Audit payload must include clips[]")
    return payload


def collect_rename_plans(
    audit_payload: dict[str, Any],
    *,
    min_best_score: float,
    allow_mismatch_status: bool,
) -> tuple[list[RenamePlan], list[str]]:
    """Create validated rename plans from flagged audit clip entries."""

    plans: list[RenamePlan] = []
    skipped: list[str] = []

    seen_source: set[str] = set()
    seen_destination: set[str] = set()
    moving_sources: set[str] = set()

    for clip in audit_payload.get("clips", []):
        if not isinstance(clip, dict):
            continue

        status = str(clip.get("status", ""))
        if status == "ok":
            continue
        if status not in {"likely_wrong_line", "mismatch"}:
            continue
        if status == "mismatch" and not allow_mismatch_status:
            continue

        source_path_raw = clip.get("audioPath")
        expected = clip.get("expected") if isinstance(
            clip.get("expected"), dict) else {}
        best = clip.get("bestOverall") if isinstance(
            clip.get("bestOverall"), dict) else {}

        expected_line_id = expected.get("lineId")
        target_line_id = best.get("lineId")
        best_score_raw = best.get("score")

        if not isinstance(source_path_raw, str):
            skipped.append("skip: clip missing audioPath")
            continue
        if not isinstance(expected_line_id, int) or not isinstance(target_line_id, int):
            skipped.append(f"skip: invalid line ids for {source_path_raw}")
            continue

        try:
            best_score = float(best_score_raw)
        except (TypeError, ValueError):
            skipped.append(f"skip: invalid best score for {source_path_raw}")
            continue

        if best_score < min_best_score:
            skipped.append(
                "skip: low score "
                f"{best_score:.4f} for {source_path_raw} "
                f"({expected_line_id} -> {target_line_id})"
            )
            continue

        source = Path(source_path_raw)
        if not source.exists():
            skipped.append(f"skip: source does not exist: {source}")
            continue

        if expected_line_id == target_line_id:
            continue

        try:
            destination = build_destination_path(source, target_line_id)
        except ValueError as exc:
            skipped.append(f"skip: {exc}")
            continue

        src_key = str(source.resolve())
        dst_key = str(destination.resolve())

        if src_key in seen_source:
            skipped.append(f"skip: duplicate source mapping {source}")
            continue
        if dst_key in seen_destination:
            skipped.append(
                f"skip: duplicate destination mapping {destination}")
            continue

        moving_sources.add(src_key)

        seen_source.add(src_key)
        seen_destination.add(dst_key)
        plans.append(
            RenamePlan(
                source=source,
                destination=destination,
                expected_line_id=expected_line_id,
                target_line_id=target_line_id,
                best_score=best_score,
            )
        )

    validated_plans: list[RenamePlan] = []
    for plan in plans:
        src_key = str(plan.source.resolve())
        dst_key = str(plan.destination.resolve())
        if plan.destination.exists() and dst_key != src_key and dst_key not in moving_sources:
            skipped.append(
                "skip: destination already exists and "
                f"is not being moved {plan.destination}"
            )
            continue
        validated_plans.append(plan)

    return validated_plans, skipped


def apply_plans(plans: list[RenamePlan], dry_run: bool) -> list[str]:
    """Apply rename plans; in apply mode uses two-pass temp renaming."""

    messages: list[str] = []
    for plan in plans:
        message = (
            f"{plan.source.name} -> {plan.destination.name} "
            "(expected "
            f"{plan.expected_line_id}, target {plan.target_line_id}, "
            f"score {plan.best_score:.4f})"
        )
        if dry_run:
            messages.append(f"DRY-RUN: {message}")
        else:
            messages.append(f"RENAMED: {message}")

    if dry_run or not plans:
        return messages

    temp_targets: list[tuple[Path, Path]] = []
    for index, plan in enumerate(plans):
        temp_path = plan.source.with_name(
            f"__audit_shift_tmp_{index}__{plan.source.name}")
        while temp_path.exists():
            temp_path = temp_path.with_name(
                f"__audit_shift_tmp_{index + 1}__{plan.source.name}")
            index += 1
        plan.source.rename(temp_path)
        temp_targets.append((temp_path, plan.destination))

    for temp_path, destination in temp_targets:
        if destination.exists():
            backup_path = destination.with_name(
                f"__audit_shift_backup__{destination.name}"
            )
            backup_index = 1
            while backup_path.exists():
                backup_path = destination.with_name(
                    f"__audit_shift_backup__{backup_index}__{destination.name}"
                )
                backup_index += 1
            destination.rename(backup_path)
        temp_path.rename(destination)

    return messages


def main() -> None:
    """Parse CLI args and run a dry-run or apply rename flow."""

    parser = argparse.ArgumentParser(
        description=(
            "Rename chapter audio files to corrected line IDs "
            "based on ASR audit best matches."
        )
    )
    parser.add_argument("--audit", type=Path, required=True,
                        help="Path to chapter_asr_audit.json")
    parser.add_argument(
        "--min-best-score",
        type=float,
        default=0.90,
        help="Minimum best-match score required before applying a rename.",
    )
    parser.add_argument(
        "--include-mismatch",
        action="store_true",
        help="Also allow status=mismatch entries (default only likely_wrong_line).",
    )
    parser.add_argument(
        "--apply",
        action="store_true",
        help="Apply renames. Without this flag, prints dry-run only.",
    )
    parser.add_argument(
        "--write-plan",
        type=Path,
        default=None,
        help="Optional output JSON file to write planned rename operations.",
    )
    args = parser.parse_args()

    audit_path = args.audit.resolve()
    if not audit_path.exists():
        raise SystemExit(f"audit file not found: {audit_path}")

    payload = load_audit(audit_path)
    plans, skipped = collect_rename_plans(
        payload,
        min_best_score=args.min_best_score,
        allow_mismatch_status=args.include_mismatch,
    )

    print(f"Audit: {audit_path}")
    print(f"Planned renames: {len(plans)}")
    print(f"Skipped entries: {len(skipped)}")

    messages = apply_plans(plans, dry_run=not args.apply)
    for line in messages[:120]:
        print(line)
    if len(messages) > 120:
        print(f"... ({len(messages) - 120} more)")

    if skipped:
        print("\nSkip reasons (first 80):")
        for reason in skipped[:80]:
            print(reason)
        if len(skipped) > 80:
            print(f"... ({len(skipped) - 80} more)")

    if args.write_plan:
        plan_path = args.write_plan.resolve()
        plan_path.parent.mkdir(parents=True, exist_ok=True)
        output = {
            "audit": str(audit_path),
            "applied": bool(args.apply),
            "minBestScore": args.min_best_score,
            "includeMismatch": bool(args.include_mismatch),
            "plannedRenames": [
                {
                    "source": str(item.source),
                    "destination": str(item.destination),
                    "expectedLineId": item.expected_line_id,
                    "targetLineId": item.target_line_id,
                    "bestScore": round(item.best_score, 4),
                }
                for item in plans
            ],
            "skipped": skipped,
        }
        plan_path.write_text(json.dumps(
            output, indent=2, ensure_ascii=False), encoding="utf-8")
        print(f"Plan written: {plan_path}")


if __name__ == "__main__":
    main()
