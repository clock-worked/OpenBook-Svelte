import type { AudioGenerationRecord, DialogueJson, ScriptJson } from '$lib/types';
import type { UnifiedLine } from '$lib/components/chapter/tools/types';
import { computeReturningFlags, isDialogueSpan } from '$lib/services/dialogueReturning';
import {
    applyDialogueStatsDelta,
    ensureCharacterNameToIdMapCached,
    invalidateCentralCharactersCache,
    readCentralCharacters,
    readTextFile,
    type WriteOutcome,
} from '$lib/services/fs';
import { isVersionedDialogue, layoutPath, openBook } from '$lib/services/bookFormat';
import { normalizeAttribution, normalizeScriptData } from './chapterNormalization';
import { alignChapterLineSpans } from './chapterPresentation';

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
        : await openBook(root).readChapterDialogue(chapter.title);
    let isV2Format = false;
    let normalized: NormalizedScriptPayload | null = null;
    let currentScript: ScriptJson | null = null;

    if (dialogue) {
        isV2Format = isVersionedDialogue(dialogue);

        // Dynamic import: keeps the characters store graph out of this
        // module's static imports.
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

    console.log('[ChapterView] Writing dialogue for chapter:', ch.title);

    const saved = await writeChapterDialogue(ch.title, dialoguePayload);
    if (saved) {
        console.log('[ChapterView] ✓ Successfully saved dialogue.json');
    } else {
        console.error('[ChapterView] ✗ Failed to save dialogue.json');
    }
    return saved;
}

/**
 * Write a chapter's dialogue document as a dialogue save, then run the
 * explicit stats step. The one write path for genuine dialogue saves (the
 * chapter save above and a fresh parse); every other dialogue writer uses
 * the pure `writeDialogue` and never touches character stats.
 */
export async function writeChapterDialogue(chapterTitle: string, payload: DialogueJson): Promise<boolean> {
    // The fs transport performs the File System API → backend fallback
    // internally, so one call is the entire write. The book format names the
    // write kind: a chapter dialogue save.
    const outcome = await openBook('').saveDialogue(chapterTitle, payload);
    if (outcome === null) return false;
    applyStatsAfterDialogueSave(layoutPath('dialogue', chapterTitle), outcome);
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
        const doc = await openBook('').read('dialogue', chapterTitle);
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
    const book = openBook('');
    const doc = await book.read('dialogue', chapterTitle);
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
    const saved = await book.write('dialogue', chapterTitle, doc, { history: false });
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
    const book = openBook('');
    const doc = await book.read('dialogue', chapterTitle);
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
    const saved = await book.write('dialogue', chapterTitle, doc, { history: false });
    if (!saved) {
        console.warn('[Audio] Failed to write read-as text to dialogue.json');
    }
}
