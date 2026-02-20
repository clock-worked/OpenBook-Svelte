import type { Voice, VoiceAssignment } from '$lib/types';
import type { ManifestData } from '$lib/services/apiContracts';

export function mergeDiscoveredVoices(
    existingVoices: Voice[],
    discoveredVoices: Voice[],
): { mergedVoices: Voice[]; newVoicesCount: number; hasUpdates: boolean } {
    const existingVoicesMap = new Map<string, Voice>();
    existingVoices.forEach((voice) => {
        existingVoicesMap.set(`${voice.provider}:${voice.providerVoiceId}`, voice);
    });

    const mergedVoices = [...existingVoices];
    const newVoices: Voice[] = [];
    let hasUpdates = false;

    for (const discoveredVoice of discoveredVoices) {
        const key = `${discoveredVoice.provider}:${discoveredVoice.providerVoiceId}`;
        const existing = existingVoicesMap.get(key);

        if (!existing) {
            newVoices.push(discoveredVoice);
            hasUpdates = true;
            continue;
        }

        const mergedMetadata = {
            ...existing.metadata,
            totalClips: discoveredVoice.metadata.totalClips || existing.metadata.totalClips,
            usedByCharacters: [
                ...(existing.metadata.usedByCharacters || []),
                ...(discoveredVoice.metadata.usedByCharacters || []),
            ].filter((value, index, array) => array.indexOf(value) === index),
            discoveredFrom: existing.metadata.discoveredFrom || discoveredVoice.metadata.discoveredFrom,
        };

        const index = mergedVoices.findIndex((voice) => voice.id === existing.id);
        if (index === -1) continue;

        const nextVoice: Voice = {
            ...existing,
            metadata: mergedMetadata,
        };

        const prev = mergedVoices[index];
        if (
            prev.metadata.totalClips !== nextVoice.metadata.totalClips ||
            (prev.metadata.usedByCharacters || []).join('|') !== (nextVoice.metadata.usedByCharacters || []).join('|') ||
            prev.metadata.discoveredFrom !== nextVoice.metadata.discoveredFrom
        ) {
            mergedVoices[index] = nextVoice;
            hasUpdates = true;
        }
    }

    return {
        mergedVoices: [...mergedVoices, ...newVoices],
        newVoicesCount: newVoices.length,
        hasUpdates,
    };
}

export function createAssignmentsFromManifests(
    manifests: ManifestData[],
    nameToIdMap: Map<string, string>,
    availableVoices: Voice[],
    existingAssignments: VoiceAssignment[],
): { assignments: VoiceAssignment[]; createdCount: number } {
    const assignments = [...existingAssignments];
    let createdCount = 0;

    for (const manifest of manifests) {
        if (manifest.formatVersion !== '2.0' || !manifest.metadata?.primaryVoiceId) continue;

        const characterName = manifest.characterName;
        const characterId = nameToIdMap.get(characterName);
        if (!characterId) continue;

        const alreadyAssigned = assignments.some((assignment) => assignment.characterId === characterId);
        if (alreadyAssigned) continue;

        const voice = availableVoices.find((candidate) => candidate.providerVoiceId === manifest.metadata?.primaryVoiceId);
        if (!voice) continue;

        assignments.push({
            characterId,
            voiceId: voice.id,
            priority: 1,
            contextOverrides: [],
        });
        createdCount += 1;
    }

    return { assignments, createdCount };
}

export function deduplicateVoicesAndAssignments(
    inputVoices: Voice[],
    inputAssignments: VoiceAssignment[],
): {
    voices: Voice[];
    assignments: VoiceAssignment[];
    removed: number;
    updated: number;
} {
    const voiceGroups = new Map<string, Voice[]>();
    inputVoices.forEach((voice) => {
        const key = `${voice.provider}:${voice.providerVoiceId}`;
        if (!voiceGroups.has(key)) {
            voiceGroups.set(key, []);
        }
        voiceGroups.get(key)!.push(voice);
    });

    const voiceIdMapping = new Map<string, string>();
    const voices: Voice[] = [];
    let removed = 0;

    for (const [key, voiceList] of voiceGroups.entries()) {
        if (voiceList.length === 1) {
            voices.push(voiceList[0]);
            continue;
        }

        console.log(`[voices] Found ${voiceList.length} duplicates for ${key}`);

        const sorted = voiceList.sort((left, right) => {
            const leftScore =
                (left.metadata.totalClips || 0) * 100 +
                (left.metadata.usedByCharacters?.length || 0) * 10 +
                (left.notes ? 1 : 0);
            const rightScore =
                (right.metadata.totalClips || 0) * 100 +
                (right.metadata.usedByCharacters?.length || 0) * 10 +
                (right.notes ? 1 : 0);
            return rightScore - leftScore;
        });

        const kept = sorted[0];
        const allUsedByCharacters = new Set<string>();
        let totalClips = 0;
        const allNotes: string[] = [];

        for (const voice of sorted) {
            (voice.metadata.usedByCharacters || []).forEach((characterId) => allUsedByCharacters.add(characterId));
            totalClips += voice.metadata.totalClips || 0;
            if (voice.notes && !allNotes.includes(voice.notes)) {
                allNotes.push(voice.notes);
            }
        }

        voices.push({
            ...kept,
            notes: allNotes.join('; ') || kept.notes,
            metadata: {
                ...kept.metadata,
                totalClips,
                usedByCharacters: Array.from(allUsedByCharacters),
            },
        });

        for (const voice of sorted) {
            if (voice.id !== kept.id) {
                voiceIdMapping.set(voice.id, kept.id);
                removed += 1;
            }
        }
    }

    const assignments = inputAssignments.map((assignment) => {
        if (!voiceIdMapping.has(assignment.voiceId)) return assignment;
        return {
            ...assignment,
            voiceId: voiceIdMapping.get(assignment.voiceId)!,
        };
    });

    return {
        voices,
        assignments,
        removed,
        updated: voiceIdMapping.size,
    };
}
