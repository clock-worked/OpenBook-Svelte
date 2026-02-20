# OpenBook Schema v3.0

## Overview

Schema v3.0 extends v2.0 with structured attribution confidence, unknown-resolution workflow, and source alias/candidate provenance used for alias-cluster learning.

This is a **dialogue format evolution**. `characters.json` and `voices.json` remain v2-compatible structures.

## What Changed from v2.0

1. Added per-line `attribution` object with confidence/risk metadata.
2. Added `resolutionStatus` to distinguish auto/unknown/user-confirmed assignments.
3. Allows unresolved lines to persist with `characterId: null` (instead of defaulting to narrator).
4. Stores parser/source speaker hints for feedback-driven alias clustering.

## dialogue.json (v3.0)

```json
{
  "formatVersion": "3.0",
  "chapterId": "01-Chapter-1",
  "lines": [
    {
      "id": 12,
      "characterId": null,
      "text": "I never asked for this.",
      "span": { "start": 1042, "end": 1066 },
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
        "candidates": [
          { "characterId": "jake-thayne", "name": "Jake Thayne", "confidence": 0.54 },
          { "characterId": "narrator", "name": "Narrator", "confidence": 0.50 }
        ]
      }
    }
  ],
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
- `candidates` (array): richer candidate list for review UI.
  - `characterId` (string|null)
  - `name` (string)
  - `confidence` (0.0-1.0)
  - `reasons` (string[], optional)

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

- Reader supports both `formatVersion: "2.0"` and `"3.0"`.
- Writer now emits `formatVersion: "3.0"` for dialogue.
- Existing v2 dialogue files remain readable and are normalized at runtime.
