# Centralized Character Colors

## Overview

OpenBook now uses a **centralized color management system** where the book-level character store (`book.characters.json`) is the single source of truth for all character colors. This ensures colors remain consistent throughout the application, across all chapters.

## Previous Issues

Before this implementation:

1. Colors were stored at both book-level and chapter-level
2. The `colorForSpeaker()` function only checked chapter-level colors
3. Hash-based default colors were generated on-the-fly (appearing random)
4. Changing colors in the SpeakerPanel didn't always reflect across the app
5. Parser assigned random colors that weren't persisted

## New Architecture

### Color Lookup Priority

The `colorForSpeaker()` function now checks in this order:

1. **Book-level store** (`bookCharacters`) - PRIMARY source
2. Chapter-level store (`characters`) - fallback only
3. Hash-based default color (from character name hash)

### Color Updates

All color updates now go through **book-level only**:

- `SpeakerPanel.setColor()` → only calls `setBookCharacterColor()`
- Chapter-level colors are automatically synced from book-level when loaded
- Parser pulls colors from book-level when creating chapter characters

### Data Flow

```text
┌─────────────────────────────────────────┐
│       book.characters.json              │
│    (Single Source of Truth)             │
│  - Contains all book characters         │
│  - Primary storage for colors/voices    │
└──────────────┬──────────────────────────┘
               │
               ▼
    ┌──────────────────────┐
    │  Color Update Flow   │
    │                      │
    │  User changes color  │
    │         ▼            │
    │  setBookCharacterColor()  │
    │         ▼            │
    │  book.characters.json │
    │         ▼            │
    │  Auto-syncs to UI    │
    └──────────────────────┘
               │
               ▼
┌──────────────────────────────────────────┐
│  chapter/chapter.characters.json         │
│  (Derived from book-level)               │
│  - Synced from book-level on load        │
│  - Book colors take precedence           │
└──────────────────────────────────────────┘
```

## Key Changes

### 1. `characters.ts`

- **`colorForSpeaker()`**: Now checks book-level first
- **Chapter loading**: Always prioritizes book-level colors over chapter-level
- Book colors override chapter colors in all scenarios

### 2. `SpeakerPanel.svelte`

- **`setColor()`**: Only updates book-level (removed chapter-level writes)
- **`createAndApplyNewSpeaker()`**: Adds to book-level first
- Local display updates from book-level changes

### 3. `parser.ts`

- Updates book-level characters **first**
- Creates chapter characters by pulling colors from book-level
- Ensures new characters inherit book-level colors if they exist

### 4. `ChapterView.svelte`

- **`syncCharactersWithUsage()`**: Pulls colors from book-level first
- **`assignSpeakerToLines()`**: Checks book-level when adding new speakers

### 5. `bookCharacters.ts`

Added new utility functions:

#### `syncAllChapterColorsFromBook()`

Synchronizes all chapter-level colors with book-level. Useful for migrating existing books.

```typescript
import { syncAllChapterColorsFromBook } from '$lib/stores/bookCharacters';

const result = await syncAllChapterColorsFromBook();
console.log(`Updated ${result.updated} chapters`);
if (result.errors.length > 0) {
  console.error('Errors:', result.errors);
}
```

#### `ensureAllCharactersHaveColors()`

Ensures all characters have colors by persisting hash-based defaults. This makes colors consistent even for characters without explicit color assignments.

```typescript
import { ensureAllCharactersHaveColors } from '$lib/stores/bookCharacters';

const result = await ensureAllCharactersHaveColors();
console.log(`Assigned colors to ${result.updated} characters`);
```

## Migration Guide

### For Existing Books

If you have existing books with inconsistent colors:

1. **Open your book** in OpenBook
2. **Open the browser console** (F12)
3. **Run the sync command**:

   ```javascript
   // Import the function
   const { syncAllChapterColorsFromBook, ensureAllCharactersHaveColors } = 
     await import('./lib/stores/bookCharacters.js');
   
   // Persist hash-based colors to book-level
   await ensureAllCharactersHaveColors();
   
   // Sync all chapters from book-level
   const result = await syncAllChapterColorsFromBook();
   console.log(`✓ Synced ${result.updated} chapters`);
   ```

### For New Books

No action needed! The parser automatically:

1. Creates book-level characters first
2. Pulls colors from book-level when creating chapters
3. Maintains consistency automatically

## Benefits

1. **Single Source of Truth**: Book-level is authoritative for all colors
2. **Consistent Colors**: Same character = same color everywhere
3. **Persistent Defaults**: Hash-based colors are stored, not regenerated
4. **Easy Updates**: Change once at book-level, reflects everywhere
5. **Parser Integration**: New chapters automatically use book-level colors

## Developer Notes

### When Adding Character Features

Always update book-level first:

```typescript
// ✅ CORRECT
await setBookCharacterColor(name, color);
// Chapter-level automatically syncs

// ❌ WRONG
await writeCharacters(chapterPath, { characters: [...] });
// Book-level won't be updated!
```

### When Reading Colors

Always use `colorForSpeaker()` or check book-level:

```typescript
// ✅ CORRECT
const color = colorForSpeaker(name);
// Checks book-level first

// ❌ WRONG
const color = characters.characters.find(c => c.name === name)?.color;
// Only checks chapter-level!
```

## Testing

To verify the centralized color system:

1. **Set a character color** in one chapter
2. **Switch to another chapter** with the same character
3. **Verify color is consistent** across all chapters
4. **Parse a new chapter** and verify existing characters use book-level colors

## Technical Details

### File Locations

- Book-level: `{bookRoot}/book.characters.json`
- Chapter-level: `{bookRoot}/{chapterName}/{chapterName}.characters.json`

### Store Hierarchy

```typescript
bookCharacters (bookCharacters.ts)
  └─ Single source of truth
     └─ Persisted to book.characters.json

characters (characters.ts)
  └─ Current chapter view
     └─ Synced from bookCharacters on load
     └─ Book colors take precedence
```

### Color Resolution

```typescript
function colorForSpeaker(name) {
  // 1. Check book-level (PRIMARY)
  const bookChar = bookCharacters.find(c => c.name === name);
  if (bookChar?.color) return bookChar.color;
  
  // 2. Check chapter-level (FALLBACK)
  const chapterChar = characters.find(c => c.name === name);
  if (chapterChar?.color) return chapterChar.color;
  
  // 3. Generate hash-based default
  return defaultColors[hashToIndex(name)];
}
```

## Future Enhancements

Potential improvements:

- Auto-sync colors when book.characters.json changes externally
- UI to view/manage all book-level characters
- Bulk color assignment tools
- Color theme presets for books

---

**Last Updated**: October 31, 2025  
**Version**: 1.0.0
