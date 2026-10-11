import type { Character, CharactersJson } from '$lib/types';
import {
    readCentralCharacters,
    readCharacters,
    readOptionalCharacters,
    writeCharacters,
} from '$lib/services/fs';
// Note: bookCharacterRepository imports loadChapterCharactersData from this
// module. The cycle is safe — both directions are only used at call time
// (inside function bodies), never during module evaluation.
import { readCharacterFolder } from '$lib/services/bookCharacterRepository';

interface ChapterCharacterReference {
    id: string;
}

interface ChapterCharacterReferencesJson {
    formatVersion: string;
    characters: ChapterCharacterReference[];
}

function normalizeLookupKey(value: unknown): string {
    return String(value ?? '').trim().toLowerCase();
}

/**
 * Resolve the book's character set for roster hydration: the v3
 * `characters/` folder first, falling back to the legacy root
 * `characters.json` for unmigrated books.
 */
async function resolveBookCharacters(root: string): Promise<CharactersJson | null> {
    try {
        const folder = await readCharacterFolder(root);
        if (folder && folder.characters.length > 0) return folder;
    } catch (err) {
        console.warn('[chapterCharacters] v3 folder read failed; falling back to characters.json:', err);
    }
    return readCentralCharacters(root);
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
        const idKey = normalizeLookupKey(character.id);
        const nameKey = normalizeLookupKey(character.name);
        if (idKey) byId.set(idKey, character);
        if (nameKey) byName.set(nameKey, character);
        for (const alias of character.aliases ?? []) {
            const aliasKey = normalizeLookupKey(alias);
            if (aliasKey && !byName.has(aliasKey)) byName.set(aliasKey, character);
        }
    }

    return { byId, byName };
}

/**
 * Load chapter-level character references and hydrate them from the book's
 * character set (v3 folder records in v3 books, characters.json otherwise).
 * Legacy chapter records that contain names are resolved to their canonical
 * book character during the same read.
 */
export async function loadChapterCharactersData(
    root: string,
    path: string,
    optional = false,
): Promise<CharactersJson | null> {
    const raw: any = await (optional ? readOptionalCharacters(path) : readCharacters(path));
    if (!raw || !Array.isArray(raw.characters)) return null;

    const central = await resolveBookCharacters(root);
    const { byId, byName } = buildCentralLookups(central);
    const hydrated: Character[] = [];
    const seenIds = new Set<string>();

    for (const entry of raw.characters) {
        const rawId = typeof entry === 'string' ? entry : entry?.id;
        const rawName = typeof entry === 'object' && entry ? entry.name : null;
        // byId first (in v3 the ref id IS the GUID); byName is legacy tolerance.
        const canonical = byId.get(normalizeLookupKey(rawId)) ?? byName.get(normalizeLookupKey(rawName));

        if (canonical) {
            const key = normalizeLookupKey(canonical.id);
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
export async function persistChapterCharactersData(
    root: string,
    path: string,
    data: CharactersJson,
): Promise<boolean> {
    const central = await resolveBookCharacters(root);
    const { byId, byName } = buildCentralLookups(central);
    const references: ChapterCharacterReference[] = [];
    const seenIds = new Set<string>();

    for (const character of data.characters ?? []) {
        const canonical = byId.get(normalizeLookupKey(character?.id))
            ?? byName.get(normalizeLookupKey(character?.name));
        const id = canonical?.id?.trim();

        if (!id) {
            console.warn(
                '[chapterCharacters] Skipping character without a canonical book ID:',
                character?.name || character?.id,
            );
            continue;
        }

        const key = normalizeLookupKey(id);
        if (seenIds.has(key)) continue;
        references.push({ id });
        seenIds.add(key);
    }

    const serialized: ChapterCharacterReferencesJson = {
        formatVersion: data.formatVersion || '2.0',
        characters: references,
    };
    return writeCharacters(path, serialized as unknown as CharactersJson);
}
