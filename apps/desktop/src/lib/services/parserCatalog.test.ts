import assert from 'node:assert/strict';
import test from 'node:test';

import { buildClosedWorldParserOptions } from './parserCatalog.ts';

const characters = {
  formatVersion: '2.0',
  characters: [
    { id: 'jake', name: 'Jake Thayne', aliases: ['Jake', 'Chosen'], gender: 'Male' },
    { id: 'miranda', name: 'Miranda', aliases: [], gender: 'Female' },
  ],
};

test('adds the canonical catalogue to parser options', () => {
  const options = buildClosedWorldParserOptions({ pov_mode: 'third_person' }, characters as any);

  assert.equal(options.closed_world_characters, true);
  assert.deepEqual(options.character_catalog, [
    { characterId: 'jake', name: 'Jake Thayne', aliases: ['Jake', 'Chosen'], gender: 'Male' },
    { characterId: 'miranda', name: 'Miranda', aliases: [], gender: 'Female' },
  ]);
});

test('omits closed-world mode when the catalogue is empty', () => {
  const options = buildClosedWorldParserOptions({ pov_mode: 'first_person' }, { formatVersion: '2.0', characters: [] });

  assert.deepEqual(options, { pov_mode: 'first_person' });
});
