# Improving Jev Attribution Accuracy — Theory & Options

Current baseline from 5-chapter pilot: **73.01%** (parser alone: 65.64%, +7.4pp from Jev).
Room for improvement: ~27% of lines still misattributed.

## What we pass to Jev today

1. **State**: raw chapter text ±1500 chars around a `<quote>`-wrapped line
2. **Question**: one `choice` question with the scene roster as `criteria`

The roster is built by `_build_scene_roster`: characters within ±6 paragraphs and ±4 dialogue turns. This is a simple distance heuristic — it doesn't use BookNLP entity data at all.

## Root causes of misattribution

| Cause | Likely share | Why |
|---|---|---|
| **Speaker missing from roster** | ~40% | Distance heuristic misses characters mentioned by alias or appearing in non-dialogue paragraphs |
| **Insufficient context** | ~30% | ±1500 chars may not capture the attribution cue (e.g. "he said" 2 paragraphs before) |
| **Ambiguous prose** | ~20% | Rapid-fire dialogue with no explicit tags; two characters of same gender |
| **No explicit tag** | ~10% | Pure action-tag or implied-speaker passages |

## Improvement vectors

### 1. Roster accuracy (biggest lever)

**Today**: `_build_scene_roster` is purely distance-based — it collects characterIds from nearby dialogue lines.

**What BookNLP gives us**:
- `build_scene_alias_memory()` — scans the full chapter for appositive patterns like "Jake, the alchemist" or "the Viper, Villy". Maps surface mentions back to characterIds. This catches characters who are *discussed* but haven't spoken yet in the chapter.
- `extract_quote_structure()` — per-paragraph quote analysis with explicit speaker cues (e.g. "Jake said")
- Chapter `.characters.json` files — BookNLP-extracted character names with their mention locations

**Proposed roster algorithm v2**:

```
For each dialogue line:
  1. Start with distance-based candidates (±6 paragraphs, ±4 turns)  ← today
  2. Expand: search ±20 paragraphs for any characterId that BookNLP's
     scene_alias_memory maps to a surface mentioned in those paragraphs
  3. Expand: any character whose name/alias appears in the current
     paragraph text (even outside quotes)
  4. Always include: last speaker, second-to-last speaker
  5. Cap at 51 options (Jev limit), prioritizing by recency
```

**Expected gain**: ~8-12pp (fixing the "speaker not in roster" cases)

### 2. Richer state formatting

**Today**: raw text dump → `"...narrative text...<quote>dialogue</quote>...more narrative..."`

**Proposed**: structured preamble before the text:

```
[Scene] Chapter 01 - A False God & Proactive Measures
[Last speaker] carmen (Carmen)
[Recent speakers] carmen → jake → carmen → jake
[Characters present] carmen (Carmen, female), jake (Jake, male, Lord Thayne)

--- Text ---
...narrative text...<quote>dialogue</quote>...more narrative...
```

This gives Jev:
- **Turn continuity**: alternating speakers in conversation
- **Character metadata**: gender and aliases help disambiguate pronouns ("he said" → Jake)
- **Scene framing**: knowing which chapter/section

**Expected gain**: ~3-5pp

### 3. Explicit attribution cues

**Today**: Jev has to scan the raw text for "X said" patterns on its own.

**What `extract_quote_structure` already computes**:

```python
{
  "cueVerb": "said",
  "speakerSurface": "Jake",
  "speakerNameMatch": "jake",
  "cuePosition": "before"
}
```

For a paragraph like: `Jake said, "What do you mean?" She shrugged.`

**Proposed**: include these pre-computed signals in the state:

```
[Attribution cues in this paragraph]
- Quote 1 (before): "Jake said" → speaker=jake (explicit)
- Quote 3 (after): "she whispered" → surface=She (ambiguous: jake or carmen)
```

This offloads the pattern-matching work from Jev and gives it structured hints.

**Expected gain**: ~2-3pp

### 4. Character metadata in criteria

**Today**: `{"carmen": "Carmen", "jake": "Jake", "None": "narration..."}`

**Proposed**: `{"carmen": "Carmen (female, Runemaiden)", "jake": "Jake (male, Lord Thayne, Protagonist)"}`

This lets Jev use gender to disambiguate pronoun-attributed quotes ("she said" → not Jake). The characters.json already has `gender` and `aliases` fields.

**Expected gain**: ~1-2pp

### 5. Multi-question design

**Today**: one `choice` question.

**Proposed**: add a `noul` question for narration detection:

```json
{
  "speaker": { "type": "choice", ... },
  "is_dialogue": {
    "type": "noul",
    "instructions": "Is the text in <quote> tags actual spoken dialogue (not internal thought or narration)?"
  }
}
```

When `is_dialogue.noul < 0.5`, override to `None` regardless of speaker choice.

**Expected gain**: ~1-2pp (mainly fixing false-positive attributions where the parser tagged narration as dialogue)

### 6. Confidence-based context expansion

**Today**: fixed ±1500 char window for every line.

**Proposed**:
1. First attempt with tight context (±800 chars)
2. If Jev confidence < 0.7, retry with expanded context (±3000 chars + previous/next paragraphs)
3. If still < 0.5, flag for review

This saves tokens on easy cases (most lines) while spending more on hard ones.

**Expected gain**: ~2-3pp on hard cases, ~30% token savings on easy ones

### 7. Roster ground-truth audit (diagnostic, not a model change)

A separate script (`audit_roster_coverage.py`) that:

```
For each chapter:
  For each curated dialogue line in dialogue.json:
    Check: is the true characterId in the scene roster Jev would receive?

Report: roster_coverage%, missed characters, per-chapter breakdown
```

This tells us the *upper bound* of what Jev can achieve. If roster coverage is 85%, max possible accuracy is 85% regardless of model quality.

**Expected value**: diagnostic only, but identifies whether roster or model is the bottleneck

## Prioritized implementation order

| # | Change | Est. effort | Est. gain | Risk |
|---|---|---|---|---|
| 1 | Roster v2 (alias memory + text scan) | Medium | +8-12pp | Low |
| 2 | Richer state formatting | Small | +3-5pp | Low |
| 3 | Character metadata in criteria | Trivial | +1-2pp | None |
| 4 | Roster audit script | Medium | Diagnostic | None |
| 5 | Multi-question (noul for narration) | Small | +1-2pp | Low |
| 6 | Attribution cues in state | Medium | +2-3pp | Medium (complex) |
| 7 | Confidence-based retry | Medium | +2-3pp | Medium (cost) |

**Projected combined gain**: +15-25pp → **88-98% accuracy**

## Quick wins to implement right now

These three are trivial changes that I can implement immediately:

1. **Character metadata in criteria** — append gender and aliases from `characters.json`
2. **Last speaker in state** — prepend "Last speaker: X" before the text
3. **Recent turn history in state** — prepend "Recent speakers: A → B → C" before the text

Estimated combined gain: +5-7pp with ~30 lines of code changed.