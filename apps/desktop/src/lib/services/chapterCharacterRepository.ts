import type { Character, CharactersJson } from '$lib/types';
import {
    readCentralCharacters,
    readCharacterFolder,
    readFileAsJson,
    readOptionalCharacters,
    writeCharacters,
} from '$lib/services/fs';
import { normalizeCharacterKey } from '$lib/services/characterIdentity';

interface ChapterCharacterReference {
    id: string;
}

interface ChapterCharacterReferencesJson {
    formatVersion: string;
    characters: ChapterCharacterReference[];
}

/**
 * The v3 book's character set read from the `characters/` folder, or `null`
 * when the book is not v3 (folder missing/empty, or the read failed).
 * This is the v3 detection predicate shared by the roster read/write paths.
 */
async function readV3BookCharacters(root: string): Promise<CharactersJson | null> {
    try {
        const folder = await readCharacterFolder(root);
        return folder && folder.characters.length > 0 ? folder : null;
    } catch (err) {
        console.warn('[chapterCharacters] v3 folder read failed; treating book as non-v3:', err);
        return null;
    }
}

/**
 * Resolve the book's character set for roster hydration: the v3
 * `characters/` folder first, falling back to the legacy root
 * `characters.json` for unmigrated books.
 *
 * Exported so the parser can consume the same resolved catalog (a resolved
 * catalog, never a raw path) when building its closed-world options.
 */
export async function resolveBookCharacters(root: string): Promise<CharactersJson | null> {
    return (await readV3BookCharacters(root)) ?? readCentralCharacters(root);
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
 * Load chapter-level character references and hydrate them from the book's
 * character set (v3 folder records in v3 books, characters.json otherwise).
 * Legacy chapter records that contain names are resolved to their canonical
 * book character during the same read.
 */
export async function loadChapterCharactersData(
    root: string,
    path: string,
    optional = true,
): Promise<CharactersJson | null> {
    // Optional by default: in v3 books a chapter roster file may legitimately
    // not exist — a missing roster is an empty roster, not an error.
    const raw: any = await (optional ? readOptionalCharacters(path) : readFileAsJson<CharactersJson>(path));
    if (!raw || !Array.isArray(raw.characters)) return null;

    const central = await resolveBookCharacters(root);
    const { byId, byName } = buildCentralLookups(central);
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
    path: string,
    data: CharactersJson,
    options: PersistChapterCharactersOptions = {},
): Promise<boolean> {
    // v3 (ADR 0003 read-legacy-write-current): a populated characters/ folder
    // is the book's live character set; normal editing flows never write
    // chapter roster files in v3 books.
    if (!options.bypassV3Guard && (await readV3BookCharacters(root)) != null) {
        console.warn('[chapterCharacters] v3 guard: roster write blocked (no bypass)', path);
        return false;
    }

    const central = await resolveBookCharacters(root);
    const { byId, byName } = buildCentralLookups(central);
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
    const ok = await writeCharacters(path, serialized as unknown as CharactersJson);
    console.log('[chapterCharacters] roster written:', path, `(${references.length} refs), ok=${ok}`);
    return ok;
}
