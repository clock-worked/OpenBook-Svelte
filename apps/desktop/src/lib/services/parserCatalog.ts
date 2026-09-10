import type { CharactersJson } from '../types.ts';

export interface ParserCharacterCatalogEntry {
  characterId: string;
  name: string;
  aliases: string[];
  gender: string;
}

export function buildClosedWorldParserOptions(
  baseOptions: Record<string, unknown>,
  data: CharactersJson,
): Record<string, unknown> {
  const characterCatalog: ParserCharacterCatalogEntry[] = data.characters
    .filter((character) => Boolean(character.id && character.name))
    .map((character) => ({
      characterId: character.id,
      name: character.name,
      aliases: Array.isArray(character.aliases) ? character.aliases : [],
      gender: character.gender || 'Unknown',
    }));
  if (!characterCatalog.length) return baseOptions;
  return {
    ...baseOptions,
    closed_world_characters: true,
    character_catalog: characterCatalog,
  };
}
