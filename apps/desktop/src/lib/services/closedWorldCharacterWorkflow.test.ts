import assert from 'node:assert/strict';
import test from 'node:test';

import {
  buildClosedWorldReviewItems,
  createClosedWorldCharacter,
  resolveClosedWorldCandidateAsAlias,
} from './closedWorldCharacterWorkflow.ts';

const characters = {
  formatVersion: '2.0',
  characters: [
    { id: 'jake', name: 'Jake', gender: 'Male', aliases: ['Chosen'], color: null, notes: '', firstAppearance: null, stats: { totalLines: 0, chapterCount: 0 }, voice: null, provider: null, voiceId: null, voiceMeta: null, manifestStats: null, count: 0, chapterCount: 0 },
  ],
} as const;

test('creates a new canonical character with a unique generated id', () => {
  const data = {
    ...characters,
    characters: [{ ...characters.characters[0], id: 'new-character' }],
  };
  const result = createClosedWorldCharacter(data as any, {
    displayName: 'New Character',
    gender: 'Unknown',
  });

  assert.equal(result.character.id, 'new-character-2');
  assert.equal(result.character.name, 'New Character');
  assert.equal(result.character.gender, 'Unknown');
  assert.equal(result.characters.characters.length, 2);
});

test('rejects a new character name that collides with an existing alias', () => {
  assert.throws(
    () => createClosedWorldCharacter(characters as any, { displayName: 'Chosen', gender: 'Unknown' }),
    /already exists/i,
  );
});

test('resolves a candidate as an alias without creating a second character', () => {
  const result = resolveClosedWorldCandidateAsAlias(characters as any, 'The Chosen', 'jake');

  assert.equal(result.changed, true);
  assert.deepEqual(result.characters.characters[0].aliases, ['Chosen', 'The Chosen']);
});

test('rejects an alias that is another canonical character name', () => {
  const data = {
    ...characters,
    characters: [...characters.characters, { ...characters.characters[0], id: 'miranda', name: 'Miranda', aliases: [] }],
  };

  assert.throws(
    () => resolveClosedWorldCandidateAsAlias(data as any, 'Miranda', 'jake'),
    /canonical character/i,
  );
});

test('queues only unresolved or out-of-catalogue dialogue candidates', () => {
  const lines = [
    { id: 1, characterId: 'jake', text: 'known', attribution: { sourceAlias: 'Jake' } },
    { id: 2, characterId: null, text: 'unknown', attribution: { sourceAlias: 'Masked Warrior', resolutionStatus: 'unknown' } },
    { id: 3, characterId: 'temporary-id', text: 'new', attribution: { sourceAlias: 'Scholar' } },
    { id: 4, characterId: 'narrator', text: 'narration', attribution: { sourceAlias: 'Narrator' } },
  ];

  const items = buildClosedWorldReviewItems(lines as any, characters as any);

  assert.deepEqual(items.map((item) => [item.lineId, item.candidateName]), [
    [2, 'Masked Warrior'],
    [3, 'Scholar'],
  ]);
});

test('known aliases do not enter the review queue', () => {
  const items = buildClosedWorldReviewItems([
    { id: 7, characterId: null, text: 'known alias', attribution: { sourceAlias: 'Chosen', resolutionStatus: 'unknown' } },
  ] as any, characters as any);

  assert.deepEqual(items, []);
});
