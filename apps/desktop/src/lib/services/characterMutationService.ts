import type { Character, CharactersJson, Gender } from '$lib/types';
import { readCentralCharacters, writeCentralCharacters } from '$lib/services/fs';
import { createClosedWorldCharacter } from '$lib/services/closedWorldCharacterWorkflow';
import {
    buildUniqueCharacterId,
    normalizeAliasList,
    remapCharacterIdInDialogueFiles,
    remapCharacterIdInVoicesAssignments,
    remapCharacterIdPrefixInDialogueFiles,
    remapCharacterIdPrefixInVoicesAssignments,
    remapCharacterNameInChapterCharacterFiles,
    remapCharacterNameInScriptFiles,
    resolveCanonicalCharacterName,
    slugifyCharacterId,
    type ChapterRemapTarget,
} from '$lib/services/characterDomain';

export async function renameCentralCharacter(
    root: string,
    remapTargets: ChapterRemapTarget[],
    oldName: string,
    newName: string,
): Promise<boolean> {
    const existing = await readCentralCharacters(root);
    if (!existing || !Array.isArray(existing.characters)) return false;

    const trimmedOldName = String(oldName || '').trim();
    const trimmedNewName = String(newName || '').trim();
    if (!trimmedOldName || !trimmedNewName || trimmedOldName === trimmedNewName) return false;

    const canonicalOldName = resolveCanonicalCharacterName(existing, trimmedOldName) ?? trimmedOldName;
    const canonicalNewName = resolveCanonicalCharacterName(existing, trimmedNewName) ?? trimmedNewName;
    const sourceIndex = existing.characters.findIndex((character) => character.name === canonicalOldName);
    if (sourceIndex === -1) return false;

    const sourceCharacter = existing.characters[sourceIndex] as any;
    const oldCharacterId = typeof sourceCharacter?.id === 'string' ? sourceCharacter.id : '';
    const conflict = existing.characters.find(
        (character) => character.name === canonicalNewName && character.name !== canonicalOldName,
    ) as any;

    if (conflict) {
        const next: CharactersJson = {
            formatVersion: existing.formatVersion || '2.0',
            characters: existing.characters.filter((character) => character.name !== canonicalOldName),
        };
        await writeCentralCharacters(root, next);

        const targetCharacterId = typeof conflict?.id === 'string' ? conflict.id : '';
        if (oldCharacterId && targetCharacterId && oldCharacterId !== targetCharacterId) {
            await remapCharacterIdInDialogueFiles(root, remapTargets, oldCharacterId, targetCharacterId);
            await remapCharacterIdInVoicesAssignments(root, oldCharacterId, targetCharacterId);
        }
        await remapCharacterNameInScriptFiles(root, remapTargets, canonicalOldName, canonicalNewName);
        await remapCharacterNameInChapterCharacterFiles(root, remapTargets, canonicalOldName, canonicalNewName);
        return true;
    }

    const idsInUse = new Set(
        existing.characters
            .filter((_, index) => index !== sourceIndex)
            .map((character: any) => character?.id)
            .filter((id: any): id is string => typeof id === 'string' && id.length > 0),
    );
    const newCharacterId = buildUniqueCharacterId(canonicalNewName, idsInUse);

    const next: CharactersJson = {
        formatVersion: existing.formatVersion || '2.0',
        characters: existing.characters.map((character, index) => {
            if (index !== sourceIndex) return character;
            return {
                ...character,
                name: canonicalNewName,
                id: newCharacterId,
            } as Character;
        }),
    };

    await writeCentralCharacters(root, next);
    if (oldCharacterId && oldCharacterId !== newCharacterId) {
        await remapCharacterIdInDialogueFiles(root, remapTargets, oldCharacterId, newCharacterId);
        await remapCharacterIdInVoicesAssignments(root, oldCharacterId, newCharacterId);
    }
    await remapCharacterNameInScriptFiles(root, remapTargets, canonicalOldName, canonicalNewName);
    await remapCharacterNameInChapterCharacterFiles(root, remapTargets, canonicalOldName, canonicalNewName);
    return true;
}

export async function mergeCentralCharacters(
    root: string,
    remapTargets: ChapterRemapTarget[],
    sourceName: string,
    targetName: string,
): Promise<boolean> {
    if (!sourceName || !targetName || sourceName === targetName) return false;

    const data = await readCentralCharacters(root);
    if (!data?.characters) return false;

    const sourceIndex = data.characters.findIndex((character: any) => character?.name === sourceName);
    const targetIndex = data.characters.findIndex((character: any) => character?.name === targetName);
    if (sourceIndex === -1 || targetIndex === -1) return false;

    const source = data.characters[sourceIndex] as any;
    const target = data.characters[targetIndex] as any;
    const sourceCharacterId = typeof source?.id === 'string' ? source.id : '';
    const targetCharacterId = typeof target?.id === 'string' ? target.id : '';

    const sourceAliases = Array.isArray(source.aliases) ? source.aliases : [];
    const targetAliases = Array.isArray(target.aliases) ? target.aliases : [];
    const mergedAliases = normalizeAliasList([...targetAliases, source.name, ...sourceAliases]).filter(
        (alias) => alias.toLowerCase() !== target.name.toLowerCase(),
    );

    const targetStats = target.stats ?? { totalLines: target.count ?? 0, chapterCount: target.chapterCount ?? 0 };
    const sourceStats = source.stats ?? { totalLines: source.count ?? 0, chapterCount: source.chapterCount ?? 0 };
    const totalLines = (targetStats.totalLines ?? 0) + (sourceStats.totalLines ?? 0);
    const chapterCount = Math.max(targetStats.chapterCount ?? 0, sourceStats.chapterCount ?? 0);

    target.aliases = mergedAliases;
    target.stats = { ...targetStats, totalLines, chapterCount };
    if (typeof target.count === 'number' || typeof source.count === 'number') {
        target.count = totalLines;
    }
    if (typeof target.chapterCount === 'number' || typeof source.chapterCount === 'number') {
        target.chapterCount = chapterCount;
    }

    if (sourceCharacterId && targetCharacterId && sourceCharacterId !== targetCharacterId) {
        await remapCharacterIdInDialogueFiles(root, remapTargets, sourceCharacterId, targetCharacterId);
        await remapCharacterIdInVoicesAssignments(root, sourceCharacterId, targetCharacterId);
    }
    await remapCharacterNameInScriptFiles(root, remapTargets, source.name, target.name);
    await remapCharacterNameInChapterCharacterFiles(root, remapTargets, source.name, target.name);

    data.characters.splice(sourceIndex, 1);
    await writeCentralCharacters(root, data);
    return true;
}

export async function setCentralCharacterPrimaryName(
    root: string,
    currentName: string,
    newPrimaryName: string,
): Promise<boolean> {
    if (!currentName || !newPrimaryName || currentName === newPrimaryName) return false;

    const data = await readCentralCharacters(root);
    if (!data?.characters) return false;

    const target = data.characters.find((character: any) => character?.name === currentName);
    if (!target) return false;

    const conflict = data.characters.find((character: any) => character?.name === newPrimaryName);
    if (conflict && conflict !== target) {
        console.warn('[bookCharacters] Cannot set primary name: target already exists', newPrimaryName);
        return false;
    }

    const aliases = Array.isArray(target.aliases) ? target.aliases : [];
    if (!aliases.some((alias) => alias === newPrimaryName)) return false;

    target.name = newPrimaryName;
    target.aliases = normalizeAliasList([...aliases.filter((alias) => alias !== newPrimaryName), currentName]).filter(
        (alias) => alias.toLowerCase() !== target.name.toLowerCase(),
    );

    await writeCentralCharacters(root, data);
    return true;
}

export async function detachCentralCharacterAlias(
    root: string,
    remapTargets: ChapterRemapTarget[],
    characterName: string,
    aliasName: string,
): Promise<boolean> {
    const data = await readCentralCharacters(root);
    if (!data?.characters) return false;

    const source = data.characters.find((character: any) => character?.name === characterName);
    if (!source) return false;

    const aliases = Array.isArray(source.aliases) ? source.aliases : [];
    if (!aliases.includes(aliasName)) return false;

    const existingCanonicalId = typeof source?.id === 'string' ? source.id.trim() : '';
    const idsInUse = new Set(
        data.characters
            .filter((character: any) => character !== source)
            .map((character: any) => character?.id)
            .filter((id: any): id is string => typeof id === 'string' && id.length > 0),
    );
    const preferredCanonicalId = slugifyCharacterId(source.name);
    const canonicalCharacterId = idsInUse.has(preferredCanonicalId)
        ? buildUniqueCharacterId(source.name, idsInUse)
        : preferredCanonicalId;

    if (!existingCanonicalId || existingCanonicalId !== canonicalCharacterId) {
        source.id = canonicalCharacterId;
    }

    source.aliases = aliases.filter((alias: string) => alias !== aliasName);

    if (canonicalCharacterId) {
        if (existingCanonicalId && existingCanonicalId !== canonicalCharacterId) {
            await remapCharacterIdInDialogueFiles(root, remapTargets, existingCanonicalId, canonicalCharacterId);
            await remapCharacterIdInVoicesAssignments(root, existingCanonicalId, canonicalCharacterId);
            await remapCharacterIdPrefixInDialogueFiles(root, remapTargets, existingCanonicalId, canonicalCharacterId);
            await remapCharacterIdPrefixInVoicesAssignments(root, existingCanonicalId, canonicalCharacterId);
        }

        const aliasIdCandidates = normalizeAliasList([aliasName, slugifyCharacterId(aliasName)]).filter(
            (candidate) => candidate !== canonicalCharacterId && candidate !== existingCanonicalId,
        );
        for (const aliasCharacterId of aliasIdCandidates) {
            await remapCharacterIdInDialogueFiles(root, remapTargets, aliasCharacterId, canonicalCharacterId);
            await remapCharacterIdInVoicesAssignments(root, aliasCharacterId, canonicalCharacterId);
            await remapCharacterIdPrefixInDialogueFiles(root, remapTargets, aliasCharacterId, canonicalCharacterId);
            await remapCharacterIdPrefixInVoicesAssignments(root, aliasCharacterId, canonicalCharacterId);
        }
    }

    await remapCharacterNameInScriptFiles(root, remapTargets, aliasName, source.name);
    await remapCharacterNameInChapterCharacterFiles(root, remapTargets, aliasName, source.name);

    await writeCentralCharacters(root, data);
    return true;
}

export async function addCentralCharacterAlias(
    root: string,
    characterName: string,
    aliasName: string,
): Promise<boolean> {
    const canonical = String(characterName || '').trim();
    const alias = String(aliasName || '').trim();
    if (!canonical || !alias) return false;
    if (canonical.toLowerCase() === alias.toLowerCase()) return false;

    const data = await readCentralCharacters(root);
    if (!data?.characters) return false;

    const target = data.characters.find((character: any) => character?.name === canonical);
    if (!target) return false;

    const aliasKey = alias.toLowerCase();
    const collidingCanonical = data.characters.find(
        (character: any) => String(character?.name || '').trim().toLowerCase() === aliasKey,
    );
    if (collidingCanonical && String(collidingCanonical.name).trim().toLowerCase() !== canonical.toLowerCase()) {
        return false;
    }

    const aliases = Array.isArray(target.aliases) ? target.aliases : [];
    if (aliases.some((existing: string) => existing.toLowerCase() === alias.toLowerCase())) return false;

    target.aliases = normalizeAliasList([...aliases, alias]).filter(
        (candidate) => candidate.toLowerCase() !== canonical.toLowerCase(),
    );

    await writeCentralCharacters(root, data);
    return true;
}

export async function addCentralCharacter(
    root: string,
    displayName: string,
    gender: Gender,
): Promise<Character | null> {
    const data = await readCentralCharacters(root);
    if (!data?.characters) return null;

    const { characters, character } = createClosedWorldCharacter(data, { displayName, gender });
    const persisted = await writeCentralCharacters(root, characters);
    return persisted ? character : null;
}
