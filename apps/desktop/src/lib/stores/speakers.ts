import { writable, get } from 'svelte/store';
import type { Voice, VoicesJson, VoiceAssignment } from '$lib/types';
import { bookRoot, audioRoot } from '$lib/stores/bookState';
import { discoverVoicesFromManifests, scanVoiceManifests } from '$lib/services/voices';
import {
  createVoiceFromSample,
  listVoiceSamples,
  saveVoiceSampleMetadata,
} from '$lib/services/vibevoice';
import {
  createAssignmentsFromManifests,
  deduplicateVoicesAndAssignments,
  mergeDiscoveredVoices,
} from '$lib/services/voiceDomain';
import { readVoices, writeVoices } from '$lib/services/fs';

export const voices = writable<VoicesJson>({
  formatVersion: '2.0',
  voices: [],
  assignments: []
});

export async function loadVoices(): Promise<void> {
  const root = get(bookRoot);
  if (!root) {
    voices.set({ formatVersion: '2.0', voices: [], assignments: [] });
    return;
  }

  try {
    console.log('[voices] Loading voices from:', root);
    const data = await readVoices(root);
    if (data) {
      const deduped = deduplicateVoicesAndAssignments(data.voices || [], data.assignments || []);

      // Ensure v2.0 format and normalize duplicate voice ids before first render.
      voices.set({
        formatVersion: '2.0',
        voices: deduped.voices,
        assignments: deduped.assignments
      });

      if (deduped.removed > 0 || deduped.updated > 0) {
        console.warn(
          `[voices] Removed ${deduped.removed} duplicate voices and updated ${deduped.updated} assignments while loading voices.json`,
        );
        await saveVoicesData();
      }

      console.log(`[voices] Loaded ${deduped.voices.length} voices, ${deduped.assignments.length} assignments`);
    } else {
      console.log('[voices] No voices.json found, starting with empty state');
      voices.set({ formatVersion: '2.0', voices: [], assignments: [] });
    }
  } catch (err) {
    console.error('Error loading voices:', err);
    voices.set({ formatVersion: '2.0', voices: [], assignments: [] });
  }
}

/**
 * Scans audio manifests and discovers voices from existing audio files.
 * Also auto-creates assignments for characters based on primaryVoiceId in manifests.
 */
export async function discoverVoices(): Promise<number> {
  const audio = get(audioRoot);
  const root = get(bookRoot);
  if (!audio) {
    console.warn('[voices] No audio root set');
    return 0;
  }

  try {
    console.log('[voices] Scanning manifests in:', audio);
    const discovered = await discoverVoicesFromManifests(audio);

    const current = get(voices);

    const merged = mergeDiscoveredVoices(current.voices, discovered);

    if (merged.hasUpdates || merged.newVoicesCount > 0) {
      voices.set({
        formatVersion: '2.0',
        voices: merged.mergedVoices,
        assignments: current.assignments
      });
      await saveVoicesData();
    }

    // Auto-create assignments based on manifest data
    if (root) {
      await autoCreateAssignmentsFromManifests(audio);
    }

    console.log('[voices] Discovered', merged.newVoicesCount, 'new voices');
    return merged.newVoicesCount;
  } catch (err) {
    console.error('Error discovering voices:', err);
    return 0;
  }
}

/**
 * Auto-create voice assignments based on manifest primaryVoiceId
 */
async function autoCreateAssignmentsFromManifests(audioRoot: string): Promise<void> {
  try {
    const root = get(bookRoot);
    if (!root) return;

    const manifestsData = await scanVoiceManifests(audioRoot);

    // Read characters.json to get character IDs
    const { readCentralCharacters } = await import('$lib/services/fs');
    const charactersData = await readCentralCharacters(root);
    if (!charactersData) return;

    // Use centralized helper to build name-to-ID map
    const { buildNameToIdMap } = await import('$lib/stores/characters');
    const nameToIdMap = buildNameToIdMap(charactersData.characters);

    const current = get(voices);
    const assignmentResult = createAssignmentsFromManifests(
      manifestsData,
      nameToIdMap,
      current.voices,
      current.assignments,
    );

    if (assignmentResult.createdCount > 0) {
      voices.set({
        ...current,
        assignments: assignmentResult.assignments
      });
      await saveVoicesData();
      console.log(`[voices] Created ${assignmentResult.createdCount} automatic assignments`);
    }
  } catch (err) {
    // Check if this is a connection error (backend not running)
    const isConnectionError = err instanceof TypeError &&
      (err.message.includes('Failed to fetch') ||
        err.message.includes('ERR_CONNECTION_REFUSED') ||
        err.message.includes('NetworkError'));

    if (isConnectionError) {
      // Silently skip auto-assignment if backend is not available
      // This is expected when backend is not running
      return;
    }
    console.error('[voices] Error auto-creating assignments:', err);
  }
}

/**
 * Deduplicates voices by provider:providerVoiceId.
 * When duplicates are found, keeps the one with most metadata/usage, 
 * and updates all assignments to point to the kept voice.
 */
export async function deduplicateVoices(): Promise<{ removed: number; updated: number }> {
  const current = get(voices);
  const deduped = deduplicateVoicesAndAssignments(current.voices, current.assignments);

  // Save the deduplicated data
  voices.set({
    formatVersion: '2.0',
    voices: deduped.voices,
    assignments: deduped.assignments
  });

  await saveVoicesData();

  console.log(`[voices] Deduplication complete: removed ${deduped.removed} duplicate voices`);
  return { removed: deduped.removed, updated: deduped.updated };
}

export async function syncVoicesFromSamples(samplesRoot: string): Promise<{ imported: number; removed: number }> {
  if (!samplesRoot) return { imported: 0, removed: 0 };

  const sampleEntries = await listVoiceSamples(samplesRoot);
  if (sampleEntries.length === 0) {
    console.warn('[voices] No compatible voice samples found; leaving existing sample voices unchanged');
    return { imported: 0, removed: 0 };
  }

  const sampleByKey = new Map(sampleEntries.map((sample) => [`vibevoice_local:${sample.sample_file}`, sample]));
  const current = get(voices);

  const nextVoices: Voice[] = [];
  const handledKeys = new Set<string>();
  const removedVoiceIds = new Set<string>();

  for (const voice of current.voices) {
    const key = `${voice.provider}:${voice.providerVoiceId}`;
    const sample = sampleByKey.get(key);
    if (sample) {
      nextVoices.push(createVoiceFromSample(samplesRoot, sample, voice));
      handledKeys.add(key);
      continue;
    }

    const isImportedSampleVoice = voice.provider === 'vibevoice_local' && voice.metadata.discoveredFrom === 'sample';
    if (isImportedSampleVoice) {
      removedVoiceIds.add(voice.id);
      continue;
    }

    nextVoices.push(voice);
  }

  for (const sample of sampleEntries) {
    const key = `vibevoice_local:${sample.sample_file}`;
    if (handledKeys.has(key)) continue;
    nextVoices.push(createVoiceFromSample(samplesRoot, sample));
  }

  const nextAssignments = current.assignments.filter((assignment) => !removedVoiceIds.has(assignment.voiceId));
  const deduped = deduplicateVoicesAndAssignments(nextVoices, nextAssignments);

  voices.set({
    formatVersion: '2.0',
    voices: deduped.voices,
    assignments: deduped.assignments,
  });

  await saveVoicesData();
  return { imported: sampleEntries.length, removed: removedVoiceIds.size };
}

export async function saveVoiceSampleMetadataForVoice(
  voiceId: string,
  samplesRoot: string,
  displayName: string,
  tags: string[],
): Promise<void> {
  const current = get(voices);
  const voice = current.voices.find((entry) => entry.id === voiceId);
  if (!voice) return;

  const sample = await saveVoiceSampleMetadata(
    samplesRoot,
    voice.providerVoiceId,
    displayName,
    tags,
  );

  voices.set({
    ...current,
    voices: current.voices.map((entry) =>
      entry.id === voiceId ? createVoiceFromSample(samplesRoot, sample, entry) : entry,
    ),
  });

  await saveVoicesData();
}

/**
 * Save the current voices data to voices.json
 */
export async function saveVoicesData(): Promise<void> {
  const root = get(bookRoot);
  if (!root) return;

  try {
    const data = get(voices);
    await writeVoices(root, data);
    console.log('[voices] Saved voices.json:', data.voices.length, 'voices,', data.assignments.length, 'assignments');
  } catch (err) {
    console.error('Error saving voices:', err);
  }
}

export async function addVoice(voice: Voice): Promise<void> {
  const current = get(voices);
  voices.set({
    ...current,
    voices: [...current.voices, voice]
  });
  await saveVoicesData();
}

export async function updateVoice(id: string, updates: Partial<Voice>): Promise<void> {
  const current = get(voices);
  voices.set({
    ...current,
    voices: current.voices.map(v => v.id === id ? { ...v, ...updates } : v)
  });
  await saveVoicesData();
}

export async function deleteVoice(id: string): Promise<void> {
  const current = get(voices);
  voices.set({
    ...current,
    voices: current.voices.filter(v => v.id !== id)
  });
  await saveVoicesData();
}

// ============================================================================
// Voice Assignment Functions
// ============================================================================

/**
 * Assign a voice to a character
 */
export async function assignVoiceToCharacter(
  characterId: string,
  voiceId: string,
  priority: number = 1
): Promise<void> {
  const current = get(voices);

  // Remove any existing assignment for this character
  const filteredAssignments = current.assignments.filter(a => a.characterId !== characterId);

  // Add new assignment
  const newAssignment: VoiceAssignment = {
    characterId,
    voiceId,
    priority,
    contextOverrides: []
  };

  voices.set({
    ...current,
    assignments: [...filteredAssignments, newAssignment]
  });

  await saveVoicesData();
  console.log(`[voices] Assigned voice ${voiceId} to character ${characterId}`);
}

/**
 * Remove voice assignment for a character
 */
export async function unassignVoiceFromCharacter(characterId: string): Promise<void> {
  const current = get(voices);

  voices.set({
    ...current,
    assignments: current.assignments.filter(a => a.characterId !== characterId)
  });

  await saveVoicesData();
  console.log(`[voices] Unassigned voice from character ${characterId}`);
}

export async function unassignVoicesFromCharacters(characterIds: string[]): Promise<void> {
  const idsToRemove = new Set(
    characterIds.filter((characterId): characterId is string => typeof characterId === 'string' && characterId.length > 0),
  );
  if (idsToRemove.size === 0) return;

  const current = get(voices);
  const nextAssignments = current.assignments.filter((assignment) => !idsToRemove.has(assignment.characterId));
  if (nextAssignments.length === current.assignments.length) return;

  voices.set({
    ...current,
    assignments: nextAssignments,
  });

  await saveVoicesData();
  console.log(`[voices] Unassigned voices from ${idsToRemove.size} character(s)`);
}

/**
 * Get the voice assignment for a character
 */
export function getCharacterAssignment(characterId: string): VoiceAssignment | null {
  const current = get(voices);
  return current.assignments.find(a => a.characterId === characterId) || null;
}

/**
 * Get the voice object assigned to a character
 */
export function getAssignedVoice(characterId: string): Voice | null {
  const assignment = getCharacterAssignment(characterId);
  if (!assignment) return null;

  const current = get(voices);
  return current.voices.find(v => v.id === assignment.voiceId) || null;
}

/**
 * Get all characters assigned to a specific voice
 */
export function getCharactersForVoice(voiceId: string): string[] {
  const current = get(voices);
  return current.assignments
    .filter(a => a.voiceId === voiceId)
    .map(a => a.characterId);
}

/**
 * Update an existing assignment
 */
export async function updateAssignment(
  characterId: string,
  updates: Partial<VoiceAssignment>
): Promise<void> {
  const current = get(voices);

  voices.set({
    ...current,
    assignments: current.assignments.map(a =>
      a.characterId === characterId
        ? { ...a, ...updates }
        : a
    )
  });

  await saveVoicesData();
}

// HMR-safe subscription: unsubscribe old listener on module reload
let _unsubBookRoot = bookRoot.subscribe(() => {
  loadVoices();
});

if (import.meta.hot) {
  import.meta.hot.dispose(() => {
    _unsubBookRoot();
  });
}

