import type { Character, CharacterConfig, CharactersJson } from '$lib/types';
import {
    getBookCharactersPath,
    getCharactersPath,
    getScriptPath,
    readCharacters,
    readScript,
    writeCharacters,
    writeCentralCharacters,
} from '$lib/services/fs';
import { normalizeCharacterKey } from '$lib/services/characterDomain';

export interface ChapterCharacterContext {
    title: string;
    parsed?: boolean;
    scriptPath?: string;
}

function normalizeCharacterConfig(input: CharacterConfig | null | undefined): CharacterConfig {
    return {
        name: input?.name ?? '',
        color: input?.color ?? null,
        voice: input?.voice ?? null,
        count: input?.count ?? 0,
        provider: input?.provider ?? null,
        voiceId: input?.voiceId ?? null,
        voiceMeta: input?.voiceMeta ?? null,
        manifestStats: input?.manifestStats ?? null,
        firstAppearance: input?.firstAppearance ?? null,
        chapterCount: input?.chapterCount ?? 0,
    };
}

export async function deriveCharactersFromChapters(
    root: string,
    chapterList: ChapterCharacterContext[],
): Promise<CharactersJson> {
    const accMap = new Map<string, CharacterConfig>();

    for (const chapter of chapterList) {
        try {
            const chars = await readCharacters(getCharactersPath(root, chapter.title));
            if (chars && Array.isArray(chars.characters)) {
                for (const character of chars.characters) {
                    const normalized = normalizeCharacterConfig(character as any);
                    const prev = accMap.get(normalized.name);
                    accMap.set(normalized.name, {
                        name: normalized.name,
                        color: prev?.color ?? normalized.color ?? null,
                        voice: prev?.voice ?? normalized.voice ?? null,
                        provider: prev?.provider ?? normalized.provider ?? null,
                        voiceId: prev?.voiceId ?? normalized.voiceId ?? null,
                        voiceMeta: prev?.voiceMeta ?? normalized.voiceMeta ?? null,
                        manifestStats: prev?.manifestStats ?? normalized.manifestStats ?? null,
                        count: prev?.count ?? normalized.count ?? 0,
                    });
                }
                continue;
            }

            if (chapter.parsed) {
                const scriptPath: string = chapter.scriptPath ?? getScriptPath(root, chapter.title);
                const script = await readScript(scriptPath);
                if (!script) continue;

                for (const line of script.lines) {
                    const name = (line.chosenSpeaker ?? '').trim();
                    if (!name || accMap.has(name)) continue;

                    accMap.set(name, {
                        name,
                        color: null,
                        voice: null,
                        provider: null,
                        voiceId: null,
                        voiceMeta: null,
                        manifestStats: null,
                        count: 0,
                        firstAppearance: chapter.title,
                        chapterCount: 0,
                    });
                }
            }
        } catch {
        }
    }

    for (const chapter of chapterList) {
        try {
            if (!chapter.parsed) continue;
            const scriptPath: string = chapter.scriptPath ?? getScriptPath(root, chapter.title);
            const script = await readScript(scriptPath);
            if (!script) continue;

            for (const line of script.lines) {
                const name = (line.chosenSpeaker ?? '').trim();
                if (!name) continue;
                const prev = accMap.get(name);
                if (prev) accMap.set(name, { ...prev, count: (prev.count || 0) + 1 });
            }
        } catch {
        }
    }

    return {
        formatVersion: '2.0',
        characters: Array.from(accMap.values()).sort((left, right) => left.name.localeCompare(right.name)) as Character[],
    };
}

export function mapRawBookCharacters(rawData: CharactersJson): CharactersJson {
    return {
        formatVersion: rawData.formatVersion || '2.0',
        characters: rawData.characters.map((char: any) => {
            const lineCount = char.stats?.totalLines ?? char.count ?? 0;
            return {
                id: char.id,
                name: char.name,
                aliases: Array.isArray(char.aliases) ? char.aliases : [],
                color: char.color ?? null,
                voice: char.voice ?? null,
                provider: char.provider ?? null,
                voiceId: char.voiceId ?? null,
                voiceMeta: char.voiceMeta ?? null,
                manifestStats: char.manifestStats ?? null,
                count: lineCount,
                firstAppearance: char.firstAppearance ?? null,
                chapterCount: char.stats?.chapterCount ?? char.chapterCount ?? 0,
            } as Character;
        }),
    };
}

export async function syncMissingBookCharacterDefaults(
    root: string,
    rawData: CharactersJson,
    chapterList: ChapterCharacterContext[],
): Promise<CharactersJson | null> {
    const missingDefaults = rawData.characters.some((char: any) =>
        !char.firstAppearance ||
        typeof char.stats?.totalLines !== 'number' ||
        typeof char.stats?.chapterCount !== 'number',
    );

    if (!missingDefaults || !chapterList.length) return null;

    const nameLookup = new Map<string, string>();
    for (const character of rawData.characters) {
        const key = normalizeCharacterKey((character as any).name);
        if (key) nameLookup.set(key, (character as any).name);
        const aliases = Array.isArray((character as any).aliases) ? (character as any).aliases : [];
        for (const alias of aliases) {
            const aliasKey = normalizeCharacterKey(alias);
            if (aliasKey) nameLookup.set(aliasKey, (character as any).name);
        }
    }

    const derivedStats = new Map<string, { totalLines: number; chapters: Set<string>; firstAppearance: string | null }>();

    for (const chapter of chapterList) {
        try {
            if (!chapter.parsed) continue;
            const scriptPath: string = chapter.scriptPath ?? getScriptPath(root, chapter.title);
            const script = await readScript(scriptPath);
            if (!script) continue;

            for (const line of script.lines) {
                const rawName = (line as any).chosenSpeaker ?? '';
                if (!rawName) continue;
                const canonical = nameLookup.get(normalizeCharacterKey(rawName));
                if (!canonical) continue;

                const entry = derivedStats.get(canonical) ?? { totalLines: 0, chapters: new Set<string>(), firstAppearance: null };
                entry.totalLines += 1;
                entry.chapters.add(chapter.title);
                if (!entry.firstAppearance) entry.firstAppearance = chapter.title;
                derivedStats.set(canonical, entry);
            }
        } catch {
        }
    }

    let updated = false;
    const next: CharactersJson = {
        ...rawData,
        formatVersion: rawData.formatVersion || '2.0',
        characters: rawData.characters.map((char: any) => {
            const derived = derivedStats.get(char.name);
            if (!derived) return char;

            let nextChar = { ...char };
            if (!nextChar.firstAppearance && derived.firstAppearance) {
                nextChar.firstAppearance = derived.firstAppearance;
                updated = true;
            }

            const stats = { ...(nextChar.stats ?? {}) } as { totalLines?: number; chapterCount?: number };
            let statsUpdated = false;

            if (typeof stats.totalLines !== 'number') {
                stats.totalLines = derived.totalLines;
                statsUpdated = true;
            }
            if (typeof stats.chapterCount !== 'number') {
                stats.chapterCount = derived.chapters.size;
                statsUpdated = true;
            }
            if (statsUpdated) {
                nextChar.stats = stats;
                updated = true;
            }

            if (typeof nextChar.count !== 'number') {
                nextChar.count = derived.totalLines;
                updated = true;
            }
            if (typeof nextChar.chapterCount !== 'number') {
                nextChar.chapterCount = derived.chapters.size;
                updated = true;
            }

            return nextChar;
        }),
    };

    if (!updated) return null;
    await writeCentralCharacters(root, next);
    return next;
}

export async function loadBookCharactersData(
    root: string,
    chapterList: ChapterCharacterContext[],
): Promise<CharactersJson> {
    const path = getBookCharactersPath(root);
    console.log('[bookCharacters] Loading from:', path);

    const existing = await readCharacters(path);
    const rawData: any = existing;

    if (rawData && Array.isArray(rawData.characters)) {
        const firstChar = rawData.characters[0];
        console.log('[bookCharacters] First character raw data:', {
            name: firstChar?.name,
            stats: firstChar?.stats,
            count: firstChar?.count,
            hasStats: !!firstChar?.stats,
            totalLines: firstChar?.stats?.totalLines,
        });

        const mapped = mapRawBookCharacters(rawData);
        console.log('[bookCharacters] Loaded', mapped.characters.length, 'characters');
        console.log(
            '[bookCharacters] Sample counts:',
            mapped.characters
                .slice(0, 3)
                .map((character) => `${character.name}: ${character.count}`)
                .join(', '),
        );

        const refreshed = await syncMissingBookCharacterDefaults(root, rawData, chapterList);
        return refreshed ? mapRawBookCharacters(refreshed) : mapped;
    }

    console.log('[bookCharacters] Fallback: deriving from chapters');
    return deriveCharactersFromChapters(root, chapterList);
}

export async function persistBookCharactersData(root: string, data: CharactersJson): Promise<void> {
    const path = getBookCharactersPath(root);

    try {
        const existing: any = await readCharacters(path);

        if (existing && Array.isArray(existing.characters)) {
            const dataByName = new Map(data.characters.map((character) => [character.name, character] as const));
            const existingNames = new Set(existing.characters.map((char: any) => char?.name).filter(Boolean));

            const updatedExisting = existing.characters
                .filter((char: any) => dataByName.has(char.name))
                .map((char: any) => {
                    const updated = dataByName.get(char.name);
                    if (!updated) return char;

                    return {
                        ...char,
                        color: updated.color,
                        voice: updated.voice,
                        provider: updated.provider,
                        voiceId: updated.voiceId,
                        voiceMeta: updated.voiceMeta,
                        aliases: Array.isArray(updated.aliases) ? updated.aliases : char.aliases,
                        stats: char.stats,
                        firstAppearance: char.firstAppearance,
                    };
                });

            const added = data.characters.filter((character) => !existingNames.has(character.name));
            await writeCharacters(path, {
                ...existing,
                characters: [...updatedExisting, ...added],
            });
            return;
        }
    } catch (err) {
        console.error('Error persisting book characters:', err);
    }

    await writeCharacters(path, data);
}
