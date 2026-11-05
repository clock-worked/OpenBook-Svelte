"""Local-only attribution audit.

Re-runs the current parser on every chapter that has a curated dialogue.json
and measures where the parser is blind, mis-scored, or correct.

Verdict classes:
  correct       - parser selected the curated name
  scoring_gap   - curated name was in the candidate pool but not selected
  blind_spot    - curated name was never in the pool at all
  narration     - curated as Narrator (excluded from problem counts)
  unlabeled     - no curated characterId

Local-only: the ``local_`` prefix means CI test discovery skips this file.

Usage:
    python -m py_services.local_attribution_audit <book_root> [book_root ...]
        [--chapter FILTER] [--report OUT.md]
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import time
from pathlib import Path
from typing import Dict, Generator, List, Optional, Tuple

from .openbook_parser.booknlp_parser_service import BookNLPParserService


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------

def _norm(s: Optional[str]) -> str:
    """Normalise a name for comparison."""
    return (s or "").strip().lower()


def _text_key(s: Optional[str]) -> str:
    """Aggressive normalisation for text matching (alignment fallback)."""
    return re.sub(r"[^a-z0-9]", "", (s or "").lower())


# ---------------------------------------------------------------------------
# catalog loading
# ---------------------------------------------------------------------------

def load_catalog(book_root: Path) -> Tuple[List[dict], Dict[str, str]]:
    """Load the character catalog from a book root.

    Supports two layouts:
      * ``characters/*.json``  – one file per character (Book-2 / v3 style)
      * ``characters.json``    – single book-level file (Primal-Hunter style)

    Returns ``(catalog_list, id_to_name)``.
    """
    catalog: List[dict] = []
    id_to_name: Dict[str, str] = {}

    # --- per-character files -------------------------------------------
    char_dir = book_root / "characters"
    if char_dir.is_dir():
        for f in sorted(char_dir.glob("*.json")):
            try:
                data = json.loads(f.read_text(encoding="utf-8"))
            except (json.JSONDecodeError, OSError):
                continue
            name = data.get("name") or data.get("title") or ""
            cid = data.get("guid") or data.get("id") or ""
            entry = {
                "characterId": cid,
                "name": name,
                "aliases": data.get("aliases") or [],
                "descriptors": data.get("descriptors") or [],
            }
            catalog.append(entry)
            if cid and name:
                id_to_name[cid] = name
        if catalog:
            return catalog, id_to_name

    # --- book-level characters.json ------------------------------------
    chars_file = book_root / "characters.json"
    if chars_file.exists():
        try:
            data = json.loads(chars_file.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            data = {}
        for char in data.get("characters", []):
            name = char.get("name") or ""
            cid = char.get("id") or ""
            entry = {
                "characterId": cid,
                "name": name,
                "aliases": char.get("aliases") or [],
                "descriptors": char.get("descriptors") or [],
            }
            catalog.append(entry)
            if cid and name:
                id_to_name[cid] = name

    return catalog, id_to_name


# ---------------------------------------------------------------------------
# chapter iteration
# ---------------------------------------------------------------------------

def iter_chapter_dirs(book_root: Path) -> Generator[Path, None, None]:
    """Yield sub-directories that contain a ``chapter.txt``."""
    for d in sorted(book_root.iterdir()):
        if d.is_dir() and (d / "chapter.txt").exists():
            yield d


# ---------------------------------------------------------------------------
# single-chapter audit
# ---------------------------------------------------------------------------

def audit_chapter(
    chapter_dir: Path,
    catalog: List[dict],
    id_to_name: Dict[str, str],
) -> Optional[dict]:
    """Parse one chapter and compare against its curated dialogue.json.

    Returns a result dict, or ``None`` if there is no dialogue.json.
    """
    chapter_txt = chapter_dir / "chapter.txt"
    dialogue_json = chapter_dir / "dialogue.json"
    if not dialogue_json.exists():
        return None

    # -- parse ----------------------------------------------------------
    options = {
        "episode_joint_decode": False,
        "closed_world_characters": True,
        "character_catalog": catalog,
        "heuristics": {"coreference": False},
    }
    lines, _, _ = BookNLPParserService().parse_file(
        str(chapter_txt), options=options
    )
    parser_dialogue = [l for l in lines if l.line_type == "dialogue"]

    # -- curated --------------------------------------------------------
    try:
        dj = json.loads(dialogue_json.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        return None
    curated = dj if isinstance(dj, list) else dj.get("lines", [])

    # -- raw text (for window checks) -----------------------------------
    raw_text = chapter_txt.read_text(encoding="utf-8")

    # -- alignment ------------------------------------------------------
    # Positional when counts match AND text is roughly verified;
    # otherwise fall back to normalised-text index lookup.
    use_positional = len(curated) == len(parser_dialogue)
    if use_positional:
        # spot-check: first and last lines should share a text prefix
        if curated and parser_dialogue:
            a = _text_key(curated[0].get("text", ""))[:20]
            b = _text_key(parser_dialogue[0].text)[:20]
            if a and b and a != b:
                use_positional = False

    # Build a text→index map for fallback alignment
    text_to_idx: Dict[str, int] = {}
    for idx, pl in enumerate(parser_dialogue):
        text_to_idx.setdefault(_text_key(pl.text), idx)

    # -- classify each curated line -------------------------------------
    results: List[dict] = []
    for i, c in enumerate(curated):
        cid = c.get("characterId")
        cname = id_to_name.get(cid) if cid else None

        # find the matching parser line
        if use_positional and i < len(parser_dialogue):
            pl = parser_dialogue[i]
        else:
            key = _text_key(c.get("text", ""))
            pl = parser_dialogue[text_to_idx.get(key, 0)] if parser_dialogue else None

        if pl is None:
            results.append({
                "id": c.get("id"), "text": (c.get("text") or "")[:60],
                "verdict": "unlabeled", "curated": cname,
            })
            continue

        tr = (pl.attribution or {}).get("decisionTrace", {})
        sel = _norm(tr.get("selectedCandidate"))
        pool = {_norm(n) for n in (tr.get("rawCandidatePool") or [])}

        in_window: Optional[bool] = None

        if not cname:
            verdict = "unlabeled"
        elif _norm(cname) in ("narrator", "narration"):
            verdict = "narration"
        elif sel == _norm(cname):
            verdict = "correct"
        elif _norm(cname) in pool:
            verdict = "scoring_gap"
        elif "protagonist" in pool:
            # Protagonist exclusion: the parser correctly identified a
            # first-person voice; curation maps "Protagonist" → real name.
            verdict = "correct"
        else:
            verdict = "blind_spot"
            # window-yield probe
            s = pl.span_start or 0
            e = pl.span_end or 0
            window = raw_text[max(0, s - 300): e + 300]
            in_window = bool(
                re.search(r"\b" + re.escape(cname) + r"\b", window, re.I)
            )

        results.append({
            "id": c.get("id"),
            "text": (c.get("text") or "")[:60],
            "verdict": verdict,
            "curated": cname,
            "selected": tr.get("selectedCandidate"),
            "pool": sorted(pool),
            "in_window": in_window,
        })

    # -- counts ---------------------------------------------------------
    counts: Dict[str, int] = {}
    for r in results:
        counts[r["verdict"]] = counts.get(r["verdict"], 0) + 1

    total_labeled = len([r for r in results if r["verdict"] != "unlabeled"])

    return {
        "chapter": chapter_dir.name,
        "results": results,
        "counts": counts,
        "total_labeled": total_labeled,
    }


# ---------------------------------------------------------------------------
# report writer
# ---------------------------------------------------------------------------

def _write_report(
    path: Path,
    chapters: List[dict],
    totals: Dict[str, int],
    elapsed: float,
    blind_total: int,
    blind_in_window: int,
) -> None:
    lines: List[str] = [
        "# Attribution Audit Report",
        "",
        f"Generated: {time.strftime('%Y-%m-%d %H:%M:%S')}",
        f"Duration: {elapsed:.0f}s",
        "",
        "## Totals",
        "",
        "| Verdict | Count | Pct |",
        "|---------|-------|-----|",
    ]
    total = sum(totals.values())
    for k in ("correct", "scoring_gap", "blind_spot", "narration", "unlabeled"):
        v = totals.get(k, 0)
        pct = f"{v / total * 100:.1f}%" if total else "0%"
        lines.append(f"| {k} | {v} | {pct} |")

    lines.append("")
    lines.append(f"Window yield: {blind_in_window}/{blind_total} blind spots")
    lines.append("")

    for ch in chapters:
        counts = ch["counts"]
        labeled = ch["total_labeled"]
        correct = counts.get("correct", 0)
        pct = f"{correct / labeled * 100:.0f}%" if labeled else "N/A"
        lines.append(f"## {ch['chapter']}")
        lines.append(
            f"{correct}/{labeled} correct ({pct}) — "
            f"gaps={counts.get('scoring_gap', 0)} "
            f"blind={counts.get('blind_spot', 0)} "
            f"narr={counts.get('narration', 0)}"
        )
        lines.append("")

        blind_spots = [r for r in ch["results"] if r["verdict"] == "blind_spot"]
        if blind_spots:
            lines.append(f"### Blind spots ({len(blind_spots)})")
            for r in blind_spots[:10]:
                flag = " [IN-WINDOW]" if r.get("in_window") else ""
                lines.append(
                    f"- line {r['id']}: curated={r['curated']} "
                    f"selected={r.get('selected')} "
                    f"pool={r.get('pool', [])}{flag} {r['text']!r}"
                )
            if len(blind_spots) > 10:
                lines.append(f"- … and {len(blind_spots) - 10} more")
            lines.append("")

    path.write_text("\n".join(lines), encoding="utf-8")


# ---------------------------------------------------------------------------
# main
# ---------------------------------------------------------------------------

def main(argv: Optional[List[str]] = None) -> None:
    parser = argparse.ArgumentParser(description="Attribution audit (local-only)")
    parser.add_argument("books", nargs="+", help="Book root directories")
    parser.add_argument("--chapter", help="Filter to chapters whose name contains this string")
    parser.add_argument("--report", help="Write a Markdown report to this path")
    args = parser.parse_args(argv)

    # Fault-tolerant stdout: chapter text can contain PUA glyphs that the
    # Windows console charmap cannot encode.
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

    totals = {
        "correct": 0, "scoring_gap": 0, "blind_spot": 0,
        "narration": 0, "unlabeled": 0,
    }
    all_chapters: List[dict] = []
    t0 = time.time()

    for book_root_str in args.books:
        book_root = Path(book_root_str)
        if not book_root.exists():
            print(f"WARNING: {book_root} does not exist, skipping")
            continue

        catalog, id_to_name = load_catalog(book_root)
        chapters = list(iter_chapter_dirs(book_root))

        print(f"\n=== {book_root.name} ({len(chapters)} chapters, {len(catalog)} characters) ===")

        for idx, ch_dir in enumerate(chapters, 1):
            if args.chapter and args.chapter not in ch_dir.name:
                continue

            result = audit_chapter(ch_dir, catalog, id_to_name)
            if result is None:
                continue

            counts = result["counts"]
            labeled = result["total_labeled"]
            correct = counts.get("correct", 0)
            gaps = counts.get("scoring_gap", 0)
            blind = counts.get("blind_spot", 0)
            narr = counts.get("narration", 0)
            unlabeled = counts.get("unlabeled", 0)
            pct = f"{correct / labeled * 100:.0f}%" if labeled else "N/A"

            print(
                f"  [{idx}/{len(chapters)}] {ch_dir.name}: "
                f"{correct}/{labeled} correct ({pct}), "
                f"gaps={gaps} blind={blind} narr={narr} unlabeled={unlabeled}"
            )

            all_chapters.append(result)
            for k in totals:
                totals[k] += counts.get(k, 0)

    elapsed = time.time() - t0
    total_labeled = sum(totals.values())

    print(f"\n{'=' * 60}")
    print(f"TOTAL: {total_labeled} labeled lines in {elapsed:.0f}s")
    for k, v in totals.items():
        pct = f" ({v / total_labeled * 100:.1f}%)" if total_labeled else ""
        print(f"  {k}: {v}{pct}")

    # window-yield aggregate
    blind_total = 0
    blind_in_window = 0
    for ch in all_chapters:
        for r in ch["results"]:
            if r["verdict"] == "blind_spot":
                blind_total += 1
                if r.get("in_window"):
                    blind_in_window += 1
    if blind_total:
        print(f"\n  Window yield: {blind_in_window}/{blind_total} blind spots "
              f"have curated name in ±300ch window")

    if args.report:
        report_path = Path(args.report)
        _write_report(report_path, all_chapters, totals, elapsed,
                       blind_total, blind_in_window)
        print(f"\n  Report written to: {report_path}")


if __name__ == "__main__":
    main()
