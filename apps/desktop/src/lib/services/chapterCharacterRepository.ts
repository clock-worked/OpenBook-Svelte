import type { Character, CharactersJson } from '$lib/types';
import { openBook, type Book } from '$lib/services/bookFormat';
import { normalizeCharacterKey } from '$lib/services/characterIdentity';

interface ChapterCharacterReference {
    id: string;
}

interface ChapterCharacterReferencesJson {
    formatVersion: string;
    characters: ChapterCharacterReference[];
}

/**
 * Lookup tables for roster resolution.
 *
 * `byId` is the primary path: in v3 the roster `id` field carries the
 * character GUID and the in-memory record's `id` is its documented mirror
 * (`id = guid`), so an id-keyed hit is an exact identity match.
 * `byName` (name + aliases) is legacy tolerance for unmigrated v2 books.
 */
function buildCentralLookups(central: CharactersJson | null): {
    byId: Map<string, Character>;
    byName: Map<string, Character>;
} {
    const byId = new Map<string, Character>();
    const byName = new Map<string, Character>();

    for (const character of central?.characters ?? []) {
        const idKey = normalizeCharacterKey(character.id);
        const nameKey = normalizeCharacterKey(character.name);
        if (idKey) byId.set(idKey, character);
        if (nameKey) byName.set(nameKey, character);
        for (const alias of character.aliases ?? []) {
            const aliasKey = normalizeCharacterKey(alias);
            if (aliasKey && !byName.has(aliasKey)) byName.set(aliasKey, character);
        }
    }

    return { byId, byName };
}

/**
 * Load a chapter's roster and hydrate it from the book's character set (v3
 * folder records in v3 books, characters.json otherwise). Legacy chapter
 * records that contain names are resolved to their canonical book character
 * during the same read. A missing roster reads as null: in v3 books the
 * roster is retired, and absent means empty.
 */
export async function loadChapterCharactersData(
    root: string,
    chapterTitle: string,
): Promise<CharactersJson | null> {
    const book = openBook(root);
    const raw: any = await book.read('roster', chapterTitle);
    if (!raw || !Array.isArray(raw.characters)) return null;

    const { byId, byName } = buildCentralLookups(await book.read('characters'));
    const hydrated: Character[] = [];
    const seenIds = new Set<string>();

    for (const entry of raw.characters) {
        const rawId = typeof entry === 'string' ? entry : entry?.id;
        const rawName = typeof entry === 'object' && entry ? entry.name : null;
        // byId first (in v3 the ref id IS the GUID); byName is legacy tolerance.
        const canonical = byId.get(normalizeCharacterKey(rawId)) ?? byName.get(normalizeCharacterKey(rawName));

        if (canonical) {
            const key = normalizeCharacterKey(canonical.id);
            if (!seenIds.has(key)) {
                hydrated.push({ ...canonical });
                seenIds.add(key);
            }
            continue;
        }

        // Keep unmatched legacy entries visible until a corresponding central
        // character exists. New writes only persist canonical ID references.
        if (rawName) hydrated.push(entry as Character);
    }

    return {
        formatVersion: raw.formatVersion || '2.0',
        characters: hydrated,
    };
}

/**
 * Persist a chapter roster as references to canonical book character IDs —
 * in v3 books these are GUIDs (the `id` field name is unchanged; only the
 * content is a GUID per docs/schema_characters_v3.md §Reference formats).
 */
export interface PersistChapterCharactersOptions {
    /**
     * The v3 migration rebuilds chapter rosters as canonical GUID references
     * and is the one flow allowed to write them in a v3 book. Normal editing
     * flows must not (re)create them there (ADR 0003).
     */
    bypassV3Guard?: boolean;
}

export async function persistChapterCharactersData(
    root: string,
    chapterTitle: string,
    data: CharactersJson,
    options: PersistChapterCharactersOptions = {},
): Promise<boolean> {
    // The book format refuses a roster write in a v3 book (ADR 0003, 0021)
    // unless the caller is one of the allowed flows; check before resolving.
    const book: Book = openBook(root);
    if (!options.bypassV3Guard && (await book.version()) === '3.0') {
        console.warn('[chapterCharacters] v3 book: roster write blocked (no bypass)', chapterTitle);
        return false;
    }

    const { byId, byName } = buildCentralLookups(await book.read('characters'));
    const references: ChapterCharacterReference[] = [];
    const seenIds = new Set<string>();

    for (const character of data.characters ?? []) {
        const canonical = byId.get(normalizeCharacterKey(character?.id))
            ?? byName.get(normalizeCharacterKey(character?.name));
        const id = canonical?.id?.trim();

        if (!id) {
            console.warn(
                '[chapterCharacters] Skipping character without a canonical book ID:',
                character?.name || character?.id,
            );
            continue;
        }

        const key = normalizeCharacterKey(id);
        if (seenIds.has(key)) continue;
        references.push({ id });
        seenIds.add(key);
    }

    const serialized: ChapterCharacterReferencesJson = {
        formatVersion: data.formatVersion || '2.0',
        characters: references,
    };
    const ok = await book.write('roster', chapterTitle, serialized as unknown as CharactersJson, {
        allowRetiredInV3: options.bypassV3Guard,
    });
    console.log('[chapterCharacters] roster written:', chapterTitle, `(${references.length} refs), ok=${ok}`);
    return ok;
}
