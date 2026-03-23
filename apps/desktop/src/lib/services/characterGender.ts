import type { CharactersJson, Gender } from '$lib/types';
import { resolveCanonicalCharacterName } from '$lib/services/characterDomain';

export type LearnedBinaryGender = Exclude<Gender, 'Unknown'>;

export interface GenderLearningLine {
    id: number;
    characterName?: string | null;
    chosenSpeaker?: string | null;
    isConflict?: boolean;
    attribution?: {
        contextGender?: string | null;
        contextGenderCue?: string | null;
        genderConflict?: boolean;
    } | null;
}

export interface CharacterGenderLearningEntry {
    name: string;
    previousGender: Gender;
    inferredGender: LearnedBinaryGender | null;
    nextGender: Gender;
    status: 'updated' | 'matched' | 'ambiguous';
    maleEvidenceCount: number;
    femaleEvidenceCount: number;
    lineIds: number[];
    cueExamples: string[];
}

export interface CharacterGenderLearningResult {
    nextCharacters: CharactersJson;
    usableCueLines: number;
    conflictingCueLines: number;
    entries: CharacterGenderLearningEntry[];
}

function uniqueStrings(values: string[]): string[] {
    const seen = new Set<string>();
    const result: string[] = [];
    for (const value of values) {
        const trimmed = String(value || '').trim();
        if (!trimmed) continue;
        const key = trimmed.toLowerCase();
        if (seen.has(key)) continue;
        seen.add(key);
        result.push(trimmed);
    }
    return result;
}

function uniqueNumbers(values: number[]): number[] {
    const seen = new Set<number>();
    const result: number[] = [];
    for (const value of values) {
        if (!Number.isFinite(value)) continue;
        if (seen.has(value)) continue;
        seen.add(value);
        result.push(value);
    }
    return result;
}

function pluralize(count: number, singular: string, plural = `${singular}s`): string {
    return `${count} ${count === 1 ? singular : plural}`;
}

function getLineSpeakerName(line: GenderLearningLine): string | null {
    const name =
        typeof line.characterName === 'string'
            ? line.characterName
            : typeof line.chosenSpeaker === 'string'
                ? line.chosenSpeaker
                : '';
    const trimmed = name.trim();
    return trimmed.length > 0 ? trimmed : null;
}

function isNonLearnableSpeaker(name: string): boolean {
    const normalized = String(name || '').trim().toLowerCase();
    return !normalized || normalized === 'unknown' || normalized === 'narrator';
}

export function normalizeCharacterGender(value: unknown): Gender {
    const normalized = String(value || '').trim().toLowerCase();
    if (normalized === 'male' || normalized === 'm') return 'Male';
    if (normalized === 'female' || normalized === 'f') return 'Female';
    return 'Unknown';
}

export function normalizeLearnedBinaryGender(value: unknown): LearnedBinaryGender | null {
    const normalized = normalizeCharacterGender(value);
    return normalized === 'Unknown' ? null : normalized;
}

export function hasUsableGenderCue(line: GenderLearningLine): boolean {
    const speakerName = getLineSpeakerName(line);
    if (!speakerName || isNonLearnableSpeaker(speakerName)) return false;
    if (line.isConflict) return false;
    if (line.attribution?.genderConflict) return false;
    return normalizeLearnedBinaryGender(line.attribution?.contextGender) !== null;
}

function chooseLearnedGender(
    previousGender: Gender,
    maleEvidenceCount: number,
    femaleEvidenceCount: number,
): LearnedBinaryGender | null {
    if (maleEvidenceCount <= 0 && femaleEvidenceCount <= 0) return null;

    if (maleEvidenceCount > 0 && femaleEvidenceCount > 0) {
        const dominantGender: LearnedBinaryGender = maleEvidenceCount > femaleEvidenceCount ? 'Male' : 'Female';
        const dominantCount = dominantGender === 'Male' ? maleEvidenceCount : femaleEvidenceCount;
        const secondaryCount = dominantGender === 'Male' ? femaleEvidenceCount : maleEvidenceCount;

        if (dominantCount < secondaryCount + 2) {
            return null;
        }

        if (previousGender !== 'Unknown' && previousGender !== dominantGender && dominantCount < 2) {
            return null;
        }

        return dominantGender;
    }

    const onlyGender: LearnedBinaryGender = maleEvidenceCount > 0 ? 'Male' : 'Female';
    const evidenceCount = maleEvidenceCount > 0 ? maleEvidenceCount : femaleEvidenceCount;
    if (previousGender !== 'Unknown' && previousGender !== onlyGender && evidenceCount < 2) {
        return null;
    }
    return onlyGender;
}

export function learnCharacterGendersFromChapter(
    lines: GenderLearningLine[],
    data: CharactersJson,
): CharacterGenderLearningResult {
    const evidenceByCharacter = new Map<string, {
        maleEvidenceCount: number;
        femaleEvidenceCount: number;
        lineIds: number[];
        cueExamples: string[];
    }>();

    let usableCueLines = 0;
    let conflictingCueLines = 0;

    for (const line of lines) {
        const speakerName = getLineSpeakerName(line);
        if (!speakerName || isNonLearnableSpeaker(speakerName) || line.isConflict) continue;

        const learnedGender = normalizeLearnedBinaryGender(line.attribution?.contextGender);
        if (!learnedGender) continue;

        if (line.attribution?.genderConflict) {
            conflictingCueLines += 1;
            continue;
        }

        const canonicalName = resolveCanonicalCharacterName(data, speakerName) ?? speakerName;
        if (isNonLearnableSpeaker(canonicalName)) continue;

        const evidence = evidenceByCharacter.get(canonicalName) ?? {
            maleEvidenceCount: 0,
            femaleEvidenceCount: 0,
            lineIds: [],
            cueExamples: [],
        };

        if (learnedGender === 'Male') {
            evidence.maleEvidenceCount += 1;
        } else {
            evidence.femaleEvidenceCount += 1;
        }

        evidence.lineIds.push(line.id);
        if (typeof line.attribution?.contextGenderCue === 'string') {
            evidence.cueExamples.push(line.attribution.contextGenderCue);
        }

        evidenceByCharacter.set(canonicalName, evidence);
        usableCueLines += 1;
    }

    const entries: CharacterGenderLearningEntry[] = [];
    const nextCharacters = {
        ...data,
        characters: data.characters.map((character) => {
            const evidence = evidenceByCharacter.get(character.name);
            if (!evidence) return character;

            const previousGender = normalizeCharacterGender((character as any).gender);
            const inferredGender = chooseLearnedGender(
                previousGender,
                evidence.maleEvidenceCount,
                evidence.femaleEvidenceCount,
            );

            const nextGender = inferredGender ?? previousGender;
            const status: CharacterGenderLearningEntry['status'] = !inferredGender
                ? 'ambiguous'
                : nextGender === previousGender
                    ? 'matched'
                    : 'updated';

            entries.push({
                name: character.name,
                previousGender,
                inferredGender,
                nextGender,
                status,
                maleEvidenceCount: evidence.maleEvidenceCount,
                femaleEvidenceCount: evidence.femaleEvidenceCount,
                lineIds: uniqueNumbers(evidence.lineIds),
                cueExamples: uniqueStrings(evidence.cueExamples).slice(0, 4),
            });

            if (status !== 'updated') return character;
            return {
                ...character,
                gender: nextGender,
            };
        }),
    } satisfies CharactersJson;

    return {
        nextCharacters,
        usableCueLines,
        conflictingCueLines,
        entries,
    };
}

export function summarizeCharacterGenderLearning(result: CharacterGenderLearningResult): string {
    const updated = result.entries.filter((entry) => entry.status === 'updated');
    const matched = result.entries.filter((entry) => entry.status === 'matched');
    const ambiguous = result.entries.filter((entry) => entry.status === 'ambiguous');

    if (result.usableCueLines === 0) {
        return 'No usable gender cues were found in this chapter.';
    }

    const parts: string[] = [];

    if (updated.length > 0) {
        const updatedNames = updated
            .slice(0, 3)
            .map((entry) => `${entry.name} -> ${entry.nextGender}`)
            .join(', ');
        parts.push(
            updated.length > 3
                ? `${pluralize(updated.length, 'character')} updated (${updatedNames}, +${updated.length - 3} more)`
                : `${pluralize(updated.length, 'character')} updated (${updatedNames})`,
        );
    } else {
        parts.push('no character genders changed');
    }

    if (matched.length > 0) {
        parts.push(`${pluralize(matched.length, 'character')} already matched saved gender`);
    }
    if (ambiguous.length > 0) {
        parts.push(`${pluralize(ambiguous.length, 'character')} had mixed evidence`);
    }
    if (result.conflictingCueLines > 0) {
        parts.push(`${pluralize(result.conflictingCueLines, 'conflicting cue line')} skipped`);
    }

    return `Processed ${pluralize(result.usableCueLines, 'cue line')}: ${parts.join('; ')}.`;
}