// ============================================================================
// Vitest service-layer matrix — chapterCharacterRepository.ts.
//
// Pins the resolveBookCharacters resolution order the parser now consumes:
// the v3 characters/ folder first, the legacy root characters.json as
// fallback, null when the book has neither.
//
// I/O is stubbed by the in-memory fs mock (helpers/inMemoryFs.ts).
// ============================================================================

import { beforeEach, describe, expect, it, vi } from 'vitest';
import { inMemoryFs } from './helpers/inMemoryFs';
import { makeCharacterFile } from './helpers/fixtures';

vi.mock('$lib/services/fs', async () => {
  const mod = await import('./helpers/inMemoryFs');
  return mod.inMemoryFs;
});

import { resolveBookCharacters } from '../chapterCharacterRepository';

const LEGACY_STORE = {
  formatVersion: '2.0',
  characters: [
    {
      guid: null,
      id: 'mara',
      name: 'Mara',
      gender: 'Female',
      aliases: [],
      color: null,
      notes: '',
      firstAppearance: null,
      stats: { totalLines: 0, chapterCount: 0 },
    },
  ],
};

describe('chapterCharacterRepository.resolveBookCharacters', () => {
  beforeEach(() => {
    inMemoryFs.__reset();
  });

  it('v3 folder wins over the legacy store when both are present', async () => {
    inMemoryFs.__seed({
      'characters/Catherine.json': makeCharacterFile({ guid: '7KQX2MFT', title: 'Catherine', gender: 'Female' }),
      'characters.json': LEGACY_STORE,
    });

    const resolved = await resolveBookCharacters('');

    expect(resolved).not.toBeNull();
    expect(resolved!.formatVersion).toBe('3.0');
    expect(resolved!.characters).toHaveLength(1);
    expect(resolved!.characters[0].id).toBe('7KQX2MFT'); // id = guid mirror
    expect(resolved!.characters[0].name).toBe('Catherine');
  });

  it('falls back to the legacy characters.json when there is no v3 folder', async () => {
    inMemoryFs.__seed({ 'characters.json': LEGACY_STORE });

    const resolved = await resolveBookCharacters('');

    expect(resolved).toEqual(LEGACY_STORE);
  });

  it('returns null when the book has neither a folder nor a legacy store', async () => {
    inMemoryFs.__seed({ 'Chapter1/chapter.txt': 'nothing to resolve' });

    const resolved = await resolveBookCharacters('');

    expect(resolved).toBeNull();
  });

  it('invalid folder files are skipped and the legacy store is used', async () => {
    inMemoryFs.__seed({
      // Bad GUID + wrong formatVersion: mapCharacterFile rejects both.
      'characters/Bad.json': {
        formatVersion: '2.0',
        guid: 'not-a-guid',
        title: 'Bad',
        gender: 'Unknown',
        aliases: [],
        descriptors: [],
        color: null,
        notes: '',
        firstAppearance: null,
        stats: { totalLines: 0, chapterCount: 0 },
      },
      'characters.json': LEGACY_STORE,
    });

    const resolved = await resolveBookCharacters('');

    expect(resolved).toEqual(LEGACY_STORE);
  });
});
