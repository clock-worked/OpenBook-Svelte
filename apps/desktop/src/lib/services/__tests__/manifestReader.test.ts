// The manifest reader's fallback chain (services/manifestReader.ts):
// reconcile endpoint -> absolute read -> relative read, the 404 latches held
// by the injected ManifestApiProbe, and the cache / invalidation API.

import { describe, expect, it } from 'vitest';

import {
  createManifestReader,
  createSessionApiProbe,
  type ManifestApiProbe,
  type ManifestFetchResult,
  type ManifestTransport,
} from '../manifestReader';
import type { AudioManifest } from '../audio';

const ROOT = '/books/demo/audio';
const CHAPTER = 'Chapter 1';
const CHARACTER = 'Alice';

function manifest(tag: string): AudioManifest {
  return {
    formatVersion: '2.0',
    characterId: tag,
    characterName: CHARACTER,
    metadata: {
      totalClips: 0,
      chapters: [],
      sources: {},
      voiceIds: {},
      primaryVoiceId: null,
      lastUpdated: '',
    },
    clips: [],
  };
}

type Step = ManifestFetchResult | Error;

interface FakeTransportScript {
  reconcile?: Step[];
  absolute?: ManifestFetchResult[];
  relative?: Array<AudioManifest | null | Error>;
}

function fakeTransport(script: FakeTransportScript) {
  const calls: string[] = [];
  const absolutePaths: string[] = [];
  const relativePaths: string[] = [];
  const next = <T>(queue: T[] | undefined, name: string): T => {
    if (!queue || queue.length === 0) throw new Error(`unexpected ${name} call`);
    return queue.length === 1 ? queue[0] : queue.shift()!;
  };
  const transport: ManifestTransport = {
    async reconcile() {
      calls.push('reconcile');
      const step = next(script.reconcile, 'reconcile');
      if (step instanceof Error) throw step;
      return step;
    },
    async readAbsolute(filePath) {
      calls.push('absolute');
      absolutePaths.push(filePath);
      return next(script.absolute, 'absolute');
    },
    async readRelative(relativePath) {
      calls.push('relative');
      relativePaths.push(relativePath);
      const step = next(script.relative, 'relative');
      if (step instanceof Error) throw step;
      return step;
    },
  };
  return { transport, calls, absolutePaths, relativePaths };
}

const silent = { log() {}, warn() {}, error() {} };

function makeReader(
  script: FakeTransportScript,
  options: { root?: string | null; probe?: ManifestApiProbe } = {},
) {
  const fake = fakeTransport(script);
  const probe = options.probe ?? createSessionApiProbe();
  const root = options.root === undefined ? ROOT : options.root;
  const reader = createManifestReader({
    transport: fake.transport,
    resolveAudioRoot: () => root,
    probe,
    logger: silent,
  });
  return { reader, probe, ...fake };
}

describe('manifest reader fallback chain', () => {
  it('returns the reconciled manifest and caches it', async () => {
    const reconciled = manifest('reconciled');
    const { reader, calls } = makeReader({ reconcile: [{ kind: 'ok', manifest: reconciled }] });

    expect(await reader.read(CHAPTER, CHARACTER)).toBe(reconciled);
    expect(await reader.read(CHAPTER, CHARACTER.toUpperCase())).toBe(reconciled); // key ignores name case
    expect(calls).toEqual(['reconcile']);
  });

  it('falls back to the absolute read when reconcile has no manifest', async () => {
    const raw = manifest('raw');
    const { reader, calls, absolutePaths, probe } = makeReader({
      reconcile: [{ kind: 'ok', manifest: null }],
      absolute: [{ kind: 'ok', manifest: raw }],
    });

    expect(await reader.read(CHAPTER, CHARACTER)).toBe(raw);
    expect(calls).toEqual(['reconcile', 'absolute']);
    expect(absolutePaths).toEqual([`${ROOT}/${CHAPTER}/audio_lines/${CHARACTER}/manifest.json`]);
    expect(probe.isMissing('reconcile')).toBe(false);
    expect(probe.isMissing('absoluteRead')).toBe(false);
  });

  it('latches reconcile off after a 404 and skips it on later reads', async () => {
    const raw = manifest('raw');
    const { reader, calls, probe } = makeReader({
      reconcile: [{ kind: 'missing' }],
      absolute: [{ kind: 'ok', manifest: raw }],
    });

    expect(await reader.read(CHAPTER, CHARACTER)).toBe(raw);
    expect(probe.isMissing('reconcile')).toBe(true);

    reader.invalidate(CHAPTER, CHARACTER);
    expect(await reader.read(CHAPTER, CHARACTER)).toBe(raw);
    expect(calls).toEqual(['reconcile', 'absolute', 'absolute']);
  });

  it('does not latch on a reconcile failure that is not a 404', async () => {
    const raw = manifest('raw');
    const { reader, calls, probe } = makeReader({
      reconcile: [new Error('500 boom')],
      absolute: [{ kind: 'ok', manifest: raw }],
    });

    expect(await reader.read(CHAPTER, CHARACTER)).toBe(raw);
    reader.invalidate(CHAPTER, CHARACTER);
    await reader.read(CHAPTER, CHARACTER);
    expect(probe.isMissing('reconcile')).toBe(false);
    expect(calls).toEqual(['reconcile', 'absolute', 'reconcile', 'absolute']);
  });

  it('latches the absolute read off after a 404 and reads relative from then on', async () => {
    const relative = manifest('relative');
    const { reader, calls, relativePaths, probe } = makeReader({
      reconcile: [{ kind: 'ok', manifest: null }],
      absolute: [{ kind: 'missing' }],
      relative: [relative],
    });

    expect(await reader.read(CHAPTER, CHARACTER)).toBe(relative);
    expect(probe.isMissing('absoluteRead')).toBe(true);
    expect(relativePaths).toEqual([`${CHAPTER}/audio_lines/${CHARACTER}/manifest.json`]);

    // With absolute reads latched off, even reconcile is skipped.
    expect(await reader.read(CHAPTER, 'Bob')).toBe(relative);
    expect(calls).toEqual(['reconcile', 'absolute', 'relative', 'relative']);
  });

  it('returns (and caches) null for another absolute-read status', async () => {
    const { reader, calls } = makeReader({
      reconcile: [{ kind: 'ok', manifest: null }],
      absolute: [{ kind: 'failed', status: 400 }],
    });

    expect(await reader.read(CHAPTER, CHARACTER)).toBeNull();
    expect(await reader.read(CHAPTER, CHARACTER)).toBeNull();
    expect(calls).toEqual(['reconcile', 'absolute']);
  });

  it('reads relative only when no audio root resolves', async () => {
    const relative = manifest('relative');
    const { reader, calls } = makeReader({ relative: [null, relative] }, { root: null });

    expect(await reader.read(CHAPTER, CHARACTER)).toBeNull();
    expect(await reader.read(CHAPTER, 'Bob')).toBe(relative);
    expect(calls).toEqual(['relative', 'relative']);
  });

  it('honours an injected probe', async () => {
    const probe = createSessionApiProbe();
    probe.markMissing('reconcile');
    const raw = manifest('raw');
    const { reader, calls } = makeReader({ absolute: [{ kind: 'ok', manifest: raw }] }, { probe });

    expect(await reader.read(CHAPTER, CHARACTER)).toBe(raw);
    expect(calls).toEqual(['absolute']);
  });

  it('turns transport errors into a null read', async () => {
    const { reader } = makeReader({ relative: [new Error('disk gone')] }, { root: null });
    expect(await reader.read(CHAPTER, CHARACTER)).toBeNull();
  });
});

describe('manifest reader cache', () => {
  it('shares one request between concurrent reads', async () => {
    const reconciled = manifest('reconciled');
    const { reader, calls } = makeReader({ reconcile: [{ kind: 'ok', manifest: reconciled }] });

    const [a, b] = await Promise.all([reader.read(CHAPTER, CHARACTER), reader.read(CHAPTER, CHARACTER)]);
    expect(a).toBe(reconciled);
    expect(b).toBe(reconciled);
    expect(calls).toEqual(['reconcile']);
  });

  it('re-reads after invalidate and clear', async () => {
    const { reader, calls } = makeReader({ reconcile: [{ kind: 'ok', manifest: manifest('m') }] });

    await reader.read(CHAPTER, CHARACTER);
    reader.invalidate(CHAPTER, CHARACTER);
    await reader.read(CHAPTER, CHARACTER);
    reader.clear();
    await reader.read(CHAPTER, CHARACTER);
    expect(calls).toEqual(['reconcile', 'reconcile', 'reconcile']);
  });

  it('serves a stored manifest without a request', async () => {
    const stored = manifest('stored');
    const { reader, calls } = makeReader({});

    reader.store(CHAPTER, CHARACTER, stored);
    expect(await reader.read(CHAPTER, CHARACTER)).toBe(stored);
    expect(calls).toEqual([]);
  });

  it('does not cache a result invalidated while in flight', async () => {
    const first = manifest('first');
    const second = manifest('second');
    const { reader } = makeReader({
      reconcile: [
        { kind: 'ok', manifest: first },
        { kind: 'ok', manifest: second },
      ],
    });

    const pending = reader.read(CHAPTER, CHARACTER);
    reader.invalidate(CHAPTER, CHARACTER);
    expect(await pending).toBe(first);
    expect(await reader.read(CHAPTER, CHARACTER)).toBe(second);
  });
});

describe('createSessionApiProbe', () => {
  it('treats unknown endpoints as available until a 404 is recorded', () => {
    const probe = createSessionApiProbe();
    expect(probe.isMissing('reconcile')).toBe(false);
    probe.markMissing('reconcile');
    expect(probe.isMissing('reconcile')).toBe(true);
    expect(probe.isMissing('absoluteRead')).toBe(false);
    probe.markAvailable('reconcile');
    expect(probe.isMissing('reconcile')).toBe(false);
  });
});
