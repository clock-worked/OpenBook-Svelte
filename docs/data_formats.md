## Data Formats

> **Note:** This document describes the legacy v1.0 format.
>
> - Current dialogue schema: [schema_v3.md](./schema_v3.md)
> - Previous normalized schema reference: [schema_v2.md](./schema_v2.md)

## Current Compatibility Policy

- **Write policy:** current app flows write `dialogue.json` with `formatVersion: "3.0"`.
- **Read policy:** legacy formats are read only where needed for compatibility.
- **Migration policy:** legacy `script.json`/v1-era data should be treated as transitional input, not the canonical output target.

## Legacy v1.0 Format (Deprecated)

### script.json (v1.0)
```json
{
  "chapter": "<Chapter>",
  "sourceFile": "../<Chapter>.txt",
  "lines": [
    { 
      "id": 1, 
      "text": "…", 
      "span": null, 
      "chosenSpeaker": "Narrator",  // v1: character name (not ID)
      "candidates": [], 
      "isConflict": false 
    }
  ],
  "stats": { "numConflicts": 0, "numLines": 0 }
}
```

### characters.json (v1.0)
```json
{ 
  "characters": [ 
    { 
      "name": "Narrator", 
      "color": "#4caf50", 
      "voice": null  // v1: mixed character and voice data
    } 
  ] 
}
```

### metadata.json
```json
{ "complete": false }
```

### audio files
- `{Book}/{Chapter}/{zeroPaddedLine}-{characterName}.wav` (e.g., `0007-Catherine.wav`)

---

## Current v3.0 Format (Write Target)

See **[schema_v3.md](./schema_v3.md)** for complete documentation.

### v3.0 Highlights:

1. **Attribution confidence model:** Adds `line.attribution` with confidence, margin, and risk.
2. **Unknown workflow:** Uses `resolutionStatus` (`auto`, `unknown`, `user_confirmed`) and allows unresolved `characterId: null`.
3. **Alias-cluster feedback:** Persists `sourceAlias` and `sourceCandidates` for user-driven alias learning.

For v2.0 structure details and migration context, see **[schema_v2.md](./schema_v2.md)**.

### Key Changes from v1.0:

1. **Terminology:**
   - `chosenSpeaker` → `characterId` (now uses stable IDs, not names)
   - **Character** = story character with dialogue
   - **Voice** = TTS voice assigned to read character's lines

2. **File Structure:**
   - `script.json` → `dialogue.json` (per chapter)
   - `book.characters.json` → `characters.json` + `voices.json` (centralized)
   - Character metadata centralized at book level
    - Optional per-chapter `*.characters.json` files may exist to preserve manual chapter lists

3. **New Features:**
   - Character IDs (stable references)
   - Gender field for characters
   - Separate voice definitions and assignments
   - Expandable line metadata (emotion, pacing, prefix)
   - Edit tracking (lastEdited, editCount)

### Migration Notes

Historical migration script references may still mention v2.0 naming, but active product direction is:

- preserve legacy reads where required,
- write current `dialogue.json` v3 format.

Legacy migration command (reference only):

```bash
cd py_services
python migrate_to_schema_v2.py /path/to/book --dry-run  # Preview changes
python migrate_to_schema_v2.py /path/to/book            # Apply migration
```

---

## Character vs Voice

**Character** = Story character (e.g., "Catherine", "Hakram", "Narrator")
- Has: name, gender, color, dialogue lines
- Defined in: `characters.json`

**Voice** = TTS voice/reader (e.g., "(F) Zephyr", "(M) Standard-B")
- Has: provider, voice ID, audio characteristics
- Defined in: `voices.json`

**Assignment** = Mapping between character and voice
- Stored in: `voices.json` → `assignments` array

