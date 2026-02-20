import { writable, get } from 'svelte/store';
import type { CharacterConfig, CharacterManifestStats, CharacterVoiceMeta, CharactersJson, TtsProvider, Character } from '$lib/types';
import { audioRoot, bookRoot, chapters } from '$lib/stores/bookState';
import { getBookCharactersPath, getCharactersPath, getDialoguePath, getScriptPath, readCharacters, readDialogue, readScript, readVoices, writeCharacters, writeDialogue, writeScript, writeVoices, readCentralCharacters, writeCentralCharacters } from '$lib/services/fs';
import { loadCharacterManifestSummary } from '$lib/services/manifests';

export const bookCharacters = writable<CharactersJson>({ formatVersion: '2.0', characters: [] });

function normalizeCharacterKey(name: string): string {
  return String(name || '').trim().toLowerCase();
}

function slugifyCharacterId(name: string): string {
  const cleaned = String(name || '')
    .trim()
    .toLowerCase()
    .replace(/[^a-z0-9\s-]/g, '')
    .replace(/\s+/g, ' ')
    .trim();
  return cleaned.length ? cleaned.replace(/\s/g, '-') : 'character';
}

function buildUniqueCharacterId(name: string, existingIds: Set<string>): string {
  const base = slugifyCharacterId(name);
  let id = base;
  let suffix = 2;
  while (existingIds.has(id)) {
    id = `${base}-${suffix}`;
    suffix += 1;
  }
  return id;
}

async function remapCharacterIdInDialogueFiles(root: string, oldCharacterId: string, newCharacterId: string): Promise<void> {
  if (!oldCharacterId || !newCharacterId || oldCharacterId === newCharacterId) return;

  const chapterList = get(chapters);
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

async function remapCharacterIdInVoicesAssignments(root: string, oldCharacterId: string, newCharacterId: string): Promise<void> {
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

async function remapCharacterIdPrefixInDialogueFiles(root: string, oldPrefix: string, newCharacterId: string): Promise<void> {
  const prefix = String(oldPrefix || '').trim();
  if (!prefix || !newCharacterId) return;

  const chapterList = get(chapters);
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

async function remapCharacterIdPrefixInVoicesAssignments(root: string, oldPrefix: string, newCharacterId: string): Promise<void> {
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

function normalizeNameKey(name: string): string {
  return String(name || '').trim().toLowerCase();
}

async function remapCharacterNameInScriptFiles(root: string, oldName: string, newName: string): Promise<void> {
  const oldKey = normalizeNameKey(oldName);
  const nextName = String(newName || '').trim();
  if (!oldKey || !nextName) return;

  const chapterList = get(chapters);
  for (const chapter of chapterList) {
    try {
      const scriptPath = (chapter as any).scriptPath ?? getScriptPath(root, chapter.title);
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

async function remapCharacterNameInChapterCharacterFiles(root: string, oldName: string, newName: string): Promise<void> {
  const oldKey = normalizeNameKey(oldName);
  const nextName = String(newName || '').trim();
  if (!oldKey || !nextName) return;

  const chapterList = get(chapters);
  for (const chapter of chapterList) {
    try {
      const charactersPath = getCharactersPath(root, chapter.title);
      const charactersData: any = await readCharacters(charactersPath);
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

      await writeCharacters(charactersPath, {
        formatVersion: charactersData.formatVersion || '2.0',
        characters: deduped,
      });
    } catch {
    }
  }
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

function resolveCanonicalCharacterName(data: CharactersJson, inputName: string): string | null {
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

async function deriveFromChapters(root: string): Promise<CharactersJson> {
  const list = get(chapters);
  const accMap = new Map<string, CharacterConfig>();
  for (const ch of list) {
    try {
      const chars = await readCharacters(getCharactersPath(root, ch.title));
      if (chars && Array.isArray(chars.characters)) {
        for (const c of chars.characters) {
          const normalized = normalizeCharacterConfig(c as any);
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
      // Fallback to script-derived names
      if (ch.parsed) {
        const scriptPath: string = (ch as any).scriptPath ?? getScriptPath(root, ch.title);
        const scr = await readScript(scriptPath);
        if (scr) {
          for (const l of scr.lines) {
            const n = (l.chosenSpeaker ?? '').trim();
            if (!n) continue;
            if (!accMap.has(n)) {
              accMap.set(n, {
                name: n,
                color: null,
                voice: null,
                provider: null,
                voiceId: null,
                voiceMeta: null,
                manifestStats: null,
                count: 0,
                firstAppearance: ch.title,
                chapterCount: 0,
              });
            }
          }
        }
      }
    } catch {
      // ignore chapter errors
    }
  }
  // After unioning names and metadata, compute counts by reading scripts
  for (const ch of list) {
    try {
      if (!ch.parsed) continue;
      const scriptPath: string = (ch as any).scriptPath ?? getScriptPath(root, ch.title);
      const scr = await readScript(scriptPath);
      if (!scr) continue;
      for (const l of scr.lines) {
        const n = (l.chosenSpeaker ?? '').trim();
        if (!n) continue;
        const prev = accMap.get(n);
        if (prev) accMap.set(n, { ...prev, count: (prev.count || 0) + 1 });
      }
    } catch {
      // ignore
    }
  }
  return { formatVersion: '2.0', characters: Array.from(accMap.values()).sort((a, b) => a.name.localeCompare(b.name)) as Character[] };
}

let _loadBookCharTimer: ReturnType<typeof setTimeout> | null = null;
function loadBookCharacters() {
  // Debounce: bookRoot and chapters both subscribe and can fire together
  if (_loadBookCharTimer) clearTimeout(_loadBookCharTimer);
  _loadBookCharTimer = setTimeout(() => {
    _loadBookCharTimer = null;
    _loadBookCharactersImpl();
  }, 150);
}

async function _loadBookCharactersImpl() {
  const root = get(bookRoot);
  if (!root) { bookCharacters.set({ formatVersion: '2.0', characters: [] }); return; }
  try {
    const path = getBookCharactersPath(root);
    console.log('[bookCharacters] Loading from:', path);
    const existing = await readCharacters(path);

    // Read the characters.json file which has stats.totalLines
    const rawData: any = existing;

    if (rawData && Array.isArray(rawData.characters)) {
      // Debug: Check first character's raw data
      const firstChar = rawData.characters[0];
      console.log('[bookCharacters] First character raw data:', {
        name: firstChar?.name,
        stats: firstChar?.stats,
        count: firstChar?.count,
        hasStats: !!firstChar?.stats,
        totalLines: firstChar?.stats?.totalLines
      });

      const mapped = mapRawCharacters(rawData);

      console.log('[bookCharacters] Loaded', mapped.characters.length, 'characters');
      console.log('[bookCharacters] Sample counts:',
        mapped.characters.slice(0, 3).map(c => `${c.name}: ${c.count}`).join(', '));

      bookCharacters.set(mapped);

      const refreshed = await syncMissingBookCharacterDefaults(root, rawData);
      if (refreshed) {
        bookCharacters.set(mapRawCharacters(refreshed));
      }
      return;
    }

    // Fallback: derive from chapters if characters.json doesn't exist or is in wrong format
    console.log('[bookCharacters] Fallback: deriving from chapters');
    const derived = await deriveFromChapters(root);
    bookCharacters.set(derived);
  } catch (err) {
    console.error('Error loading book characters:', err);
    bookCharacters.set({ formatVersion: '2.0', characters: [] });
  }
}

function mapRawCharacters(rawData: CharactersJson): CharactersJson {
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
    })
  };
}

async function syncMissingBookCharacterDefaults(
  root: string,
  rawData: CharactersJson
): Promise<CharactersJson | null> {
  const missingDefaults = rawData.characters.some((char: any) =>
    !char.firstAppearance ||
    typeof char.stats?.totalLines !== 'number' ||
    typeof char.stats?.chapterCount !== 'number'
  );

  if (!missingDefaults) return null;

  const chapterList = get(chapters);
  if (!chapterList.length) return null;

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

  for (const ch of chapterList) {
    try {
      if (!ch.parsed) continue;
      const scriptPath: string = (ch as any).scriptPath ?? getScriptPath(root, ch.title);
      const scr = await readScript(scriptPath);
      if (!scr) continue;

      for (const line of scr.lines) {
        const rawName = (line as any).chosenSpeaker ?? '';
        if (!rawName) continue;
        const canonical = nameLookup.get(normalizeCharacterKey(rawName));
        if (!canonical) continue;

        const entry = derivedStats.get(canonical) ?? { totalLines: 0, chapters: new Set<string>(), firstAppearance: null };
        entry.totalLines += 1;
        entry.chapters.add(ch.title);
        if (!entry.firstAppearance) entry.firstAppearance = ch.title;
        derivedStats.set(canonical, entry);
      }
    } catch {
      // ignore chapter errors
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
    })
  };

  if (!updated) return null;
  await writeCentralCharacters(root, next);
  return next;
}

function eNormalize(c: CharacterConfig | undefined): CharacterConfig {
  return normalizeCharacterConfig(c);
}

// HMR-safe subscriptions: unsubscribe old listeners on module reload
let _unsub1 = bookRoot.subscribe(() => { loadBookCharacters(); });
let _unsub2 = chapters.subscribe(() => { loadBookCharacters(); });

if (import.meta.hot) {
  import.meta.hot.dispose(() => {
    _unsub1();
    _unsub2();
    if (_loadBookCharTimer) clearTimeout(_loadBookCharTimer);
  });
}

export async function forceRefreshBookCharacters(): Promise<void> {
  await _loadBookCharactersImpl();
}

export async function persistBookCharacters(): Promise<void> {
  const root = get(bookRoot);
  if (!root) return;
  const path = getBookCharactersPath(root);
  const data = get(bookCharacters);

  // Read the original file to preserve the full structure
  try {
    const existing: any = await readCharacters(path);

    if (existing && Array.isArray(existing.characters)) {
      const dataByName = new Map(data.characters.map(c => [c.name, c] as const));
      const existingNames = new Set(existing.characters.map((char: any) => char?.name).filter(Boolean));

      // Update only the fields we manage (color, voice, provider, voiceId, voiceMeta)
      // NEVER overwrite stats or firstAppearance - those are managed by the backend
      const updatedExisting = existing.characters
        .filter((char: any) => dataByName.has(char.name))
        .map((char: any) => {
          const updated = dataByName.get(char.name);
          if (updated) {
            return {
              ...char,
              color: updated.color,
              voice: updated.voice,
              provider: updated.provider,
              voiceId: updated.voiceId,
              voiceMeta: updated.voiceMeta,
              aliases: Array.isArray(updated.aliases) ? updated.aliases : char.aliases,
              // ALWAYS preserve stats and firstAppearance from file - never overwrite
              stats: char.stats,
              firstAppearance: char.firstAppearance
            };
          }
          return char;
        });

      // Append any new characters that were added in memory but not present on disk
      const added = data.characters.filter(c => !existingNames.has(c.name));

      const updated = {
        ...existing,
        characters: [...updatedExisting, ...added]
      };
      await writeCharacters(path, updated);
      return;
    }
  } catch (err) {
    console.error('Error persisting book characters:', err);
  }

  // Fallback: write our format (should rarely happen)
  await writeCharacters(path, data);
}

export async function setBookCharacterVoice(name: string, voice: string | null): Promise<void> {
  const data = get(bookCharacters);
  const canonicalName = resolveCanonicalCharacterName(data, name) ?? name;
  const exists = data.characters.find(c => c.name === canonicalName);
  const next: CharactersJson = exists
    ? { formatVersion: data.formatVersion, characters: data.characters.map(c => c.name === canonicalName ? { ...c, voice } : c) }
    : {
      formatVersion: data.formatVersion,
      characters: [
        ...data.characters,
        {
          name: canonicalName,
          color: null,
          voice,
          provider: null,
          voiceId: null,
          voiceMeta: null,
          manifestStats: null,
          count: 0,
          firstAppearance: null,
          chapterCount: 0,
        } as Character,
      ],
    };
  bookCharacters.set(next);
  await persistBookCharacters();
}

export async function setBookCharacterColor(name: string, color: string | null): Promise<void> {
  const data = get(bookCharacters);
  const canonicalName = resolveCanonicalCharacterName(data, name) ?? name;
  const exists = data.characters.find(c => c.name === canonicalName);
  const next: CharactersJson = exists
    ? { formatVersion: data.formatVersion, characters: data.characters.map(c => c.name === canonicalName ? { ...c, color } : c) }
    : {
      formatVersion: data.formatVersion,
      characters: [
        ...data.characters,
        {
          name: canonicalName,
          color,
          voice: null,
          provider: null,
          voiceId: null,
          voiceMeta: null,
          manifestStats: null,
          count: 0,
          firstAppearance: null,
          chapterCount: 0,
        } as Character,
      ],
    };
  bookCharacters.set(next);
  await persistBookCharacters();
}

export async function setBookCharacterProvider(name: string, provider: TtsProvider | null): Promise<void> {
  const data = get(bookCharacters);
  const canonicalName = resolveCanonicalCharacterName(data, name) ?? name;
  const exists = data.characters.find(c => c.name === canonicalName);
  const next: CharactersJson = exists
    ? { formatVersion: data.formatVersion, characters: data.characters.map(c => c.name === canonicalName ? { ...c, provider } : c) }
    : {
      formatVersion: data.formatVersion,
      characters: [
        ...data.characters,
        {
          name: canonicalName,
          color: null,
          voice: null,
          provider,
          voiceId: null,
          voiceMeta: null,
          manifestStats: null,
          count: 0,
          firstAppearance: null,
          chapterCount: 0,
        } as Character,
      ],
    };
  bookCharacters.set(next);
  await persistBookCharacters();
}

export async function setBookCharacterVoiceId(name: string, voiceId: string | null): Promise<void> {
  const data = get(bookCharacters);
  const canonicalName = resolveCanonicalCharacterName(data, name) ?? name;
  const exists = data.characters.find(c => c.name === canonicalName);
  const next: CharactersJson = exists
    ? { formatVersion: data.formatVersion, characters: data.characters.map(c => c.name === canonicalName ? { ...c, voiceId } : c) }
    : {
      formatVersion: data.formatVersion,
      characters: [
        ...data.characters,
        {
          name: canonicalName,
          color: null,
          voice: null,
          provider: null,
          voiceId,
          voiceMeta: null,
          manifestStats: null,
          count: 0,
          firstAppearance: null,
          chapterCount: 0,
        } as Character,
      ],
    };
  bookCharacters.set(next);
  await persistBookCharacters();
}

export async function setBookCharacterVoiceMeta(name: string, voiceMeta: CharacterVoiceMeta | null): Promise<void> {
  const data = get(bookCharacters);
  const canonicalName = resolveCanonicalCharacterName(data, name) ?? name;
  const exists = data.characters.find(c => c.name === canonicalName);
  const next: CharactersJson = exists
    ? { formatVersion: data.formatVersion, characters: data.characters.map(c => c.name === canonicalName ? { ...c, voiceMeta } : c) }
    : {
      formatVersion: data.formatVersion,
      characters: [
        ...data.characters,
        {
          name: canonicalName,
          color: null,
          voice: null,
          provider: null,
          voiceId: null,
          voiceMeta,
          manifestStats: null,
          count: 0,
          firstAppearance: null,
          chapterCount: 0,
        } as Character,
      ],
    };
  bookCharacters.set(next);
  await persistBookCharacters();
}

export async function setBookCharacterManifestStats(name: string, manifestStats: CharacterManifestStats | null): Promise<void> {
  const data = get(bookCharacters);
  const canonicalName = resolveCanonicalCharacterName(data, name) ?? name;
  const exists = data.characters.find(c => c.name === canonicalName);
  const next: CharactersJson = exists
    ? { formatVersion: data.formatVersion, characters: data.characters.map(c => c.name === canonicalName ? { ...c, manifestStats } : c) }
    : {
      formatVersion: data.formatVersion,
      characters: [
        ...data.characters,
        {
          name: canonicalName,
          color: null,
          voice: null,
          provider: null,
          voiceId: null,
          voiceMeta: null,
          manifestStats,
          count: 0,
          firstAppearance: null,
          chapterCount: 0,
        } as Character,
      ],
    };
  bookCharacters.set(next);
  await persistBookCharacters();
}

interface RefreshManifestOptions {
  totalLines?: number;
  force?: boolean;
}

export async function refreshCharacterManifestStats(
  name: string,
  options?: RefreshManifestOptions,
): Promise<CharacterManifestStats | null> {
  const root = get(audioRoot);
  if (!root) return null;
  const summary = await loadCharacterManifestSummary(root, name, {
    totalLines: options?.totalLines,
    force: options?.force,
  });
  await setBookCharacterManifestStats(name, summary.stats);
  return summary.stats;
}

export async function renameBookCharacter(oldName: string, newName: string): Promise<void> {
  const root = get(bookRoot);
  if (!root) return;

  const existing = await readCentralCharacters(root);
  if (!existing || !Array.isArray(existing.characters)) return;

  const trimmedOldName = String(oldName || '').trim();
  const trimmedNewName = String(newName || '').trim();
  if (!trimmedOldName || !trimmedNewName || trimmedOldName === trimmedNewName) return;

  const canonicalOldName = resolveCanonicalCharacterName(existing, trimmedOldName) ?? trimmedOldName;
  const canonicalNewName = resolveCanonicalCharacterName(existing, trimmedNewName) ?? trimmedNewName;
  const sourceIndex = existing.characters.findIndex(c => c.name === canonicalOldName);
  if (sourceIndex === -1) return;

  const sourceCharacter = existing.characters[sourceIndex] as any;
  const oldCharacterId = typeof sourceCharacter?.id === 'string' ? sourceCharacter.id : '';
  const conflict = existing.characters.find(c => c.name === canonicalNewName && c.name !== canonicalOldName) as any;

  if (conflict) {
    const next: CharactersJson = {
      formatVersion: existing.formatVersion || '2.0',
      characters: existing.characters.filter(c => c.name !== canonicalOldName)
    };
    await writeCentralCharacters(root, next);

    const targetCharacterId = typeof conflict?.id === 'string' ? conflict.id : '';
    if (oldCharacterId && targetCharacterId && oldCharacterId !== targetCharacterId) {
      await remapCharacterIdInDialogueFiles(root, oldCharacterId, targetCharacterId);
      await remapCharacterIdInVoicesAssignments(root, oldCharacterId, targetCharacterId);
    }
    await remapCharacterNameInScriptFiles(root, canonicalOldName, canonicalNewName);
    await remapCharacterNameInChapterCharacterFiles(root, canonicalOldName, canonicalNewName);

    await forceRefreshBookCharacters();
    return;
  }

  const idsInUse = new Set(
    existing.characters
      .filter((_, index) => index !== sourceIndex)
      .map((character: any) => character?.id)
      .filter((id: any): id is string => typeof id === 'string' && id.length > 0)
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
    })
  };

  await writeCentralCharacters(root, next);
  if (oldCharacterId && oldCharacterId !== newCharacterId) {
    await remapCharacterIdInDialogueFiles(root, oldCharacterId, newCharacterId);
    await remapCharacterIdInVoicesAssignments(root, oldCharacterId, newCharacterId);
  }
  await remapCharacterNameInScriptFiles(root, canonicalOldName, canonicalNewName);
  await remapCharacterNameInChapterCharacterFiles(root, canonicalOldName, canonicalNewName);

  await forceRefreshBookCharacters();
}

export async function removeBookCharacter(name: string): Promise<void> {
  const data = get(bookCharacters);
  const canonicalName = resolveCanonicalCharacterName(data, name) ?? name;
  const next: CharactersJson = { formatVersion: data.formatVersion, characters: data.characters.filter(c => c.name !== canonicalName) };
  bookCharacters.set(next);
  await persistBookCharacters();
}

function normalizeAliasList(names: string[]): string[] {
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

export async function mergeBookCharacters(sourceName: string, targetName: string): Promise<void> {
  if (!sourceName || !targetName || sourceName === targetName) return;
  const root = get(bookRoot);
  if (!root) return;

  const data = await readCentralCharacters(root);
  if (!data?.characters) return;

  const sourceIndex = data.characters.findIndex((c: any) => c?.name === sourceName);
  const targetIndex = data.characters.findIndex((c: any) => c?.name === targetName);
  if (sourceIndex === -1 || targetIndex === -1) return;

  const source = data.characters[sourceIndex] as any;
  const target = data.characters[targetIndex] as any;
  const sourceCharacterId = typeof source?.id === 'string' ? source.id : '';
  const targetCharacterId = typeof target?.id === 'string' ? target.id : '';

  const sourceAliases = Array.isArray(source.aliases) ? source.aliases : [];
  const targetAliases = Array.isArray(target.aliases) ? target.aliases : [];
  const mergedAliases = normalizeAliasList([
    ...targetAliases,
    source.name,
    ...sourceAliases,
  ]).filter((alias) => alias.toLowerCase() !== target.name.toLowerCase());

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
    await remapCharacterIdInDialogueFiles(root, sourceCharacterId, targetCharacterId);
    await remapCharacterIdInVoicesAssignments(root, sourceCharacterId, targetCharacterId);
  }
  await remapCharacterNameInScriptFiles(root, source.name, target.name);
  await remapCharacterNameInChapterCharacterFiles(root, source.name, target.name);

  data.characters.splice(sourceIndex, 1);
  await writeCentralCharacters(root, data);
  await forceRefreshBookCharacters();
}

export async function setBookCharacterPrimaryName(currentName: string, newPrimaryName: string): Promise<void> {
  if (!currentName || !newPrimaryName || currentName === newPrimaryName) return;
  const root = get(bookRoot);
  if (!root) return;

  const data = await readCentralCharacters(root);
  if (!data?.characters) return;

  const target = data.characters.find((c: any) => c?.name === currentName);
  if (!target) return;

  const conflict = data.characters.find((c: any) => c?.name === newPrimaryName);
  if (conflict && conflict !== target) {
    console.warn('[bookCharacters] Cannot set primary name: target already exists', newPrimaryName);
    return;
  }

  const aliases = Array.isArray(target.aliases) ? target.aliases : [];
  if (!aliases.some((alias) => alias === newPrimaryName)) return;

  target.name = newPrimaryName;
  target.aliases = normalizeAliasList([
    ...aliases.filter((alias) => alias !== newPrimaryName),
    currentName,
  ]).filter((alias) => alias.toLowerCase() !== target.name.toLowerCase());

  await writeCentralCharacters(root, data);
  await forceRefreshBookCharacters();
}

export async function detachBookCharacterAlias(characterName: string, aliasName: string): Promise<void> {
  const root = get(bookRoot);
  if (!root) return;

  const data = await readCentralCharacters(root);
  if (!data?.characters) return;

  const source = data.characters.find((c: any) => c?.name === characterName);
  if (!source) return;

  const aliases = Array.isArray(source.aliases) ? source.aliases : [];
  if (!aliases.includes(aliasName)) return;

  const existingCanonicalId = typeof source?.id === 'string' ? source.id.trim() : '';
  const idsInUse = new Set(
    data.characters
      .filter((character: any) => character !== source)
      .map((character: any) => character?.id)
      .filter((id: any): id is string => typeof id === 'string' && id.length > 0)
  );
  const preferredCanonicalId = slugifyCharacterId(source.name);
  const canonicalCharacterId = idsInUse.has(preferredCanonicalId)
    ? buildUniqueCharacterId(source.name, idsInUse)
    : preferredCanonicalId;

  if (!existingCanonicalId || existingCanonicalId !== canonicalCharacterId) {
    source.id = canonicalCharacterId;
  }

  source.aliases = aliases.filter((a: string) => a !== aliasName);

  if (canonicalCharacterId) {
    if (existingCanonicalId && existingCanonicalId !== canonicalCharacterId) {
      await remapCharacterIdInDialogueFiles(root, existingCanonicalId, canonicalCharacterId);
      await remapCharacterIdInVoicesAssignments(root, existingCanonicalId, canonicalCharacterId);
      await remapCharacterIdPrefixInDialogueFiles(root, existingCanonicalId, canonicalCharacterId);
      await remapCharacterIdPrefixInVoicesAssignments(root, existingCanonicalId, canonicalCharacterId);
    }

    const aliasIdCandidates = normalizeAliasList([aliasName, slugifyCharacterId(aliasName)])
      .filter((candidate) => candidate !== canonicalCharacterId && candidate !== existingCanonicalId);
    for (const aliasCharacterId of aliasIdCandidates) {
      await remapCharacterIdInDialogueFiles(root, aliasCharacterId, canonicalCharacterId);
      await remapCharacterIdInVoicesAssignments(root, aliasCharacterId, canonicalCharacterId);
      await remapCharacterIdPrefixInDialogueFiles(root, aliasCharacterId, canonicalCharacterId);
      await remapCharacterIdPrefixInVoicesAssignments(root, aliasCharacterId, canonicalCharacterId);
    }
  }

  await remapCharacterNameInScriptFiles(root, aliasName, source.name);
  await remapCharacterNameInChapterCharacterFiles(root, aliasName, source.name);

  await writeCentralCharacters(root, data);
  await forceRefreshBookCharacters();
}

export async function addBookCharacterAlias(characterName: string, aliasName: string): Promise<void> {
  const canonical = String(characterName || '').trim();
  const alias = String(aliasName || '').trim();
  if (!canonical || !alias) return;
  if (canonical.toLowerCase() === alias.toLowerCase()) return;

  const root = get(bookRoot);
  if (!root) return;

  const data = await readCentralCharacters(root);
  if (!data?.characters) return;

  const target = data.characters.find((character: any) => character?.name === canonical);
  if (!target) return;

  const aliasKey = alias.toLowerCase();
  const collidingCanonical = data.characters.find(
    (character: any) => String(character?.name || '').trim().toLowerCase() === aliasKey
  );
  if (collidingCanonical && String(collidingCanonical.name).trim().toLowerCase() !== canonical.toLowerCase()) {
    return;
  }

  const aliases = Array.isArray(target.aliases) ? target.aliases : [];
  if (aliases.some((existing: string) => existing.toLowerCase() === alias.toLowerCase())) return;

  target.aliases = normalizeAliasList([...aliases, alias]).filter(
    (candidate) => candidate.toLowerCase() !== canonical.toLowerCase()
  );

  await writeCentralCharacters(root, data);
  await forceRefreshBookCharacters();
}

/**
 * Synchronize all chapter-level character colors with book-level colors.
 * This ensures the book-level is the single source of truth.
 * Useful for migrating existing books to the centralized color system.
 */
export async function syncAllChapterColorsFromBook(): Promise<{ updated: number; errors: string[] }> {
  const root = get(bookRoot);
  if (!root) return { updated: 0, errors: ['No book root'] };

  const bookChars = get(bookCharacters);
  const bookColorMap = new Map(bookChars.characters.map(c => [c.name, c]));

  const chapterList = get(chapters);
  let updated = 0;
  const errors: string[] = [];

  for (const ch of chapterList) {
    try {
      const charsPath = getCharactersPath(root, ch.title);
      const chapterChars = await readCharacters(charsPath);

      if (chapterChars && chapterChars.characters) {
        // Update chapter characters with book-level colors
        const synced: CharactersJson = {
          formatVersion: chapterChars.formatVersion || '2.0',
          characters: chapterChars.characters.map(c => {
            const bookChar = bookColorMap.get(c.name);
            return {
              ...c,
              color: bookChar?.color ?? c.color ?? null,
              voice: bookChar?.voice ?? c.voice ?? null,
              provider: bookChar?.provider ?? c.provider ?? null,
              voiceId: bookChar?.voiceId ?? c.voiceId ?? null,
              voiceMeta: bookChar?.voiceMeta ?? c.voiceMeta ?? null,
            };
          })
        };

        await writeCharacters(charsPath, synced);
        updated++;
      }
    } catch (e) {
      errors.push(`${ch.title}: ${String(e)}`);
    }
  }

  return { updated, errors };
}

/**
 * Ensure all characters in the book have colors assigned.
 * Characters without colors will get hash-based default colors persisted.
 * This makes colors consistent across all chapters.
 */
export async function ensureAllCharactersHaveColors(): Promise<{ updated: number }> {
  const data = get(bookCharacters);
  let updated = 0;

  // Import hashToIndex and defaultColors at runtime to avoid circular deps
  const { hashToIndex } = await import('$lib/stores/characters');
  const { defaultColors } = await import('$lib/theme/colors');

  const next: CharactersJson = {
    formatVersion: data.formatVersion,
    characters: data.characters.map(c => {
      if (!c.color) {
        updated++;
        return { ...c, color: defaultColors[hashToIndex(c.name)] };
      }
      return c;
    })
  };

  if (updated > 0) {
    bookCharacters.set(next);
    await persistBookCharacters();
  }

  return { updated };
}

/**
 * Sync voice assignments from voices.json to populate character voice metadata
 * This reads the assignments from voices.json and updates character properties
 */
export async function syncAssignmentsFromVoicesJson(): Promise<void> {
  const root = get(bookRoot);
  if (!root) return;

  try {
    // Import voices store functions
    const { voices: voicesStore, getCharacterAssignment, getAssignedVoice } = await import('$lib/stores/speakers');
    const voicesData = get(voicesStore);

    if (!voicesData || voicesData.assignments.length === 0) {
      console.log('[bookCharacters] No assignments to sync');
      return;
    }

    // Read the characters.json to get character IDs
    const charactersData = await readCentralCharacters(root);
    if (!charactersData) {
      console.log('[bookCharacters] No characters.json found');
      return;
    }

    const data = get(bookCharacters);
    let updated = false;

    // Create a map from character name to character ID (from characters.json)
    const nameToIdMap = new Map<string, string>();
    charactersData.characters.forEach((char: any) => {
      nameToIdMap.set(char.name, char.id);
    });

    const next: CharactersJson = {
      formatVersion: data.formatVersion,
      characters: data.characters.map(c => {
        const characterId = nameToIdMap.get(c.name);
        if (!characterId) return c;

        const assignment = getCharacterAssignment(characterId);
        if (!assignment) return c;

        const assignedVoice = getAssignedVoice(characterId);
        if (!assignedVoice) return c;

        updated = true;

        // Update character with voice metadata from assignment
        return {
          ...c,
          provider: assignedVoice.provider,
          voiceId: assignedVoice.providerVoiceId,
          voiceMeta: {
            name: assignedVoice.displayName,
            previewUrl: assignedVoice.previewUrl,
            fetchedAt: new Date().toISOString(),
            provider: assignedVoice.provider
          }
        };
      })
    };

    if (updated) {
      bookCharacters.set(next);
      console.log('[bookCharacters] Synced voice assignments from voices.json');
    }
  } catch (err) {
    console.error('[bookCharacters] Error syncing assignments:', err);
  }
}

/**
 * Get character ID from character name by reading characters.json
 */
async function getCharacterIdByName(name: string): Promise<string | null> {
  const root = get(bookRoot);
  if (!root) return null;

  try {
    const charactersData = await readCentralCharacters(root);
    if (!charactersData) return null;

    const char = charactersData.characters.find((c: any) => c.name === name);
    return char?.id || null;
  } catch (err) {
    console.error('[bookCharacters] Error getting character ID:', err);
    return null;
  }
}

/**
 * Update setBookCharacterProvider to also update voices.json assignments
 */
export async function setBookCharacterProviderAndAssign(
  name: string,
  provider: TtsProvider | null,
  voiceId: string | null
): Promise<void> {
  // Update character in bookCharacters
  await setBookCharacterProvider(name, provider);

  if (voiceId) {
    await setBookCharacterVoiceId(name, voiceId);
  }

  // Also update voices.json assignment
  if (provider && voiceId) {
    const characterId = await getCharacterIdByName(name);
    if (characterId) {
      const { assignVoiceToCharacter } = await import('$lib/stores/speakers');
      const { voices: voicesStore } = await import('$lib/stores/speakers');
      const voicesData = get(voicesStore);

      // Find the voice with matching provider and providerVoiceId
      const voice = voicesData.voices.find(v =>
        v.provider === provider && v.providerVoiceId === voiceId
      );

      if (voice) {
        await assignVoiceToCharacter(characterId, voice.id);
        console.log(`[bookCharacters] Created assignment: ${characterId} -> ${voice.id}`);
      }
    }
  }
}


