// ============================================================================
// v2/v1 → v3.0 character folder migration — FE auto-on-open driver (ruling R9)
// ============================================================================
// Implements the SAME five-stage algorithm as the Python ops script
// scripts/python/ai/migrate_characters_v3.py (normative:
// docs/schema_characters_v3.md §Migration + §GUID spec, and
// docs/character_details_alias_design.md §Migration):
//
//   0. Idempotency gate (read-only)
//        - characters/ holds files AND the legacy store is present → ABORT
//          (crashed prior run / in-flight; never guess — the report says to
//          restore from _backups/character-v3/<ts>/ or re-run). Checked
//          FIRST: a partial folder may already hold valid v3.0 files.
//        - characters/ holds ≥1 valid v3.0 file (formatVersion "3.0" +
//          pattern-valid GUID) with all GUIDs unique → already migrated,
//          no-op success.
//   1. Inventory (read-only): parse the legacy store (utf-8 BOM tolerated);
//      validate every record (id + name, case-insensitively unique ids, no
//      ambiguous name/alias, no case-insensitive filename collision); walk
//      every reference family: dialogue (lines[].characterId,
//      lines[].candidates[].characterId,
//      lines[].attribution.candidates[].characterId,
//      stats.characterBreakdown keys), rosters (<Ch>/<Ch>.characters.json),
//      voices.json (assignments + usedByCharacters), audio manifests.
//   2. Deterministic minting: SHA-256("<book-name>::v3::<v2-id>") → first 5
//      bytes → 8-char Crockford base32 (uppercase; the SAME table as
//      py_services/character_store.py). Mint order: clusters sorted by
//      firstAppearance (nulls last) then title (casefold).
//   3. Validation gates: any hit → abort, zero writes (parse failures,
//      duplicate ids, duplicate filenames, ambiguous names, unresolvable
//      dialogue/voices/manifest references, unresolvable roster entries,
//      minted-GUID collisions, in-flight co-existence).
//   4. Backup: _backups/character-v3/<UTC ts>/ mirroring the relative path
//      of every file that will change (the legacy store + every touched
//      reference file).
//   5. Ordered apply (crash-safe): (a) write all characters/<Title>.json →
//      (b) remap + write every touched reference file (exact
//      old-id → GUID substitution at every inventoried site) → (c) LAST,
//      delete the legacy store → (d) post-apply rescan asserting zero
//      occurrences of any v2 slug across all inventoried reference families.
//
// Crash-safety: the stage ordering plus the stage-0 idempotency gate make a
// mid-run crash resumable by re-open. A crash between stages 4 and 5 leaves
// the in-flight state (folder + legacy store), which the gate aborts with
// restore guidance instead of guessing.
//
// All I/O goes through the fs.ts primitives (dual-path: File System Access
// API or backend); no direct fetch in this module.
//
// Lockstep with the Python script: identical GUID derivation, v3 field
// mapping, stage ordering, abort conditions, and backup layout. Two
// deliberate, documented deltas:
//   - Manifest discovery: the script globs audio_lines/**/manifest.json
//     recursively; the FE discovers manifests from chapter × known character
//     surface names (title + aliases) because the fs primitives expose no
//     recursive directory listing.
//   - Backup fidelity: the script copies byte-identical pre-images
//     (shutil.copy2); the fs primitives re-serialize JSON, so the FE backup
//     is a JSON-equivalent pre-image.
// ============================================================================

import { CHARACTER_GUID_PATTERN } from '$lib/types';
import type { CharactersJson, DialogueJson, VoicesJson } from '$lib/types';
import {
    deleteFile,
    getRootDirInfo,
    listCharacterFiles,
    readCharacterFile,
    readTextFile,
    writeCharacters,
    writeCharacterFile,
    writeDialogue,
    writeVoices,
} from '$lib/services/fs';

// === Pinned public interface (Lane 5 imports this; do not deviate) ===

export interface V3MigrationResult {
    ok: boolean;
    abortReason?: string; // set when !ok
    changedFiles: string[]; // every rel path written or deleted
    createdCharacterFiles: string[];
    retiredLegacy: boolean; // the legacy store file was deleted
    remap: Record<string, string>; // v2 id/slug → guid
    counts: {
        clusters: number;
        dialogueRefs: number;
        rosterRefs: number;
        voiceRefs: number;
        manifestRefs: number;
    };
}

/**
 * Migrate a v2 book (root `characters.json`) to the v3.0 `characters/`
 * folder. Auto-on-open driver: runs the full five-stage algorithm and is
 * idempotent (stage 0). Never throws: every abort/failure is reported via
 * the result so book opening can fall back to the v2 load.
 */
export async function migrateCharactersV2ToV3(
    root: string,
    chapterNames: string[],
): Promise<V3MigrationResult> {
    return migrateStoreToV3(root, chapterNames, {
        mode: 'v2',
        legacyRelPath: 'characters.json',
        loadRecords: async () => {
            const doc = await readInventoried(root, 'characters.json');
            if (doc == null) return null;
            if (!Array.isArray(doc?.characters)) {
                throw new MigrationAbort('characters.json: expected a "characters" array');
            }
            return doc.characters.filter((entry) => entry != null && typeof entry === 'object');
        },
    });
}

/**
 * Migrate a v1 book (root `book.characters.json`, name-keyed, m/f/u
 * genders; ruling R11). Synthesizes v2 records in memory (slug ids, gender
 * mapped bijectively, aliases/color carried, zero stats) and runs the
 * identical v2 → v3 pipeline. The v1 store is retired (deleted) last, and
 * chapters with only a v1 script.json are left name-based and flagged.
 */
export async function migrateCharactersV1ToV3(
    root: string,
    chapterNames: string[],
): Promise<V3MigrationResult> {
    return migrateStoreToV3(root, chapterNames, {
        mode: 'v1',
        legacyRelPath: 'book.characters.json',
        loadRecords: async () => {
            const doc = await readInventoried(root, 'book.characters.json');
            if (doc == null) return null;
            if (!Array.isArray(doc?.characters)) {
                throw new MigrationAbort('book.characters.json: expected a "characters" array');
            }
            return synthesizeV2Records(doc.characters);
        },
    });
}

// === Constants (normative: docs/schema_characters_v3.md) ===

const FORMAT_VERSION = '3.0';
const NARRATOR_SENTINEL = 'narrator';
const BACKUP_DIR_PARTS = ['_backups', 'character-v3'] as const;
const INVALID_FILENAME_CHARS = /[/\\:*?"<>|\u0000-\u001f\u007f]/g;
const CONSECUTIVE_DASHES = /-{2,}/g;
const WINDOWS_RESERVED_NAMES = new Set([
    'CON', 'PRN', 'AUX', 'NUL',
    ...Array.from({ length: 9 }, (_, i) => `COM${i + 1}`),
    ...Array.from({ length: 9 }, (_, i) => `LPT${i + 1}`),
]);
// Crockford base32, uppercase: 0-9 + A-Z minus I, L, O, U (32 symbols).
// MUST stay identical to CROCKFORD_BASE32_ALPHABET in py_services/character_store.py.
const CROCKFORD_BASE32_ALPHABET = '0123456789ABCDEFGHJKMNPQRSTVWXYZ';

class MigrationAbort extends Error {}

// === Small shared helpers (mirror the Python script byte-for-byte) ===

function cleanText(value: unknown): string | null {
    if (typeof value !== 'string') return null;
    const cleaned = value.trim();
    return cleaned || null;
}

function lookupKey(value: unknown): string {
    return String(value ?? '').trim().toLowerCase();
}

/** Conservative legacy-name variants that may already be central IDs
 *  (identical to id_candidates() in migrate_chapter_character_ids.py). */
function idCandidates(value: string): string[] {
    const slug = value
        .toLowerCase()
        .replace(/[^a-z0-9\s-]/g, '')
        .replace(/[\s-]+/g, '-')
        .replace(/^-+|-+$/g, '');
    if (!slug) return [];
    const candidates = [slug, `the-${slug}`];
    if (slug.endsWith('s') && slug.length > 1) {
        const singular = slug.slice(0, -1);
        candidates.push(singular, `the-${singular}`);
    }
    return candidates;
}

/**
 * `characters/` filename stem (mirror of py_services/character_store.py
 * `sanitize_filename`): replace `\/:*?"<>|` + control chars with `-`,
 * collapse repeats, trim, cap 80 chars, guard Windows reserved names.
 * (The migration aborts on case-insensitive collisions, so no GUID
 * fallback naming is needed here.)
 */
function sanitizeCharacterFileStem(title: string): string {
    let text = String(title ?? '').replace(INVALID_FILENAME_CHARS, '-');
    text = text.replace(CONSECUTIVE_DASHES, '-').trim().replace(/^-+|-+$/g, '');
    text = text.slice(0, 80).replace(/-+$/g, '');
    if (!text) return '-';
    if (WINDOWS_RESERVED_NAMES.has(text.toUpperCase())) return `-${text}`;
    return text;
}

function stripBom(text: string): string {
    return text.charCodeAt(0) === 0xfeff ? text.slice(1) : text;
}

/** UTC timestamp for the backup dir: <YYYYMMDDTHHMMSS>Z (house format). */
function backupTimestamp(): string {
    return new Date().toISOString().replace(/[-:]/g, '').replace(/\.\d{3}Z$/, 'Z');
}

/** The book's directory name — the input to the deterministic GUID mint. */
function resolveBookName(root: string): string {
    const handleName = getRootDirInfo().name;
    if (handleName && handleName !== 'none') return handleName;
    const trimmed = String(root ?? '').replace(/[\\/]+$/, '');
    const segment = trimmed.split(/[\\/]/).filter(Boolean).pop();
    return segment || 'unknown';
}

// === Deterministic GUID minting (SHA-256 → 5 bytes → Crockford base32) ===

async function sha256First5Bytes(input: string): Promise<Uint8Array> {
    const digest = await crypto.subtle.digest('SHA-256', new TextEncoder().encode(input));
    return new Uint8Array(digest.slice(0, 5));
}

/** Encode exactly 5 big-endian bytes (40 bits) as an 8-char uppercase GUID. */
function crockfordFrom5Bytes(bytes: Uint8Array): string {
    let value = 0;
    for (let i = 0; i < 5; i++) value = value * 256 + bytes[i];
    const chars: string[] = [];
    for (let i = 0; i < 8; i++) {
        chars.push(CROCKFORD_BASE32_ALPHABET[value % 32]);
        value = Math.floor(value / 32);
    }
    return chars.reverse().join('');
}

/**
 * Mint deterministic GUIDs for every cluster, in mint order (sorted by
 * firstAppearance, nulls last, then title). A collision is a validation
 * abort (returned via `collision`), never a silent re-mint.
 */
async function mintDeterministicGuids(
    bookName: string,
    orderedRecords: any[],
): Promise<{ remap: Record<string, string>; collision: string | null }> {
    const remap: Record<string, string> = {};
    const taken = new Set<string>();
    for (const record of orderedRecords) {
        const v2Id = cleanText(record?.id);
        if (!v2Id) continue; // validation reports this separately
        const bytes = await sha256First5Bytes(`${bookName}::v3::${v2Id}`);
        const guid = crockfordFrom5Bytes(bytes);
        if (taken.has(guid)) return { remap, collision: guid };
        taken.add(guid);
        remap[v2Id] = guid;
    }
    return { remap, collision: null };
}

/** Mint order: firstAppearance (nulls last) then title (code-point order). */
function sortClustersForMinting(records: any[]): any[] {
    const codePointCompare = (a: string, b: string): number => (a < b ? -1 : a > b ? 1 : 0);
    return [...records].sort((left, right) => {
        const fa = typeof left?.firstAppearance === 'string' ? left.firstAppearance : null;
        const fb = typeof right?.firstAppearance === 'string' ? right.firstAppearance : null;
        if (fa === null && fb === null) return 0;
        if (fa === null) return 1; // nulls last
        if (fb === null) return -1;
        return codePointCompare(fa, fb) || codePointCompare(String(left?.name ?? ''), String(right?.name ?? ''));
    });
}

// === Reading (fs primitives only) ===

/**
 * Read an inventoried file: null when absent; a MigrationAbort on a JSON
 * parse failure (a validation gate — never a silent skip).
 */
async function readInventoried(root: string, relPath: string): Promise<any | null> {
    const text = await readTextFile(relPath);
    if (text == null) return null;
    try {
        return JSON.parse(stripBom(text));
    } catch (error) {
        throw new MigrationAbort(`JSON parse failure in ${relPath}: ${error instanceof Error ? error.message : String(error)}`);
    }
}

// === Reference resolution (id → name/alias → slug candidates) ===

type Resolver = (value: string) => string | null;

function makeResolver(ids: Map<string, string>, names: Map<string, string>): Resolver {
    return (value: string): string | null => {
        const key = lookupKey(value);
        if (!key) return null;
        const byId = ids.get(key);
        if (byId) return byId;
        const byName = names.get(key);
        if (byName) return byName;
        for (const candidate of idCandidates(value)) {
            const hit = ids.get(lookupKey(candidate));
            if (hit) return hit;
        }
        return null;
    };
}

// === v1 → synthesized v2 records (ruling R11) ===

const V1_GENDER_MAP: Record<string, string> = { m: 'Male', f: 'Female', u: 'Unknown' };

function synthesizeV2Records(entries: any[]): any[] {
    return entries
        .filter((entry) => entry != null && typeof entry === 'object')
        .map((entry: any, index: number) => {
            const name = cleanText(entry.name);
            if (!name) throw new MigrationAbort(`book.characters.json entry ${index} has no name`);
            const candidates = idCandidates(name);
            if (candidates.length === 0) {
                throw new MigrationAbort(`Cannot derive a slug id for character '${name}'`);
            }
            const genderRaw = typeof entry.gender === 'string' ? entry.gender.trim().toLowerCase() : 'u';
            return {
                id: candidates[0],
                name,
                gender: V1_GENDER_MAP[genderRaw] ?? 'Unknown',
                aliases: Array.isArray(entry.aliases) ? entry.aliases.filter((a) => typeof a === 'string') : [],
                color: entry.color ?? null,
                voice: entry.voice ?? null,
                firstAppearance: cleanText(entry.first_seen_chapter),
                notes: '',
                stats: { totalLines: 0, chapterCount: 0 },
            };
        });
}

// === Record validation (stage 1/3) ===

interface RecordValidation {
    ids: Map<string, string>;
    names: Map<string, string>;
    issues: string[];
}

function validateRecords(records: any[]): RecordValidation {
    const ids = new Map<string, string>();
    const names = new Map<string, string>();
    const ambiguous = new Map<string, Set<string>>();
    const issues: string[] = [];

    records.forEach((record: any, index: number) => {
        const id = cleanText(record?.id);
        const name = cleanText(record?.name);
        if (!id || !name) {
            issues.push(`record ${index} must have non-empty id and name`);
            return;
        }
        const idKey = lookupKey(id);
        const existing = ids.get(idKey);
        if (existing && existing !== id) {
            issues.push(`Duplicate character ID (case-insensitive): ${id}`);
        }
        ids.set(idKey, id);

        const surfaces: unknown[] = [name, ...(Array.isArray(record.aliases) ? record.aliases : [])];
        for (const surface of surfaces) {
            const text = cleanText(surface);
            if (!text) continue;
            const key = lookupKey(text);
            const previous = names.get(key);
            if (previous && previous !== id) {
                const clusterIds = ambiguous.get(key) ?? new Set<string>();
                clusterIds.add(previous);
                clusterIds.add(id);
                ambiguous.set(key, clusterIds);
            } else if (!previous || previous === id) {
                names.set(key, id);
            }
        }
    });

    if (ambiguous.size > 0) {
        const details = [...ambiguous.entries()]
            .sort(([a], [b]) => (a < b ? -1 : 1))
            .map(([surface, characterIds]) => `${JSON.stringify(surface)} -> ${[...characterIds].sort().join(', ')}`)
            .join('; ');
        issues.push(`Ambiguous central character names/aliases: ${details}`);
    }

    // Case-insensitive filename collision (also catches duplicate titles,
    // including case/whitespace variants) → abort naming the pair.
    const byStem = new Map<string, string[]>();
    for (const record of records) {
        const name = cleanText(record?.name);
        if (!name) continue;
        const stem = sanitizeCharacterFileStem(name);
        const key = lookupKey(stem);
        const titles = byStem.get(key) ?? [];
        titles.push(name);
        byStem.set(key, titles);
    }
    for (const [stemKey, titles] of byStem) {
        if (titles.length > 1) {
            issues.push(
                `Filename collision: ${titles.map((t) => JSON.stringify(t)).join(' vs ')} ` +
                    `→ characters/${stemKey}.json (case-insensitive)`,
            );
        }
    }

    return { ids, names, issues };
}

// === Reference-family remap walks (exact old-id → GUID substitution) ===

interface WalkOutcome {
    changed: boolean;
    refs: number;
    unresolved: string[];
}

function remapField(
    container: Record<string, any>,
    field: string,
    resolve: Resolver,
    remap: Record<string, string>,
): { changed: boolean; unresolved: string | null } {
    const value = container[field];
    if (typeof value !== 'string') return { changed: false, unresolved: null };
    const text = value.trim();
    if (!text || text === NARRATOR_SENTINEL) return { changed: false, unresolved: null };
    const id = resolve(text);
    if (!id) return { changed: false, unresolved: text };
    const guid = remap[id];
    if (container[field] === guid) return { changed: false, unresolved: null };
    container[field] = guid;
    return { changed: true, unresolved: null };
}

/** dialogue.json: the four inventoried reference sites. */
function walkDialogue(payload: any, resolve: Resolver, remap: Record<string, string>): WalkOutcome {
    let changed = false;
    let refs = 0;
    const unresolved: string[] = [];

    const lines = Array.isArray(payload?.lines) ? payload.lines : [];
    lines.forEach((line: any, lineIndex: number) => {
        if (!line || typeof line !== 'object') return;
        const main = remapField(line, 'characterId', resolve, remap);
        if (main.changed) { changed = true; refs += 1; }
        else if (main.unresolved) unresolved.push(`lines[${lineIndex}].characterId '${main.unresolved}'`);

        (Array.isArray(line.candidates) ? line.candidates : []).forEach((candidate: any, i: number) => {
            if (!candidate || typeof candidate !== 'object') return;
            const result = remapField(candidate, 'characterId', resolve, remap);
            if (result.changed) { changed = true; refs += 1; }
            else if (result.unresolved) unresolved.push(`lines[${lineIndex}].candidates[${i}].characterId '${result.unresolved}'`);
        });

        const attribution = line.attribution;
        if (attribution && typeof attribution === 'object' && Array.isArray(attribution.candidates)) {
            attribution.candidates.forEach((candidate: any, i: number) => {
                if (!candidate || typeof candidate !== 'object') return;
                const result = remapField(candidate, 'characterId', resolve, remap);
                if (result.changed) { changed = true; refs += 1; }
                else if (result.unresolved) unresolved.push(`lines[${lineIndex}].attribution.candidates[${i}].characterId '${result.unresolved}'`);
            });
        }
    });

    const stats = payload?.stats;
    if (stats && typeof stats === 'object' && stats.characterBreakdown && typeof stats.characterBreakdown === 'object') {
        const breakdown = stats.characterBreakdown;
        const next: Record<string, any> = {};
        for (const [key, count] of Object.entries(breakdown)) {
            if (!key || key === NARRATOR_SENTINEL) {
                next[key] = count;
                continue;
            }
            const id = resolve(key);
            if (!id) {
                unresolved.push(`stats.characterBreakdown key '${key}'`);
                next[key] = count;
                continue;
            }
            const guid = remap[id];
            next[guid] = (typeof next[guid] === 'number' ? next[guid] : 0) + (typeof count === 'number' ? count : 0);
            changed = true;
            refs += 1;
        }
        stats.characterBreakdown = next;
    }

    return { changed, refs, unresolved };
}

/** Roster `<Ch>/<Ch>.characters.json`: entries become `[{ "id": "<GUID>" }]`. */
function walkRoster(payload: any, resolve: Resolver, remap: Record<string, string>): WalkOutcome {
    const entries = Array.isArray(payload?.characters) ? payload.characters : [];
    const next: any[] = [];
    const seen = new Set<string>();
    let changed = false;
    let refs = 0;
    const unresolved: string[] = [];

    entries.forEach((entry: any, index: number) => {
        let surface: string | null = null;
        if (typeof entry === 'string') surface = entry;
        else if (entry && typeof entry === 'object') surface = cleanText(entry.id) ?? cleanText(entry.name);
        if (!surface) {
            unresolved.push(`entry ${index}: ${JSON.stringify(entry)}`);
            return;
        }
        const id = resolve(surface);
        if (!id) {
            unresolved.push(`entry ${index}: ${JSON.stringify(entry)}`);
            return;
        }
        const key = lookupKey(remap[id]);
        if (seen.has(key)) return; // dedupe (house behavior of the legacy roster migrator)
        seen.add(key);
        next.push({ id: remap[id] });
        refs += 1;
        changed = true;
    });

    if (changed) {
        payload.characters = next;
        payload.formatVersion = FORMAT_VERSION; // v3 roster file
    }
    return { changed, refs, unresolved };
}

/** voices.json: assignments[].characterId + voices[].metadata.usedByCharacters[]. */
function walkVoices(payload: any, resolve: Resolver, remap: Record<string, string>): WalkOutcome {
    let changed = false;
    let refs = 0;
    const unresolved: string[] = [];

    (Array.isArray(payload?.assignments) ? payload.assignments : []).forEach((assignment: any, i: number) => {
        if (!assignment || typeof assignment !== 'object') return;
        const result = remapField(assignment, 'characterId', resolve, remap);
        if (result.changed) { changed = true; refs += 1; }
        else if (result.unresolved) unresolved.push(`assignments[${i}].characterId '${result.unresolved}'`);
    });

    (Array.isArray(payload?.voices) ? payload.voices : []).forEach((voice: any, i: number) => {
        const metadata = voice && typeof voice === 'object' ? voice.metadata : null;
        if (!metadata || !Array.isArray(metadata.usedByCharacters)) return;
        metadata.usedByCharacters = metadata.usedByCharacters.map((value: any, j: number) => {
            if (typeof value !== 'string') return value;
            const text = value.trim();
            if (!text || text === NARRATOR_SENTINEL) return value;
            const id = resolve(text);
            if (!id) {
                unresolved.push(`voices[${i}].metadata.usedByCharacters[${j}] '${text}'`);
                return value;
            }
            changed = true;
            refs += 1;
            return remap[id];
        });
    });

    return { changed, refs, unresolved };
}

/** audio_lines manifests: top-level + per-clip characterId (characterName stays a name). */
function walkManifest(payload: any, resolve: Resolver, remap: Record<string, string>): WalkOutcome {
    let changed = false;
    let refs = 0;
    const unresolved: string[] = [];
    if (!payload || typeof payload !== 'object') return { changed, refs, unresolved };

    const main = remapField(payload, 'characterId', resolve, remap);
    if (main.changed) { changed = true; refs += 1; }
    else if (main.unresolved) unresolved.push(`characterId '${main.unresolved}'`);

    (Array.isArray(payload.clips) ? payload.clips : []).forEach((clip: any, i: number) => {
        if (!clip || typeof clip !== 'object') return;
        const result = remapField(clip, 'characterId', resolve, remap);
        if (result.changed) { changed = true; refs += 1; }
        else if (result.unresolved) unresolved.push(`clips[${i}].characterId '${result.unresolved}'`);
    });

    return { changed, refs, unresolved };
}

// === v3 file shape (v2 → v3 field mapping, docs/schema_characters_v3.md) ===

function asNumber(value: unknown, fallback: number): number {
    return typeof value === 'number' && Number.isFinite(value) ? value : fallback;
}

function buildV3File(cluster: { record: any; guid: string; title: string }): Record<string, any> {
    const record = cluster.record;
    const stats = record.stats && typeof record.stats === 'object' ? record.stats : {};
    const file: Record<string, any> = {
        formatVersion: FORMAT_VERSION,
        guid: cluster.guid,
        title: cluster.title, // v2 name → v3 title
        gender: typeof record.gender === 'string' ? record.gender : 'Unknown',
        aliases: Array.isArray(record.aliases) ? record.aliases : [],
        descriptors: Array.isArray(record.descriptors) ? record.descriptors : [],
        race: record.race ?? null,
        color: record.color ?? null,
        notes: typeof record.notes === 'string' ? record.notes : '',
        firstAppearance: record.firstAppearance ?? null,
        // Legacy count/chapterCount fold into stats (stats wins).
        stats: {
            totalLines: asNumber(stats.totalLines, asNumber(record.count, 0)),
            chapterCount: asNumber(stats.chapterCount, asNumber(record.chapterCount, 0)),
        },
        voice: record.voice ?? null,
        provider: record.provider ?? null,
        voiceId: record.voiceId ?? null,
        voiceMeta: record.voiceMeta ?? null,
        manifestStats: record.manifestStats ?? null,
        roleLabels: Array.isArray(record.roleLabels) ? record.roleLabels : [],
        updatedAt: new Date().toISOString(),
    };
    // Round-trip any unknown v2 fields verbatim (never lose data).
    for (const [key, value] of Object.entries(record)) {
        if (key === 'id' || key in file) continue; // drop the v2 id
        file[key] = value;
    }
    return file;
}

// === Stage 0: idempotency gate (read-only) ===

type GateOutcome = 'already-migrated' | 'in-flight' | 'proceed';

async function runIdempotencyGate(root: string, legacyRelPath: string): Promise<GateOutcome> {
    const fileNames = await listCharacterFiles(root);
    const validGuids: string[] = [];
    for (const fileName of fileNames) {
        const raw = await readCharacterFile(root, fileName);
        if (
            raw &&
            typeof raw === 'object' &&
            raw.formatVersion === FORMAT_VERSION &&
            typeof raw.guid === 'string' &&
            CHARACTER_GUID_PATTERN.test(raw.guid)
        ) {
            validGuids.push(raw.guid);
        }
    }
    const legacyPresent = (await readTextFile(legacyRelPath)) != null;
    // In-flight FIRST: a folder with any files alongside the legacy store is
    // a crashed prior run (a partial folder may already hold valid files) —
    // never guess, never auto-adopt it.
    if (fileNames.length >= 1 && legacyPresent) return 'in-flight';
    if (validGuids.length >= 1 && new Set(validGuids).size === validGuids.length) {
        return 'already-migrated';
    }
    return 'proceed';
}

// === Stage 1: reference inventory ===

type ReferenceWriter = 'dialogue' | 'roster' | 'voices' | 'manifest';

interface ReferenceChange {
    path: string;
    writer: ReferenceWriter;
    payload: any;
    refs: number;
}

interface InventoryResult {
    changes: ReferenceChange[];
    inventory: Set<string>;
    counts: { dialogueRefs: number; rosterRefs: number; voiceRefs: number; manifestRefs: number };
    unresolved: { dialogue: string[]; roster: string[]; voices: string[]; manifest: string[] };
    v1ScriptOnlyChapters: string[];
}

/** Candidate manifest paths for one chapter × one character surface name
 *  (the chapter-level layout the app uses, plus the book-level variant). */
function manifestCandidatePaths(chapter: string, surface: string): string[] {
    if (!surface || /[\\/:*?"<>|]/.test(surface)) return [];
    return [
        `${chapter}/audio_lines/${surface}/manifest.json`,
        `audio_lines/${chapter}/audio_lines/${surface}/manifest.json`,
    ];
}

async function inventoryReferences(
    root: string,
    chapterNames: string[],
    surfaceNames: string[],
    resolve: Resolver,
    remap: Record<string, string>,
): Promise<InventoryResult> {
    const changes: ReferenceChange[] = [];
    const inventory = new Set<string>();
    const counts = { dialogueRefs: 0, rosterRefs: 0, voiceRefs: 0, manifestRefs: 0 };
    const unresolved = { dialogue: [] as string[], roster: [] as string[], voices: [] as string[], manifest: [] as string[] };
    const v1ScriptOnlyChapters: string[] = [];

    const chapters = [...new Set(chapterNames.map((c) => String(c ?? '').trim()).filter(Boolean))];

    for (const chapter of chapters) {
        // <Ch>/dialogue.json (four reference sites)
        const dialoguePath = `${chapter}/dialogue.json`;
        const dialogue = await readInventoried(root, dialoguePath);
        if (dialogue != null) {
            inventory.add(dialoguePath);
            const copy = structuredClone(dialogue);
            const outcome = walkDialogue(copy, resolve, remap);
            counts.dialogueRefs += outcome.refs;
            outcome.unresolved.forEach((site) => unresolved.dialogue.push(`${dialoguePath}: ${site}`));
            if (outcome.changed) changes.push({ path: dialoguePath, writer: 'dialogue', payload: copy, refs: outcome.refs });
        } else if ((await readTextFile(`${chapter}/${chapter}.script.json`)) != null) {
            // R11: v1-script chapters have no characterId slot; they stay
            // name-based and are flagged (never silently remapped).
            v1ScriptOnlyChapters.push(chapter);
        }

        // <Ch>/<Ch>.characters.json (roster)
        const rosterPath = `${chapter}/${chapter}.characters.json`;
        const roster = await readInventoried(root, rosterPath);
        if (roster != null) {
            inventory.add(rosterPath);
            const copy = structuredClone(roster);
            const outcome = walkRoster(copy, resolve, remap);
            counts.rosterRefs += outcome.refs;
            outcome.unresolved.forEach((site) => unresolved.roster.push(`${rosterPath}: ${site}`));
            if (outcome.changed) changes.push({ path: rosterPath, writer: 'roster', payload: copy, refs: outcome.refs });
        }
    }

    // voices.json
    const voices = await readInventoried(root, 'voices.json');
    if (voices != null) {
        inventory.add('voices.json');
        const copy = structuredClone(voices);
        const outcome = walkVoices(copy, resolve, remap);
        counts.voiceRefs += outcome.refs;
        outcome.unresolved.forEach((site) => unresolved.voices.push(`voices.json: ${site}`));
        if (outcome.changed) changes.push({ path: 'voices.json', writer: 'voices', payload: copy, refs: outcome.refs });
    }

    // audio manifests: chapter × surface name (title + aliases)
    const manifestPaths = new Set<string>();
    for (const chapter of chapters) {
        for (const surface of surfaceNames) {
            for (const path of manifestCandidatePaths(chapter, surface)) manifestPaths.add(path);
        }
    }
    for (const path of [...manifestPaths].sort()) {
        const manifest = await readInventoried(root, path);
        if (manifest == null) continue;
        inventory.add(path);
        const copy = structuredClone(manifest);
        const outcome = walkManifest(copy, resolve, remap);
        counts.manifestRefs += outcome.refs;
        outcome.unresolved.forEach((site) => unresolved.manifest.push(`${path}: ${site}`));
        if (outcome.changed) changes.push({ path, writer: 'manifest', payload: copy, refs: outcome.refs });
    }

    return { changes, inventory, counts, unresolved, v1ScriptOnlyChapters };
}

// === Stage 5: ordered apply + post-apply rescan ===

async function writeReferenceFile(root: string, change: ReferenceChange): Promise<boolean> {
    switch (change.writer) {
        case 'dialogue':
            return writeDialogue(change.path, change.payload as DialogueJson);
        case 'voices':
            return writeVoices(root, change.payload as VoicesJson);
        case 'roster':
        case 'manifest':
            // The generic single-file JSON writer (house pattern for
            // arbitrary-path JSON files such as rosters and manifests).
            return writeCharacters(change.path, change.payload as CharactersJson);
    }
}

function containsStaleId(node: any, oldIds: Set<string>): boolean {
    if (typeof node === 'string') return oldIds.has(node);
    if (Array.isArray(node)) return node.some((item) => containsStaleId(item, oldIds));
    if (node && typeof node === 'object') {
        return Object.entries(node).some(([key, value]) => oldIds.has(key) || containsStaleId(value, oldIds));
    }
    return false;
}

/** Stage 5d: assert zero occurrences of any v2 slug across ALL inventoried
 *  reference families (every string value and object key, exact match). */
async function rescanForStaleIds(root: string, inventory: Set<string>, remap: Record<string, string>): Promise<string[]> {
    const oldIds = new Set(Object.keys(remap));
    if (oldIds.size === 0) return [];
    const stale: string[] = [];
    for (const relPath of [...inventory].sort()) {
        const text = await readTextFile(relPath);
        if (text == null) continue;
        let payload: any;
        try {
            payload = JSON.parse(stripBom(text));
        } catch {
            stale.push(relPath);
            continue;
        }
        if (containsStaleId(payload, oldIds)) stale.push(relPath);
    }
    return stale;
}

interface ApplyPlan {
    clusters: { record: any; guid: string; title: string; fileName: string }[]; // mint order
    remap: Record<string, string>;
    changes: ReferenceChange[];
    inventory: Set<string>;
}

async function applyPlan(
    root: string,
    legacyRelPath: string,
    plan: ApplyPlan,
    started: V3MigrationResult,
): Promise<V3MigrationResult> {
    const backupDir = `${BACKUP_DIR_PARTS[0]}/${BACKUP_DIR_PARTS[1]}/${backupTimestamp()}`;
    const writtenSoFar: string[] = [];

    // Stage 4: back up every file that will change (pre-images; the new
    // character files have no pre-image).
    const toBackUp = [legacyRelPath, ...plan.changes.map((change) => change.path)];
    for (const relPath of toBackUp) {
        const text = await readTextFile(relPath);
        if (text == null) continue;
        let parsed: any;
        try {
            parsed = JSON.parse(stripBom(text));
        } catch {
            continue; // unreadable pre-image: nothing to mirror
        }
        const ok = await writeCharacters(`${backupDir}/${relPath}`, parsed as CharactersJson);
        if (!ok) {
            throw new MigrationAbort(`Backup failed for ${relPath} before any book change was written.`);
        }
    }

    // Stage 5a: all character files, in mint order.
    for (const cluster of plan.clusters) {
        const ok = await writeCharacterFile(root, cluster.fileName, buildV3File(cluster));
        if (!ok) throw new MigrationAbort(`Failed to write characters/${cluster.fileName}`);
        writtenSoFar.push(`characters/${cluster.fileName}`);
    }

    // Stage 5b: every touched reference file (deterministic order).
    for (const change of [...plan.changes].sort((a, b) => (a.path < b.path ? -1 : 1))) {
        const ok = await writeReferenceFile(root, change);
        if (!ok) throw new MigrationAbort(`Failed to remap ${change.path}`);
        writtenSoFar.push(change.path);
    }

    // Stage 5c: LAST — retire the legacy store.
    const deleted = await deleteFile(root, legacyRelPath);
    if (!deleted) {
        throw new MigrationAbort(
            `Failed to delete the retired ${legacyRelPath}; the book is now in the ` +
            `in-flight state. Restore from ${backupDir}/ and re-run.`,
        );
    }
    writtenSoFar.push(legacyRelPath);

    // Stage 5d: post-apply rescan (mandatory before success).
    const stale = await rescanForStaleIds(root, plan.inventory, plan.remap);
    if (stale.length > 0) {
        return {
            ...started,
            ok: false,
            abortReason:
                `Post-apply rescan found surviving v2 ids in: ${stale.join(', ')}. ` +
                `Migration is incomplete — restore from ${backupDir}/ and re-run.`,
            changedFiles: writtenSoFar,
        };
    }

    // `started` carries the inventory counts (clusters + per-family refs).
    return {
        ...started,
        ok: true,
        changedFiles: writtenSoFar,
        createdCharacterFiles: plan.clusters.map((cluster) => `characters/${cluster.fileName}`),
        retiredLegacy: true,
        remap: plan.remap,
    };
}

// === Entry pipeline (shared by the v2 and v1 drivers) ===

interface MigrationSpec {
    mode: 'v2' | 'v1';
    legacyRelPath: string;
    loadRecords: () => Promise<any[] | null>;
}

function emptyResult(): V3MigrationResult {
    return {
        ok: false,
        changedFiles: [],
        createdCharacterFiles: [],
        retiredLegacy: false,
        remap: {},
        counts: { clusters: 0, dialogueRefs: 0, rosterRefs: 0, voiceRefs: 0, manifestRefs: 0 },
    };
}

async function migrateStoreToV3(
    root: string,
    chapterNames: string[],
    spec: MigrationSpec,
): Promise<V3MigrationResult> {
    const base = emptyResult();
    try {
        // Stage 0: idempotency gate (read-only).
        const gate = await runIdempotencyGate(root, spec.legacyRelPath);
        if (gate === 'already-migrated') {
            console.log('[characterMigration] v3 migration: already migrated (no-op)');
            return { ...base, ok: true };
        }
        if (gate === 'in-flight') {
            console.warn('[characterMigration] v3 migration aborted: in-flight state detected');
            return {
                ...base,
                abortReason:
                    `Migration appears to be in flight: the characters/ folder and ${spec.legacyRelPath} ` +
                    `are both present. This state is never resolved automatically: restore from ` +
                    `_backups/character-v3/<ts>/ (or clear the partial characters/ folder) and re-run.`,
            };
        }

        // Stage 1: inventory — records first (they drive the reference walk).
        const records = await spec.loadRecords();
        if (!records || records.length === 0) {
            console.log('[characterMigration] no legacy character records to migrate (no-op)');
            return { ...base, ok: true };
        }

        const violations: string[] = [];
        const { ids, names, issues } = validateRecords(records);
        violations.push(...issues);

        // Stage 2: deterministic minting (mint order: firstAppearance, nulls
        // last, then title).
        const bookName = resolveBookName(root);
        const ordered = sortClustersForMinting(records);
        const { remap, collision } = await mintDeterministicGuids(bookName, ordered);
        if (collision) violations.push(`Minted GUID collision: ${collision}`);

        // Stage 1 (cont.): walk every reference family.
        const surfaceNames = [...new Set(
            records.flatMap((record: any) => [cleanText(record?.name), ...(Array.isArray(record?.aliases) ? record.aliases : [])])
                .filter((surface): surface is string => !!surface),
        )];
        const resolve = makeResolver(ids, names);
        const inventory = await inventoryReferences(root, chapterNames, surfaceNames, resolve, remap);
        violations.push(
            ...inventory.unresolved.dialogue,
            ...inventory.unresolved.voices,
            ...inventory.unresolved.manifest,
            ...inventory.unresolved.roster,
        );
        if (inventory.v1ScriptOnlyChapters.length > 0) {
            // R11: explicit flag, one line per v1-script-only chapter.
            inventory.v1ScriptOnlyChapters.forEach((chapter) => {
                console.warn(`[characterMigration] v1-script-only chapter '${chapter}': script stays name-based (no characterId slot)`);
            });
        }

        // Stage 3: validation gates — any hit aborts with zero writes.
        if (violations.length > 0) {
            console.warn('[characterMigration] v3 migration aborted:', violations.join('; '));
            return {
                ...base,
                abortReason: `Validation failed; nothing was written. ${violations.join('; ')}`,
            };
        }

        // Stages 4–5: backup + ordered apply + post-apply rescan.
        const clusters = ordered
            .map((record: any) => {
                const id = cleanText(record?.id);
                const title = cleanText(record?.name);
                if (!id || !title || !remap[id]) return null;
                return {
                    record,
                    guid: remap[id],
                    title,
                    fileName: `${sanitizeCharacterFileStem(title)}.json`,
                };
            })
            .filter((cluster): cluster is NonNullable<typeof cluster> => cluster != null);

        const plan: ApplyPlan = {
            clusters,
            remap,
            changes: inventory.changes,
            inventory: inventory.inventory,
        };
        const result = await applyPlan(root, spec.legacyRelPath, plan, {
            ...base,
            remap,
            counts: {
                clusters: clusters.length,
                dialogueRefs: inventory.counts.dialogueRefs,
                rosterRefs: inventory.counts.rosterRefs,
                voiceRefs: inventory.counts.voiceRefs,
                manifestRefs: inventory.counts.manifestRefs,
            },
        });
        if (result.ok) {
            console.log(
                `[characterMigration] v3 migration (${spec.mode}) complete: ${result.createdCharacterFiles.length} character files, ` +
                `${result.changedFiles.length} files changed`,
            );
        }
        return result;
    } catch (error) {
        if (error instanceof MigrationAbort) {
            return { ...base, abortReason: error.message };
        }
        console.warn('[characterMigration] v3 migration threw:', error);
        return {
            ...base,
            abortReason: `Migration failed: ${error instanceof Error ? error.message : String(error)}`,
        };
    }
}
