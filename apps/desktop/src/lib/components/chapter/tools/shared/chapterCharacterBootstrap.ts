import { buildNameToIdMap } from '$lib/stores/characters';

function slugifyCharacterId(name: string): string {
  const cleaned = String(name || '')
    .trim()
    .toLowerCase()
    .replace(/[^a-z0-9\s-]/g, '')
    .replace(/\s+/g, ' ')
    .trim();
  return cleaned.length ? cleaned.replace(/\s/g, '-') : 'character';
}

function stripNumericSlugSuffix(name: string): string {
  return String(name || '').trim().toLowerCase().replace(/(?:[-_]\d+)+$/g, '');
}

function resolveExistingCanonicalForSlugVariant(
  name: string,
  existingByName: Map<string, any>
): string | null {
  const normalized = String(name || '').trim().toLowerCase();
  if (!normalized) return null;
  if (existingByName.has(normalized)) return null;

  const base = stripNumericSlugSuffix(normalized);
  if (!base || base === normalized) return null;

  const baseEntry = existingByName.get(base);
  if (!baseEntry || typeof baseEntry.name !== 'string') return null;
  return baseEntry.name;
}

export async function ensureCharacterNameToIdMapCached(params: {
  cachedMap: Map<string, string> | null;
  setCachedMap: (map: Map<string, string>) => void;
  root: string | null;
  readCentralCharacters: (root: string) => Promise<{ characters?: any[] } | null>;
}): Promise<Map<string, string>> {
  if (params.cachedMap) return params.cachedMap;
  if (!params.root) return new Map();

  try {
    const centralChars = await params.readCentralCharacters(params.root);
    const nextMap = centralChars?.characters ? buildNameToIdMap(centralChars.characters) : new Map<string, string>();
    params.setCachedMap(nextMap);
    return nextMap;
  } catch (error) {
    console.warn('[ChapterView] Failed to load characters for name-to-ID map:', error);
    const empty = new Map<string, string>();
    params.setCachedMap(empty);
    return empty;
  }
}

export async function ensureCentralCharactersForNames(params: {
  names: string[];
  root: string | null;
  readCentralCharacters: (root: string) => Promise<{ formatVersion?: string; characters?: any[] } | null>;
  writeCentralCharacters: (root: string, data: any) => Promise<boolean>;
  getBackendRootAbsolutePath: () => string | null;
  apiSave: (payload: { file_path: string; content: any }) => Promise<Response>;
  onCharactersUpdated: (central: any) => void;
  resetCharacterNameToIdCache: () => void;
}): Promise<void> {
  if (!params.root) return;

  const loadedCentral = await params.readCentralCharacters(params.root);
  const central =
    loadedCentral && Array.isArray(loadedCentral.characters)
      ? loadedCentral
      : ({ formatVersion: '2.0', characters: [] } as any);

  const existingByName = new Map<string, any>(
    central.characters
      .filter((character: any) => character && typeof character.name === 'string')
      .map((character: any) => [character.name.toLowerCase(), character] as const)
  );
  const existingIds = new Set(
    central.characters
      .map((character: any) => (character?.id ? String(character.id).toLowerCase() : null))
      .filter((id: string | null): id is string => !!id)
  );

  let changed = false;
  for (const entry of central.characters) {
    if (!entry || typeof entry.name !== 'string') continue;
    if (!entry.id) {
      let id = slugifyCharacterId(entry.name);
      let suffix = 2;
      while (existingIds.has(id)) {
        id = `${slugifyCharacterId(entry.name)}-${suffix}`;
        suffix += 1;
      }
      entry.id = id;
      existingIds.add(id);
      changed = true;
    }
  }

  for (const rawName of params.names) {
    const name = rawName.trim();
    if (!name) continue;

    const key = name.toLowerCase();
    if (existingByName.has(key)) continue;

    const canonicalForVariant = resolveExistingCanonicalForSlugVariant(name, existingByName);
    if (canonicalForVariant) {
      const canonicalKey = canonicalForVariant.toLowerCase();
      const canonicalEntry = existingByName.get(canonicalKey) as any;
      if (canonicalEntry) {
        const aliases = Array.isArray(canonicalEntry.aliases) ? canonicalEntry.aliases : [];
        if (!aliases.some((alias: string) => String(alias || '').trim().toLowerCase() === key)) {
          canonicalEntry.aliases = [...aliases, name];
          changed = true;
        }
        existingByName.set(key, canonicalEntry);
      }
      continue;
    }

    let id = slugifyCharacterId(name);
    let suffix = 2;
    while (existingIds.has(id)) {
      id = `${slugifyCharacterId(name)}-${suffix}`;
      suffix += 1;
    }

    existingIds.add(id);
    central.characters.push({
      id,
      name,
      gender: 'Unknown',
      aliases: [],
      color: null,
      notes: '',
      stats: {
        totalLines: 0,
        chapterCount: 0,
      },
      voice: null,
      provider: null,
      voiceId: null,
      voiceMeta: null,
      manifestStats: null,
      count: 0,
      firstAppearance: null,
      chapterCount: 0,
    });
    existingByName.set(key, central.characters[central.characters.length - 1]);
    changed = true;
  }

  if (!changed) return;

  const savedLocally = await params.writeCentralCharacters(params.root, central as any);
  if (!savedLocally) {
    const backendRoot = params.getBackendRootAbsolutePath();
    if (!backendRoot) {
      console.warn('[ChapterView] Failed to save characters.json: no backend root path available');
    } else {
      try {
        const response = await params.apiSave({
          file_path: 'characters.json',
          content: central,
        });

        if (response.ok) {
          console.log('[ChapterView] Successfully saved characters.json via API');
        } else {
          const errorData = await response.json();
          console.error('[ChapterView] Failed to save characters.json via API:', errorData.detail || response.statusText);
        }
      } catch (error) {
        console.error('[ChapterView] Network error saving characters.json via API:', error);
      }
    }
  }

  params.onCharactersUpdated(central as any);
  params.resetCharacterNameToIdCache();
}
