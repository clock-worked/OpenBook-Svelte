# OpenBook Color Architecture - Visual Reference

## System Architecture

```text
┌─────────────────────────────────────────────────────────────────┐
│                         USER INTERACTION                        │
│                                                                 │
│  ┌──────────────┐        ┌──────────────┐      ┌─────────────┐  │
│  │ SpeakerPanel │        │ ChapterView  │      │   Parser    │  │
│  │   (UI)       │        │    (UI)      │      │  (Backend)  │  │
│  └──────┬───────┘        └──────┬───────┘      └──────┬──────┘  │
│         │                       │                     │          │
└─────────┼───────────────────────┼─────────────────────┼──────────┘
          │                       │                     │
          │ setBookCharacterColor │                     │
          │                       │                     │
          ▼                       ▼                     ▼
┌─────────────────────────────────────────────────────────────────┐
│                    BOOK-LEVEL STORE (Single Source of Truth)     │
│  ╔═══════════════════════════════════════════════════════════╗  │
│  ║          bookCharacters Store (bookCharacters.ts)          ║  │
│  ║                                                             ║  │
│  ║  - All book characters                                     ║  │
│  ║  - PRIMARY color storage                                   ║  │
│  ║  - Persisted to: book.characters.json                      ║  │
│  ║  - Authoritative for all chapters                          ║  │
│  ╚═══════════════════════════════════════════════════════════╝  │
└───────────┬─────────────────────────────────────────────────────┘
            │
            │ Auto-syncs on chapter load
            │ Book colors take precedence
            │
            ▼
┌─────────────────────────────────────────────────────────────────┐
│                      CHAPTER-LEVEL STORE                         │
│  ┌─────────────────────────────────────────────────────────┐    │
│  │       characters Store (characters.ts)                  │    │
│  │                                                           │    │
│  │  - Current chapter characters                            │    │
│  │  - Synced from book-level on load                        │    │
│  │  - Book colors override chapter colors                   │    │
│  │  - Persisted to: {chapter}/{chapter}.characters.json     │    │
│  └─────────────────────────────────────────────────────────┘    │
└───────────┬─────────────────────────────────────────────────────┘
            │
            │ Used for display
            │
            ▼
┌─────────────────────────────────────────────────────────────────┐
│                          UI RENDERING                            │
│  ┌──────────────┐  ┌──────────────┐  ┌──────────────┐           │
│  │ SpeakerList  │  │ ParagraphRow │  │ ColorPicker  │           │
│  │              │  │              │  │              │           │
│  │ colorFor...  │  │ colorFor...  │  │ colorFor...  │           │
│  └──────────────┘  └──────────────┘  └──────────────┘           │
└─────────────────────────────────────────────────────────────────┘
```

## Color Lookup Priority Flow

```text
colorForSpeaker("Alice") is called
            │
            ▼
    ┌───────────────┐
    │  1. Check     │
    │  bookCharacters│──── Found with color? ──────► Return color ✓
    └───────┬───────┘
            │ Not found or no color
            ▼
    ┌───────────────┐
    │  2. Check     │
    │  characters   │──── Found with color? ──────► Return color ✓
    │  (chapter)    │
    └───────┬───────┘
            │ Not found or no color
            ▼
    ┌───────────────┐
    │  3. Generate  │
    │  hash-based   │──────────────────────────────► Return color ✓
    │  default      │                                (may be persisted
    └───────────────┘                                 to book-level)
```

## Color Update Flow

```text
User Changes Color
        │
        ▼
┌──────────────────────┐
│  setBookCharacterColor │
│       (name, color)    │
└──────────┬─────────────┘
           │
           ▼
┌──────────────────────────┐
│  Update bookCharacters    │◄─── SINGLE WRITE POINT
│  store in memory          │     (Book-level only)
└──────────┬────────────────┘
           │
           ▼
┌──────────────────────────┐
│  Persist to               │
│  book.characters.json     │
└──────────┬────────────────┘
           │
           ▼
┌──────────────────────────┐
│  Store update triggers    │
│  UI re-render             │
└──────────┬────────────────┘
           │
           ├──► SpeakerPanel updates ✓
           │
           ├──► ChapterView updates ✓
           │
           ├──► All chapter UIs update ✓
           │
           └──► Color now consistent everywhere ✓
```

## Parser Integration Flow

```text
User Parses New Chapter
        │
        ▼
┌──────────────────────────┐
│  Parser extracts          │
│  character names from     │
│  text                     │
└──────────┬────────────────┘
           │
           ▼
┌──────────────────────────┐
│  1. Update book-level     │◄─── BOOK-LEVEL FIRST
│  (add new characters)     │
└──────────┬────────────────┘
           │
           ▼
┌──────────────────────────┐
│  2. Create chapter        │
│  characters.json          │
│  pulling colors from      │◄─── PULL FROM BOOK
│  book-level               │
└──────────┬────────────────┘
           │
           ▼
┌──────────────────────────┐
│  Chapter ready with       │
│  consistent colors ✓      │
└───────────────────────────┘
```

## Data Flow: Setting a Color

### Before (INCORRECT) ❌

```text
User sets color in SpeakerPanel
        │
        ├──► Update chapter.characters.json
        │
        └──► Update book.characters.json
                    │
                    └──► Two writes, sync issues ❌
```

### After (CORRECT) ✅

```text
User sets color in SpeakerPanel
        │
        └──► setBookCharacterColor()
                    │
                    └──► Update book.characters.json ONLY
                                │
                                └──► Auto-syncs to all UIs ✓
```

## File Structure

```text
my-book/
├── book.characters.json          ◄──── SINGLE SOURCE OF TRUTH
│   └── { characters: [
│         { name: "Alice", color: "#FF6B6B", voice: "alloy" },
│         { name: "Bob", color: "#4ECDC4", voice: "echo" }
│       ]}
│
├── chapter1/
│   ├── chapter1.txt
│   ├── chapter1.script.json
│   └── chapter1.characters.json  ◄──── Synced from book-level
│       └── { characters: [
│             { name: "Alice", color: "#FF6B6B", ... }  ◄─┐
│           ]}                                           │
│                                                        │ Same color
├── chapter2/                                            │
│   ├── chapter2.txt                                     │
│   ├── chapter2.script.json                             │
│   └── chapter2.characters.json  ◄──── Synced from book-level
│       └── { characters: [                              │
│             { name: "Alice", color: "#FF6B6B", ... }  ◄─┘
│           ]}
│
└── chapter3/
    └── ...
```

## Component Dependencies

```text
┌─────────────────────────────────────────────────────┐
│                  Component Tree                     │
│                                                     │
│  App                                                │
│   │                                                 │
│   ├─── BookView                                     │
│   │     │                                           │
│   │     ├─── ChapterList                            │
│   │     │                                           │
│   │     └─── ChapterView ◄──────────┐               │
│   │           │                     │               │
│   │           ├─── ParagraphRow     │               │
│   │           │      │              │               │
│   │           │      └─ colorForSpeaker() ───┐      │
│   │           │                     │        │      │
│   │           └─── SpeakerPanel     │        │      │
│   │                  │              │        │      │
│   │                  ├─ SpeakerList │        │      │
│   │                  │   │          │        │      │
│   │                  │   └─ colorForSpeaker()┤      │
│   │                  │              │        │      │
│   │                  └─ setBookCharacterColor()     │
│   │                                 │        │      │
│   └─────────────────────────────────┼────────┼──────┘
│                                     │        │
│                         ┌───────────▼────────▼──────┐
│                         │   bookCharacters Store    │
│                         │  (Single Source of Truth) │
│                         └───────────────────────────┘
└─────────────────────────────────────────────────────┘
```

## Synchronization Utilities

```text
┌──────────────────────────────────────────────────────────┐
│              Utility Functions                           │
│                                                          │
│  syncAllChapterColorsFromBook()                          │
│  ┌────────────────────────────────────────┐              │
│  │ 1. Read book.characters.json           │              │
│  │ 2. For each chapter:                   │              │
│  │    - Read chapter.characters.json      │              │
│  │    - Override colors from book-level   │              │
│  │    - Write back to chapter file        │              │
│  │ 3. Return { updated, errors }          │              │
│  └────────────────────────────────────────┘              │
│                                                          │
│  ensureAllCharactersHaveColors()                         │
│  ┌────────────────────────────────────────┐              │
│  │ 1. Read book.characters.json           │              │
│  │ 2. For each character without color:   │              │
│  │    - Generate hash-based color         │              │
│  │    - Assign to character               │              │
│  │ 3. Write back to book.characters.json  │              │
│  │ 4. Return { updated }                  │              │
│  └────────────────────────────────────────┘              │
└──────────────────────────────────────────────────────────┘
```

## Key Principles

### ✅ DO

- ✅ Update colors via `setBookCharacterColor()`
- ✅ Read colors via `colorForSpeaker()`
- ✅ Trust book-level as single source of truth
- ✅ Let chapter-level sync automatically
- ✅ Use utility functions for bulk operations

### ❌ DON'T

- ❌ Directly modify chapter.characters.json colors
- ❌ Update colors without going through book-level
- ❌ Rely on chapter-level as source of truth
- ❌ Assume chapter colors override book colors
- ❌ Manually sync colors between files

---

**Visual Reference Version**: 1.0.0  
**Last Updated**: October 31, 2025
