// ============================================================================
// Shared fixture factory for the v3 character service-layer matrix.
// Builds a schema-complete characters/<Title>.json record (docs/schema_characters_v3.md
// §File format) so tests can override only the fields they care about.
// ============================================================================

import type { CharacterFile } from '$lib/types';

export function makeCharacterFile(
  overrides: Partial<CharacterFile> & { guid: string; title: string },
): CharacterFile {
  return {
    formatVersion: '3.0',
    gender: 'Unknown',
    aliases: [],
    descriptors: [],
    race: null,
    color: null,
    notes: '',
    firstAppearance: null,
    stats: { totalLines: 0, chapterCount: 0 },
    voice: null,
    provider: null,
    voiceId: null,
    voiceMeta: null,
    manifestStats: null,
    roleLabels: [],
    updatedAt: '2026-10-06T00:00:00.000Z',
    ...overrides,
  };
}
