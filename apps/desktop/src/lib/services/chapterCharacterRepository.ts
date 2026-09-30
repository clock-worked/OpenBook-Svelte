import type { Character, CharactersJson } from '$lib/types';
import {
    readCentralCharacters,
    readCharacters,
    readOptionalCharacters,
    writeCharacters,
} from '$lib/services/fs';

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
 * Load chapter-level character references and hydrate them from the central
 * characters.json. Legacy chapter records that contain names are resolved to
 * their canonical book character during the same read.
 */
export async function loadChapterCharactersData(
    root: string,
    path: string,
    optional = false,
): Promise<CharactersJson | null> {
    const raw: any = await (optional ? readOptionalCharacters(path) : readCharacters(path));
    if (!raw || !Array.isArray(raw.characters)) return null;

    const central = await readCentralCharacters(root);
    const { byId, byName } = buildCentralLookups(central);
    const hydrated: Character[] = [];
    const seenIds = new Set<string>();

    for (const entry of raw.characters) {
        const rawId = typeof entry === 'string' ? entry : entry?.id;
        const rawName = typeof entry === 'object' && entry ? entry.name : null;
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

/** Persist a chapter roster as references to canonical book character IDs. */
export async function persistChapterCharactersData(
    root: string,
    path: string,
    data: CharactersJson,
): Promise<boolean> {
    const central = await readCentralCharacters(root);
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
