import type { CharactersJson } from '$lib/types';
import { openBook } from '$lib/services/bookFormat';
import {
    CROCKFORD_BASE32_ALPHABET,
    normalizeCharacterKey,
    sanitizeCharacterFileStem,
} from './characterIdentity';

export interface ChapterRemapTarget {
    title: string;
    scriptPath?: string;
}

// Canonical key form lives in characterIdentity (single copy); re-exported
// so existing importers keep their path.
export { normalizeCharacterKey };

export function normalizeAliasList(names: string[]): string[] {
    const seen = new Set<string>();
    const result: string[] = [];
    for (const name of names) {
        const trimmed = String(name || '').trim();
        if (!trimmed) continue;
        const key = trimmed.toLowerCase();
        if (seen.has(key)) continue;
        seen.add(key);
        result.push(trimmed);
    }
    return result;
}

export function resolveCanonicalCharacterName(data: CharactersJson, inputName: string): string | null {
    const key = normalizeCharacterKey(inputName);
    if (!key) return null;

    for (const character of data.characters) {
        if (normalizeCharacterKey(character.name) === key) {
            return character.name;
        }
    }

    for (const character of data.characters) {
        const aliases = Array.isArray((character as any).aliases) ? (character as any).aliases : [];
        for (const alias of aliases) {
            if (normalizeCharacterKey(alias) === key) {
                return character.name;
            }
        }
    }

    return null;
}

// === v3 GUID identity (docs/schema_characters_v3.md §GUID spec) ===

// CROCKFORD_BASE32_ALPHABET is imported from characterIdentity (single copy).

function encodeCrockfordBase32(bytes: Uint8Array): string {
    let bits = 0;
    for (let i = 0; i < bytes.length; i++) {
        bits = (bits << 8) | bytes[i];
    }
    const charCount = (bytes.length * 8) / 5;
    const chars: string[] = [];
    for (let i = charCount - 1; i >= 0; i--) {
        chars.push(CROCKFORD_BASE32_ALPHABET[(bits >>> (5 * i)) & 0b11111]);
    }
    return chars.join('');
}

/**
 * Mint a new v3 character GUID: 5 random bytes → 40 bits → 8-char uppercase
 * Crockford base32 (big-endian, `crypto.getRandomValues`).
 *
 * Uniqueness against `existing` (the book's `characters/` folder GUIDs) is
 * mandatory: re-mints up to 5×, then throws.
 */
export function mintCharacterGuid(existing: Iterable<string>): string {
    const taken = new Set(
        Array.from(existing, (value) => String(value ?? '').trim()).filter(Boolean),
    );
    const bytes = new Uint8Array(5);
    let candidate = '';
    for (let attempt = 0; attempt < 5; attempt++) {
        crypto.getRandomValues(bytes);
        candidate = encodeCrockfordBase32(bytes);
        if (!taken.has(candidate)) return candidate;
    }
    throw new Error(`mintCharacterGuid: no unique GUID after 5 attempts (last: ${candidate})`);
}

// === v3 filename rules (docs/schema_characters_v3.md §Filename rules) ===

/**
 * Derive the full `characters/` filename for a title: the canonical stem
 * (characterIdentity, Python-parity) + `.json`. The filename is a cache of
 * the title, not identity — the caller resolves case-collisions (GUID
 * fallback).
 */
export function sanitizeCharacterFileName(title: string): string {
    return `${sanitizeCharacterFileStem(title)}.json`;
}

// === Reference remaps (exact-string GUID substitution, invariant IN-5) ===

export async function remapCharacterIdInDialogueFiles(
    root: string,
    chapterList: ChapterRemapTarget[],
    oldCharacterId: string,
    newCharacterId: string,
): Promise<void> {
    if (!oldCharacterId || !newCharacterId || oldCharacterId === newCharacterId) return;

    const book = openBook(root);
    for (const chapter of chapterList) {
        try {
            const dialogueData: any = await book.read('dialogue', chapter.title);
            if (!dialogueData || !Array.isArray(dialogueData.lines)) continue;

            let changed = false;

            for (const line of dialogueData.lines) {
                if (line?.characterId === oldCharacterId) {
                    line.characterId = newCharacterId;
                    changed = true;
                }

                if (Array.isArray(line?.candidates)) {
                    for (const candidate of line.candidates) {
                        if (candidate?.characterId === oldCharacterId) {
                            candidate.characterId = newCharacterId;
                            changed = true;
                        }
                    }
                }

                if (Array.isArray(line?.attribution?.candidates)) {
                    for (const candidate of line.attribution.candidates) {
                        if (candidate?.characterId === oldCharacterId) {
                            candidate.characterId = newCharacterId;
                            changed = true;
                        }
                    }
                }
            }

            const breakdown = dialogueData?.stats?.characterBreakdown;
            if (breakdown && typeof breakdown === 'object' && Object.prototype.hasOwnProperty.call(breakdown, oldCharacterId)) {
                const count = Number(breakdown[oldCharacterId] ?? 0);
                breakdown[newCharacterId] = Number(breakdown[newCharacterId] ?? 0) + count;
                delete breakdown[oldCharacterId];
                changed = true;
            }

            if (changed) {
                await book.write('dialogue', chapter.title, dialogueData);
            }
        } catch {
        }
    }
}

export async function remapCharacterIdInVoicesAssignments(
    root: string,
    oldCharacterId: string,
    newCharacterId: string,
): Promise<void> {
    if (!oldCharacterId || !newCharacterId || oldCharacterId === newCharacterId) return;

    try {
        const book = openBook(root);
        const voicesData: any = await book.read('voices');
        if (!voicesData || !Array.isArray(voicesData.assignments)) return;

        let changed = false;
        voicesData.assignments = voicesData.assignments.map((assignment: any) => {
            if (assignment?.characterId !== oldCharacterId) return assignment;
            changed = true;
            return { ...assignment, characterId: newCharacterId };
        });

        if (changed) {
            await book.write('voices', voicesData);
        }
    } catch {
    }
}

/**
 * Remap a character GUID in chapter roster files (`<Ch>/<Ch>.characters.json`,
 * `characters[].id` exact-match) and re-persist.
 *
 * v3 rosters are plain reference files (id = GUID), so this is a pure
 * exact-string substitution of an existing roster, so it may write one in
 * a v3 book (`allowRetiredInV3`) — deliberately NOT through
 * `persistChapterCharactersData`, whose v2
 * canonicalization resolves references against the root `characters.json`
 * (absent in v3 books) and would drop every entry.
 */
export async function remapCharacterGuidInChapterRosters(
    root: string,
    chapterTitles: string[],
    oldGuid: string,
    newGuid: string,
): Promise<void> {
    if (!oldGuid || !newGuid || oldGuid === newGuid) return;

    const book = openBook(root);
    for (const title of chapterTitles) {
        if (!title) continue;
        try {
            const raw: any = await book.read('roster', title);
            if (!raw || !Array.isArray(raw.characters)) continue;

            let changed = false;
            const characters = raw.characters
                .map((entry: any) => {
                    if (entry && typeof entry === 'object' && entry.id === oldGuid) {
                        changed = true;
                        return { ...entry, id: newGuid };
                    }
                    return entry;
                })
                // A roster listing both a merged source and its target would
                // otherwise carry the same GUID twice — dedupe by id (mirrors
                // the migration's roster remap). Legacy string entries pass
                // through untouched.
                .filter((entry: any, i: number, arr: any[]) => {
                    if (!entry || typeof entry !== 'object' || !entry.id) return true;
                    return arr.findIndex((e: any) => e && typeof e === 'object' && e.id === entry.id) === i;
                });

            if (changed) {
                await book.write('roster', title, { ...raw, characters }, { allowRetiredInV3: true });
            }
        } catch {
        }
    }
}
