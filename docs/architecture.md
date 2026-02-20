# Architecture

> **Schema Version:** v2.0 (see [schema_v2.md](./schema_v2.md) for data format details)

## Tech Stack

- **App shell:** Tauri 2 + SvelteKit (SPA). Desktop file access and native window chrome.
- **Bridge:** Tauri commands spawn Python for parsing and audio generation.
- **Parser:** Python CLI wrapping dialogue parsing with coreference resolution.
- **Data:** Local filesystem under selected root `{Book}/`

## Terminology (v2.0)

- **Character:** Story character with dialogue (e.g., "Catherine", "Narrator")
- **Voice:** TTS voice/reader assigned to a character (e.g., "(F) Zephyr")
- **Dialogue:** Lines of text with character assignments and metadata
- **Assignment:** Mapping between a character and a voice

## Key Flows

### 1. Book Selection & Scanning
- User selects book folder → scan for chapters → display TOC with parse/audio status icons
- Load centralized `characters.json` and `voices.json`

### 2. Chapter Parsing
- Parse chapter source text → generate `dialogue.json` with AI-suggested character assignments
- Characters auto-added to centralized `characters.json` if new
- Mark conflicts where AI confidence is low

### 3. Conflict Resolution
- User reviews dialogue lines in Chapter View
- Assign/reassign characters to lines
- Apply assignments to selections or paragraphs
- Mark chapter complete when all conflicts resolved

### 4. Voice Assignment
- User assigns TTS voices to characters in `voices.json`
- Assignments persist at book level (shared across chapters)

### 5. Audio Generation
- Generate per-line WAV files: `{lineId}-{characterId}.wav`
- Use voice assignments from `voices.json`
- Track generation state per chapter

## Data Architecture

```
book/
├── characters.json          # All story characters (centralized)
├── voices.json              # Voice definitions & assignments (centralized)
├── speaker_blocklist.json   # Parser blocklist
└── chapters/
    ├── 00-Prologue/
    │   ├── 00-Prologue.txt       # Source text
    │   ├── dialogue.json          # Dialogue lines (v2.0)
    │   ├── metadata.json          # Chapter status
    │   └── 00-Prologue_audio/
    │       ├── 0001-catherine.wav
    │       └── generation_state.json
    └── 01-Chapter-1/
        └── ...
```

## Modules

### Stores (`src/lib/stores`)
- **`bookState.ts`**: Book root, chapters list, current chapter
- **`characters.ts`**: Character data, color utilities (v1.0 compat layer)
- **`bookCharacters.ts`**: Book-level character management
- **`selection.ts`**: Text selection state for batch assignments
- **`settings.ts`**: App preferences

### Services (`src/lib/services`)
- **`fs.ts`**: File system operations (read/write dialogue, characters, voices)
- **`parser.ts`**: Bridge to Python parsing service
- **`audio.ts`**: Audio generation coordination
- **`elevenlabs.ts`**: ElevenLabs TTS integration
- **`manifests.ts`**: Audio generation tracking

### Components (`src/lib/components`)
- **`chapter/`**: Chapter view, character panels, dialogue display
- **`common/`**: Reusable UI components (color picker, dropdown, etc.)
- **Layout:** Main page with TOC, chapter view, and character panels

### Tauri Backend (`src-tauri`)
- Rust commands: `pick_folder`, `run_parser`, `gen_audio`
- File system access with security sandboxing

## Edit/Save Flow (Character Assignment)

1. **User Action:**
   - Click highlighted dialogue text
   - Select range and choose character from menu
   - Click "Apply" on character in side panel
   - Use paragraph character chip

2. **State Update:**
   - Update `DialogueLine.characterId` in memory
   - Recompute conflict state
   - Update character breakdown stats

3. **Persistence:**
   - Debounced write to `{Chapter}/dialogue.json`
   - Atomic file write via filesystem service
   - Update centralized `characters.json` if new character added

4. **UI Refresh:**
   - Svelte stores trigger reactive updates
   - Character colors update from centralized definitions
   - Line highlighting reflects new assignments

## Migration from v1.0

See [data_formats.md](./data_formats.md) for migration instructions.

Key changes:
- `script.json` → `dialogue.json` (per chapter)
- `chosenSpeaker` (name) → `characterId` (ID)
- Book-level character definitions
- Separate voice management


