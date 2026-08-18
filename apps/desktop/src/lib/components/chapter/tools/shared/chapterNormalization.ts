import type { DialogueJson, DialogueLine, LineAttribution, LineItem, ScriptJson } from '$lib/types';
import type { UnifiedLine } from '$lib/components/chapter/tools/types';

type AttributionInputCandidate = {
    name: string;
    characterId?: string | null;
    confidence: number;
};

type NormalizeScriptOptions = {
    root?: string;
    unknownThreshold: number;
    unknownSpeakerLabel?: string;
    readCentralCharacters: (root: string) => Promise<{ characters?: any[] } | null>;
    buildIdToNameMap: (characters: any[]) => Map<string, string>;
};

export function clampConfidence(value: number | null | undefined): number {
    const numeric = Number(value);
    if (!Number.isFinite(numeric)) return 0;
    if (numeric < 0) return 0;
    if (numeric > 1) return 1;
    return numeric;
}

export function normalizeAttribution(
    existing: any,
    candidates: AttributionInputCandidate[],
    characterName: string | null,
    threshold: number,
    fallbackSourceAlias: string | null = null,
    fallbackSourceCandidates: string[] = [],
    unknownSpeakerLabel = 'Unknown'
): LineAttribution {
    const sortedCandidates = [...(Array.isArray(candidates) ? candidates : [])]
        .filter((candidate) => candidate && candidate.name)
        .map((candidate) => ({
            ...candidate,
            characterId: candidate.characterId ?? null,
            confidence: clampConfidence(candidate.confidence),
        }))
        .sort((left, right) => right.confidence - left.confidence);

    const normalizedName = String(characterName || '').trim().toLowerCase();
    const hasAssignedSpeaker = !!characterName && normalizedName !== unknownSpeakerLabel.toLowerCase();
    const top = sortedCandidates[0]?.confidence ?? 0;
    const inferredTop = sortedCandidates.length > 0 ? top : (hasAssignedSpeaker ? 1 : 0);
    const second = sortedCandidates[1]?.confidence ?? 0;
    const margin = Math.max(0, inferredTop - second);
    const confidence = clampConfidence(typeof existing?.confidence === 'number' ? existing.confidence : inferredTop);
    const risk = clampConfidence(
        typeof existing?.misattributionRisk === 'number'
            ? existing.misattributionRisk
            : (1 - inferredTop) * 0.7 + (1 - margin) * 0.3
    );
    const sourceAlias =
        typeof existing?.sourceAlias === 'string'
            ? existing.sourceAlias
            : fallbackSourceAlias;
    const sourceCandidates = Array.isArray(existing?.sourceCandidates)
        ? existing.sourceCandidates.filter((candidate: any) => typeof candidate === 'string' && candidate.trim().length > 0)
        : fallbackSourceCandidates;

    let resolutionStatus: 'auto' | 'unknown' | 'user_confirmed' =
        existing?.resolutionStatus === 'auto' || existing?.resolutionStatus === 'unknown' || existing?.resolutionStatus === 'user_confirmed'
            ? existing.resolutionStatus
            : 'auto';

    const narratorLabel = 'narrator';
    const isNarratorWithoutCandidates =
        normalizedName === narratorLabel &&
        sortedCandidates.length === 0;

    if (resolutionStatus !== 'user_confirmed') {
        if (!characterName || normalizedName === unknownSpeakerLabel.toLowerCase()) {
            resolutionStatus = 'unknown';
        } else if (isNarratorWithoutCandidates) {
            resolutionStatus = 'auto';
        } else if (sortedCandidates.length > 0 && top < threshold) {
            resolutionStatus = 'unknown';
        } else {
            resolutionStatus = 'auto';
        }
    }

    return {
        confidence,
        topCandidateConfidence: inferredTop,
        marginToSecond: margin,
        misattributionRisk: risk,
        resolutionStatus,
        thresholdUsed: clampConfidence(typeof existing?.thresholdUsed === 'number' ? existing.thresholdUsed : threshold),
        sourceAlias: sourceAlias || null,
        sourceCandidates,
        sourceDescriptors: Array.isArray(existing?.sourceDescriptors)
            ? existing.sourceDescriptors.filter((value: any) => typeof value === 'string' && value.trim().length > 0)
            : undefined,
        contextGender: typeof existing?.contextGender === 'string' ? existing.contextGender : null,
        contextGenderCue: typeof existing?.contextGenderCue === 'string' ? existing.contextGenderCue : null,
        genderConflict: typeof existing?.genderConflict === 'boolean' ? existing.genderConflict : false,
        parserBackend: typeof existing?.parserBackend === 'string' ? existing.parserBackend : null,
        decisionTrace: existing?.decisionTrace ?? null,
        candidates: sortedCandidates,
    };
}

function normalizeLine(
    line: DialogueLine | LineItem,
    unknownThreshold: number,
    unknownSpeakerLabel: string,
    charactersMap?: Map<string, string>
): UnifiedLine {
    if ('characterId' in line) {
        const metadataTags = (line.metadata?.customTags || {}) as Record<string, any>;
        const fallbackSourceAlias = typeof metadataTags.sourceAlias === 'string' ? metadataTags.sourceAlias : null;
        const fallbackSourceCandidates = Array.isArray(metadataTags.sourceCandidates)
            ? metadataTags.sourceCandidates.filter((candidate): candidate is string => typeof candidate === 'string')
            : [];
        const rawAttribution = (line as any).attribution || metadataTags.attribution || null;

        let charName: string | null = null;
        if (line.characterId) {
            charName = charactersMap?.get(line.characterId) || line.characterId;
        }

        const normalizedSpan = (line.span && line.span.start === 0 && line.span.end === 0) ? null : line.span;
        const normalizedCandidates = line.candidates.map((candidate) => {
            const candidateName = candidate.characterId ? (charactersMap?.get(candidate.characterId) || candidate.characterId) : null;
            return {
                name: candidateName || unknownSpeakerLabel,
                characterId: candidate.characterId || null,
                confidence: clampConfidence(candidate.confidence)
            };
        });

        const normalizedAttribution = normalizeAttribution(
            rawAttribution,
            normalizedCandidates,
            charName,
            unknownThreshold,
            fallbackSourceAlias,
            fallbackSourceCandidates,
            unknownSpeakerLabel
        );
        const isReturning = typeof line.isReturning === 'boolean' ? line.isReturning : false;

        if (normalizedAttribution.resolutionStatus === 'unknown') {
            charName = unknownSpeakerLabel;
        }

        return {
            id: line.id,
            text: line.text,
            span: normalizedSpan,
            characterName: charName,
            candidates: normalizedCandidates.map((candidate) => ({ name: candidate.name, confidence: candidate.confidence })),
            isConflict: line.isConflict || normalizedAttribution.resolutionStatus === 'unknown',
            isReturning,
            attribution: normalizedAttribution
        };
    }

    const normalizedSpan = (line.span && line.span.start === 0 && line.span.end === 0) ? null : line.span;
    const normalizedCandidates = (line.candidates || []).map((candidate) => ({
        name: candidate.name || unknownSpeakerLabel,
        confidence: clampConfidence(candidate.confidence),
    }));
    const normalizedAttribution = normalizeAttribution(
        (line as any).attribution || null,
        normalizedCandidates,
        line.chosenSpeaker,
        unknownThreshold,
        null,
        [],
        unknownSpeakerLabel
    );
    const normalizedSpeaker = normalizedAttribution.resolutionStatus === 'unknown'
        ? unknownSpeakerLabel
        : line.chosenSpeaker;

    return {
        id: line.id,
        text: line.text,
        span: normalizedSpan,
        characterName: normalizedSpeaker,
        candidates: normalizedCandidates,
        isConflict: line.isConflict || normalizedAttribution.resolutionStatus === 'unknown',
        isReturning: typeof (line as any).isReturning === 'boolean' ? Boolean((line as any).isReturning) : false,
        attribution: normalizedAttribution
    };
}

function buildAliasMaps(centralCharacters: any[] | null | undefined) {
    const aliasToCanonical = new Map<string, string>();
    const nameToCanonical = new Map<string, string>();
    for (const entry of centralCharacters ?? []) {
        if (!entry?.name) continue;
        const canonical = String(entry.name);
        nameToCanonical.set(canonical.toLowerCase(), canonical);
        const aliases = Array.isArray(entry.aliases) ? entry.aliases : [];
        for (const alias of aliases) {
            const key = String(alias || '').trim().toLowerCase();
            if (!key) continue;
            aliasToCanonical.set(key, canonical);
        }
    }
    return { aliasToCanonical, nameToCanonical };
}

function canonicalizeName(
    name: string | null | undefined,
    aliasToCanonical: Map<string, string>,
    nameToCanonical: Map<string, string>,
    unknownSpeakerLabel: string
): string | null {
    if (!name) return null;
    const key = String(name).trim().toLowerCase();
    if (!key) return null;
    if (key === unknownSpeakerLabel.toLowerCase()) return unknownSpeakerLabel;
    return aliasToCanonical.get(key) || nameToCanonical.get(key) || name;
}

export async function normalizeScriptData(
    data: ScriptJson | DialogueJson | null,
    options: NormalizeScriptOptions
): Promise<{ lines: UnifiedLine[]; stats: any; hadAliasChanges: boolean } | null> {
    if (!data) return null;

    const unknownSpeakerLabel = options.unknownSpeakerLabel ?? 'Unknown';

    let charactersMap = new Map<string, string>();
    let aliasToCanonical = new Map<string, string>();
    let nameToCanonical = new Map<string, string>();

    // Dialogue v2 and every v3 revision store canonical character IDs. Resolve
    // them for presentation regardless of the exact formatVersion value.
    if (options.root) {
        try {
            const centralChars = await options.readCentralCharacters(options.root);
            if (centralChars?.characters) {
                charactersMap = options.buildIdToNameMap(centralChars.characters);
            }
            const aliasMaps = buildAliasMaps(centralChars?.characters);
            aliasToCanonical = aliasMaps.aliasToCanonical;
            nameToCanonical = aliasMaps.nameToCanonical;
        } catch {
            // Individual IDs remain visible if the central catalog cannot load.
        }
    }

    let hadAliasChanges = false;
    const lines = data.lines.map((line) => {
        const normalized = normalizeLine(line, options.unknownThreshold, unknownSpeakerLabel, charactersMap);
        const canonical = canonicalizeName(normalized.characterName, aliasToCanonical, nameToCanonical, unknownSpeakerLabel);
        if (canonical && canonical !== normalized.characterName) {
            hadAliasChanges = true;
        }
        const nextCandidates = normalized.candidates.map((candidate) => {
            const nextName = canonicalizeName(candidate.name, aliasToCanonical, nameToCanonical, unknownSpeakerLabel) || candidate.name;
            if (nextName !== candidate.name) hadAliasChanges = true;
            return { ...candidate, name: nextName };
        });
        const nextAttribution = normalized.attribution
            ? {
                ...normalized.attribution,
                candidates: normalized.attribution.candidates.map((candidate) => {
                    const nextName = canonicalizeName(candidate.name, aliasToCanonical, nameToCanonical, unknownSpeakerLabel) || candidate.name;
                    return { ...candidate, name: nextName };
                }),
            }
            : undefined;

        return { ...normalized, characterName: canonical, candidates: nextCandidates, attribution: nextAttribution };
    });

    return {
        lines,
        stats: data.stats,
        hadAliasChanges
    };
}
