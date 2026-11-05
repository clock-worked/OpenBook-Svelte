// ============================================================================
// voiceDomain.ts: merge/dedup behavior for discovered voices.
//
// The key behavior under test is the local-engine switch: a voice discovered
// under one local engine (Chatterbox) that already exists under the other
// (VibeVoice) must flip the existing entry's provider IN PLACE — keeping its
// id so character assignments keep resolving — instead of adding a duplicate.
// ============================================================================

import { describe, expect, it } from 'vitest';

import type { Voice } from '$lib/types';
import {
  deduplicateVoicesAndAssignments,
  mergeDiscoveredVoices,
} from '../voiceDomain';

function makeVoice(overrides: Partial<Voice> & { id: string; providerVoiceId: string }): Voice {
  return {
    id: overrides.id,
    displayName: overrides.displayName ?? overrides.id,
    provider: (overrides.provider as Voice['provider']) ?? 'chatterbox',
    providerVoiceId: overrides.providerVoiceId,
    previewUrl: null,
    notes: overrides.notes ?? '',
    metadata: overrides.metadata ?? { discoveredFrom: 'manifest' },
  };
}

describe('mergeDiscoveredVoices', () => {
  it('adds a discovered voice that has no existing match', () => {
    const result = mergeDiscoveredVoices([], [
      makeVoice({ id: 'chatterbox-jake.wav', provider: 'chatterbox', providerVoiceId: 'jake.wav' }),
    ]);

    expect(result.mergedVoices).toHaveLength(1);
    expect(result.newVoicesCount).toBe(1);
    expect(result.hasUpdates).toBe(true);
  });

  it('merges metadata for an existing same-key voice without adding a duplicate', () => {
    const existing = makeVoice({
      id: 'chatterbox-jake.wav',
      provider: 'chatterbox',
      providerVoiceId: 'jake.wav',
      metadata: { discoveredFrom: 'manifest', totalClips: 5, usedByCharacters: ['C1'] },
    });
    const discovered = makeVoice({
      id: 'chatterbox-jake.wav',
      provider: 'chatterbox',
      providerVoiceId: 'jake.wav',
      metadata: { discoveredFrom: 'manifest', totalClips: 9, usedByCharacters: ['C2'] },
    });

    const result = mergeDiscoveredVoices([existing], [discovered]);

    expect(result.mergedVoices).toHaveLength(1);
    expect(result.newVoicesCount).toBe(0);
    expect(result.mergedVoices[0].metadata.totalClips).toBe(9);
    expect(result.mergedVoices[0].metadata.usedByCharacters).toEqual(['C1', 'C2']);
  });

  it('flips a VibeVoice voice to Chatterbox in place when the manifest rediscovers it', () => {
    const existing = makeVoice({
      id: 'vibevoice_local-jake.wav',
      provider: 'vibevoice_local',
      providerVoiceId: 'jake.wav',
      metadata: { discoveredFrom: 'manifest', totalClips: 3, usedByCharacters: ['C1'] },
    });
    const discovered = makeVoice({
      id: 'chatterbox-jake.wav',
      provider: 'chatterbox',
      providerVoiceId: 'jake.wav',
      metadata: { discoveredFrom: 'manifest', totalClips: 12, usedByCharacters: ['C1', 'C2'] },
    });

    const result = mergeDiscoveredVoices([existing], [discovered]);

    // No duplicate: the existing entry is flipped, not supplemented.
    expect(result.mergedVoices).toHaveLength(1);
    expect(result.newVoicesCount).toBe(0);
    expect(result.hasUpdates).toBe(true);

    const flipped = result.mergedVoices[0];
    // Id is preserved so assignments pointing at it keep resolving.
    expect(flipped.id).toBe('vibevoice_local-jake.wav');
    expect(flipped.provider).toBe('chatterbox');
    expect(flipped.providerVoiceId).toBe('jake.wav');
    // Metadata merged from the fresh manifest scan; discovery source kept.
    expect(flipped.metadata.totalClips).toBe(12);
    expect(flipped.metadata.usedByCharacters).toEqual(['C1', 'C2']);
    expect(flipped.metadata.discoveredFrom).toBe('manifest');
  });

  it('does not flip across engine families (cloud provider vs local engine)', () => {
    // 'elevenlabs' stands in for any non-local provider (outside the
    // TtsProvider union) to prove the flip never crosses engine families.
    const existing = makeVoice({
      id: 'elevenlabs-jake',
      provider: 'elevenlabs' as unknown as Voice['provider'],
      providerVoiceId: 'jake',
      metadata: { discoveredFrom: 'manual' },
    });
    const discovered = makeVoice({
      id: 'chatterbox-jake',
      provider: 'chatterbox',
      providerVoiceId: 'jake',
      metadata: { discoveredFrom: 'manifest' },
    });

    const result = mergeDiscoveredVoices([existing], [discovered]);

    // Different families: both voices are kept.
    expect(result.mergedVoices).toHaveLength(2);
    expect(result.newVoicesCount).toBe(1);
    expect(result.mergedVoices[0].provider).toBe('elevenlabs');
  });
});

describe('deduplicateVoicesAndAssignments', () => {
  it('collapses a stale duplicate left by a pre-fix discovery run and remaps assignments', () => {
    // A pre-fix book load may have persisted BOTH the flipped entry and a
    // duplicate 'chatterbox:JAKE' entry. Loading dedupes them and points
    // assignments at the kept id.
    const voices: Voice[] = [
      makeVoice({
        id: 'vibevoice_local-jake.wav',
        provider: 'chatterbox', // already flipped in place
        providerVoiceId: 'jake.wav',
        metadata: { totalClips: 12, usedByCharacters: ['C1'] },
      }),
      makeVoice({
        id: 'chatterbox-jake.wav',
        provider: 'chatterbox',
        providerVoiceId: 'jake.wav',
        metadata: { totalClips: 4, usedByCharacters: ['C1'] },
      }),
    ];
    const assignments = [
      { characterId: 'C1', voiceId: 'chatterbox-jake.wav', priority: 1, contextOverrides: [] },
      { characterId: 'C2', voiceId: 'vibevoice_local-jake.wav', priority: 1, contextOverrides: [] },
    ];

    const result = deduplicateVoicesAndAssignments(voices, assignments);

    expect(result.voices).toHaveLength(1);
    const keptId = result.voices[0].id;
    expect(['vibevoice_local-jake.wav', 'chatterbox-jake.wav']).toContain(keptId);
    // Every assignment now points at the kept voice.
    for (const assignment of result.assignments) {
      expect(assignment.voiceId).toBe(keptId);
    }
  });
});
