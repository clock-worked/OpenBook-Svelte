import type { Character, CharacterConfig, CharacterFile, CharactersJson } from '$lib/types';
import { CHARACTER_GUID_PATTERN } from '$lib/types';
import {
    getBookCharactersPath,
    getCharactersPath,
    getDialoguePath,
    getScriptPath,
    listCharacterFiles,
    readCharacterFile,
    readCharacters,
    readDialogue,
    readScript,
    writeCentralCharacters,
    writeCharacterFile,
    writeCharacters,
} from '$lib/services/fs';
import { normalizeCharacterGender } from '$lib/services/characterGender';
import { normalizeCharacterKey } from '$lib/services/characterDomain';
import { loadChapterCharactersData } from '$lib/services/chapterCharacterRepository';
// Lane 4 (parallel): pinned interface — (root, chapterNames) → V3MigrationResult
// { ok, abortReason?, changedFiles, createdCharacterFiles, retiredLegacy, remap, counts }.
import { migrateCharactersV2ToV3 } from './characterMigration';

export interface ChapterCharacterContext {
    title: string;
    parsed?: boolean;
    scriptPath?: string;
}

// === v3 folder record cache (unknown-field round-trip) ===

/**
 * Per-record cache of raw v3 file documents, keyed by GUID.
 *
 * The TS `Character` type is intentionally narrower than the v3 file format:
 * fields such as `roleLabels` (read by `dialogue_ai_context.py:288`) have no
 * slot on `Character`. The in-memory record is therefore lossy by design, so
 * we keep the raw document per GUID and the write path re-serializes the raw
 * document as the base, overlaying the in-memory display fields on top.
 * Unknown raw fields (including `roleLabels`) round-trip untouched.
 * The cache is disposable (schema doc §GUID spec): it is rebuilt on every
 * folder read and never authoritative.
 */
const rawCharacterFileDocs = new Map<string, Record<string, unknown>>();

function isV3Guid(value: unknown): value is string {
    return typeof value === 'string' && CHARACTER_GUID_PATTERN.test(value.trim());
}

/**
 * Map a raw `characters/<Title>.json` document (v3.0) to an in-memory
 * `Character`. Returns null (and warns) for records that fail the schema
 * validation rules (formatVersion 3.0, valid GUID, non-empty title).
 *
 * `id` is the documented mirror of `guid` (`id = guid`) so existing read
 * sites keep working; the GUID is the identity, the title is display data.
 */
export function mapCharacterFile(raw: unknown): Character | null {
    if (!raw || typeof raw !== 'object') return null;
    const doc = raw as Record<string, any>;
    const guid = typeof doc.guid === 'string' ? doc.guid.trim() : '';
    const title = typeof doc.title === 'string' ? doc.title.trim() : '';

    if (doc.formatVersion !== '3.0' || !CHARACTER_GUID_PATTERN.test(guid) || !title) {
        console.warn('[bookCharacters] Skipping invalid character file record:', {
            formatVersion: doc.formatVersion,
            guid,
            title,
        });
        return null;
    }

    const stats = doc.stats ?? {};
    return {
        guid,
        id: guid, // documented mirror: in v3, id = guid
        name: title,
        gender: normalizeCharacterGender(doc.gender),
        aliases: Array.isArray(doc.aliases) ? doc.aliases.filter((alias: unknown) => typeof alias === 'string') : [],
        descriptors: Array.isArray(doc.descriptors) ? doc.descriptors.filter((descriptor: unknown) => typeof descriptor === 'string') : [],
        race: typeof doc.race === 'string' ? doc.race : undefined,
        color: doc.color ?? null,
        notes: typeof doc.notes === 'string' ? doc.notes : '',
        firstAppearance: typeof doc.firstAppearance === 'string' ? doc.firstAppearance : null,
        stats: {
            totalLines: typeof stats.totalLines === 'number' ? stats.totalLines : 0,
            chapterCount: typeof stats.chapterCount === 'number' ? stats.chapterCount : 0,
        },
        voice: doc.voice ?? null,
        provider: doc.provider ?? null,
        voiceId: doc.voiceId ?? null,
        voiceMeta: doc.voiceMeta ?? null,
        manifestStats: doc.manifestStats ?? null,
    };
}

/**
 * Read the v3 `characters/` folder: list the files, map each through
 * `mapCharacterFile`, and refresh the raw-document cache.
 * Returns null when the folder is absent/empty or yields no valid records.
 */
export async function readCharacterFolder(root: string): Promise<CharactersJson | null> {
    const fileNames = await listCharacterFiles(root);
    if (fileNames.length === 0) return null;

    rawCharacterFileDocs.clear();
    const characters: Character[] = [];
    for (const fileName of fileNames) {
        const raw = await readCharacterFile(root, fileName);
        if (!raw) continue;
        const character = mapCharacterFile(raw);
        if (!character) continue;
        rawCharacterFileDocs.set(character.guid, raw as Record<string, unknown>);
        characters.push(character);
    }

    if (characters.length === 0) return null;
    characters.sort((left, right) => left.name.localeCompare(right.name));
    return { formatVersion: '3.0', characters };
}

// === Filename rules (docs/schema_characters_v3.md §Filename rules) ===

const WINDOWS_RESERVED_NAMES = new Set([
    'CON', 'PRN', 'AUX', 'NUL',
    ...Array.from({ length: 9 }, (_, i) => `COM${i + 1}`),
    ...Array.from({ length: 9 }, (_, i) => `LPT${i + 1}`),
]);

/**
 * `characters/<sanitize(title)>.json` — the filename is a cache of the title,
 * not identity. All lookups go by GUID from file contents, never filenames.
 */
export function sanitizeCharacterFileName(title: string): string {
    let name = String(title ?? '')
        .replace(/[\\/:*?"<>|\u0000-\u001f]/g, '-')
        .replace(/-+/g, '-')
        .trim();
    if (name.length > 80) name = name.slice(0, 80).replace(/-+$/g, '');
    if (name && WINDOWS_RESERVED_NAMES.has(name.toUpperCase())) name = `-${name}`;
    return name || 'character';
}

// === Per-file persistence (replaces the whole-file merge) ===

/**
 * Persist a single character record as `characters/<sanitized title>.json`
 * (v3.0 `CharacterFile` shape). The in-memory record IS the whole record —
 * there is no cross-record merge.
 *
 * Unknown raw fields (e.g. `roleLabels`) are preserved: when the record was
 * loaded from the folder, the cached raw document is used as the serialization
 * base and the display fields are overlaid on top.
 *
 * Records without a valid GUID cannot be written (IN-1: identity is the GUID);
 * return false so callers can surface the failure. New clusters get a minted
 * GUID via the mutation service (Lane 6, `createCharacterFile`).
 */
export async function writeCharacterRecord(root: string, record: Character): Promise<boolean> {
    const guid = record.guid?.trim() ?? '';
    if (!CHARACTER_GUID_PATTERN.test(guid)) {
        console.warn('[bookCharacters] Cannot write character file without a valid GUID:', record.name);
        return false;
    }

    // Surviving slice of the old whole-file merge: gender normalization +
    // array coalescing. (Legacy bookCharacterRepository.ts, pre-v3 merge.)
    const normalized = {
        ...record,
        guid,
        id: guid, // documented mirror
        gender: normalizeCharacterGender(record.gender),
        aliases: Array.isArray(record.aliases) ? record.aliases : [],
        descriptors: Array.isArray(record.descriptors) ? record.descriptors : [],
        notes: typeof record.notes === 'string' ? record.notes : '',
        stats: {
            totalLines: record.stats?.totalLines ?? 0,
            chapterCount: record.stats?.chapterCount ?? 0,
        },
    };

    const base: Partial<CharacterFile> =
        (rawCharacterFileDocs.get(guid) as Partial<CharacterFile> | undefined) ?? {};

    const file: CharacterFile = {
        ...base,
        formatVersion: '3.0',
        guid,
        title: normalized.name,
        gender: normalized.gender,
        aliases: normalized.aliases,
        descriptors: normalized.descriptors,
        race: normalized.race ?? null,
        color: normalized.color ?? null,
        notes: normalized.notes,
        firstAppearance: normalized.firstAppearance ?? null,
        stats: normalized.stats,
        voice: normalized.voice ?? null,
        provider: normalized.provider ?? null,
        voiceId: normalized.voiceId ?? null,
        voiceMeta: normalized.voiceMeta ?? null,
        manifestStats: normalized.manifestStats ?? null,
        updatedAt: new Date().toISOString(),
    };

    return writeCharacterFile(root, `${sanitizeCharacterFileName(normalized.name)}.json`, file);
}

// === Derived stats (dialogue-driven) ===

interface DerivedStatsEntry {
    totalLines: number;
    chapters: Set<string>;
    firstAppearance: string | null;
}

/**
 * Walk each parsed chapter's dialogue (falling back to legacy script.json)
 * and accumulate per-identity line counts / chapter sets. `resolveIdentity`
 * maps a normalized raw identity (GUID in v3, id/alias in v2) to the stable
 * record key, or undefined when the identity is unknown.
 */
async function collectDerivedStats(
    root: string,
    chapterList: ChapterCharacterContext[],
    resolveIdentity: (normalizedKey: string) => string | undefined,
): Promise<Map<string, DerivedStatsEntry>> {
    const derivedStats = new Map<string, DerivedStatsEntry>();

    const recordUsage = (rawIdentity: unknown, chapterTitle: string): void => {
        const key = normalizeCharacterKey(String(rawIdentity || ''));
        if (!key) return;
        const identityKey = resolveIdentity(key);
        if (!identityKey) return;

        const entry = derivedStats.get(identityKey) ?? { totalLines: 0, chapters: new Set<string>(), firstAppearance: null };
        entry.totalLines += 1;
        entry.chapters.add(chapterTitle);
        if (!entry.firstAppearance) entry.firstAppearance = chapterTitle;
        derivedStats.set(identityKey, entry);
    };

    for (const chapter of chapterList) {
        if (chapter.parsed === false) continue;
        try {
            const dialogue = await readDialogue(getDialoguePath(root, chapter.title));
            if (dialogue?.lines) {
                for (const line of dialogue.lines) recordUsage((line as any).characterId, chapter.title);
                continue;
            }

            // Legacy fallback for books that have not yet migrated to dialogue.json.
            const scriptPath: string = chapter.scriptPath ?? getScriptPath(root, chapter.title);
            const script = await readScript(scriptPath);
            for (const line of script?.lines ?? []) recordUsage((line as any).chosenSpeaker, chapter.title);
        } catch {
        }
    }

    return derivedStats;
}

/**
 * v3 folder mode: recompute per-record derived stats with GUID-exact matching
 * (v3 dialogue `characterId` values ARE the GUIDs) and write ONLY the files
 * whose stats changed (write-only-on-change, per-file).
 */
async function syncFolderCharacterStats(
    root: string,
    data: CharactersJson,
    chapterList: ChapterCharacterContext[],
): Promise<CharactersJson | null> {
    const guidLookup = new Map<string, string>();
    for (const character of data.characters) {
        if (character.guid) guidLookup.set(normalizeCharacterKey(character.guid), character.guid);
    }

    const derivedStats = await collectDerivedStats(root, chapterList, (key) => guidLookup.get(key));

    let updated = false;
    const nextCharacters: Character[] = [];
    for (const character of data.characters) {
        const derived = character.guid ? derivedStats.get(character.guid) : undefined;
        const totalLines = derived?.totalLines ?? 0;
        const chapterCount = derived?.chapters.size ?? 0;
        const nextFirstAppearance = character.firstAppearance ?? derived?.firstAppearance ?? null;

        const changed =
            character.stats.totalLines !== totalLines
            || character.stats.chapterCount !== chapterCount
            || character.firstAppearance !== nextFirstAppearance;
        if (!changed) {
            nextCharacters.push(character);
            continue;
        }

        const next: Character = {
            ...character,
            firstAppearance: nextFirstAppearance,
            stats: { totalLines, chapterCount },
        };
        nextCharacters.push(next);

        // Write-only-on-change: rewrite exactly this character's file.
        const written = await writeCharacterRecord(root, next);
        if (written) updated = true;
    }

    return updated ? { ...data, characters: nextCharacters } : null;
}

export async function syncMissingBookCharacterDefaults(
    root: string,
    rawData: CharactersJson,
    chapterList: ChapterCharacterContext[],
): Promise<CharactersJson | null> {
    if (!chapterList.length) return null;

    // Folder mode ⇔ the records carry v3 GUIDs (Tier 1 / post-migration load).
    const isFolderMode = rawData.characters.some((character) => isV3Guid(character.guid));
    if (isFolderMode) {
        return syncFolderCharacterStats(root, rawData, chapterList);
    }

    // Legacy mode: today's characters.json behavior, unchanged.
    const idLookup = new Map<string, string>();
    const nameLookup = new Map<string, string>();
    for (const character of rawData.characters) {
        const characterId = String((character as any).id || '').trim();
        if (!characterId) continue;
        idLookup.set(normalizeCharacterKey(characterId), characterId);
        const key = normalizeCharacterKey((character as any).name);
        if (key) nameLookup.set(key, characterId);
        const aliases = Array.isArray((character as any).aliases) ? (character as any).aliases : [];
        for (const alias of aliases) {
            const aliasKey = normalizeCharacterKey(alias);
            if (aliasKey) nameLookup.set(aliasKey, characterId);
        }
    }

    const derivedStats = await collectDerivedStats(root, chapterList, (key) => idLookup.get(key) ?? nameLookup.get(key));

    let updated = false;
    const next: CharactersJson = {
        ...rawData,
        formatVersion: rawData.formatVersion || '2.0',
        characters: rawData.characters.map((char: any) => {
            const characterId = String(char.id || '').trim();
            const derived = derivedStats.get(characterId);
            const totalLines = derived?.totalLines ?? 0;
            const chapterCount = derived?.chapters.size ?? 0;

            let nextChar = { ...char };
            if (!nextChar.firstAppearance && derived?.firstAppearance) {
                nextChar.firstAppearance = derived.firstAppearance;
                updated = true;
            }

            const stats = { ...(nextChar.stats ?? {}) } as { totalLines?: number; chapterCount?: number };
            let statsUpdated = false;

            if (stats.totalLines !== totalLines) {
                stats.totalLines = totalLines;
                statsUpdated = true;
            }
            if (stats.chapterCount !== chapterCount) {
                stats.chapterCount = chapterCount;
                statsUpdated = true;
            }
            if (statsUpdated) {
                nextChar.stats = stats;
                updated = true;
            }

            if (nextChar.count !== totalLines) {
                nextChar.count = totalLines;
                updated = true;
            }
            if (nextChar.chapterCount !== chapterCount) {
                nextChar.chapterCount = chapterCount;
                updated = true;
            }

            return nextChar;
        }),
    };

    if (!updated) return null;
    await writeCentralCharacters(root, next);
    return next;
}

// === Load cascade (v3 folder → v2 auto-migrate → derive-from-chapters) ===

export async function loadBookCharactersData(
    root: string,
    chapterList: ChapterCharacterContext[],
    onMigrationNotice?: (notice: { ok: boolean; message: string }) => void,
): Promise<CharactersJson> {
    // Tier 1 (v3): the characters/ folder is the source of truth.
    const folderData = await readCharacterFolder(root);
    if (folderData) {
        console.log('[bookCharacters] Loaded v3 character folder:', folderData.characters.length, 'characters');
        const refreshed = await syncMissingBookCharacterDefaults(root, folderData, chapterList);
        return refreshed ?? folderData;
    }

    // Tier 2 (v2): characters.json present, folder absent/empty.
    const legacyPath = getBookCharactersPath(root);
    console.log('[bookCharacters] No v3 folder; checking legacy file:', legacyPath);
    const legacyRaw = await readCharacters(legacyPath);

    if (legacyRaw && Array.isArray(legacyRaw.characters)) {
        // Ruling R9: auto-migrate on open. The migration must never break
        // book opening: any abort/throw falls through to the v2 load below.
        try {
            const result = await migrateCharactersV2ToV3(root, chapterList.map((chapter) => chapter.title));
            if (result?.ok) {
                const migrated = await readCharacterFolder(root);
                if (migrated) {
                    if ((result.unresolvedRefs ?? 0) > 0) {
                        // Not an error: the book migrated; some references just
                        // pointed at names with no character cluster.
                        console.info(`[bookCharacters] v3 migration succeeded with ${result.unresolvedRefs} unresolvable references left in place`);
                    }
                    console.log('[bookCharacters] v3 migration succeeded; loaded folder:', migrated.characters.length, 'characters');
                    const refreshed = await syncMissingBookCharacterDefaults(root, migrated, chapterList);
                    return refreshed ?? migrated;
                }
                console.warn('[bookCharacters] v3 migration reported ok but the folder read returned nothing; falling back to v2');
            } else {
                const reason = result?.abortReason ?? 'unknown reason';
                console.warn('[bookCharacters] v3 migration aborted:', reason);
                onMigrationNotice?.({ ok: false, message: `Auto-migration to the new character format was skipped for this book: ${reason}` });
            }
        } catch (err) {
            console.warn('[bookCharacters] v3 migration threw; continuing with legacy characters.json:', err);
            onMigrationNotice?.({ ok: false, message: `Auto-migration to the new character format failed: ${err instanceof Error ? err.message : String(err)}` });
        }

        // v2 fallback load (today's behavior, unchanged).
        const mapped = mapRawBookCharacters(legacyRaw);
        const refreshed = await syncMissingBookCharacterDefaults(root, legacyRaw, chapterList);
        return refreshed ? mapRawBookCharacters(refreshed) : mapped;
    }

    // Tier 3: fresh book — derive from chapters.
    console.log('[bookCharacters] Fallback: deriving from chapters');
    return deriveCharactersFromChapters(root, chapterList);
}

/**
 * @deprecated The whole-file merge is gone in v3: books with a characters/
 * folder persist one file per record via `writeCharacterRecord`. This shim
 * exists only so the existing store call site (stores/bookCharacters.ts,
 * rewired by Lane 7) keeps compiling and behaving correctly meanwhile:
 * - any record with a v3 GUID → per-file writes (folder mode; root
 *   characters.json is never touched, invariant IN-2)
 * - no GUIDs at all → whole-file characters.json write (today's v2 behavior
 *   for unmigrated books)
 */
export async function persistBookCharactersData(root: string, data: CharactersJson): Promise<void> {
    const folderRecords = data.characters.filter((character) => isV3Guid(character.guid));

    if (folderRecords.length > 0) {
        for (const record of folderRecords) {
            const ok = await writeCharacterRecord(root, record);
            if (!ok) console.warn('[bookCharacters] Failed to write character file:', record.name);
        }
        return;
    }

    const path = getBookCharactersPath(root);
    try {
        await writeCharacters(path, data);
    } catch (err) {
        console.error('Error persisting book characters:', err);
    }
}

// === v2 readers (legacy tolerance) ===

export function mapRawBookCharacters(rawData: CharactersJson): CharactersJson {
    return {
        formatVersion: rawData.formatVersion || '2.0',
        characters: rawData.characters.map((char: any) => {
            const lineCount = char.stats?.totalLines ?? char.count ?? 0;
            return {
                guid: typeof char.guid === 'string' && char.guid ? char.guid : null,
                id: char.id,
                name: char.name,
                gender: normalizeCharacterGender(char.gender),
                aliases: Array.isArray(char.aliases) ? char.aliases : [],
                descriptors: Array.isArray(char.descriptors) ? char.descriptors : [],
                color: char.color ?? null,
                voice: char.voice ?? null,
                provider: char.provider ?? null,
                voiceId: char.voiceId ?? null,
                voiceMeta: char.voiceMeta ?? null,
                manifestStats: char.manifestStats ?? null,
                notes: typeof char.notes === 'string' ? char.notes : '',
                stats: {
                    totalLines: lineCount,
                    chapterCount: char.stats?.chapterCount ?? char.chapterCount ?? 0,
                },
                count: lineCount,
                firstAppearance: char.firstAppearance ?? null,
                chapterCount: char.stats?.chapterCount ?? char.chapterCount ?? 0,
            } as Character;
        }),
    };
}

type DerivedBookCharacter = CharacterConfig & { guid: string | null };

export async function deriveCharactersFromChapters(
    root: string,
    chapterList: ChapterCharacterContext[],
): Promise<CharactersJson> {
    const accMap = new Map<string, DerivedBookCharacter>();

    for (const chapter of chapterList) {
        if (chapter.parsed === false) continue;
        try {
            const chars = await loadChapterCharactersData(root, getCharactersPath(root, chapter.title));
            if (chars && Array.isArray(chars.characters)) {
                for (const character of chars.characters) {
                    const normalized = normalizeCharacterConfig(character as any);
                    const prev = accMap.get(normalized.name);
                    accMap.set(normalized.name, {
                        // Derived records are in-memory only: no GUID until a
                        // cluster file is created for them (silent auto-upsert).
                        guid: null,
                        id: prev?.id ?? normalized.id,
                        name: normalized.name,
                        gender: prev?.gender ?? normalized.gender ?? 'Unknown',
                        aliases: prev?.aliases ?? normalized.aliases ?? [],
                        descriptors: prev?.descriptors ?? normalized.descriptors ?? [],
                        color: prev?.color ?? normalized.color ?? null,
                        voice: prev?.voice ?? normalized.voice ?? null,
                        provider: prev?.provider ?? normalized.provider ?? null,
                        voiceId: prev?.voiceId ?? normalized.voiceId ?? null,
                        voiceMeta: prev?.voiceMeta ?? normalized.voiceMeta ?? null,
                        manifestStats: prev?.manifestStats ?? normalized.manifestStats ?? null,
                        notes: prev?.notes ?? normalized.notes ?? '',
                        stats: prev?.stats ?? normalized.stats ?? { totalLines: 0, chapterCount: 0 },
                        count: prev?.count ?? normalized.count ?? 0,
                        firstAppearance: prev?.firstAppearance ?? normalized.firstAppearance ?? null,
                        chapterCount: prev?.chapterCount ?? normalized.chapterCount ?? 0,
                    });
                }
                continue;
            }

            if (chapter.parsed) {
                const scriptPath: string = chapter.scriptPath ?? getScriptPath(root, chapter.title);
                const script = await readScript(scriptPath);
                if (!script) continue;

                for (const line of script.lines) {
                    const name = (line.chosenSpeaker ?? '').trim();
                    if (!name || accMap.has(name)) continue;

                    accMap.set(name, {
                        guid: null,
                        name,
                        gender: 'Unknown',
                        aliases: [],
                        color: null,
                        voice: null,
                        provider: null,
                        voiceId: null,
                        voiceMeta: null,
                        manifestStats: null,
                        notes: '',
                        stats: {
                            totalLines: 0,
                            chapterCount: 0,
                        },
                        count: 0,
                        firstAppearance: chapter.title,
                        chapterCount: 0,
                    });
                }
            }
        } catch {
        }
    }

    for (const chapter of chapterList) {
        try {
            if (!chapter.parsed) continue;
            const scriptPath: string = chapter.scriptPath ?? getScriptPath(root, chapter.title);
            const script = await readScript(scriptPath);
            if (!script) continue;

            for (const line of script.lines) {
                const name = (line.chosenSpeaker ?? '').trim();
                if (!name) continue;
                const prev = accMap.get(name);
                if (prev) accMap.set(name, { ...prev, count: (prev.count || 0) + 1 });
            }
        } catch {
        }
    }

    return {
        formatVersion: '2.0',
        characters: Array.from(accMap.values()).sort((left, right) => left.name.localeCompare(right.name)) as Character[],
    };
}

function normalizeCharacterConfig(input: CharacterConfig | null | undefined): CharacterConfig {
    return {
        id: input?.id,
        name: input?.name ?? '',
        gender: normalizeCharacterGender((input as any)?.gender),
        aliases: Array.isArray((input as any)?.aliases) ? (input as any).aliases : [],
        descriptors: Array.isArray((input as any)?.descriptors) ? (input as any).descriptors : [],
        color: input?.color ?? null,
        voice: input?.voice ?? null,
        notes: typeof (input as any)?.notes === 'string' ? (input as any).notes : '',
        stats: {
            totalLines: typeof (input as any)?.stats?.totalLines === 'number' ? (input as any).stats.totalLines : (input?.count ?? 0),
            chapterCount: typeof (input as any)?.stats?.chapterCount === 'number' ? (input as any).stats.chapterCount : (input?.chapterCount ?? 0),
        },
        count: input?.count ?? 0,
        provider: input?.provider ?? null,
        voiceId: input?.voiceId ?? null,
        voiceMeta: input?.voiceMeta ?? null,
        manifestStats: input?.manifestStats ?? null,
        firstAppearance: input?.firstAppearance ?? null,
        chapterCount: input?.chapterCount ?? 0,
    };
}
