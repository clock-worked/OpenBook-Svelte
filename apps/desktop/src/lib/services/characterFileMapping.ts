// ============================================================================
// v3.0 character file schema mapping + raw-document cache.
//
// Kept in its own (fs-independent) module on purpose:
//   - `readCentralCharacters` (fs.ts) falls back to the characters/ folder,
//     so the mapper must not create an fs -> repository import cycle; and
//   - the service-layer test matrix mocks `$lib/services/fs` wholesale, so the
//     raw-document cache and mapper must stay reachable (unmocked) for the
//     in-memory fs stand-in to drive the real round-trip logic.
// ============================================================================

import type { Character } from '$lib/types';
import { CHARACTER_GUID_PATTERN, normalizeCharacterGender } from '$lib/types';

/**
 * Per-record cache of raw v3 file documents, keyed by GUID.
 *
 * The TS `Character` type is intentionally narrower than the v3 file format:
 * fields such as `roleLabels` (read by `dialogue_ai_context.py:288`) have no
 * slot on `Character`. The in-memory record is therefore lossy by design, so
 * the write path re-serializes the raw document as the base, overlaying the
 * in-memory display fields on top. Unknown raw fields (including `roleLabels`)
 * round-trip untouched.
 * The cache is disposable (schema doc §GUID spec): it is rebuilt on every
 * folder read and never authoritative.
 */
const rawCharacterFileDocs = new Map<string, Record<string, unknown>>();

/** Read-only accessor for the raw v3 document cache (write path round-trip). */
export function getRawCharacterFileDoc(guid: string): Record<string, unknown> | undefined {
  return rawCharacterFileDocs.get(guid);
}

/** Drop all cached raw documents (called at the start of a folder read). */
export function clearRawCharacterFileDocs(): void {
  rawCharacterFileDocs.clear();
}

/** Cache one raw document under its GUID (called per file during a folder read). */
export function recordRawCharacterFileDoc(guid: string, raw: Record<string, unknown>): void {
  rawCharacterFileDocs.set(guid, raw);
}

/**
 * Map a raw `characters/<Title>.json` document (v3.0) to an in-memory
 * `Character`. Returns null (and warns) for records that fail the schema
 * validation rules (formatVersion 3.0, valid GUID, non-empty title).
 *
 * `id` is the documented mirror of `guid` (`id = guid`) so existing read
 * sites keep working; the GUID is the identity, the title is display data.
 */
export function mapCharacterFile(raw: unknown): Character | null {
  if (!raw || typeof raw !== 'object') return null;
  const doc = raw as Record<string, any>;
  const guid = typeof doc.guid === 'string' ? doc.guid.trim() : '';
  const title = typeof doc.title === 'string' ? doc.title.trim() : '';

  if (doc.formatVersion !== '3.0' || !CHARACTER_GUID_PATTERN.test(guid) || !title) {
    console.warn('[bookCharacters] Skipping invalid character file record:', {
      formatVersion: doc.formatVersion,
      guid,
      title,
    });
    return null;
  }

  const stats = doc.stats ?? {};
  return {
    guid,
    id: guid, // documented mirror: in v3, id = guid
    name: title,
    gender: normalizeCharacterGender(doc.gender),
    aliases: Array.isArray(doc.aliases) ? doc.aliases.filter((alias: unknown) => typeof alias === 'string') : [],
    descriptors: Array.isArray(doc.descriptors) ? doc.descriptors.filter((descriptor: unknown) => typeof descriptor === 'string') : [],
    race: typeof doc.race === 'string' ? doc.race : undefined,
    color: doc.color ?? null,
    notes: typeof doc.notes === 'string' ? doc.notes : '',
    firstAppearance: typeof doc.firstAppearance === 'string' ? doc.firstAppearance : null,
    stats: {
      totalLines: typeof stats.totalLines === 'number' ? stats.totalLines : 0,
      chapterCount: typeof stats.chapterCount === 'number' ? stats.chapterCount : 0,
    },
    voice: doc.voice ?? null,
    provider: doc.provider ?? null,
    voiceId: doc.voiceId ?? null,
    voiceMeta: doc.voiceMeta ?? null,
    manifestStats: doc.manifestStats ?? null,
  };
}
