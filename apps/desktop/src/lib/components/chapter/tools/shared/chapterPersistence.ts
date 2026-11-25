import type { AudioGenerationRecord, DialogueJson, ScriptJson } from '$lib/types';
import type { UnifiedLine } from '$lib/components/chapter/tools/types';
import { computeReturningFlags, isDialogueSpan } from '$lib/services/dialogueReturning';
import {
    applyDialogueStatsDelta,
    ensureCharacterNameToIdMapCached,
    getScriptPath,
    invalidateCentralCharactersCache,
    readCentralCharacters,
    readDialogueForChapter,
    readOptionalDialogue,
    readTextFile,
    writeDialogue,
    writeDialogueSave,
    writeScript,
    type WriteOutcome,
} from '$lib/services/fs';
import { normalizeAttribution, normalizeScriptData } from './chapterNormalization';
import { alignChapterLineSpans } from './chapterPresentation';

/**
 * The dialogue.json format versions read as a versioned (DialogueJson)
 * document; anything else is a legacy script. This module owns the list:
 * fs (readDialogueForChapter) and the book migration import it.
 */
export const DIALOGUE_FORMAT_VERSIONS: readonly string[] = ['2.0', '3.0', '3.1', '3.2'];

export function isVersionedDialogue(data: unknown): data is DialogueJson {
    return (
        !!data
        && typeof data === 'object'
        && 'formatVersion' in data
        && DIALOGUE_FORMAT_VERSIONS.includes(String((data as DialogueJson).formatVersion))
    );
}

type ChapterLike = {
    title: string;
    path?: string;
    parsed?: boolean;
};

type NormalizedScriptPayload = {
    lines: UnifiedLine[];
    stats: any;
    hadAliasChanges?: boolean;
};

function applyReturningFlags(lines: UnifiedLine[], rawText: string | null): UnifiedLine[] {
    if (!lines.length) return lines;
    if (!rawText) {
        return lines.map((line) => ({
            ...line,
            isReturning: line.isReturning || false,
        }));
    }

    const returningFlags = computeReturningFlags(lines, rawText, (line) => {
        if (line.span) {
            return isDialogueSpan(rawText, line.span.start);
        }
        const normalizedName = String(line.characterName || '').trim().toLowerCase();
        return normalizedName.length > 0 && normalizedName !== 'narrator';
    });

    return lines.map((line, index) => ({
        ...line,
        isReturning: returningFlags[index],
    }));
}

/**
 * Load a chapter (the one owner of chapter load): the dialogue document (or
 * legacy script) normalized against the book's characters, the chapter text,
 * spans aligned to the text, returning flags, and the reviewed state.
 */
export async function loadChapterContent(params: {
    chapter: ChapterLike;
    root: string;
    unknownThreshold: number;
}): Promise<{
    isV2Format: boolean;
    normalized: NormalizedScriptPayload | null;
    currentScript: ScriptJson | null;
    rawText: string | null;
    reviewed: boolean;
    reviewedAt: string | null;
}> {
    const { chapter, root } = params;

    const dialogue = chapter.parsed === false
        ? null
        : await readDialogueForChapter(chapter.title);
    let isV2Format = false;
    let normalized: NormalizedScriptPayload | null = null;
    let currentScript: ScriptJson | null = null;

    if (dialogue) {
        isV2Format = isVersionedDialogue(dialogue);

        // Dynamic import: keeps the characters store graph out of this
        // module's static imports (fs imports the version list from here).
        const { buildIdToNameMap } = await import('$lib/stores/characters');
        normalized = await normalizeScriptData(dialogue, {
            root,
            unknownThreshold: params.unknownThreshold,
            readCentralCharacters,
            buildIdToNameMap,
        });
    }

    let rawText: string | null = null;
    if (chapter.path) {
        rawText = await readTextFile(chapter.path);
    }

    if (normalized) {
        const alignedLines = rawText ? alignChapterLineSpans(normalized.lines, rawText) : normalized.lines;
        const normalizedWithReturning = applyReturningFlags(alignedLines, rawText);
        normalized = {
            ...normalized,
            lines: normalizedWithReturning,
        };
        currentScript = {
            chapter: chapter.title,
            sourceFile: chapter.path || '',
            lines: normalizedWithReturning.map((line) => ({
                id: line.id,
                text: line.text,
                span: line.span,
                chosenSpeaker: line.characterName,
                candidates: line.candidates,
                isConflict: line.isConflict,
                isNonSpeaker: line.isNonSpeaker,
                isReturning: line.isReturning,
                attribution: line.attribution,
            })),
            stats: normalized.stats,
        };
    }

    return {
        isV2Format,
        normalized,
        currentScript,
        rawText,
        reviewed: Boolean((dialogue as DialogueJson | null)?.reviewed),
        reviewedAt: (dialogue as DialogueJson | null)?.reviewedAt ?? null,
    };
}

/**
 * Save a chapter's dialogue (the one owner of chapter save). Callers supply
 * only what varies; normalization, identity resolution, the transport and
 * the stats step are internal. Steps, in order: returning flags → disk line
 * metadata merge → name→id resolution (fs's cached resolver, coherent with
 * every character-data write) → payload → dialogue save write → stats step.
 */
export async function saveDialogueFromNormalized(params: {
    normalized: { lines: UnifiedLine[]; stats: any };
    chapter: ChapterLike;
    root: string;
    rawText: string | null;
    unknownThreshold: number;
    unknownSpeakerLabel: string;
    reviewed?: boolean;
    reviewedAt?: string | null;
}): Promise<boolean> {
    const scr = params.normalized;
    const ch = params.chapter;
    const linesWithReturning = applyReturningFlags(scr.lines, params.rawText);

    // Pull per-line generator records and read-as texts that exist on disk
    // but not in memory (e.g. a line generated after this chapter view was
    // loaded) so a review-save can't wipe them.
    const diskLineMetadata = await readDiskLineMetadata(ch.title);

    console.log('[ChapterView] Saving changes...', {
        format: 'v3.2',
        numLines: linesWithReturning.length,
        chapter: ch.title,
    });

    const nameToIdMap = await ensureCharacterNameToIdMapCached(params.root);

    const characterBreakdown: Record<string, number> = {};
    for (const line of linesWithReturning) {
        if (!line.characterName || line.characterName.trim().toLowerCase() === 'unknown') continue;
        const charId = nameToIdMap.get(line.characterName.toLowerCase());
        if (charId) {
            characterBreakdown[charId] = (characterBreakdown[charId] || 0) + 1;
        } else {
            console.warn(
                `[ChapterView] Character "${line.characterName}" not found in the book roster, skipping from breakdown`
            );
        }
    }

    const dialoguePayload: DialogueJson = {
        formatVersion: '3.2',
        chapterId: ch.title,
        reviewed: params.reviewed ?? false,
        reviewedAt: params.reviewed ? (params.reviewedAt ?? null) : null,
        reviewedLineCount: params.reviewed ? linesWithReturning.length : undefined,
        lines: linesWithReturning.map((line) => {
            const isNonSpeaker = line.isNonSpeaker === true;
            const attribution = normalizeAttribution(
                line.attribution || null,
                (line.candidates || []).map((candidate) => ({
                    name: candidate.name,
                    characterId: candidate.name ? (nameToIdMap.get(candidate.name.toLowerCase()) || null) : null,
                    confidence: candidate.confidence,
                    reasons: candidate.reasons,
                })),
                isNonSpeaker ? null : line.characterName,
                params.unknownThreshold,
                null,
                [],
                params.unknownSpeakerLabel
            );

            const nameLower = String(line.characterName || '').trim().toLowerCase();
            let characterId: string | null = null;
            if (isNonSpeaker || attribution.resolutionStatus === 'unknown' || nameLower === params.unknownSpeakerLabel.toLowerCase()) {
                characterId = null;
            } else if (line.characterName) {
                characterId = nameToIdMap.get(line.characterName.toLowerCase()) || null;
                if (!characterId) {
                    console.warn(
                        `[ChapterView] Character "${line.characterName}" not found in characters.json for line ${line.id}, defaulting to narrator`
                    );
                    characterId = 'narrator';
                }
            }

            return {
                id: line.id,
                characterId,
                text: line.text,
                span: line.span || { start: 0, end: 0 },
                metadata: {
                    emotion: null,
                    intensity: 1.0,
                    pacing: null,
                    prefix: null,
                    customTags: {
                        attribution,
                        sourceAlias: attribution.sourceAlias,
                        sourceCandidates: attribution.sourceCandidates,
                    },
                },
                candidates: isNonSpeaker ? [] : Array.from(
                    (line.candidates || []).reduce((acc, candidate) => {
                        const candidateId = candidate.name ? (nameToIdMap.get(candidate.name.toLowerCase()) || null) : null;
                        if (candidate.name && !candidateId) {
                            console.warn(`[ChapterView] Candidate "${candidate.name}" not found in characters.json`);
                            return acc;
                        }
                        if (!candidateId) return acc;
                        const existing = acc.get(candidateId);
                        if (!existing || candidate.confidence > existing.confidence) {
                            acc.set(candidateId, {
                                characterId: candidateId,
                                confidence: candidate.confidence,
                            });
                        }
                        return acc;
                    }, new Map<string, { characterId: string; confidence: number }>()).values()
                ).sort((left, right) => right.confidence - left.confidence),
                isConflict: isNonSpeaker ? false : line.isConflict || attribution.resolutionStatus === 'unknown',
                isNonSpeaker,
                isReturning: line.isReturning,
                attribution,
                audioGeneration: line.audioGeneration ?? diskLineMetadata.audioGeneration.get(line.id) ?? null,
                readAs: line.readAs ?? diskLineMetadata.readAs.get(line.id) ?? null,
            };
        }),
        stats: {
            totalLines: linesWithReturning.length,
            conflicts: scr.stats?.numConflicts || 0,
            characterBreakdown,
        },
    };

    const relativePath = `${ch.title}/dialogue.json`;
    console.log('[ChapterView] Writing dialogue to relative path:', relativePath);

    const saved = await writeChapterDialogue(relativePath, dialoguePayload);
    if (saved) {
        console.log('[ChapterView] ✓ Successfully saved dialogue.json');
    } else {
        console.error('[ChapterView] ✗ Failed to save dialogue.json');
    }
    return saved;
}

/** A versioned dialogue.json whose lines can be edited in place. */
function isEditableDialogue(doc: unknown): doc is DialogueJson {
    return isVersionedDialogue(doc) && Array.isArray(doc.lines);
}

/**
 * Persist speaker edits made on the loader's v1 `currentScript` view (set
 * speaker, merge/rename, remove character, reviewed lines).
 *
 * When the chapter has a versioned dialogue.json, the edits are applied to
 * that document in place — `characterId`, `isConflict`, `isNonSpeaker`,
 * `attribution` per line, plus the stats — so everything the v1 view does
 * not carry (formatVersion, metadata, audioGeneration, readAs, …) survives
 * (ADR 0003: write the current schema only). Only a chapter with no
 * versioned dialogue.json gets the v1 script written to `scriptPath`.
 *
 * `nameToId` maps lower-cased names and aliases to character ids. A line
 * whose new speaker name it cannot resolve keeps its current characterId;
 * a line carrying an explicit `characterId` (reviewed lines) uses that.
 */
export async function writeChapterScriptEdits(params: {
    chapter: { title: string; scriptPath?: string };
    script: ScriptJson;
    nameToId: Map<string, string>;
    unknownSpeakerLabel?: string;
}): Promise<boolean> {
    const { chapter, script, nameToId } = params;
    const unknownKey = (params.unknownSpeakerLabel ?? 'Unknown').trim().toLowerCase();
    const dialoguePath = `${chapter.title}/dialogue.json`;
    const doc = await readOptionalDialogue(dialoguePath);

    if (!isEditableDialogue(doc)) {
        return writeScript(chapter.scriptPath ?? getScriptPath(chapter.title), script);
    }

    const edits = new Map(script.lines.map((line) => [line.id, line] as const));
    for (const line of doc.lines) {
        const edit = edits.get(line.id) as (ScriptJson['lines'][number] & { characterId?: string | null }) | undefined;
        if (!edit) continue;
        const isNonSpeaker = edit.isNonSpeaker === true;
        const name = String(edit.chosenSpeaker ?? '').trim();
        const nameKey = name.toLowerCase();

        if (isNonSpeaker) {
            line.characterId = null;
            line.candidates = [];
        } else if (Object.prototype.hasOwnProperty.call(edit, 'characterId')) {
            line.characterId = edit.characterId ?? null;
        } else if (!name || nameKey === unknownKey) {
            line.characterId = null;
        } else {
            const id = nameToId.get(nameKey);
            if (id) {
                line.characterId = id;
            } else {
                console.warn(`[ChapterPersistence] Speaker "${name}" not in the book roster; line ${line.id} keeps its characterId`);
            }
        }
        line.isNonSpeaker = isNonSpeaker;
        line.isConflict = isNonSpeaker ? false : !!edit.isConflict;
        if (edit.attribution) {
            line.attribution = edit.attribution as any;
            const customTags = line.metadata?.customTags as Record<string, unknown> | undefined;
            if (customTags && 'attribution' in customTags) customTags.attribution = edit.attribution;
        }
    }

    recomputeDialogueStats(doc);
    return writeDialogue(dialoguePath, doc);
}

function recomputeDialogueStats(doc: DialogueJson): void {
    const characterBreakdown: Record<string, number> = {};
    let conflicts = 0;
    for (const line of doc.lines) {
        if (line.isConflict) conflicts += 1;
        if (line.isNonSpeaker || !line.characterId) continue;
        characterBreakdown[line.characterId] = (characterBreakdown[line.characterId] || 0) + 1;
    }
    doc.stats = { ...doc.stats, totalLines: doc.lines.length, conflicts, characterBreakdown };
}

/**
 * Count the lines of a chapter's versioned dialogue.json spoken by
 * `characterId`. Returns `null` when the chapter has no versioned
 * dialogue.json (the caller falls back to the legacy v1 script).
 */
export async function countCharacterIdInDialogue(
    chapterTitle: string,
    characterId: string
): Promise<number | null> {
    const doc = await readOptionalDialogue(`${chapterTitle}/dialogue.json`);
    if (!isEditableDialogue(doc)) return null;
    let total = 0;
    for (const line of doc.lines) {
        if (!line.isNonSpeaker && line.characterId === characterId) total += 1;
    }
    return total;
}

/**
 * Book-wide character reassign for one chapter: every line of the versioned
 * dialogue.json whose `characterId` is `oldId` moves to `newId` (`null` =
 * Unknown, flagged as a conflict for review). Candidate ids (the line's and
 * its attribution's) are remapped the same way — merged into an existing
 * `newId` candidate at the higher confidence, or dropped when `newId` is
 * null. Everything else on the document (metadata, audioGeneration, readAs,
 * review state, …) is kept (ADR 0003: write the current schema only).
 *
 * Returns `null` when the chapter has no versioned dialogue.json (the caller
 * keeps the legacy v1 path), otherwise how many lines were reassigned; the
 * file is only written when that is > 0.
 */
export async function reassignCharacterIdInDialogue(
    chapterTitle: string,
    oldId: string,
    newId: string | null
): Promise<number | null> {
    if (!oldId || oldId === newId) return null;
    const dialoguePath = `${chapterTitle}/dialogue.json`;
    const doc = await readOptionalDialogue(dialoguePath);
    if (!isEditableDialogue(doc)) return null;

    let reassigned = 0;
    let touched = false;
    for (const line of doc.lines) {
        if (Array.isArray(line.candidates) && line.candidates.some((c) => c?.characterId === oldId)) {
            line.candidates = remapLineCandidates(line.candidates, oldId, newId);
            touched = true;
        }

        const attributionCandidates = line.attribution?.candidates;
        if (Array.isArray(attributionCandidates)) {
            for (const candidate of attributionCandidates) {
                if (candidate?.characterId === oldId) {
                    candidate.characterId = newId;
                    touched = true;
                }
            }
        }

        if (line.characterId === oldId) {
            line.characterId = newId;
            // Same rule as the v1 path: Unknown needs review; a named
            // replacement is a conflict only when the line had competing
            // candidates and none of them is the replacement.
            line.isConflict = newId === null
                || (line.candidates.length > 1 && !line.candidates.some((c) => c.characterId === newId));
            reassigned += 1;
            touched = true;
        }
    }

    if (!touched) return 0;
    recomputeDialogueStats(doc);
    const saved = await writeDialogue(dialoguePath, doc);
    if (!saved) throw new Error(`Failed to save reassigned dialogue for ${chapterTitle}`);
    return reassigned;
}

function remapLineCandidates(
    candidates: DialogueJson['lines'][number]['candidates'],
    oldId: string,
    newId: string | null
): DialogueJson['lines'][number]['candidates'] {
    const byId = new Map<string, DialogueJson['lines'][number]['candidates'][number]>();
    for (const candidate of candidates) {
        if (!candidate) continue;
        const id = candidate.characterId === oldId ? newId : candidate.characterId;
        if (!id) continue;
        const existing = byId.get(id);
        if (!existing || candidate.confidence > existing.confidence) {
            byId.set(id, { ...candidate, characterId: id });
        }
    }
    return Array.from(byId.values()).sort((left, right) => right.confidence - left.confidence);
}

/**
 * Write a chapter's dialogue document as a dialogue save, then run the
 * explicit stats step. The one write path for genuine dialogue saves (the
 * chapter save above and a fresh parse); every other dialogue writer uses
 * the pure `writeDialogue` and never touches character stats.
 */
export async function writeChapterDialogue(relativePath: string, payload: DialogueJson): Promise<boolean> {
    // The fs transport performs the File System API → backend fallback
    // internally, so one call is the entire write. This module names the
    // write kind: a chapter dialogue save.
    const outcome = await writeDialogueSave(relativePath, payload);
    if (outcome === null) return false;
    applyStatsAfterDialogueSave(relativePath, outcome);
    return true;
}

/**
 * The stats step of a dialogue save (ADR 0012), explicit rather than a
 * low-level write side effect:
 *   - `sidecar`   — `/api/save-dialogue` already applied the delta: nothing.
 *   - `handle`    — the File System Access write never reached the sidecar:
 *                   ask it to apply the delta (fire-and-forget; stats are
 *                   eventually consistent and a failure only warns).
 *   - `unchanged` — nothing was written: nothing.
 */
export function applyStatsAfterDialogueSave(relativePath: string, outcome: WriteOutcome): void {
    if (outcome === 'handle') void applyDialogueStatsDelta(relativePath);
}

/**
 * The identity-cache step for character data that changed outside the
 * frontend's own write path (e.g. the backend's review-chapter descriptor
 * learning rewrote character files): drop the central-characters and
 * name→id caches, then reload the book-characters store. Frontend character
 * writes already invalidate the caches in fs, and a dialogue save changes no
 * identity data, so a plain save needs no invalidation of its own.
 */
export async function refreshCharacterCaches(): Promise<void> {
    invalidateCentralCharactersCache();
    // Dynamic import: keeps the store (and its top-level subscriptions) out of
    // this module's static graph, which services such as fs and the parser load.
    const { forceRefreshBookCharacters } = await import('$lib/stores/bookCharacters');
    await forceRefreshBookCharacters();
}

/**
 * Read per-line audio metadata (generator records + read-as texts) from the
 * chapter's dialogue.json on disk. Returns lineId → value maps (empty on any
 * failure).
 */
async function readDiskLineMetadata(chapterTitle: string): Promise<{
    audioGeneration: Map<number, AudioGenerationRecord>;
    readAs: Map<number, string>;
}> {
    const audioGeneration = new Map<number, AudioGenerationRecord>();
    const readAs = new Map<number, string>();
    try {
        const doc = await readOptionalDialogue(`${chapterTitle}/dialogue.json`);
        if (!doc || !Array.isArray(doc.lines)) return { audioGeneration, readAs };
        for (const line of doc.lines) {
            if (!line || typeof line.id !== 'number') continue;
            if (line.audioGeneration) audioGeneration.set(line.id, line.audioGeneration);
            if (typeof line.readAs === 'string' && line.readAs) readAs.set(line.id, line.readAs);
        }
    } catch (error) {
        console.warn('[ChapterView] Failed to read disk line metadata:', error);
    }
    return { audioGeneration, readAs };
}

/**
 * Record which generator produced audio for the given lines in the chapter's
 * dialogue.json. Best-effort read-modify-write through the dual I/O (File
 * System Access → backend); a stamp failure never breaks generation.
 */
export async function recordLineAudioGeneration(
    chapterTitle: string,
    lineIds: number[],
    record: AudioGenerationRecord
): Promise<void> {
    if (!chapterTitle || lineIds.length === 0) return;
    const relativePath = `${chapterTitle}/dialogue.json`;
    const doc = await readOptionalDialogue(relativePath);
    if (!doc || !Array.isArray(doc.lines)) {
        console.warn('[Audio] Cannot record generator: no dialogue.json for', chapterTitle);
        return;
    }
    const idSet = new Set(lineIds);
    let touched = false;
    for (const line of doc.lines) {
        if (line && idSet.has(Number(line.id))) {
            line.audioGeneration = record;
            touched = true;
        }
    }
    if (!touched) return;
    // recordHistory=false: a generator stamp is metadata, not a content edit.
    const saved = await writeDialogue(relativePath, doc, false);
    if (!saved) {
        console.warn('[Audio] Failed to write generator record to dialogue.json');
    }
}

/**
 * Record (or clear, with `null`) the custom "Read As" spoken text for the
 * given lines in the chapter's dialogue.json. Best-effort read-modify-write;
 * a stamp failure never breaks generation.
 */
export async function recordLineReadAs(
    chapterTitle: string,
    lineIds: number[],
    text: string | null
): Promise<void> {
    if (!chapterTitle || lineIds.length === 0) return;
    const relativePath = `${chapterTitle}/dialogue.json`;
    const doc = await readOptionalDialogue(relativePath);
    if (!doc || !Array.isArray(doc.lines)) {
        console.warn('[Audio] Cannot record read-as text: no dialogue.json for', chapterTitle);
        return;
    }
    const idSet = new Set(lineIds);
    let touched = false;
    for (const line of doc.lines) {
        if (line && idSet.has(Number(line.id))) {
            line.readAs = text;
            touched = true;
        }
    }
    if (!touched) return;
    // recordHistory=false: a read-as stamp is metadata, not a content edit.
    const saved = await writeDialogue(relativePath, doc, false);
    if (!saved) {
        console.warn('[Audio] Failed to write read-as text to dialogue.json');
    }
}
