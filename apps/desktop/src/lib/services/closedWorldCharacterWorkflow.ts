import type { Character, CharactersJson, DialogueLine, Gender } from '../types.ts';

function slugifyCharacterId(name: string): string {
  const cleaned = String(name ?? '')
    .trim()
    .toLowerCase()
    .replace(/[^a-z0-9\s-]/g, '')
    .replace(/\s+/g, ' ')
    .trim();
  return cleaned ? cleaned.replace(/\s/g, '-') : 'character';
}

function buildUniqueCharacterId(name: string, existingIds: Set<string>): string {
  const base = slugifyCharacterId(name);
  let id = base;
  let suffix = 2;
  while (existingIds.has(id)) id = `${base}-${suffix++}`;
  return id;
}

function normalizeAliasList(values: string[]): string[] {
  const seen = new Set<string>();
  return values.filter((value) => {
    const key = normalizeKey(value);
    if (!key || seen.has(key)) return false;
    seen.add(key);
    return true;
  });
}

function normalizeGender(value: Gender): Gender {
  return value === 'Male' || value === 'Female' ? value : 'Unknown';
}

export interface ClosedWorldReviewItem {
  lineId: number;
  candidateName: string;
  candidateCharacterId: string | null;
  text: string;
}

export interface NewClosedWorldCharacterInput {
  displayName: string;
  gender: Gender;
}

function normalizeKey(value: unknown): string {
  return String(value ?? '').trim().toLowerCase();
}

function createCharacterRecord(id: string, name: string, gender: Gender): Character {
  return {
    id,
    name,
    gender: normalizeGender(gender),
    aliases: [],
    color: null,
    notes: '',
    firstAppearance: null,
    stats: { totalLines: 0, chapterCount: 0 },
    voice: null,
    provider: null,
    voiceId: null,
    voiceMeta: null,
    manifestStats: null,
    count: 0,
    chapterCount: 0,
  } as Character;
}

export function createClosedWorldCharacter(
  data: CharactersJson,
  input: NewClosedWorldCharacterInput,
): { characters: CharactersJson; character: Character } {
  const displayName = String(input.displayName ?? '').trim();
  if (!displayName) throw new Error('Character display name is required');
  const displayKey = normalizeKey(displayName);
  const collides = data.characters.some((character) =>
    normalizeKey(character.name) === displayKey
    || (character.aliases ?? []).some((alias) => normalizeKey(alias) === displayKey),
  );
  if (collides) throw new Error(`Character name already exists: ${displayName}`);
  const existingIds = new Set(
    data.characters.map((character) => character.id).filter((id): id is string => Boolean(id)),
  );
  const character = createCharacterRecord(buildUniqueCharacterId(displayName, existingIds), displayName, input.gender);
  return {
    character,
    characters: {
      formatVersion: data.formatVersion || '2.0',
      characters: [...data.characters, character],
    },
  };
}

export function resolveClosedWorldCandidateAsAlias(
  data: CharactersJson,
  candidateName: string,
  targetCharacterId: string,
): { characters: CharactersJson; changed: boolean } {
  const alias = String(candidateName ?? '').trim();
  const targetIdKey = normalizeKey(targetCharacterId);
  if (!alias || !targetIdKey) throw new Error('Alias and target character are required');
  const targetIndex = data.characters.findIndex((character) => normalizeKey(character.id) === targetIdKey);
  if (targetIndex < 0) throw new Error('Target character was not found');
  const aliasKey = normalizeKey(alias);
  const canonicalCollision = data.characters.find(
    (character, index) => index !== targetIndex && normalizeKey(character.name) === aliasKey,
  );
  if (canonicalCollision) throw new Error(`Alias is already a canonical character: ${canonicalCollision.name}`);
  const target = data.characters[targetIndex];
  if (normalizeKey(target.name) === aliasKey || (target.aliases ?? []).some((value) => normalizeKey(value) === aliasKey)) {
    return { characters: data, changed: false };
  }
  const characters = data.characters.map((character, index) =>
    index === targetIndex
      ? { ...character, aliases: normalizeAliasList([...(character.aliases ?? []), alias]) }
      : character,
  );
  return {
    changed: true,
    characters: { formatVersion: data.formatVersion || '2.0', characters },
  };
}

function catalogueKeys(data: CharactersJson): Set<string> {
  const keys = new Set<string>();
  for (const character of data.characters) {
    for (const value of [character.id, character.name, ...(character.aliases ?? [])]) {
      const key = normalizeKey(value);
      if (key) keys.add(key);
    }
  }
  return keys;
}

function candidateSurface(line: DialogueLine): string {
  const attribution = line.attribution as any;
  const values = [
    attribution?.sourceAlias,
    attribution?.decisionTrace?.selectedCandidate,
    attribution?.candidates?.[0]?.name,
    (line as any).chosenSpeaker,
  ];
  return String(values.find((value) => typeof value === 'string' && value.trim()) ?? '').trim();
}

export function buildClosedWorldReviewItems(
  lines: DialogueLine[],
  data: CharactersJson,
): ClosedWorldReviewItem[] {
  const known = catalogueKeys(data);
  const items: ClosedWorldReviewItem[] = [];
  const seen = new Set<string>();
  for (const line of lines) {
    if (normalizeKey(line.characterId) === 'narrator') continue;
    const candidateName = candidateSurface(line);
    const candidateId = String(line.characterId ?? '').trim();
    const candidateKey = normalizeKey(candidateName);
    const idKey = normalizeKey(candidateId);
    if ((idKey && known.has(idKey)) || (candidateKey && known.has(candidateKey))) continue;
    if (!candidateName || normalizeKey(candidateName) === 'narrator') continue;
    const dedupeKey = `${line.id}:${candidateKey}:${idKey}`;
    if (seen.has(dedupeKey)) continue;
    seen.add(dedupeKey);
    items.push({
      lineId: line.id,
      candidateName,
      candidateCharacterId: candidateId || null,
      text: line.text,
    });
  }
  return items;
}

export function preferredCharacterId(displayName: string): string {
  return slugifyCharacterId(displayName);
}
