// Cross-language parity for TTS provider classification.
//
// classifyProvider (services/audioGenerator.ts) must agree with the BE
// registry (py_services/audio/provider_profile.py) on every vector in
// tests/provider_profile_vectors.json, which the Python side asserts in
// py_services/test_provider_profile_parity.py. Keep both green when
// changing either side.

import { readFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import { describe, expect, it, vi } from 'vitest';

// audioGenerator reads the audiobookSettings store; classification does not.
vi.mock('$lib/stores/settings', () => ({
  audiobookSettings: { subscribe: (fn: (value: unknown) => void) => (fn({}), () => {}) },
}));

import { classifyProvider, knownProviderKeys } from '../audioGenerator';

interface ProviderVector {
  provider: string;
  canonical: string;
  endpointKind: string;
  fileExtension: string;
}

interface ProviderProfileVectors {
  registered: string[];
  providers: ProviderVector[];
  unknown: string[];
}

const VECTORS_PATH = fileURLToPath(
  new URL('../../../../../../tests/provider_profile_vectors.json', import.meta.url),
);

const vectors: ProviderProfileVectors = JSON.parse(readFileSync(VECTORS_PATH, 'utf8'));

describe('provider profile parity vectors (tests/provider_profile_vectors.json)', () => {
  it('accepts exactly the registered provider strings', () => {
    expect(knownProviderKeys()).toEqual([...vectors.registered].sort());
  });

  it.each(vectors.providers)('classifyProvider($provider) → $canonical / $endpointKind / $fileExtension', (vector) => {
    expect(classifyProvider(vector.provider)).toEqual({
      provider: vector.canonical,
      endpointKind: vector.endpointKind,
      fileExtension: vector.fileExtension,
    });
  });

  it.each(vectors.unknown)('classifyProvider(%j) is null (unknown)', (provider) => {
    expect(classifyProvider(provider)).toBeNull();
  });
});
