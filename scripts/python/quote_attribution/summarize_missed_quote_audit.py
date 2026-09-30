"""Measure candidate narration cues against every exported quote, without reparsing."""

import argparse
from collections import Counter, defaultdict
import json
from pathlib import Path
import re


def normalize(value):
    return " ".join(re.findall(r"[\w']+", str(value or "").casefold().replace("_", " ").replace("-", " ")))


def build_matchers(characters, verbs):
    owners = defaultdict(set)
    lookup = defaultdict(set)
    for character in characters["characters"]:
        identity = character.get("id")
        if not identity:
            continue
        lookup[normalize(identity)].add(identity)
        for surface in [character.get("name"), *(character.get("aliases") or [])]:
            if surface:
                owners[str(surface)].add(identity)
                lookup[normalize(surface)].add(identity)
    surfaces = sorted((surface for surface, identities in owners.items() if len(identities) == 1 and len(lookup[normalize(surface)]) == 1), key=len, reverse=True)
    name_pattern = "|".join(re.escape(surface) for surface in surfaces)
    verb_pattern = "|".join(re.escape(verb) for verb in sorted(verbs, key=len, reverse=True))
    direct = re.compile(rf"(?<!\w)(?:(?P<name>{name_pattern})\s+(?P<verb>{verb_pattern})|(?P<reverse_verb>{verb_pattern})\s+(?P<reverse_name>{name_pattern}))(?!\w)", re.I)
    auxiliary = re.compile(rf"(?<!\w)(?P<name>{name_pattern})\s+(?:(?:had|has|have|was|is|just|then|finally|quietly|softly|quickly|suddenly|simply|instead|also|immediately)\s+){{1,3}}(?P<verb>{verb_pattern})(?!\w)", re.I)
    names = re.compile(rf"(?<!\w)(?P<name>{name_pattern})(?!\w)", re.I)
    return direct, auxiliary, names, {key: next(iter(identities)) for key, identities in lookup.items() if len(identities) == 1}


def unique_tag(text, pattern, lookup, immediate=False):
    matches = []
    for match in pattern.finditer(text):
        if immediate and re.search(r"[\w]", text[:match.start()]):
            continue
        groups = match.groupdict()
        name = groups.get("name") or groups.get("reverse_name")
        identity = lookup.get(normalize(name))
        if identity:
            matches.append((identity, match.group()))
    return matches[0] if len({identity for identity, _ in matches}) == 1 else None


def first_sentence(text):
    return re.split(r"(?<=[.!?])\s+", text.strip(), maxsplit=1)[0]


def last_sentence(text):
    return re.split(r"(?<=[.!?])\s+", text.rstrip(" \t\r\n\"\u201c\u201d"))[-1]


def audit(input_dir, resource_root):
    verbs_path = Path(__file__).resolve().parents[3] / "py_services/openbook_parser/data/speech_verbs.txt"
    verbs = {line.strip() for line in verbs_path.read_text(encoding="utf-8").splitlines() if line.strip() and not line.startswith("#")}
    reactions = set(verbs_path.with_name("reaction_verbs.txt").read_text(encoding="utf-8").splitlines())
    stats = defaultdict(Counter)
    examples = defaultdict(list)
    all_misses = []
    counts = Counter()
    for path in sorted(input_dir.glob("book-*_quotes.jsonl")):
        book = path.name.removesuffix("_quotes.jsonl").replace("book-", "Book-")
        characters = json.loads((resource_root / book / "characters.json").read_text(encoding="utf-8-sig"))
        direct, auxiliary, names, lookup = build_matchers(characters, verbs)
        nonreaction, _, _, _ = build_matchers(characters, verbs - reactions)
        speech_phase, _, _, _ = build_matchers(characters, {"began", "started", "finished"})
        for raw in path.read_text(encoding="utf-8").splitlines():
            row = json.loads(raw)
            counts["total"] += 1
            counts["correct"] += row["correct"]
            counts["unmatched"] += not row["matched"]
            counts["inexact_spans"] += row["matched"] and not row["span_text_matches"]
            if not row["matched"] or not row["span_text_matches"]:
                if not row["correct"]:
                    all_misses.append({**row, "candidate_fix_categories": []})
                continue
            decision = row.get("decision") or {}
            signals = decision.get("signals") or {}
            modern = decision.get("modernbooknlp") or {}
            before = row["same_paragraph_before"]
            after = row["same_paragraph_after"]
            cues = {}
            action_sentence = last_sentence(before).lstrip(" \t\r\n\"\u201c\u201d")
            action_mentions = list(names.finditer(action_sentence))
            action_owners = {lookup.get(normalize(match.group("name"))) for match in action_mentions}
            if action_mentions and action_mentions[0].start() == 0 and len(action_owners) == 1 and None not in action_owners:
                cues["single_named_action_before"] = (next(iter(action_owners)), action_sentence)
            phase_match = speech_phase.search(after)
            if phase_match and re.match(r"\s*[,.;:]", after[phase_match.end():]):
                phase_cue = unique_tag(after, speech_phase, lookup, immediate=True)
                if phase_cue:
                    cues["punctuated_speech_phase_post_tag"] = phase_cue
            for name, text, pattern, immediate in (
                ("immediate_post_tag", after, direct, True),
                ("same_paragraph_pre_tag", last_sentence(before), direct, False),
                ("same_paragraph_post_auxiliary_tag", first_sentence(after), auxiliary, True),
                ("nonreaction_immediate_post_tag", after, nonreaction, True),
                ("subject_led_pre_tag", action_sentence, nonreaction, True),
                ("adjacent_previous_tag", last_sentence(row["before"]), direct, False),
                ("adjacent_following_tag", first_sentence(row["after"]), direct, False),
            ):
                cue = unique_tag(text, pattern, lookup, immediate)
                if cue:
                    cues[name] = cue
            anchor = cues.get("immediate_post_tag") or cues.get("same_paragraph_pre_tag")
            if anchor:
                cues["post_then_pre_tag"] = anchor
            strict_anchor = cues.get("nonreaction_immediate_post_tag") or cues.get("subject_led_pre_tag")
            if strict_anchor:
                cues["nonreaction_post_then_subject_pre"] = strict_anchor
            post_match = nonreaction.search(after)
            if post_match and post_match.groupdict().get("name") and cues.get("nonreaction_immediate_post_tag"):
                recipient_match = names.match(after[post_match.end():].lstrip())
                if recipient_match and lookup.get(normalize(recipient_match.group("name"))) == row["predicted"]:
                    if cues["nonreaction_immediate_post_tag"][0] != row["predicted"]:
                        cues["named_addressee_chosen_over_subject"] = cues["nonreaction_immediate_post_tag"]
            action_anchor = cues.get("nonreaction_immediate_post_tag") or cues.get("single_named_action_before")
            if action_anchor:
                cues["nonreaction_post_then_action"] = action_anchor
            signal_post = lookup.get(normalize(signals.get("nextSentenceAttributedSpeaker")))
            if signal_post:
                cues["existing_post_tag_signal"] = (signal_post, str(signals["nextSentenceAttributedSpeaker"]))
                if modern:
                    cues["existing_post_tag_over_modern"] = cues["existing_post_tag_signal"]
            descriptor = lookup.get(normalize(signals.get("descriptorMatch")))
            if descriptor:
                cues["existing_descriptor_signal"] = (descriptor, str(signals["descriptorMatch"]))
            if anchor and modern:
                cues["bounded_tag_over_modern"] = anchor
            if anchor and "same_paragraph_context_consistency" in decision.get("selected_reasons", []):
                cues["bounded_tag_over_consistency"] = anchor
            previous = lookup.get(normalize(modern.get("previousSpeaker")))
            if previous:
                cues["all_legacy_over_modern"] = (previous, str(modern["previousSpeaker"]))
            if signal_post and not unique_tag(first_sentence(after), direct, lookup):
                counts["unbounded_post_signal"] += 1
                counts["unbounded_post_signal_misses"] += not row["correct"]
            categories = []
            for name, (identity, evidence) in cues.items():
                stats[name]["support"] += 1
                stats[name]["cue_correct"] += identity == row["gold"]
                changed = identity != row["predicted"]
                stats[name]["changed"] += changed
                stats[name]["fixes"] += changed and identity == row["gold"]
                stats[name]["regressions"] += changed and row["correct"]
                stats[name]["wrong_to_wrong"] += changed and not row["correct"] and identity != row["gold"]
                if not row["correct"] and identity == row["gold"]:
                    categories.append(name)
                if changed:
                    examples[name].append({"book": row["book"], "chapter": row["chapter"], "line": row["gold_line_id"], "gold": row["gold"], "predicted": row["predicted"], "cue": identity, "evidence": evidence, "fix": identity == row["gold"], "regression": row["correct"], "text": row["text"], "before": before, "after": after, "reasons": decision.get("selected_reasons"), "modern": modern})
            if not row["correct"]:
                all_misses.append({**row, "candidate_fix_categories": categories})
    report = {"collection_complete": (input_dir / "summary.json").is_file(), "counts": dict(counts), "cues": {name: {**dict(values), "net": values["fixes"] - values["regressions"]} for name, values in stats.items()}, "misses_with_any_candidate_cue": sum(bool(row["candidate_fix_categories"]) for row in all_misses)}
    (input_dir / "cue_summary.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
    (input_dir / "cue_examples.json").write_text(json.dumps(examples, indent=2, ensure_ascii=False), encoding="utf-8")
    (input_dir / "all_misses.jsonl").write_text("".join(json.dumps(row, ensure_ascii=False) + "\n" for row in all_misses), encoding="utf-8")
    print(json.dumps(report, indent=2))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input-dir", type=Path, required=True)
    parser.add_argument("--resource-root", type=Path, required=True)
    args = parser.parse_args()
    audit(args.input_dir, args.resource_root)