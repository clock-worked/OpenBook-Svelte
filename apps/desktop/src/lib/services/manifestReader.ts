// The character audio manifest reader: one cache, one fallback chain.
//
// audio.ts builds the app's single instance (`readManifest` and friends) over
// the HTTP transport; this module has no runtime imports so the chain is
// testable with a fake transport (__tests__/manifestReader.test.ts).
//
// Fallback chain, for a configured audio root:
//   1. POST /api/reconcile-audio-manifest  (aligns with disk, returns v2)
//   2. POST /api/audio-manifest            (stored manifest, read-only)
//   3. book-root-relative read             (fs transport)
// Steps 1 and 2 address the manifest by chapter + character; the backend
// resolves the path inside the audio root, so no file path crosses the wire.
// Older sidecars lack 1 and/or 2. A 404 from one marks it missing in the
// reader's ManifestApiProbe for the probe's lifetime (the session, by
// default), and later reads skip it. Without an audio root only 3 is tried.

import type { AudioManifest } from './audio';

/** The optional sidecar endpoints the chain probes. */
export type ManifestApi = 'reconcile' | 'manifestRead';

/**
 * Remembers which optional endpoints the sidecar has. `isMissing` is true
 * only after a 404 was recorded; unknown counts as available.
 */
export interface ManifestApiProbe {
  isMissing(api: ManifestApi): boolean;
  markAvailable(api: ManifestApi): void;
  markMissing(api: ManifestApi): void;
}

/** Latches that last as long as the probe (a fresh page load resets them). */
export function createSessionApiProbe(): ManifestApiProbe {
  const state = new Map<ManifestApi, boolean>();
  return {
    isMissing: (api) => state.get(api) === false,
    markAvailable: (api) => {
      state.set(api, true);
    },
    markMissing: (api) => {
      state.set(api, false);
    },
  };
}

/** One endpoint call: the manifest (or none), or the endpoint itself is missing (404). */
export type ManifestFetchResult =
  | { kind: 'ok'; manifest: AudioManifest | null }
  | { kind: 'missing' }
  | { kind: 'failed'; status: number };

export interface ManifestTransport {
  /** Reconcile endpoint. Throws for failures other than a 404. */
  reconcile(request: {
    chapterTitle: string;
    characterName: string;
    audioRoot: string;
  }): Promise<ManifestFetchResult>;
  /** Read-only stored-manifest endpoint. */
  readStored(request: {
    chapterTitle: string;
    characterName: string;
    audioRoot: string;
  }): Promise<ManifestFetchResult>;
  /** Book-root-relative read; `null` when the file is absent. */
  readRelative(relativePath: string): Promise<AudioManifest | null>;
}

type ManifestLogger = Pick<Console, 'log' | 'warn' | 'error'>;

export interface ManifestReaderDeps {
  transport: ManifestTransport;
  /** The effective audio root (audio.ts: `audioRootFor`). */
  resolveAudioRoot: () => string | null;
  probe?: ManifestApiProbe;
  logger?: ManifestLogger;
}

export interface ManifestReader {
  /** The manifest for one character in one chapter; cached (including `null`). */
  read(chapterTitle: string, characterName: string): Promise<AudioManifest | null>;
  /** Record a manifest obtained elsewhere (e.g. an explicit reconcile). */
  store(chapterTitle: string, characterName: string, manifest: AudioManifest | null): void;
  invalidate(chapterTitle: string, characterName: string): void;
  clear(): void;
}

export function manifestRelativePath(chapterTitle: string, characterName: string): string {
  return `${chapterTitle}/audio_lines/${characterName}/manifest.json`;
}

function cacheKey(chapterTitle: string, characterName: string): string {
  return `${chapterTitle}::${characterName.toLowerCase()}`;
}

export function createManifestReader(deps: ManifestReaderDeps): ManifestReader {
  const { transport, resolveAudioRoot } = deps;
  const probe = deps.probe ?? createSessionApiProbe();
  const logger = deps.logger ?? console;

  const cache = new Map<string, AudioManifest | null>();
  const inFlight = new Map<string, Promise<AudioManifest | null>>();
  let warnedMissingAudioRoot = false;

  async function fetchManifest(chapterTitle: string, characterName: string): Promise<AudioManifest | null> {
    const relativePath = manifestRelativePath(chapterTitle, characterName);
    const root = resolveAudioRoot();
    if (!root) {
      const relativeManifest = await transport.readRelative(relativePath);
      if (relativeManifest) return relativeManifest;
      if (!warnedMissingAudioRoot) {
        logger.warn('[audio] Audio root not set; skipping manifest reads');
        warnedMissingAudioRoot = true;
      }
      return null;
    }

    if (probe.isMissing('manifestRead')) {
      return transport.readRelative(relativePath);
    }

    if (!probe.isMissing('reconcile')) {
      try {
        const reconciled = await transport.reconcile({ chapterTitle, characterName, audioRoot: root });
        if (reconciled.kind === 'missing') {
          probe.markMissing('reconcile');
          logger.warn('[audio] /api/reconcile-audio-manifest returned 404; disabling manifest reconciliation for this session');
        } else {
          probe.markAvailable('reconcile');
          if (reconciled.kind === 'ok' && reconciled.manifest && typeof reconciled.manifest === 'object') {
            return reconciled.manifest;
          }
        }
      } catch (reconcileError) {
        logger.warn('[audio] Manifest reconciliation failed; falling back to manifest read:', reconcileError);
      }
    }

    const stored = await transport.readStored({ chapterTitle, characterName, audioRoot: root });
    if (stored.kind === 'ok') {
      probe.markAvailable('manifestRead');
      logger.log('[audio] Manifest loaded for', characterName, '- clips:', stored.manifest?.clips?.length);
      return stored.manifest;
    }
    if (stored.kind === 'missing') {
      probe.markMissing('manifestRead');
      logger.warn('[audio] /api/audio-manifest returned 404; disabling stored manifest reads for this session');
      return transport.readRelative(relativePath);
    }
    logger.log('[audio] Manifest not found for', characterName, '- status:', stored.status);
    return null;
  }

  return {
    async read(chapterTitle, characterName) {
      const key = cacheKey(chapterTitle, characterName);
      if (cache.has(key)) return cache.get(key) ?? null;
      const pending = inFlight.get(key);
      if (pending) return pending;

      const request = fetchManifest(chapterTitle, characterName).catch((error) => {
        logger.error('[audio] Error reading manifest:', error);
        return null;
      });
      inFlight.set(key, request);
      const result = await request;
      // An invalidate() during the request drops it from inFlight; don't
      // cache a result that was invalidated mid-flight.
      if (inFlight.get(key) === request) {
        inFlight.delete(key);
        cache.set(key, result);
      }
      return result;
    },
    store(chapterTitle, characterName, manifest) {
      const key = cacheKey(chapterTitle, characterName);
      inFlight.delete(key);
      cache.set(key, manifest);
    },
    invalidate(chapterTitle, characterName) {
      const key = cacheKey(chapterTitle, characterName);
      cache.delete(key);
      inFlight.delete(key);
    },
    clear() {
      cache.clear();
      inFlight.clear();
    },
  };
}
