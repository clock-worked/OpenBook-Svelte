# OpenBook Schema v2.0

> **Status:** Dialogue format has advanced to **v3.0**.
>
> - Use [schema_v3.md](./schema_v3.md) for current dialogue schema.
> - This v2 document remains as reference for v2-era structures and migration history.

## Overview

This document describes the new normalized data schema that properly separates:
- **Characters**: Story characters with dialogue lines
- **Voices**: TTS voice options available for assignment
- **Voice Assignments**: Mapping between characters and voices
- **Dialogue**: Lines of text with character assignments and metadata

## File Structure

```
book/
├── book.json                    # Book metadata and settings
├── characters.json              # All character definitions (centralized)
├── voices.json                  # Voice definitions and assignments (centralized)
└── chapters/
    ├── 00-Prologue/
    │   ├── 00-Prologue.txt      # Source text
    │   ├── dialogue.json        # Dialogue lines for this chapter
    │   └── metadata.json        # Chapter metadata
    └── 01-Chapter-1/
        └── ...
```

### Optional Chapter Character List (Compatibility)

Some workflows keep a per-chapter character list to preserve chapter-specific entries
even when they have zero assigned lines. When present, this file lives alongside the
chapter source and dialogue files and uses a simplified character list format.

```
chapters/
  00-Prologue/
    00-Prologue.characters.json
```

Example:

```json
{
  "formatVersion": "2.0",
  "characters": [
    { "name": "New Character", "color": null, "voice": null }
  ]
}
```

This file is optional and does not replace the centralized `characters.json`. It is
used to retain chapter-specific lists in the editor UI.

## Schema Definitions

### characters.json

Centralized definition of all story characters in the book.

```json
{
  "formatVersion": "2.0",
  "characters": [
    {
      "id": "catherine",
      "name": "Catherine",
      "gender": "Female",
      "aliases": ["Cat", "Squire"],
      "color": "#cdb4db",
      "notes": "Protagonist, first-person narrator in some scenes",
      "firstAppearance": "00-Prologue",
      "stats": {
        "totalLines": 1456,
        "chapterCount": 30
      }
    },
    {
      "id": "narrator",
      "name": "Narrator",
      "gender": "Unknown",
      "aliases": [],
      "color": null,
      "notes": "Third-person narration",
      "firstAppearance": "00-Prologue",
      "stats": {
        "totalLines": 3844,
        "chapterCount": 30
      }
    }
  ]
}
```

**Field Definitions:**
- `id` (string, required): Unique identifier for the character (lowercase, no spaces)
- `name` (string, required): Display name as it appears in the story
- `gender` (enum, required): One of `"Male"`, `"Female"`, `"Unknown"`
- `aliases` (string[], optional): Alternative names the character is known by
- `color` (string|null, optional): Hex color for UI highlighting (e.g., `"#cdb4db"`)
- `notes` (string, optional): Description, context, or notes about the character
- `firstAppearance` (string, optional): Chapter ID where character first appears
- `stats` (object, optional): Computed statistics
  - `totalLines` (number): Total dialogue lines across all chapters
  - `chapterCount` (number): Number of chapters character appears in

---

### voices.json

Centralized definition of TTS voices and their assignments to characters.

```json
{
  "formatVersion": "2.0",
  "voices": [
    {
      "id": "elevenlabs-zephyr-001",
      "displayName": "(F) Zephyr",
      "provider": "elevenlabs",
      "providerVoiceId": "ZF6FPAbjXT4488VcRRnw",
      "previewUrl": "https://storage.googleapis.com/...",
      "description": "Clear, confident female voice. Works well for young adult protagonist.",
      "metadata": {
        "gender": "F",
        "ageRange": "young-adult",
        "accent": "neutral",
        "tags": ["clear", "confident", "versatile"]
      }
    },
    {
      "id": "google-en-us-standard-b",
      "displayName": "(M) Standard B",
      "provider": "google-tts",
      "providerVoiceId": "en-US-Standard-B",
      "previewUrl": null,
      "description": null,
      "metadata": {
        "gender": "M",
        "accent": "en-US"
      }
    }
  ],
  "assignments": [
    {
      "characterId": "catherine",
      "voiceId": "elevenlabs-zephyr-001",
      "priority": 1,
      "contextOverrides": []
    },
    {
      "characterId": "narrator",
      "voiceId": "google-en-us-standard-b",
      "priority": 1,
      "contextOverrides": []
    }
  ]
}
```

**Voice Field Definitions:**
- `id` (string, required): Unique identifier for this voice configuration
- `displayName` (string, required): Human-readable name shown in UI
- `provider` (enum, required): TTS provider - `"google-tts"`, `"elevenlabs"`, `"chirp3"`
- `providerVoiceId` (string, required): The voice ID used by the TTS provider
- `previewUrl` (string|null, optional): URL to audio preview of this voice
- `description` (string|null, optional): Notes about the voice, when to use it, characteristics
- `metadata` (object, optional): Additional voice characteristics
  - `gender` (string): "M", "F", or other
  - `ageRange` (string): e.g., "child", "young-adult", "adult", "elderly"
  - `accent` (string): e.g., "neutral", "en-US", "en-GB"
  - `tags` (string[]): Descriptive tags like "clear", "deep", "energetic"

**Assignment Field Definitions:**
- `characterId` (string, required): Reference to character ID
- `voiceId` (string, required): Reference to voice ID
- `priority` (number, optional): Priority level (1 = primary, higher = fallback)
- `contextOverrides` (array, optional): Future feature for emotion-specific voice changes

---

### dialogue.json (per chapter)

Dialogue lines for a single chapter, with character references and extensible metadata.

```json
{
  "formatVersion": "2.0",
  "chapterId": "00-Prologue",
  "sourceFile": "../00-Prologue.txt",
  "lines": [
    {
      "id": 0,
      "characterId": "narrator",
      "text": "In the beginning, there were only the Gods.",
      "span": { "start": 2, "end": 45 },
      "metadata": {
        "emotion": null,
        "intensity": 1.0,
        "pacing": null,
        "prefix": null,
        "customTags": {}
      },
      "candidates": [
        { "characterId": "narrator", "confidence": 1.0, "source": "parser" }
      ],
      "isConflict": false,
      "lastEdited": null,
      "editCount": 0
    },
    {
      "id": 1,
      "characterId": "catherine",
      "text": "This changes everything.",
      "span": { "start": 500, "end": 525 },
      "metadata": {
        "emotion": "surprised",
        "intensity": 0.7,
        "pacing": "normal",
        "prefix": null,
        "customTags": {}
      },
      "candidates": [
        { "characterId": "catherine", "confidence": 0.95, "source": "parser" },
        { "characterId": "hakram", "confidence": 0.05, "source": "parser" }
      ],
      "isConflict": false,
      "lastEdited": "2025-10-31T12:00:00Z",
      "editCount": 2
    }
  ],
  "stats": {
    "totalLines": 142,
    "conflicts": 0,
    "characterBreakdown": {
      "narrator": 120,
      "black": 15,
      "captain": 7
    }
  }
}
```

**Line Field Definitions:**
- `id` (number, required): Unique line number within this chapter (0-indexed)
- `characterId` (string|null, required): Reference to character ID who speaks this line
- `text` (string, required): The dialogue or narration text
- `span` (object|null, optional): Character offsets in source file
  - `start` (number): Starting character position
  - `end` (number): Ending character position
- `metadata` (object, optional): Extensible metadata for TTS and display
  - `emotion` (string|null): Emotion tag (e.g., "neutral", "angry", "sad", "excited")
  - `intensity` (number): Emotion intensity from 0.0 to 1.0
  - `pacing` (string|null): Speaking pace ("slow", "normal", "fast")
  - `prefix` (string|null): Custom prefix like "[shouting]" or "[whispers]"
  - `customTags` (object): Arbitrary key-value pairs for future features
- `candidates` (array, required): AI-suggested character assignments
  - `characterId` (string): Reference to character ID
  - `confidence` (number): Confidence score 0.0 to 1.0
  - `source` (string): Origin of suggestion ("parser", "user", "ai")
- `isConflict` (boolean, required): Whether this line needs manual review
- `lastEdited` (string|null, optional): ISO 8601 timestamp of last edit
- `editCount` (number, optional): Number of times this line has been edited

**Stats Field Definitions:**
- `totalLines` (number): Total number of lines in chapter
- `conflicts` (number): Number of lines marked as conflicts
- `characterBreakdown` (object): Map of characterId → line count

---

### metadata.json (per chapter)

Chapter-level metadata (minimal, most data is in dialogue.json).

```json
{
  "complete": false,
  "parsed": true,
  "lastModified": "2025-10-31T12:00:00Z",
  "wordCount": 4523,
  "audioGenerated": false
}
```

---

### manifest.json (per character, in audio directory)

Tracks generated audio clips for a character across all chapters.

```json
{
  "formatVersion": "2.0",
  "characterId": "black",
  "characterName": "Black",
  "metadata": {
    "totalClips": 8,
    "chapters": ["00-Prologue"],
    "sources": {
      "ElevenLabs": 8
    },
    "voiceIds": {
      "kNS2rxxquHK0xi0lmF1f": 8
    },
    "primaryVoiceId": "kNS2rxxquHK0xi0lmF1f",
    "lastUpdated": "2025-11-01T00:00:00Z"
  },
  "clips": [
    {
      "id": 12,
      "characterId": "black",
      "characterName": "Black",
      "text": "Soon,",
      "audioFile": "12-Black.mp3",
      "chapter": "00-Prologue",
      "sourceFile": "00-Prologue.txt",
      "voiceId": "kNS2rxxquHK0xi0lmF1f",
      "provider": "ElevenLabs",
      "emotion": "serious",
      "metadata": {
        "generatedAt": "2025-10-31T15:30:00Z",
        "duration": 1.2
      }
    }
  ]
}
```

**Metadata Field Definitions:**
- `totalClips` (number): Total number of audio clips generated
- `chapters` (string[]): List of chapters with audio clips
- `sources` (object): Provider → count mapping
- `voiceIds` (object): Voice ID → count mapping
- `primaryVoiceId` (string|null): Most frequently used voice ID
- `lastUpdated` (string): ISO 8601 timestamp of last update

**Clip Field Definitions:**
- `id` (number): Line ID from dialogue.json
- `characterId` (string): Reference to character ID
- `characterName` (string): Display name (for readability)
- `text` (string): The dialogue text
- `audioFile` (string): Filename of the audio file
- `chapter` (string): Chapter ID where this line appears
- `sourceFile` (string): Original source text filename
- `voiceId` (string): TTS voice ID used to generate this clip
- `provider` (string): TTS provider ("ElevenLabs", "Google", "Chirp3", etc.)
- `emotion` (string|null): Emotion tag if specified
- `metadata` (object): Additional clip metadata
  - `generatedAt` (string): When clip was generated
  - `duration` (number): Audio duration in seconds

**Location:**
Audio manifests are stored in the audio directory structure:
```
audio/
└── Book-1/
    └── 00-Prologue/
        ├── Black/
        │   ├── manifest.json
        │   ├── 12-Black.mp3
        │   ├── 14-Black.mp3
        │   └── ...
        └── Catherine/
            └── manifest.json
```

---

## Migration from v1.0

### Key Changes

1. **Terminology fix:**
   - `chosenSpeaker` → `characterId`
   - "Speaker" now refers to TTS voices, not story characters

2. **Centralization:**
   - Character definitions moved from per-chapter to single `characters.json`
   - Voice assignments extracted to `voices.json`

3. **Character IDs:**
   - Characters now have stable IDs (lowercase, no spaces)
   - References use IDs instead of display names

4. **New fields:**
   - Characters: `id`, `gender`, `aliases`, `notes`, `stats`
   - Voices: `id`, `description`, detailed `metadata`
   - Lines: `metadata` object, `lastEdited`, `editCount`

5. **Format version:**
   - All files include `formatVersion: "2.0"` for future migrations

### Migration Script

See `py_services/migrate_to_schema_v2.py` for automated migration tool.

---

## Backward Compatibility

The v2.0 schema is **not backward compatible** with v1.0. All data files must be migrated.

Future versions will detect `formatVersion` field and apply appropriate migrations automatically.

---

## Extensibility

The schema is designed for future expansion:

- **Line metadata:** Add new fields like `volume`, `pitch`, `effects` without schema changes
- **Context overrides:** Voice assignments can specify emotion-specific voices
- **Character relationships:** Can add `relationships` field to characters
- **Scene metadata:** Can add scene boundaries to dialogue files
- **Multi-language:** Can add `language` field and translation mappings

---

## Example Workflow

1. **Parse source text** → Generate initial `dialogue.json` with candidates
2. **Review conflicts** → User assigns correct `characterId` to lines
3. **Add emotion tags** → User annotates lines with emotion metadata
4. **Assign voices** → Map characters to TTS voices in `voices.json`
5. **Generate audio** → TTS service reads dialogue.json + voice assignments
6. **Edit & refine** → Update line text, metadata, tracked in `editCount`

---

## Validation Rules

- All `characterId` references must exist in `characters.json`
- All `voiceId` references must exist in `voices.json`
- Character `id` must match pattern: `^[a-z0-9-]+$` (lowercase, numbers, hyphens only)
- Voice `id` must match pattern: `^[a-z0-9-]+$`
- `gender` must be one of: "Male", "Female", "Unknown"
- `provider` must be one of: "google-tts", "elevenlabs", "chirp3"
- Line `id` must be sequential within a chapter (0, 1, 2, ...)
- Candidate `confidence` must be between 0.0 and 1.0
- Metadata `intensity` must be between 0.0 and 1.0

