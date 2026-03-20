import type { ScriptJson } from '$lib/types';
import type { UnifiedLine } from '$lib/components/chapter/tools/types';

export function buildLegacyScriptFromNormalized(
    normalized: { lines: UnifiedLine[]; stats: any },
    chapter: { title?: string | null; path?: string | null } | null | undefined
): ScriptJson {
    return {
        chapter: chapter?.title || '',
        sourceFile: chapter?.path || '',
        lines: normalized.lines.map((line) => ({
            id: line.id,
            text: line.text,
            span: line.span,
            chosenSpeaker: line.characterName,
            candidates: line.candidates,
            isConflict: line.isConflict,
            isReturning: line.isReturning,
            attribution: line.attribution,
        })),
        stats: normalized.stats,
    };
}

function candidatesEqual(
    left: { name: string; confidence: number }[] = [],
    right: { name: string; confidence: number }[] = []
): boolean {
    if (left.length !== right.length) return false;
    for (let index = 0; index < left.length; index += 1) {
        if (left[index]?.name !== right[index]?.name || left[index]?.confidence !== right[index]?.confidence) {
            return false;
        }
    }
    return true;
}

export function mergeNormalizedFromLegacy(params: {
    legacy: ScriptJson;
    normalized: { lines: UnifiedLine[]; stats: any } | null;
    normalizeAttribution: (
        existing: any,
        candidates: Array<{ name: string; characterId?: string | null; confidence: number }>,
        characterName: string | null,
        threshold: number,
        fallbackSourceAlias?: string | null,
        fallbackSourceCandidates?: string[],
        unknownSpeakerLabel?: string
    ) => any;
    unknownThreshold: number;
}): { lines: UnifiedLine[]; stats: any } | null {
    if (!params.normalized) return null;

    const byId = new Map(params.legacy.lines.map((line) => [line.id, line] as const));
    let changed = false;

    const nextLines = params.normalized.lines.map((line) => {
        const source = byId.get(line.id);
        if (!source) return line;

        const nextName = source.chosenSpeaker ?? null;
        const nextCandidates = Array.isArray(source.candidates) ? source.candidates : [];
        const nextConflict = !!source.isConflict;
        const nextAttribution = params.normalizeAttribution(
            source.attribution || line.attribution || null,
            nextCandidates.map((candidate) => ({
                name: candidate.name,
                confidence: candidate.confidence,
                characterId: null,
            })),
            nextName,
            params.unknownThreshold
        );

        const sameCandidates = candidatesEqual(line.candidates, nextCandidates);
        const sameAttribution = JSON.stringify(line.attribution || null) === JSON.stringify(nextAttribution || null);

        if (
            line.characterName !== nextName ||
            line.isConflict !== nextConflict ||
            !sameCandidates ||
            !sameAttribution
        ) {
            changed = true;
            return {
                ...line,
                characterName: nextName,
                candidates: nextCandidates,
                isConflict: nextConflict,
                attribution: nextAttribution,
            };
        }

        return line;
    });

    if (!changed) return null;
    return {
        ...params.normalized,
        lines: nextLines,
    };
}

function normalizeAudioLineText(value: string): string {
    return value
        .normalize('NFKC')
        .toLowerCase()
        .replace(/[^a-z0-9]+/g, ' ')
        .replace(/\s+/g, ' ')
        .trim();
}

export async function buildGeneratedAudioMarkerState(params: {
    chapterTitle: string;
    lines: UnifiedLine[];
    root: string | null;
    readJsonRelative: <T>(path: string) => Promise<T | null>;
    readManifest: (chapterTitle: string, characterName: string) => Promise<any | null>;
    readCentralCharacters: (root: string) => Promise<{ characters?: any[] } | null>;
}): Promise<{
    generatedLineIds: Set<number>;
    generatedLineTexts: Set<string>;
    audioExistsCache: Map<string, boolean>;
}> {
    const namesToCheck = new Set<string>();
    const generatedLineIds = new Set<number>();
    const generatedLineTexts = new Set<string>();

    const chapterManifest = await params.readJsonRelative<{
        lines?: Array<{ id: number | string; text?: string; skipped?: boolean; output?: string | null }>;
    }>(`${params.chapterTitle}/audio_lines/manifest.json`);

    if (Array.isArray(chapterManifest?.lines)) {
        for (const line of chapterManifest.lines) {
            const normalizedLineId = Number(line?.id);
            if (!Number.isFinite(normalizedLineId)) continue;
            if (line?.skipped) continue;
            if (!line?.output) continue;
            generatedLineIds.add(normalizedLineId);
            if (typeof line?.text === 'string') {
                const normalizedText = normalizeAudioLineText(line.text);
                if (normalizedText) generatedLineTexts.add(normalizedText);
            }
        }
    }

    for (const line of params.lines) {
        if (line.characterName) namesToCheck.add(line.characterName);
    }

    if (params.root) {
        try {
            const central = await params.readCentralCharacters(params.root);
            for (const character of central?.characters ?? []) {
                if (character?.name) namesToCheck.add(String(character.name));
            }
        } catch (error) {
            console.warn('[ChapterView] Failed to load central characters for audio marker refresh:', error);
        }
    }

    if (generatedLineIds.size === 0) {
        await Promise.all(
            Array.from(namesToCheck).map(async (characterName) => {
                const manifest = await params.readManifest(params.chapterTitle, characterName);
                if (!manifest?.clips) return;
                for (const clip of manifest.clips) {
                    const clipChapter = String((clip as any).chapter ?? '').trim();
                    const normalizedLineId = Number((clip as any).id);
                    if (clipChapter === params.chapterTitle && Number.isFinite(normalizedLineId)) {
                        generatedLineIds.add(normalizedLineId);
                    }
                }
            })
        );
    }

    const audioExistsCache = new Map<string, boolean>();
    for (const line of params.lines) {
        if (!line.characterName) continue;
        const cacheKey = `${params.chapterTitle}-${line.characterName}-${line.id}`;
        audioExistsCache.set(cacheKey, generatedLineIds.has(line.id));
    }

    return {
        generatedLineIds,
        generatedLineTexts,
        audioExistsCache,
    };
}
