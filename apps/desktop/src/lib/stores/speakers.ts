import { writable, get } from 'svelte/store';
import type { Voice, VoicesJson, VoiceAssignment } from '$lib/types';
import { bookRoot, audioRoot } from '$lib/stores/bookState';
import { discoverVoicesFromManifests } from '$lib/services/voices';
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
      // Ensure v2.0 format
      voices.set({
        formatVersion: '2.0',
        voices: data.voices || [],
        assignments: data.assignments || []
      });
      console.log(`[voices] Loaded ${data.voices?.length || 0} voices, ${data.assignments?.length || 0} assignments`);
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

    // Build a map of existing voices by provider:providerVoiceId
    const existingVoicesMap = new Map<string, Voice>();
    current.voices.forEach(v => {
      existingVoicesMap.set(`${v.provider}:${v.providerVoiceId}`, v);
    });

    const newVoices: Voice[] = [];
    const updatedVoices = [...current.voices];

    // Process discovered voices
    for (const discoveredVoice of discovered) {
      const key = `${discoveredVoice.provider}:${discoveredVoice.providerVoiceId}`;
      const existing = existingVoicesMap.get(key);

      if (existing) {
        // Voice already exists - merge metadata
        const mergedMetadata = {
          ...existing.metadata,
          totalClips: discoveredVoice.metadata.totalClips || existing.metadata.totalClips,
          usedByCharacters: [
            ...(existing.metadata.usedByCharacters || []),
            ...(discoveredVoice.metadata.usedByCharacters || [])
          ].filter((v, i, a) => a.indexOf(v) === i), // dedupe
          discoveredFrom: existing.metadata.discoveredFrom || discoveredVoice.metadata.discoveredFrom,
        };

        // Update the existing voice in the array
        const index = updatedVoices.findIndex(v => v.id === existing.id);
        if (index !== -1) {
          updatedVoices[index] = {
            ...existing,
            metadata: mergedMetadata
          };
        }
      } else {
        // New voice - add it
        newVoices.push(discoveredVoice);
      }
    }

    if (newVoices.length > 0 || updatedVoices.length !== current.voices.length) {
      voices.set({
        formatVersion: '2.0',
        voices: [...updatedVoices, ...newVoices],
        assignments: current.assignments
      });
      await saveVoicesData();
    }

    // Auto-create assignments based on manifest data
    if (root) {
      await autoCreateAssignmentsFromManifests(audio);
    }

    console.log('[voices] Discovered', newVoices.length, 'new voices');
    return newVoices.length;
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

    // Call backend to get manifest data
    const response = await fetch('http://127.0.0.1:8010/api/scan-voice-manifests', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ audioRoot }),
    });

    if (!response.ok) return;

    const data = await response.json();
    const manifestsData: any[] = data.manifests || [];

    // Read characters.json to get character IDs
    const { readCentralCharacters } = await import('$lib/services/fs');
    const charactersData = await readCentralCharacters(root);
    if (!charactersData) return;

    // Use centralized helper to build name-to-ID map
    const { buildNameToIdMap } = await import('$lib/stores/characters');
    const nameToIdMap = buildNameToIdMap(charactersData.characters);

    const current = get(voices);
    const newAssignments = [...current.assignments];
    let assignmentCount = 0;

    // Process each manifest
    for (const manifest of manifestsData) {
      if (manifest.formatVersion === '2.0' && manifest.metadata?.primaryVoiceId) {
        const characterName = manifest.characterName;
        const characterId = nameToIdMap.get(characterName);
        const primaryVoiceId = manifest.metadata.primaryVoiceId;

        if (!characterId) {
          console.warn(`[voices] Character "${characterName}" not found in characters.json`);
          continue;
        }

        // Check if assignment already exists
        const existingAssignment = newAssignments.find(a => a.characterId === characterId);
        if (existingAssignment) {
          console.log(`[voices] Assignment already exists for ${characterName}`);
          continue;
        }

        // Find the voice in our voices list
        const voice = current.voices.find(v => v.providerVoiceId === primaryVoiceId);
        if (!voice) {
          console.warn(`[voices] Voice ${primaryVoiceId} not found for character ${characterName}`);
          continue;
        }

        // Create assignment
        newAssignments.push({
          characterId,
          voiceId: voice.id,
          priority: 1,
          contextOverrides: []
        });

        assignmentCount++;
        console.log(`[voices] Auto-assigned ${voice.displayName} to ${characterName}`);
      }
    }

    if (assignmentCount > 0) {
      voices.set({
        ...current,
        assignments: newAssignments
      });
      await saveVoicesData();
      console.log(`[voices] Created ${assignmentCount} automatic assignments`);
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

  // Group voices by provider:providerVoiceId
  const voiceGroups = new Map<string, Voice[]>();
  current.voices.forEach(v => {
    const key = `${v.provider}:${v.providerVoiceId}`;
    if (!voiceGroups.has(key)) {
      voiceGroups.set(key, []);
    }
    voiceGroups.get(key)!.push(v);
  });

  // Find duplicates and select which one to keep
  const voiceIdMapping = new Map<string, string>(); // old ID -> kept ID
  const keptVoices: Voice[] = [];
  let removedCount = 0;

  for (const [key, voiceList] of voiceGroups.entries()) {
    if (voiceList.length === 1) {
      // No duplicates
      keptVoices.push(voiceList[0]);
    } else {
      // Has duplicates - select the best one to keep
      console.log(`[voices] Found ${voiceList.length} duplicates for ${key}`);

      // Sort by: 1) has usage data, 2) total clips, 3) has notes
      const sorted = voiceList.sort((a, b) => {
        const aScore = (a.metadata.totalClips || 0) * 100 +
          (a.metadata.usedByCharacters?.length || 0) * 10 +
          (a.notes ? 1 : 0);
        const bScore = (b.metadata.totalClips || 0) * 100 +
          (b.metadata.usedByCharacters?.length || 0) * 10 +
          (b.notes ? 1 : 0);
        return bScore - aScore;
      });

      const kept = sorted[0];

      // Merge metadata from all duplicates
      const allUsedByCharacters = new Set<string>();
      let totalClips = 0;
      const allNotes: string[] = [];

      for (const voice of sorted) {
        (voice.metadata.usedByCharacters || []).forEach(c => allUsedByCharacters.add(c));
        totalClips += voice.metadata.totalClips || 0;
        if (voice.notes && !allNotes.includes(voice.notes)) {
          allNotes.push(voice.notes);
        }
      }

      const mergedVoice: Voice = {
        ...kept,
        notes: allNotes.join('; ') || kept.notes,
        metadata: {
          ...kept.metadata,
          totalClips,
          usedByCharacters: Array.from(allUsedByCharacters)
        }
      };

      keptVoices.push(mergedVoice);

      // Map all old IDs to the kept ID
      for (const voice of sorted) {
        if (voice.id !== kept.id) {
          voiceIdMapping.set(voice.id, kept.id);
          removedCount++;
          console.log(`[voices] Merging "${voice.displayName}" (${voice.id}) into "${kept.displayName}" (${kept.id})`);
        }
      }
    }
  }

  // Update assignments to use the kept voice IDs
  const updatedAssignments = current.assignments.map(a => {
    if (voiceIdMapping.has(a.voiceId)) {
      const newVoiceId = voiceIdMapping.get(a.voiceId)!;
      console.log(`[voices] Updating assignment for ${a.characterId}: ${a.voiceId} -> ${newVoiceId}`);
      return { ...a, voiceId: newVoiceId };
    }
    return a;
  });

  // Save the deduplicated data
  voices.set({
    formatVersion: '2.0',
    voices: keptVoices,
    assignments: updatedAssignments
  });

  await saveVoicesData();

  console.log(`[voices] Deduplication complete: removed ${removedCount} duplicate voices`);
  return { removed: removedCount, updated: voiceIdMapping.size };
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

