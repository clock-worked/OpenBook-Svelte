import type { CharactersJson } from '$lib/types';
import {
    getCharactersPath,
    getDialoguePath,
    getScriptPath,
    readDialogue,
    readScript,
    readVoices,
    writeDialogue,
    writeScript,
    writeVoices,
} from '$lib/services/fs';
import { loadChapterCharactersData, persistChapterCharactersData } from '$lib/services/chapterCharacterRepository';

export interface ChapterRemapTarget {
    title: string;
    scriptPath?: string;
}

export function normalizeCharacterKey(name: string): string {
    return String(name || '').trim().toLowerCase();
}

function normalizeNameKey(name: string): string {
    return String(name || '').trim().toLowerCase();
}

export function slugifyCharacterId(name: string): string {
    const cleaned = String(name || '')
        .trim()
        .toLowerCase()
        .replace(/[^a-z0-9\s-]/g, '')
        .replace(/\s+/g, ' ')
        .trim();
    return cleaned.length ? cleaned.replace(/\s/g, '-') : 'character';
}

export function buildUniqueCharacterId(name: string, existingIds: Set<string>): string {
    const base = slugifyCharacterId(name);
    let id = base;
    let suffix = 2;
    while (existingIds.has(id)) {
        id = `${base}-${suffix}`;
        suffix += 1;
    }
    return id;
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
            const dialogueData: any = await readDialogue(dialoguePath);
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

export async function remapCharacterIdPrefixInDialogueFiles(
    root: string,
    chapterList: ChapterRemapTarget[],
    oldPrefix: string,
    newCharacterId: string,
): Promise<void> {
    const prefix = String(oldPrefix || '').trim();
    if (!prefix || !newCharacterId) return;

    for (const chapter of chapterList) {
        try {
            const dialoguePath = getDialoguePath(root, chapter.title);
            const dialogueData: any = await readDialogue(dialoguePath);
            if (!dialogueData || !Array.isArray(dialogueData.lines)) continue;

            let changed = false;
            const shouldRemapId = (value: any): boolean => {
                const id = typeof value === 'string' ? value : '';
                if (!id || id === newCharacterId) return false;
                return id === prefix || id.startsWith(`${prefix}-`);
            };

            for (const line of dialogueData.lines) {
                if (shouldRemapId(line?.characterId)) {
                    line.characterId = newCharacterId;
                    changed = true;
                }

                if (Array.isArray(line?.candidates)) {
                    for (const candidate of line.candidates) {
                        if (shouldRemapId(candidate?.characterId)) {
                            candidate.characterId = newCharacterId;
                            changed = true;
                        }
                    }
                }

                if (Array.isArray(line?.attribution?.candidates)) {
                    for (const candidate of line.attribution.candidates) {
                        if (shouldRemapId(candidate?.characterId)) {
                            candidate.characterId = newCharacterId;
                            changed = true;
                        }
                    }
                }
            }

            const breakdown = dialogueData?.stats?.characterBreakdown;
            if (breakdown && typeof breakdown === 'object') {
                const keys = Object.keys(breakdown);
                for (const key of keys) {
                    if (key === prefix || key.startsWith(`${prefix}-`)) {
                        const count = Number(breakdown[key] ?? 0);
                        breakdown[newCharacterId] = Number(breakdown[newCharacterId] ?? 0) + count;
                        delete breakdown[key];
                        changed = true;
                    }
                }
            }

            if (changed) {
                await writeDialogue(dialoguePath, dialogueData);
            }
        } catch {
        }
    }
}

export async function remapCharacterIdPrefixInVoicesAssignments(
    root: string,
    oldPrefix: string,
    newCharacterId: string,
): Promise<void> {
    const prefix = String(oldPrefix || '').trim();
    if (!prefix || !newCharacterId) return;

    try {
        const voicesData: any = await readVoices(root);
        if (!voicesData || !Array.isArray(voicesData.assignments)) return;

        let changed = false;
        voicesData.assignments = voicesData.assignments.map((assignment: any) => {
            const id = typeof assignment?.characterId === 'string' ? assignment.characterId : '';
            if (!id || id === newCharacterId) return assignment;
            if (id === prefix || id.startsWith(`${prefix}-`)) {
                changed = true;
                return { ...assignment, characterId: newCharacterId };
            }
            return assignment;
        });

        if (changed) {
            await writeVoices(root, voicesData);
        }
    } catch {
    }
}

export async function remapCharacterNameInScriptFiles(
    root: string,
    chapterList: ChapterRemapTarget[],
    oldName: string,
    newName: string,
): Promise<void> {
    const oldKey = normalizeNameKey(oldName);
    const nextName = String(newName || '').trim();
    if (!oldKey || !nextName) return;

    for (const chapter of chapterList) {
        try {
            const scriptPath = chapter.scriptPath ?? getScriptPath(root, chapter.title);
            const scriptData: any = await readScript(scriptPath);
            if (!scriptData || !Array.isArray(scriptData.lines)) continue;

            let changed = false;
            for (const line of scriptData.lines) {
                if (normalizeNameKey(line?.chosenSpeaker) === oldKey) {
                    line.chosenSpeaker = nextName;
                    changed = true;
                }

                if (Array.isArray(line?.candidates)) {
                    for (const candidate of line.candidates) {
                        if (normalizeNameKey(candidate?.name) === oldKey) {
                            candidate.name = nextName;
                            changed = true;
                        }
                    }
                }

                if (Array.isArray(line?.attribution?.candidates)) {
                    for (const candidate of line.attribution.candidates) {
                        if (normalizeNameKey(candidate?.name) === oldKey) {
                            candidate.name = nextName;
                            changed = true;
                        }
                    }
                }
            }

            const breakdown = scriptData?.stats?.characterBreakdown;
            if (breakdown && typeof breakdown === 'object' && Object.prototype.hasOwnProperty.call(breakdown, oldName)) {
                const count = Number(breakdown[oldName] ?? 0);
                breakdown[nextName] = Number(breakdown[nextName] ?? 0) + count;
                delete breakdown[oldName];
                changed = true;
            }

            if (changed) {
                await writeScript(scriptPath, scriptData);
            }
        } catch {
        }
    }
}

export async function remapCharacterNameInChapterCharacterFiles(
    root: string,
    chapterList: ChapterRemapTarget[],
    oldName: string,
    newName: string,
): Promise<void> {
    const oldKey = normalizeNameKey(oldName);
    const nextName = String(newName || '').trim();
    if (!oldKey || !nextName) return;

    for (const chapter of chapterList) {
        try {
            const charactersPath = getCharactersPath(root, chapter.title);
            const charactersData: any = await loadChapterCharactersData(root, charactersPath);
            if (!charactersData || !Array.isArray(charactersData.characters)) continue;

            let changed = false;
            const remapped = charactersData.characters.map((character: any) => {
                if (normalizeNameKey(character?.name) !== oldKey) return character;
                changed = true;
                return {
                    ...character,
                    name: nextName,
                };
            });

            if (!changed) continue;

            const deduped: any[] = [];
            const keyToIndex = new Map<string, number>();
            for (const character of remapped) {
                const key = normalizeNameKey(character?.name);
                if (!key) continue;
                const existingIndex = keyToIndex.get(key);
                if (existingIndex == null) {
                    keyToIndex.set(key, deduped.length);
                    deduped.push(character);
                    continue;
                }

                const existing = deduped[existingIndex] ?? {};
                deduped[existingIndex] = {
                    ...existing,
                    ...character,
                    name: existing.name || character.name,
                    color: existing.color ?? character.color ?? null,
                    voice: existing.voice ?? character.voice ?? null,
                    provider: existing.provider ?? character.provider ?? null,
                    voiceId: existing.voiceId ?? character.voiceId ?? null,
                    voiceMeta: existing.voiceMeta ?? character.voiceMeta ?? null,
                    manifestStats: existing.manifestStats ?? character.manifestStats ?? null,
                    count: Number(existing.count ?? 0) + Number(character.count ?? 0),
                    chapterCount: Math.max(Number(existing.chapterCount ?? 0), Number(character.chapterCount ?? 0)),
                    firstAppearance: existing.firstAppearance ?? character.firstAppearance ?? null,
                    aliases: normalizeAliasList([
                        ...(Array.isArray(existing.aliases) ? existing.aliases : []),
                        ...(Array.isArray(character.aliases) ? character.aliases : []),
                    ]),
                };
            }

            await persistChapterCharactersData(root, charactersPath, {
                formatVersion: charactersData.formatVersion || '2.0',
                characters: deduped,
            });
        } catch {
        }
    }
}
