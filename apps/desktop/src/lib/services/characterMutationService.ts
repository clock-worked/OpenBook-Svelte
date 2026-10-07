import type { CharacterFile, Gender } from '$lib/types';
import {
    deleteFile,
    listCharacterFiles,
    readCharacterFile,
    writeCharacterFile,
} from '$lib/services/fs';
import {
    mintCharacterGuid,
    normalizeAliasList,
    remapCharacterGuidInChapterRosters,
    remapCharacterIdInDialogueFiles,
    remapCharacterIdInVoicesAssignments,
    sanitizeCharacterFileName,
    type ChapterRemapTarget,
} from '$lib/services/characterDomain';

// ============================================================================
// v3.0 single-file character mutations (docs/schema_characters_v3.md,
// docs/character_details_alias_design.md).
//
// Layering: this service is op-level orchestration; all record I/O goes
// through the fs.ts folder primitives (listCharacterFiles / readCharacterFile
// / writeCharacterFile / deleteFile). Invariant IN-3: rename / alias /
// descriptor / gender / color / notes / voice each touch exactly ONE file.
// Merge is the only routine multi-file operation (invariant IN-4).
// ============================================================================

/**
 * A character file as loaded from the book's `characters/` folder: the
 * on-disk basename plus the parsed v3.0 record. The folder store
 * (bookCharacterRepository) produces these; every op below reads, modifies,
 * and writes that exact file.
 */
export interface CharacterFileRecord {
    /** Basename inside `characters/` (e.g. `Catherine.json`, or `<GUID>.json` in the case-collision fallback). */
    fileName: string;
    data: CharacterFile;
}

/**
 * Outcome of a v3 mutation op.
 * - `ok: false` + `message` → visible rejection (e.g. title collision, R7/IN-8;
 *   never a silent no-op, never an implicit merge).
 * - `ok: true` + `warning` → the op committed, but a best-effort cleanup step
 *   failed (e.g. a renamed character's old file is left behind; the
 *   duplicate-GUID pair self-heals on the next load — newest `updatedAt` wins).
 */
export interface CharacterOpResult {
    ok: boolean;
    warning?: string;
    message?: string;
}

function nowIso(): string {
    return new Date().toISOString();
}

/** Load the book's `characters/` folder as (fileName, record) pairs. [] pre-migration. */
async function loadCharacterFolderRecords(root: string): Promise<CharacterFileRecord[]> {
    const fileNames = await listCharacterFiles(root);
    const records: CharacterFileRecord[] = [];
    for (const fileName of fileNames) {
        const data: any = await readCharacterFile(root, fileName);
        if (data && typeof data === 'object' && typeof data.guid === 'string' && data.guid.length > 0 && data.title) {
            records.push({ fileName, data: data as CharacterFile });
        }
    }
    return records;
}

/**
 * Single-file read-modify-write core (IN-3): re-read the one character file,
 * hand the fresh record to `transform`, persist the result back to the same
 * file. Returns false (no write) when the file is missing, its GUID no longer
 * matches the in-memory record, or `transform` returns null.
 */
async function updateCharacterFile(
    root: string,
    record: CharacterFileRecord,
    transform: (current: CharacterFile) => CharacterFile | null,
): Promise<boolean> {
    const current: any = await readCharacterFile(root, record.fileName);
    if (!current || typeof current !== 'object') return false;
    if (current.guid !== record.data.guid) return false;
    const next = transform(current as CharacterFile);
    if (!next) return false;
    next.updatedAt = nowIso();
    return writeCharacterFile(root, record.fileName, next);
}

/**
 * Pick the on-disk basename for a title. A case-insensitive filename
 * collision with another character falls back to the GUID name
 * (schema §Filename rules, rule 3).
 */
function pickCharacterFileName(title: string, guid: string, others: CharacterFileRecord[]): string {
    const preferred = sanitizeCharacterFileName(title);
    const collides = others.some((other) => other.fileName.toLowerCase() === preferred.toLowerCase());
    return collides ? `${guid}.json` : preferred;
}

// === Ops ===

/**
 * Rename a character (v3: file rename + `title` update; zero reference
 * fan-out, IN-1/IN-3).
 *
 * (1) Collision check against folder records → visible rejection, NO implicit
 *     merge (the v2 merge-on-conflict behavior is deleted; R7/IN-8).
 * (2) `title = newTitle`; the old title is unshifted into `aliases` (R1/IN-7);
 *     `updatedAt = now`.
 * (3) Write the new file (in place when the sanitized name is unchanged).
 * (4) Read it back; verify the GUID (and title) round-tripped.
 * (5) Delete the old file — NEVER before the new file is verified.
 *     A failed delete returns success-with-warning: the duplicate-GUID pair
 *     self-heals on the next load (newest `updatedAt` wins).
 */
export async function renameCentralCharacter(
    root: string,
    record: CharacterFileRecord,
    newTitle: string,
): Promise<CharacterOpResult> {
    const currentTitle = String(record.data.title || '').trim();
    const nextTitle = String(newTitle || '').trim();
    if (!currentTitle || !nextTitle) {
        return { ok: false, message: 'Cannot rename: the new title is empty.' };
    }
    if (nextTitle === currentTitle) {
        return { ok: false, message: 'No change: the new title matches the current title.' };
    }

    // (1) Collision checks → visible rejection.
    const folder = await loadCharacterFolderRecords(root);
    const others = folder.filter((other) => other.data.guid !== record.data.guid);

    const titleCollision = others.find(
        (other) => String(other.data.title || '').trim().toLowerCase() === nextTitle.toLowerCase(),
    );
    if (titleCollision) {
        return {
            ok: false,
            message: `Cannot rename: a character named “${titleCollision.data.title}” already exists.`,
        };
    }

    const newFileName = sanitizeCharacterFileName(nextTitle);
    const fileNameCollision = others.find((other) => other.fileName.toLowerCase() === newFileName.toLowerCase());
    if (fileNameCollision) {
        return {
            ok: false,
            message: `Cannot rename: the filename for “${nextTitle}” collides with “${fileNameCollision.data.title}”.`,
        };
    }

    // (2) Update the record: new title, old title preserved as an alias.
    const updated: CharacterFile = {
        ...record.data,
        title: nextTitle,
        aliases: normalizeAliasList([currentTitle, ...(record.data.aliases ?? [])]).filter(
            (alias) => alias.toLowerCase() !== nextTitle.toLowerCase(),
        ),
        updatedAt: nowIso(),
    };

    // (3) Write the new file.
    const inPlace = newFileName.toLowerCase() === record.fileName.toLowerCase();
    const written = await writeCharacterFile(root, newFileName, updated);
    if (!written) {
        return { ok: false, message: 'Rename failed: the new character file could not be written. Nothing was changed.' };
    }

    // (4) Read back and verify.
    const readBack: any = await readCharacterFile(root, newFileName);
    if (!readBack || readBack.guid !== record.data.guid || readBack.title !== nextTitle) {
        return { ok: false, message: 'Rename failed: the new character file could not be verified. Nothing was changed.' };
    }

    if (inPlace) return { ok: true };

    // (5) Delete the old file (only after verification above).
    const deleted = await deleteFile(root, `characters/${record.fileName}`);
    if (!deleted) {
        console.warn('[characterMutations] Rename left the old character file behind:', `characters/${record.fileName}`);
        return {
            ok: true,
            warning: 'The old character file could not be deleted; it will be cleaned up on the next load.',
        };
    }

    return { ok: true };
}

/**
 * Ruling R7: the service is kept, the UI is retired. Thin delegate to the
 * rename op — promoting an alias to the title is just a rename (the old
 * title becomes an alias; collisions are rejected visibly).
 */
export function setCentralCharacterPrimaryName(
    root: string,
    record: CharacterFileRecord,
    newPrimaryName: string,
): Promise<CharacterOpResult> {
    return renameCentralCharacter(root, record, newPrimaryName);
}

/**
 * Add an alias: single-file read-modify-write (IN-3). Guards: the alias must
 * not equal the title (case-insensitive), must not already be an alias
 * (no write), and must not be another character's title.
 */
export async function addCentralCharacterAlias(
    root: string,
    record: CharacterFileRecord,
    aliasName: string,
): Promise<boolean> {
    const alias = String(aliasName || '').trim();
    if (!alias) return false;

    const title = String(record.data.title || '').trim();
    if (!title || title.toLowerCase() === alias.toLowerCase()) return false;

    const folder = await loadCharacterFolderRecords(root);
    const collision = folder.find(
        (other) =>
            other.data.guid !== record.data.guid &&
            String(other.data.title || '').trim().toLowerCase() === alias.toLowerCase(),
    );
    if (collision) return false;

    return updateCharacterFile(root, record, (current) => {
        const aliases = Array.isArray(current.aliases) ? current.aliases : [];
        if (aliases.some((existing) => String(existing).toLowerCase() === alias.toLowerCase())) return null;
        const nextAliases = normalizeAliasList([...aliases, alias]).filter(
            (candidate) => candidate.toLowerCase() !== title.toLowerCase(),
        );
        return { ...current, aliases: nextAliases };
    });
}

/**
 * Detach an alias: single-file read-modify-write (IN-3). A GUID never
 * changes on alias detach — the v2 slug re-derivation + alias-ID fan-out is
 * gone.
 */
export async function detachCentralCharacterAlias(
    root: string,
    record: CharacterFileRecord,
    aliasName: string,
): Promise<boolean> {
    const alias = String(aliasName || '').trim();
    if (!alias) return false;

    return updateCharacterFile(root, record, (current) => {
        const aliases = Array.isArray(current.aliases) ? current.aliases : [];
        if (!aliases.some((existing) => String(existing).toLowerCase() === alias.toLowerCase())) return null;
        return {
            ...current,
            aliases: aliases.filter((existing) => String(existing).toLowerCase() !== alias.toLowerCase()),
        };
    });
}

/** Set the descriptor list: single-file read-modify-write (IN-3). */
export async function setCentralCharacterDescriptors(
    root: string,
    record: CharacterFileRecord,
    descriptors: string[],
): Promise<boolean> {
    const next = normalizeAliasList(descriptors);
    return updateCharacterFile(root, record, (current) => ({ ...current, descriptors: next }));
}

/** Set gender: single-file read-modify-write (IN-3). */
export async function setCentralCharacterGender(
    root: string,
    record: CharacterFileRecord,
    gender: Gender,
): Promise<boolean> {
    if (!gender) return false;
    return updateCharacterFile(root, record, (current) => ({ ...current, gender }));
}

/** Set color: single-file read-modify-write (IN-3). */
export async function setCentralCharacterColor(
    root: string,
    record: CharacterFileRecord,
    color: string | null,
): Promise<boolean> {
    return updateCharacterFile(root, record, (current) => ({ ...current, color: color ?? null }));
}

/** Set notes: single-file read-modify-write (IN-3). */
export async function setCentralCharacterNotes(
    root: string,
    record: CharacterFileRecord,
    notes: string,
): Promise<boolean> {
    return updateCharacterFile(root, record, (current) => ({ ...current, notes: String(notes ?? '') }));
}

/** Set voice assignment fields: single-file read-modify-write (IN-3). */
export async function setCentralCharacterVoice(
    root: string,
    record: CharacterFileRecord,
    voice: Partial<Pick<CharacterFile, 'voice' | 'provider' | 'voiceId' | 'voiceMeta'>>,
): Promise<boolean> {
    if (!voice || Object.keys(voice).length === 0) return false;
    return updateCharacterFile(root, record, (current) => ({ ...current, ...voice }));
}

/**
 * Create a character: mint a GUID against the folder's existing GUIDs and
 * write ONE new `characters/<Title>.json` (silent auto-upsert, R7).
 * Returns null on an empty title, a case-insensitive title collision, or a
 * failed write.
 */
export async function createCentralCharacter(
    root: string,
    title: string,
    gender: Gender,
): Promise<CharacterFile | null> {
    const nextTitle = String(title || '').trim();
    if (!nextTitle) return null;

    const folder = await loadCharacterFolderRecords(root);
    const collision = folder.find(
        (other) => String(other.data.title || '').trim().toLowerCase() === nextTitle.toLowerCase(),
    );
    if (collision) return null;

    let guid: string;
    try {
        guid = mintCharacterGuid(folder.map((other) => other.data.guid));
    } catch (error) {
        console.error('[characterMutations] Failed to mint a character GUID', error);
        return null;
    }

    const record: CharacterFile = {
        formatVersion: '3.0',
        guid,
        title: nextTitle,
        gender,
        aliases: [],
        descriptors: [],
        race: null,
        color: null,
        notes: '',
        firstAppearance: null,
        stats: { totalLines: 0, chapterCount: 0 },
        voice: null,
        provider: null,
        voiceId: null,
        voiceMeta: null,
        manifestStats: null,
        roleLabels: [],
        updatedAt: nowIso(),
    };

    const fileName = pickCharacterFileName(nextTitle, guid, folder);
    const written = await writeCharacterFile(root, fileName, record);
    return written ? record : null;
}

/**
 * Merge two characters — the ONE surviving fan-out (IN-4). The target file
 * absorbs the source's aliases INCLUDING the source's title (R1/IN-7) plus
 * the descriptor union; then exact-string GUID remaps across dialogue files,
 * voice assignments, and chapter rosters; LAST the source file is deleted.
 * The survivor keeps its GUID; the absorbed GUID dies with its file.
 */
export async function mergeCentralCharacters(
    root: string,
    remapTargets: ChapterRemapTarget[],
    source: CharacterFileRecord,
    target: CharacterFileRecord,
): Promise<CharacterOpResult> {
    const sourceGuid = String(source.data.guid || '').trim();
    const targetGuid = String(target.data.guid || '').trim();
    if (!sourceGuid || !targetGuid || sourceGuid === targetGuid) {
        return { ok: false, message: 'Cannot merge: source and target must be two different characters.' };
    }

    const chapterTitles = remapTargets
        .map((chapter) => String(chapter?.title || '').trim())
        .filter(Boolean);

    // 1. Target absorbs the source (single-file write; abort if it fails).
    const merged = await updateCharacterFile(root, target, (current) => {
        const title = String(current.title || '').trim();
        const sourceAliases = Array.isArray(source.data.aliases) ? source.data.aliases : [];
        const targetAliases = Array.isArray(current.aliases) ? current.aliases : [];
        const aliases = normalizeAliasList([...targetAliases, source.data.title, ...sourceAliases]).filter(
            (alias) => alias.toLowerCase() !== title.toLowerCase(),
        );

        const targetDescriptors = Array.isArray(current.descriptors) ? current.descriptors : [];
        const sourceDescriptors = Array.isArray(source.data.descriptors) ? source.data.descriptors : [];
        const descriptors = normalizeAliasList([...targetDescriptors, ...sourceDescriptors]);

        const targetStats = current.stats ?? { totalLines: 0, chapterCount: 0 };
        const sourceStats = source.data.stats ?? { totalLines: 0, chapterCount: 0 };

        return {
            ...current,
            aliases,
            descriptors,
            stats: {
                totalLines: Number(targetStats.totalLines ?? 0) + Number(sourceStats.totalLines ?? 0),
                chapterCount: Math.max(Number(targetStats.chapterCount ?? 0), Number(sourceStats.chapterCount ?? 0)),
            },
        };
    });
    if (!merged) {
        return { ok: false, message: 'Merge failed: the target character file could not be updated. Nothing was changed.' };
    }

    // 2. The surviving fan-out: exact-string GUID substitution (IN-5).
    await remapCharacterIdInDialogueFiles(root, remapTargets, sourceGuid, targetGuid);
    await remapCharacterIdInVoicesAssignments(root, sourceGuid, targetGuid);
    await remapCharacterGuidInChapterRosters(root, chapterTitles, sourceGuid, targetGuid);

    // 3. LAST: delete the source file.
    const deleted = await deleteFile(root, `characters/${source.fileName}`);
    if (!deleted) {
        console.warn('[characterMutations] Merge left the source character file behind:', `characters/${source.fileName}`);
        return {
            ok: true,
            warning: 'The merged source character file could not be deleted; it will be cleaned up on the next load.',
        };
    }

    return { ok: true };
}
