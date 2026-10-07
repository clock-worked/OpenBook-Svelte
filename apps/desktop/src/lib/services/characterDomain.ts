import type { CharactersJson } from '$lib/types';
import {
    getCharactersPath,
    getDialoguePath,
    readOptionalCharacters,
    readOptionalDialogue,
    readVoices,
    writeCharacters,
    writeDialogue,
    writeVoices,
} from '$lib/services/fs';

export interface ChapterRemapTarget {
    title: string;
    scriptPath?: string;
}

export function normalizeCharacterKey(name: string): string {
    return String(name || '').trim().toLowerCase();
}

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

/** Crockford base32 alphabet: 0-9 + A-Z minus I, L, O, U (32 symbols). Local to this file by design. */
const CROCKFORD_BASE32_ALPHABET = '0123456789BHJKMNPQRSTVWXYZ';

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

const WINDOWS_RESERVED_FILE_NAMES = new Set([
    'CON', 'PRN', 'AUX', 'NUL',
    'COM1', 'COM2', 'COM3', 'COM4', 'COM5', 'COM6', 'COM7', 'COM8', 'COM9',
    'LPT1', 'LPT2', 'LPT3', 'LPT4', 'LPT5', 'LPT6', 'LPT7', 'LPT8', 'LPT9',
]);

/**
 * Derive the `characters/` basename for a title:
 * replace `\/:*?"<>|` + control chars with `-`, collapse repeats, trim, cap
 * 80 chars, guard Windows reserved names. The filename is a cache of the
 * title, not identity — the caller resolves case-collisions (GUID fallback).
 */
export function sanitizeCharacterFileName(title: string): string {
    let name = String(title || '')
        .trim()
        .replace(/[/\\:*?"<>|\u0000-\u001F]/g, '-')
        .replace(/-+/g, '-')
        .replace(/^-+|-+$/g, '');
    if (name.length > 80) {
        name = name.slice(0, 80).replace(/-+$/g, '');
    }
    if (!name) name = 'character';
    if (WINDOWS_RESERVED_FILE_NAMES.has(name.toUpperCase())) name = `-${name}`;
    return `${name}.json`;
}

// === Reference remaps (exact-string GUID substitution, invariant IN-5) ===

export async function remapCharacterIdInDialogueFiles(
    root: string,
    chapterList: ChapterRemapTarget[],
    oldCharacterId: string,
    newCharacterId: string,
): Promise<void> {
    if (!oldCharacterId || !newCharacterId || oldCharacterId === newCharacterId) return;

    for (const chapter of chapterList) {
        try {
            const dialoguePath = getDialoguePath(root, chapter.title);
            const dialogueData: any = await readOptionalDialogue(dialoguePath);
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
                await writeDialogue(dialoguePath, dialogueData);
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
        const voicesData: any = await readVoices(root);
        if (!voicesData || !Array.isArray(voicesData.assignments)) return;

        let changed = false;
        voicesData.assignments = voicesData.assignments.map((assignment: any) => {
            if (assignment?.characterId !== oldCharacterId) return assignment;
            changed = true;
            return { ...assignment, characterId: newCharacterId };
        });

        if (changed) {
            await writeVoices(root, voicesData);
        }
    } catch {
    }
}

/**
 * Remap a character GUID in chapter roster files (`<Ch>/<Ch>.characters.json`,
 * `characters[].id` exact-match) and re-persist.
 *
 * v3 rosters are plain reference files (id = GUID), so this is a pure
 * exact-string substitution. It persists through the same raw
 * `readOptionalCharacters`/`writeCharacters` pair the chapter repository
 * uses — deliberately NOT through `persistChapterCharactersData`, whose v2
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

    for (const title of chapterTitles) {
        if (!title) continue;
        try {
            const charactersPath = getCharactersPath(root, title);
            const raw: any = await readOptionalCharacters(charactersPath);
            if (!raw || !Array.isArray(raw.characters)) continue;

            let changed = false;
            const characters = raw.characters.map((entry: any) => {
                if (entry && typeof entry === 'object' && entry.id === oldGuid) {
                    changed = true;
                    return { ...entry, id: newGuid };
                }
                return entry;
            });

            if (changed) {
                await writeCharacters(charactersPath, { ...raw, characters });
            }
        } catch {
        }
    }
}
