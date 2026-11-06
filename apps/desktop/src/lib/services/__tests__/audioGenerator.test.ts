import { describe, expect, it, vi, beforeEach } from 'vitest';

// Controllable audiobookSettings store (the only state dependency).
// Minimal svelte-store-compatible object: svelte's get() only needs
// subscribe() to emit the current value synchronously.
const mockSettings = vi.hoisted(() => {
  let value: any = { audioGenerator: undefined, chatterboxSettings: undefined };
  const subscribers = new Set<() => void>();
  return {
    set(next: any) {
      value = next;
      subscribers.forEach((fn) => fn(value));
    },
    subscribe(fn: (value: any) => void) {
      subscribers.add(fn);
      fn(value);
      return () => {
        subscribers.delete(fn);
      };
    },
  };
});

vi.mock('$lib/stores/settings', () => ({
  audiobookSettings: mockSettings,
}));

import {
  DEFAULT_AUDIO_GENERATOR,
  DEFAULT_CHATTERBOX_CHOICE,
  EXPRESSIVE_EXAGGERATION,
  sanitizeChatterboxChoice,
  resolveChatterboxSettings,
  resolveGeneratorChoice,
  resolveEffectiveAudioGenerator,
} from '../audioGenerator';

beforeEach(() => {
  mockSettings.set({ audioGenerator: undefined, chatterboxSettings: undefined });
});

describe('sanitizeChatterboxChoice', () => {
  it('returns the default choice for null/undefined', () => {
    expect(sanitizeChatterboxChoice(null)).toEqual(DEFAULT_CHATTERBOX_CHOICE);
    expect(sanitizeChatterboxChoice(undefined)).toEqual(DEFAULT_CHATTERBOX_CHOICE);
  });

  it('coerces an unknown preset to default and non-finite numbers to null', () => {
    const out = sanitizeChatterboxChoice({
      preset: 'bogus' as any,
      temperature: NaN,
      cfgWeight: 1.2,
      exaggeration: undefined as any,
    });
    expect(out).toEqual({ preset: 'default', temperature: null, cfgWeight: 1.2, exaggeration: null });
  });
});

describe('resolveChatterboxSettings', () => {
  it('base + default → variant only', () => {
    expect(resolveChatterboxSettings(null, 'base')).toEqual({ variant: 'base' });
    expect(resolveChatterboxSettings(DEFAULT_CHATTERBOX_CHOICE, 'base')).toEqual({ variant: 'base' });
  });

  it('base + expressive → variant + pinned exaggeration', () => {
    const out = resolveChatterboxSettings({ preset: 'expressive', temperature: null, cfgWeight: null, exaggeration: null }, 'base');
    expect(out).toEqual({ variant: 'base', exaggeration: EXPRESSIVE_EXAGGERATION });
  });

  it('base + custom → variant + user values, nulls omitted', () => {
    const out = resolveChatterboxSettings(
      { preset: 'custom', temperature: 0.7, cfgWeight: null, exaggeration: 0.4 },
      'base',
    );
    expect(out).toEqual({ variant: 'base', temperature: 0.7, exaggeration: 0.4 });
    expect('cfg_weight' in out).toBe(false);
  });

  it('turbo + custom → temperature only (cfg/exaggeration stripped)', () => {
    const out = resolveChatterboxSettings(
      { preset: 'custom', temperature: 0.9, cfgWeight: 2.0, exaggeration: 0.6 },
      'turbo',
    );
    expect(out).toEqual({ variant: 'turbo', temperature: 0.9 });
  });

  it('turbo + expressive → variant only (expressive is a base lever)', () => {
    expect(resolveChatterboxSettings({ preset: 'expressive', temperature: null, cfgWeight: null, exaggeration: null }, 'turbo'))
      .toEqual({ variant: 'turbo' });
  });
});

describe('resolveGeneratorChoice', () => {
  it('chatterbox_base → chatterbox provider + base settings', () => {
    expect(resolveGeneratorChoice('chatterbox_base', null, 'vibevoice_local'))
      .toEqual({ provider: 'chatterbox', chatterboxSettings: { variant: 'base' } });
  });

  it('chatterbox_turbo → chatterbox provider + turbo settings', () => {
    expect(resolveGeneratorChoice('chatterbox_turbo', null, 'vibevoice_local'))
      .toEqual({ provider: 'chatterbox', chatterboxSettings: { variant: 'turbo' } });
  });

  it('vibevoice → vibevoice_local provider, no settings', () => {
    expect(resolveGeneratorChoice('vibevoice', null, 'chatterbox'))
      .toEqual({ provider: 'vibevoice_local', chatterboxSettings: null });
  });

  it('per_voice → the voice assigned provider, no settings', () => {
    expect(resolveGeneratorChoice('per_voice', null, 'vibevoice_local'))
      .toEqual({ provider: 'vibevoice_local', chatterboxSettings: null });
    expect(resolveGeneratorChoice('per_voice', null, 'chatterbox'))
      .toEqual({ provider: 'chatterbox', chatterboxSettings: null });
  });

  it('null → the default generator (chatterbox base), not per-voice', () => {
    expect(resolveGeneratorChoice(null, null, 'vibevoice_local'))
      .toEqual({ provider: 'chatterbox', chatterboxSettings: { variant: 'base' } });
  });
});

describe('resolveEffectiveAudioGenerator', () => {
  it('uses the settings-tab default when there is no override', () => {
    mockSettings.set({
      audioGenerator: 'vibevoice',
      chatterboxSettings: DEFAULT_CHATTERBOX_CHOICE,
    });
    expect(resolveEffectiveAudioGenerator('chatterbox'))
      .toEqual({ provider: 'vibevoice_local', chatterboxSettings: null });
  });

  it('falls back to chatterbox_base when the settings store is empty', () => {
    expect(resolveEffectiveAudioGenerator('vibevoice_local'))
      .toEqual({ provider: 'chatterbox', chatterboxSettings: { variant: 'base' } });
    expect(DEFAULT_AUDIO_GENERATOR).toBe('chatterbox_base');
  });

  it('an explicit override wins over the settings default', () => {
    mockSettings.set({ audioGenerator: 'vibevoice', chatterboxSettings: DEFAULT_CHATTERBOX_CHOICE });
    const out = resolveEffectiveAudioGenerator('vibevoice_local', {
      generator: 'chatterbox_turbo',
      chatterboxChoice: { preset: 'custom', temperature: 0.9, cfgWeight: null, exaggeration: null },
    });
    expect(out).toEqual({ provider: 'chatterbox', chatterboxSettings: { variant: 'turbo', temperature: 0.9 } });
  });

  it('a use_default/null override falls through to the settings default', () => {
    mockSettings.set({
      audioGenerator: 'chatterbox_base',
      chatterboxSettings: { preset: 'expressive', temperature: null, cfgWeight: null, exaggeration: null },
    });
    expect(resolveEffectiveAudioGenerator('vibevoice_local', { generator: 'use_default' }))
      .toEqual({ provider: 'chatterbox', chatterboxSettings: { variant: 'base', exaggeration: EXPRESSIVE_EXAGGERATION } });
    expect(resolveEffectiveAudioGenerator('vibevoice_local', { generator: null }))
      .toEqual({ provider: 'chatterbox', chatterboxSettings: { variant: 'base', exaggeration: EXPRESSIVE_EXAGGERATION } });
  });

  it('an override with no explicit choice borrows the settings-tab choice', () => {
    mockSettings.set({
      audioGenerator: 'vibevoice',
      chatterboxSettings: { preset: 'expressive', temperature: null, cfgWeight: null, exaggeration: null },
    });
    const out = resolveEffectiveAudioGenerator('vibevoice_local', { generator: 'chatterbox_base' });
    expect(out).toEqual({ provider: 'chatterbox', chatterboxSettings: { variant: 'base', exaggeration: EXPRESSIVE_EXAGGERATION } });
  });
});
