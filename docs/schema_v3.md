# OpenBook Schema v3.2

## Overview

Schema v3.2 extends v3.1 with richer attribution provenance for parser output, including gender-cue metadata, parser backend provenance, decision trace details, and candidate collapse semantics.

This is a **dialogue format evolution**. `characters.json` and `voices.json` remain v2-compatible structures.

## What Changed from v3.1

1. Added `contextGender` and `contextGenderCue` so attribution can record nearby pronoun-based gender cues such as `he said` or `she asked`.
2. Added `genderConflict` to flag when the selected speaker conflicts with the detected context gender cue.
3. Added `parserBackend` and `decisionTrace` to preserve how the parser reached the selected candidate.
4. Candidate lists are now expected to be collapsed by canonical `characterId` before persistence so duplicate aliases do not create repeated options.

## Prior v3.1 Changes

1. Added per-line `isReturning` to mark the final `dialogue.json` line emitted from a source paragraph that contains dialogue.

## Prior v3.0 Changes

1. Added per-line `attribution` object with confidence/risk metadata.
2. Added `resolutionStatus` to distinguish auto/unknown/user-confirmed assignments.
3. Allows unresolved lines to persist with `characterId: null` (instead of defaulting to narrator).
4. Stores parser/source speaker hints for feedback-driven alias clustering.

## dialogue.json (v3.2)

```json
{
  "formatVersion": "3.2",
  "chapterId": "01-Chapter-1",
  "lines": [
    {
      "id": 12,
      "characterId": null,
      "text": "I never asked for this.",
      "span": { "start": 1042, "end": 1066 },
      "isReturning": true,
      "metadata": {
        "emotion": null,
        "intensity": 1.0,
        "pacing": null,
        "prefix": null,
        "customTags": {
          "sourceAlias": "the hunter",
          "sourceCandidates": ["jake", "jake thayne"],
          "attribution": {
            "confidence": 0.54,
            "topCandidateConfidence": 0.54,
            "marginToSecond": 0.04,
            "misattributionRisk": 0.71,
            "resolutionStatus": "unknown",
            "thresholdUsed": 0.62,
            "sourceAlias": "the hunter",
            "sourceCandidates": ["jake", "jake thayne"],
            "contextGender": "male",
            "contextGenderCue": "he said",
            "genderConflict": false,
            "parserBackend": "legacy",
            "decisionTrace": {
              "selectedCandidate": "Jake Thayne",
              "selectedReasons": ["legacy_match", "context_gender_match"],
              "signals": {
                "contextGender": "male",
                "contextGenderCue": "he said"
              }
            },
            "candidates": [
              { "characterId": "jake-thayne", "name": "Jake Thayne", "confidence": 0.54 },
              { "characterId": "narrator", "name": "Narrator", "confidence": 0.50 }
            ]
          }
        }
      },
      "candidates": [
        { "characterId": "jake-thayne", "confidence": 0.54 },
        { "characterId": "narrator", "confidence": 0.50 }
      ],
      "isConflict": true,
      "attribution": {
        "confidence": 0.54,
        "topCandidateConfidence": 0.54,
        "marginToSecond": 0.04,
        "misattributionRisk": 0.71,
        "resolutionStatus": "unknown",
        "thresholdUsed": 0.62,
        "sourceAlias": "the hunter",
        "sourceCandidates": ["jake", "jake thayne"],
        "contextGender": "male",
        "contextGenderCue": "he said",
        "genderConflict": false,
        "parserBackend": "legacy",
        "decisionTrace": {
          "selectedCandidate": "Jake Thayne",
          "selectedReasons": ["legacy_match", "context_gender_match"],
          "signals": {
            "contextGender": "male",
            "contextGenderCue": "he said"
          }
        },
        "candidates": [
          { "characterId": "jake-thayne", "name": "Jake Thayne", "confidence": 0.54 },
          { "characterId": "narrator", "name": "Narrator", "confidence": 0.50 }
        ]
      }
    }
  ],
  "reviewed": true,
  "reviewedAt": "2026-09-10T04:02:04.243754+00:00",
  "reviewedLineCount": 128,
  "stats": {
    "totalLines": 128,
    "conflicts": 19,
    "characterBreakdown": {
      "jake-thayne": 44,
      "narrator": 65
    }
  }
}
```

## New `attribution` Fields

- `confidence` (0.0-1.0): Selected attribution confidence.
- `topCandidateConfidence` (0.0-1.0): Confidence of highest-ranked candidate.
- `marginToSecond` (0.0-1.0): Confidence gap between top and second candidate.
- `misattributionRisk` (0.0-1.0): Risk proxy for likely misattribution.
- `resolutionStatus` (enum):
  - `auto`: accepted automatically above threshold
  - `unknown`: below threshold or unresolved
  - `user_confirmed`: explicitly set by user
- `thresholdUsed` (0.0-1.0): active unknown threshold at decision time.
- `sourceAlias` (string|null): upstream source name/alias for the line.
- `sourceCandidates` (string[]): upstream source candidate names.
- `sourceDescriptors` (string[], optional): extra parser/source descriptors preserved for review workflows.
- `contextGender` (string|null): detected pronoun-based gender cue near the line (`male`, `female`, `neutral`).
- `contextGenderCue` (string|null): the cue phrase that produced `contextGender`, such as `he said`.
- `genderConflict` (boolean): `true` when the selected candidate conflicts with the detected context gender cue.
- `parserBackend` (string|null): parser backend that produced the attribution payload.
- `decisionTrace` (object|null): parser scoring trace for inspection and debugging.
  - `selectedCandidate` (string|null)
  - `selectedReasons` (string[])
  - `overrideReason` (string|null, optional)
  - `signals` (object|null)
- `candidates` (array): richer candidate list for review UI.
  - `characterId` (string|null)
  - `name` (string)
  - `confidence` (0.0-1.0)
  - `reasons` (string[], optional)

## New `isReturning` Field

- `isReturning` (boolean): `true` only for the final persisted `lines[]` entry of a source paragraph that contains dialogue.
- When a paragraph is split into dialogue plus narration tail, the narration tail may carry `isReturning: true`.
- Narration-only paragraphs should write `false` for all entries.
- This field is intended for parser and downstream flow decisions that need to know when a spoken exchange has finished within the source paragraph.

## Chapter Review Semantics

- `reviewed = true` means every persisted line assignment has been accepted as gold.
- `reviewedAt` records when the chapter was finalized.
- `reviewedLineCount` records the number of lines covered by that review.
- Marking a chapter reviewed dynamically extracts conservative descriptive references from narration adjacent to assigned dialogue and stores them in each character's optional `descriptors` list.
- Descriptors are soft attribution evidence, not aliases. A descriptor associated with multiple characters is ignored during attribution.
- Any later chapter edit clears reviewed state until the chapter is reviewed again.

## Unknown Resolution Semantics

- `resolutionStatus = "unknown"` means the line should be shown for manual review.
- Unknown lines may be persisted with `characterId: null`.
- When user assigns a speaker manually, status should transition to `user_confirmed`.

## Alias-Cluster Feedback Semantics

- `sourceAlias` and `sourceCandidates` are stored to support alias learning.
- When a user resolves an unknown line, source alias/candidates can be merged into the selected character's alias cluster.

## Threshold Configuration

UI setting path:

- `parserHints.attribution.unknownThreshold`

Behavior:

- If confidence is below threshold, line becomes `unknown` and remains in conflict/review workflow.

## Compatibility Notes

- Reader supports `formatVersion: "2.0"`, `"3.0"`, `"3.1"`, and `"3.2"`.
- Writer now emits `formatVersion: "3.2"` for dialogue.
- Existing v3.1 dialogue files can be upgraded in place by adding the new attribution provenance fields and bumping the version.
- Existing v2 dialogue files remain readable and are normalized at runtime.
- Legacy v1-era inputs are compatibility-read paths only; they are not a current write target.
