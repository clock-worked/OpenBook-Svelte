// ============================================================================
// Manual, whole-book migration driver (Settings → "Migrate book data").
//
// Two passes, in order:
//
//   1. Characters — the legacy root store (characters.json v2 or
//      book.characters.json v1) is migrated into the v3.0 characters/ folder
//      by the existing auto-on-open driver (characterMigration.ts). A book
//      that already has the folder is a no-op; an in-flight state (folder AND
//      legacy store) aborts the whole run with the driver's restore guidance.
//
//   2. Dialogue — every chapter's dialogue.json is inspected. A file needs
//      migrating when it is a legacy non-versioned script (chosenSpeaker
//      names, no characterId slot) OR a versioned file that still carries a
//      v2 slug reference the character migration could not resolve (e.g.
//      "warlock" with no cluster file). Each such file is pushed through the
//      SAME normalize → save pipeline the chapter view uses
//      (chapterNormalization.ts / chapterPersistence.ts) so there is exactly
//      one v3.2 writer. Chosen-speaker references that match no character
//      get a new v3 character file (silent auto-upsert, R7) before the save
//      so no line assignment is lost. Unresolved *candidate* names (never
//      chosen on any line — "they", "my-name", …) are not promoted to
//      characters; the writer drops them and they are listed in the report.
//      Chapter rosters (<Ch>.characters.json) are rebuilt from the migrated
//      lines; roster entries that still resolve are kept, stale ones are
//      dropped (they were already invisible on read).
//
// Pre-images of every rewritten dialogue/roster file are copied to
// _backups/dialogue-v3/<UTC ts>/ before the first write.
// ============================================================================

import type { ChapterStatus } from '$lib/stores/bookState';
import type { Character, CharactersJson, DialogueJson, ScriptJson } from '$lib/types';
import { CHARACTER_GUID_PATTERN } from '$lib/types';
import {
    getCharactersPath,
    getDialoguePath,
    getRootDirInfo,
    listCharacterFiles,
    readCharacterFolder,
    readDialogueForChapter,
    readOptionalCharacters,
    readOptionalJsonRelative,
    readTextFile,
    writeCharacters,
    writeDialogue,
} from '$lib/services/fs';
import { migrateCharactersV1ToV3, migrateCharactersV2ToV3 } from '$lib/services/characterMigration';
import { createCentralCharacter } from '$lib/services/characterMutationService';
import { persistChapterCharactersData } from '$lib/services/chapterCharacterRepository';
import { buildIdToNameMap, buildNameToIdMap } from '$lib/stores/characters';
import {
    normalizeAttribution,
    normalizeScriptData,
    slugifyCharacterName,
} from '$lib/components/chapter/tools/shared/chapterNormalization';
import { saveDialogueFromNormalized } from '$lib/components/chapter/tools/shared/chapterPersistence';
import { recomputeStats } from '$lib/components/chapter/tools/shared/toolUtils';
import type { UnifiedLine } from '$lib/components/chapter/tools/types';

export type CharacterMigrationStatus = 'migrated' | 'already-v3' | 'none' | 'aborted';

export interface BookMigrationReport {
    ok: boolean;
    abortReason?: string;
    characters: {
        status: CharacterMigrationStatus;
        message: string;
        createdFiles: number;
        unresolvedRefs: number;
    };
    dialogue: {
        scanned: number;
        migrated: string[];
        skipped: number;
        failed: Array<{ chapter: string; reason: string }>;
        createdCharacters: string[];
        unresolvedNames: string[];
        /** Candidate-only names with no character; dropped from the rewritten candidate lists. */
        droppedCandidateNames: string[];
        rostersRewritten: number;
        staleRosterEntriesDropped: number;
    };
    backupDir: string | null;
}

export interface MigrateBookOptions {
    root: string;
    chapters: ChapterStatus[];
    unknownThreshold: number;
    unknownSpeakerLabel?: string;
    /** Backend fallback used only when the File System Access write fails. */
    apiSave: (relativePath: string, content: unknown) => Promise<Response>;
    getBackendRootAbsolutePath: () => string | null;
    onProgress?: (message: string) => void;
}

const VERSIONED_DIALOGUE = new Set(['2.0', '3.0', '3.1', '3.2']);
const NARRATOR_SENTINEL = 'narrator';
const BACKUP_DIR_PARTS = ['_backups', 'dialogue-v3'] as const;

function backupTimestamp(): string {
    return new Date().toISOString().replace(/[-:]/g, '').replace(/\.\d{3}Z$/, 'Z');
}

function isVersionedDialogue(data: DialogueJson | ScriptJson): data is DialogueJson {
    return 'formatVersion' in data && VERSIONED_DIALOGUE.has(String((data as DialogueJson).formatVersion));
}

/** null, the narrator sentinel, and pattern-valid GUIDs are already v3 references. */
function isV3Reference(value: unknown): boolean {
    if (value == null) return true;
    if (typeof value !== 'string') return false;
    return value === NARRATOR_SENTINEL || CHARACTER_GUID_PATTERN.test(value);
}

/** Every non-v3 reference value in a versioned dialogue file (all four reference sites). */
export function collectStaleDialogueRefs(data: DialogueJson): string[] {
    const stale = new Set<string>();
    const consider = (value: unknown) => {
        if (!isV3Reference(value)) stale.add(String(value));
    };
    for (const line of data.lines ?? []) {
        consider((line as any)?.characterId);
        for (const candidate of (line as any)?.candidates ?? []) consider(candidate?.characterId);
        for (const candidate of (line as any)?.attribution?.candidates ?? []) consider(candidate?.characterId);
    }
    for (const key of Object.keys(data.stats?.characterBreakdown ?? {})) consider(key);
    return [...stale].sort();
}

/** Every non-v3 `id` in a chapter roster. */
function collectStaleRosterRefs(raw: any): string[] {
    const stale: string[] = [];
    for (const entry of raw?.characters ?? []) {
        const id = typeof entry === 'string' ? entry : entry?.id;
        if (id != null && !isV3Reference(id)) stale.push(String(id));
    }
    return stale;
}

/**
 * v2 slug → display title ("unknown-women" → "Unknown Women"). Non-slug
 * values (real names from legacy chosenSpeaker fields) pass through
 * unchanged. The chapter normalizer maps slug-of-title back to the title, so
 * the created character resolves the original reference on the next read.
 */
export function humanizeSlug(value: string): string {
    const trimmed = value.trim();
    if (!/^[a-z0-9]+(?:-[a-z0-9]+)*$/.test(trimmed)) return trimmed;
    return trimmed
        .split('-')
        .map((word) => word.charAt(0).toUpperCase() + word.slice(1))
        .join(' ');
}

function cleanName(name: string | null | undefined, unknownSpeakerLabel: string): string | null {
    const trimmed = String(name ?? '').trim();
    if (!trimmed || trimmed.toLowerCase() === unknownSpeakerLabel.toLowerCase()) return null;
    return trimmed;
}

/** Names chosen as the speaker of at least one line. */
function speakerNames(lines: UnifiedLine[], unknownSpeakerLabel: string): Set<string> {
    const names = new Set<string>();
    for (const line of lines) {
        if (line.isNonSpeaker) continue;
        const name = cleanName(line.characterName, unknownSpeakerLabel);
        if (name) names.add(name);
    }
    return names;
}

/** Names that only ever appear as candidates. */
function candidateOnlyNames(lines: UnifiedLine[], unknownSpeakerLabel: string): Set<string> {
    const speakers = new Set([...speakerNames(lines, unknownSpeakerLabel)].map((name) => name.toLowerCase()));
    const names = new Set<string>();
    for (const line of lines) {
        if (line.isNonSpeaker) continue;
        for (const candidate of line.candidates ?? []) {
            const name = cleanName(candidate?.name, unknownSpeakerLabel);
            if (name && !speakers.has(name.toLowerCase())) names.add(name);
        }
    }
    return names;
}

/** Roster membership: every speaker plus the candidates of conflict lines (mirrors reviewHandlers). */
function rosterNames(lines: UnifiedLine[], unknownSpeakerLabel: string): Set<string> {
    const names = speakerNames(lines, unknownSpeakerLabel);
    for (const line of lines) {
        if (line.isNonSpeaker || !line.isConflict) continue;
        for (const candidate of line.candidates ?? []) {
            const name = cleanName(candidate?.name, unknownSpeakerLabel);
            if (name) names.add(name);
        }
    }
    return names;
}

async function readFolderCharacters(root: string): Promise<Character[]> {
    const folder = await readCharacterFolder(root);
    return folder?.characters ?? [];
}

// === Pass 1: characters ===

async function migrateCharacterStore(
    root: string,
    chapterNames: string[],
    report: BookMigrationReport,
    onProgress?: (message: string) => void,
): Promise<boolean> {
    const hasFolder = (await listCharacterFiles(root)).length > 0;
    const hasV2 = (await readOptionalJsonRelative<any>('characters.json')) != null;
    const hasV1 = !hasV2 && (await readOptionalJsonRelative<any>('book.characters.json')) != null;

    if (!hasV2 && !hasV1) {
        report.characters = hasFolder
            ? { status: 'already-v3', message: 'Characters already use the v3 folder format.', createdFiles: 0, unresolvedRefs: 0 }
            : { status: 'none', message: 'No character store found; characters will be created from dialogue as needed.', createdFiles: 0, unresolvedRefs: 0 };
        return true;
    }

    onProgress?.(`Migrating ${hasV2 ? 'characters.json' : 'book.characters.json'} to the characters/ folder…`);
    const result = hasV2
        ? await migrateCharactersV2ToV3(root, chapterNames)
        : await migrateCharactersV1ToV3(root, chapterNames);

    if (!result.ok) {
        report.characters = {
            status: 'aborted',
            message: result.abortReason ?? 'Character migration aborted for an unknown reason.',
            createdFiles: 0,
            unresolvedRefs: 0,
        };
        return false;
    }

    const created = result.createdCharacterFiles.length;
    report.characters = {
        status: created > 0 || result.retiredLegacy ? 'migrated' : 'already-v3',
        message: created > 0
            ? `Created ${created} character file${created === 1 ? '' : 's'}; ${result.changedFiles.length} file${result.changedFiles.length === 1 ? '' : 's'} changed.`
            : 'Characters already use the v3 folder format.',
        createdFiles: created,
        unresolvedRefs: result.unresolvedRefs ?? 0,
    };
    return true;
}

// === Pass 2: dialogue + rosters ===

interface ChapterPlan {
    chapter: ChapterStatus;
    dialogue: DialogueJson | ScriptJson | null;
    dialogueStale: boolean;
    rosterRaw: any | null;
    rosterStale: string[];
}

async function planChapters(chapters: ChapterStatus[], root: string): Promise<ChapterPlan[]> {
    const plans: ChapterPlan[] = [];
    for (const chapter of chapters) {
        const dialogue = chapter.parsed === false ? null : await readDialogueForChapter(chapter.title);
        const rosterRaw = await readOptionalCharacters(getCharactersPath(root, chapter.title));
        const dialogueStale = dialogue != null && (!isVersionedDialogue(dialogue) || collectStaleDialogueRefs(dialogue).length > 0);
        plans.push({
            chapter,
            dialogue,
            dialogueStale,
            rosterRaw,
            rosterStale: rosterRaw ? collectStaleRosterRefs(rosterRaw) : [],
        });
    }
    return plans;
}

async function normalizeChapter(
    plan: ChapterPlan,
    root: string,
    unknownThreshold: number,
    unknownSpeakerLabel: string,
): Promise<{ lines: UnifiedLine[]; stats: any } | null> {
    const normalized = await normalizeScriptData(plan.dialogue, {
        root,
        unknownThreshold,
        unknownSpeakerLabel,
        readCentralCharacters: async (bookRoot) => ({ characters: await readFolderCharacters(bookRoot) }),
        buildIdToNameMap,
    });
    if (!normalized) return null;
    const result = { lines: normalized.lines, stats: normalized.stats };
    recomputeStats(result);
    return result;
}

/** Create a v3 character file for every chosen-speaker name that does not resolve; names that still fail are reported. */
async function ensureCharactersForNames(
    root: string,
    names: Iterable<string>,
    report: BookMigrationReport,
    onProgress?: (message: string) => void,
): Promise<void> {
    let nameToId = buildNameToIdMap(await readFolderCharacters(root));
    const missing = [...new Set([...names])].filter((name) => !nameToId.has(name.toLowerCase()));
    if (missing.length === 0) return;

    onProgress?.(`Creating ${missing.length} missing character${missing.length === 1 ? '' : 's'}…`);
    for (const name of missing) {
        if (nameToId.has(name.toLowerCase())) continue;
        const title = humanizeSlug(name);
        let created = await createCentralCharacter(root, title, 'Unknown');
        if (!created && title !== name) created = await createCentralCharacter(root, name, 'Unknown');
        // Re-read after each write: a title collision means the name now
        // resolves through an existing file, which is not a failure.
        nameToId = buildNameToIdMap(await readFolderCharacters(root));
        if (created) {
            report.dialogue.createdCharacters.push(created.title);
        } else if (!nameToId.has(name.toLowerCase())) {
            report.dialogue.unresolvedNames.push(name);
        }
    }
}

async function backupPreImages(
    plans: ChapterPlan[],
    root: string,
    report: BookMigrationReport,
): Promise<void> {
    const touched = plans.filter((plan) => plan.dialogueStale || plan.rosterStale.length > 0);
    if (touched.length === 0) return;
    const backupDir = `${BACKUP_DIR_PARTS.join('/')}/${backupTimestamp()}`;
    for (const plan of touched) {
        if (plan.dialogue && plan.dialogueStale) {
            await writeDialogue(`${backupDir}/${getDialoguePath(root, plan.chapter.title)}`, plan.dialogue as DialogueJson);
        }
        if (plan.rosterRaw && (plan.dialogueStale || plan.rosterStale.length > 0)) {
            await writeCharacters(`${backupDir}/${getCharactersPath(root, plan.chapter.title)}`, plan.rosterRaw);
        }
    }
    report.backupDir = backupDir;
}

async function rewriteRoster(
    plan: ChapterPlan,
    root: string,
    lines: UnifiedLine[] | null,
    unknownSpeakerLabel: string,
    folder: Character[],
    report: BookMigrationReport,
): Promise<void> {
    const byGuid = new Map(folder.filter((c) => c.guid).map((c) => [c.guid as string, c] as const));
    const nameToId = buildNameToIdMap(folder);
    // v2 roster ids are slugs of the (then) name; the same slug-of-title
    // inverse the chapter normalizer uses resolves them to the v3 record.
    const slugToGuid = new Map<string, string>();
    for (const character of folder) {
        const slug = slugifyCharacterName(character.name);
        if (slug && character.guid && !slugToGuid.has(slug)) slugToGuid.set(slug, character.guid);
    }
    const next = new Map<string, Character>();

    for (const entry of plan.rosterRaw?.characters ?? []) {
        const id = typeof entry === 'string' ? entry : entry?.id;
        if (typeof id !== 'string' || !id) continue;
        const key = id.toLowerCase();
        const guid = byGuid.has(id) ? id : (nameToId.get(key) ?? slugToGuid.get(key));
        const resolved = guid ? byGuid.get(guid) : undefined;
        if (resolved) next.set(resolved.guid as string, resolved);
        else if (!isV3Reference(id)) report.dialogue.staleRosterEntriesDropped += 1;
    }

    if (lines) {
        for (const name of rosterNames(lines, unknownSpeakerLabel)) {
            const guid = nameToId.get(name.toLowerCase());
            const character = guid ? byGuid.get(guid) : undefined;
            if (character) next.set(character.guid as string, character);
        }
    }

    const existingIds = new Set<string>(
        (plan.rosterRaw?.characters ?? [])
            .map((entry: any) => (typeof entry === 'string' ? entry : entry?.id))
            .filter((id: unknown): id is string => typeof id === 'string'),
    );
    const nextIds = new Set(next.keys());
    const unchanged = existingIds.size === nextIds.size && [...nextIds].every((id) => existingIds.has(id));
    if (unchanged) return;

    const roster: CharactersJson = {
        formatVersion: '3.0',
        characters: [...next.values()].map((character) => ({ ...character, id: character.guid as string })),
    };
    const ok = await persistChapterCharactersData(root, getCharactersPath(root, plan.chapter.title), roster);
    if (ok) report.dialogue.rostersRewritten += 1;
}

async function migrateDialogueFiles(options: MigrateBookOptions, report: BookMigrationReport): Promise<void> {
    const { root, onProgress } = options;
    const unknownSpeakerLabel = options.unknownSpeakerLabel ?? 'Unknown';

    onProgress?.('Scanning chapters…');
    const plans = await planChapters(options.chapters, root);
    report.dialogue.scanned = plans.filter((plan) => plan.dialogue != null).length;

    const stalePlans = plans.filter((plan) => plan.dialogueStale);
    const rosterOnlyPlans = plans.filter((plan) => !plan.dialogueStale && plan.rosterStale.length > 0);
    report.dialogue.skipped = report.dialogue.scanned - stalePlans.length;
    if (stalePlans.length === 0 && rosterOnlyPlans.length === 0) return;

    // Inventory every speaker name first so characters are created once, book-wide.
    const normalizedByChapter = new Map<string, { lines: UnifiedLine[]; stats: any }>();
    const allSpeakers = new Set<string>();
    const allCandidateOnly = new Set<string>();
    for (const plan of stalePlans) {
        const normalized = await normalizeChapter(plan, root, options.unknownThreshold, unknownSpeakerLabel);
        if (!normalized) {
            report.dialogue.failed.push({ chapter: plan.chapter.title, reason: 'Could not normalize dialogue.json' });
            continue;
        }
        normalizedByChapter.set(plan.chapter.title, normalized);
        for (const name of speakerNames(normalized.lines, unknownSpeakerLabel)) allSpeakers.add(name);
        for (const name of candidateOnlyNames(normalized.lines, unknownSpeakerLabel)) allCandidateOnly.add(name);
    }
    await ensureCharactersForNames(root, allSpeakers, report, onProgress);

    const speakerKeys = new Set([...allSpeakers].map((name) => name.toLowerCase()));
    const nameToId = buildNameToIdMap(await readFolderCharacters(root));
    report.dialogue.droppedCandidateNames = [...allCandidateOnly]
        .filter((name) => !speakerKeys.has(name.toLowerCase()) && !nameToId.has(name.toLowerCase()))
        .sort();

    await backupPreImages(plans, root, report);

    // Re-normalize against the expanded folder so newly created titles resolve, then save.
    for (const plan of stalePlans) {
        if (!normalizedByChapter.has(plan.chapter.title)) continue;
        onProgress?.(`Migrating ${plan.chapter.title}/dialogue.json…`);
        try {
            const normalized = await normalizeChapter(plan, root, options.unknownThreshold, unknownSpeakerLabel);
            if (!normalized) throw new Error('Could not normalize dialogue.json');
            const rawText = plan.chapter.path ? await readTextFile(plan.chapter.path) : null;
            const versioned = plan.dialogue && isVersionedDialogue(plan.dialogue) ? plan.dialogue : null;
            let written = false;
            await saveDialogueFromNormalized({
                normalized,
                chapter: { title: plan.chapter.title, path: plan.chapter.path },
                root,
                rawText,
                unknownThreshold: options.unknownThreshold,
                unknownSpeakerLabel,
                // Character creation already happened book-wide above; in a v3 book this bootstrap is a no-op anyway.
                ensureCentralCharactersForNames: async () => {},
                ensureCharacterNameToIdMap: async () => buildNameToIdMap(await readFolderCharacters(root)),
                normalizeAttribution,
                writeDialogue: async (path, data) => {
                    written = await writeDialogue(path, data);
                    return written;
                },
                getRootDirInfo,
                getBackendRootAbsolutePath: options.getBackendRootAbsolutePath,
                apiSave: async (relativePath, content) => {
                    const response = await options.apiSave(relativePath, content);
                    written = written || response.ok;
                    return response;
                },
                reviewed: Boolean(versioned?.reviewed),
                reviewedAt: versioned?.reviewedAt ?? null,
            });
            if (!written) throw new Error('dialogue.json write failed');
            report.dialogue.migrated.push(plan.chapter.title);
            await rewriteRoster(plan, root, normalized.lines, unknownSpeakerLabel, await readFolderCharacters(root), report);
        } catch (error) {
            report.dialogue.failed.push({
                chapter: plan.chapter.title,
                reason: error instanceof Error ? error.message : String(error),
            });
        }
    }

    if (rosterOnlyPlans.length > 0) {
        onProgress?.('Cleaning stale chapter rosters…');
        const folder = await readFolderCharacters(root);
        for (const plan of rosterOnlyPlans) {
            await rewriteRoster(plan, root, null, unknownSpeakerLabel, folder, report);
        }
    }
}

// === Entry point ===

function emptyReport(): BookMigrationReport {
    return {
        ok: false,
        characters: { status: 'none', message: '', createdFiles: 0, unresolvedRefs: 0 },
        dialogue: {
            scanned: 0,
            migrated: [],
            skipped: 0,
            failed: [],
            createdCharacters: [],
            unresolvedNames: [],
            droppedCandidateNames: [],
            rostersRewritten: 0,
            staleRosterEntriesDropped: 0,
        },
        backupDir: null,
    };
}

/**
 * Migrate an open book's characters and dialogue files to the current
 * formats. Never throws: every failure is reported via the result.
 */
export async function migrateBookToV3(options: MigrateBookOptions): Promise<BookMigrationReport> {
    const report = emptyReport();
    try {
        const chapterNames = options.chapters.map((chapter) => chapter.title);
        const charactersOk = await migrateCharacterStore(options.root, chapterNames, report, options.onProgress);
        if (!charactersOk) {
            report.abortReason = report.characters.message;
            return report;
        }
        await migrateDialogueFiles(options, report);
        report.ok = report.dialogue.failed.length === 0;
        if (!report.ok) {
            report.abortReason = `${report.dialogue.failed.length} chapter${report.dialogue.failed.length === 1 ? '' : 's'} failed to migrate.`;
        }
        return report;
    } catch (error) {
        console.warn('[bookMigration] migration threw:', error);
        report.abortReason = `Migration failed: ${error instanceof Error ? error.message : String(error)}`;
        return report;
    }
}
