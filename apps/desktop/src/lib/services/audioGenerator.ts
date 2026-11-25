import { get } from 'svelte/store';
import { audiobookSettings } from '$lib/stores/settings';
import type {
  AudioGeneratorId,
  ChatterboxSettingsChoice,
  ChatterboxPreset,
} from '$lib/types';
import type { ChatterboxSettingsPayload } from '$lib/services/apiContracts';

// === Provider classification (the one FE home) ===
//
// "Which engine does this provider use, which endpoint does it hit?" is
// answered here and only here on the FE. The BE twin is the registry in
// py_services/audio/provider_profile.py; the two share no code and are
// pinned to each other by tests/provider_profile_vectors.json. Adding a
// provider = one entry here, one in the BE registry, one vector.

/**
 * Which generation endpoint family a provider uses:
 * - 'local_chunked' — local models (VibeVoice, Chatterbox) behind the
 *   /api/vibevoice/* chunked pipeline (line, character, chapter).
 * - 'cloud' — remote TTS APIs behind /api/generate_audio_line (line only).
 */
export type EndpointKind = 'local_chunked' | 'cloud';

export interface ProviderClassification {
  /** Canonical provider string (the registry name the alias resolved to). */
  provider: string;
  endpointKind: EndpointKind;
  /** Extension the backend writes the generated clip as. */
  fileExtension: '.wav' | '.mp3';
}

interface ProviderProfileEntry {
  aliases: readonly string[];
  endpointKind: EndpointKind;
  fileExtension: '.wav' | '.mp3';
}

/** Canonical provider name → profile. Mirrors the BE PROVIDER_PROFILES. */
const PROVIDER_PROFILES: Readonly<Record<string, ProviderProfileEntry>> = {
  vibevoice_local: { aliases: ['vibevoice-local', 'vibevoice'], endpointKind: 'local_chunked', fileExtension: '.wav' },
  chatterbox: { aliases: ['chatterbox_local', 'chatterbox-local'], endpointKind: 'local_chunked', fileExtension: '.wav' },
  google_tts: { aliases: ['chirp3', 'google', 'chirp'], endpointKind: 'cloud', fileExtension: '.mp3' },
  elevenlabs: { aliases: [], endpointKind: 'cloud', fileExtension: '.mp3' },
};

const PROVIDER_INDEX: ReadonlyMap<string, ProviderClassification> = new Map(
  Object.entries(PROVIDER_PROFILES).flatMap(([name, entry]) => {
    const classification: ProviderClassification = {
      provider: name,
      endpointKind: entry.endpointKind,
      fileExtension: entry.fileExtension,
    };
    return [name, ...entry.aliases].map((key) => [key, classification] as const);
  }),
);

/**
 * Classify a provider string (canonical name or alias; case/whitespace
 * insensitive). Returns null for an unknown provider — callers must handle
 * it rather than fall through to a default endpoint.
 */
export function classifyProvider(provider: string | null | undefined): ProviderClassification | null {
  const key = String(provider ?? '').trim().toLowerCase();
  return PROVIDER_INDEX.get(key) ?? null;
}

/** Every accepted provider string (names + aliases), sorted. */
export function knownProviderKeys(): string[] {
  return [...PROVIDER_INDEX.keys()].sort();
}

/** True for a known provider on the local chunked pipeline. */
export function isLocalChunkedProvider(provider: string | null | undefined): boolean {
  return classifyProvider(provider)?.endpointKind === 'local_chunked';
}

/** Default generator when the settings tab has no saved choice. */
export const DEFAULT_AUDIO_GENERATOR: AudioGeneratorId = 'chatterbox_base';

/** Default Chatterbox settings when none are saved. */
export const DEFAULT_CHATTERBOX_CHOICE: ChatterboxSettingsChoice = {
  preset: 'default',
  temperature: null,
  cfgWeight: null,
  exaggeration: null,
};

/** The 'expressive' preset pins exaggeration to this value. */
export const EXPRESSIVE_EXAGGERATION = 0.6;

/**
 * Coerce a raw (possibly stale/partial) choice into a well-formed one.
 * Unknown preset → 'default'; non-finite numbers → null.
 */
export function sanitizeChatterboxChoice(
  choice: ChatterboxSettingsChoice | null | undefined,
): ChatterboxSettingsChoice {
  const rawPreset = choice?.preset;
  const preset: ChatterboxPreset =
    rawPreset === 'expressive' || rawPreset === 'custom' ? rawPreset : 'default';
  const num = (v: unknown): number | null =>
    typeof v === 'number' && Number.isFinite(v) ? v : null;
  return {
    preset,
    temperature: num(choice?.temperature),
    cfgWeight: num(choice?.cfgWeight),
    exaggeration: num(choice?.exaggeration),
  };
}

/**
 * Resolve a user choice into the wire payload sent as `chatterbox_settings`.
 *
 * - Always includes the variant.
 * - 'default' → variant only (engine defaults).
 * - 'expressive' → variant + exaggeration 0.6.
 * - 'custom' → variant + whatever the user entered (nulls omitted on the wire).
 * - The turbo model ignores cfg_weight/exaggeration, so they are never sent
 *   for it (temperature is the only turbo lever).
 */
export function resolveChatterboxSettings(
  choice: ChatterboxSettingsChoice | null | undefined,
  variant: 'base' | 'turbo',
): ChatterboxSettingsPayload {
  const c = sanitizeChatterboxChoice(choice);
  const payload: ChatterboxSettingsPayload = { variant };
  if (variant === 'base') {
    let temperature: number | null = null;
    let cfgWeight: number | null = null;
    let exaggeration: number | null = null;
    if (c.preset === 'expressive') {
      exaggeration = EXPRESSIVE_EXAGGERATION;
    } else if (c.preset === 'custom') {
      temperature = c.temperature;
      cfgWeight = c.cfgWeight;
      exaggeration = c.exaggeration;
    }
    if (temperature !== null) payload.temperature = temperature;
    if (cfgWeight !== null) payload.cfg_weight = cfgWeight;
    if (exaggeration !== null) payload.exaggeration = exaggeration;
  } else if (c.preset === 'custom') {
    if (c.temperature !== null) payload.temperature = c.temperature;
  }
  return payload;
}

export interface ResolvedGenerator {
  /** Concrete TTS provider string for the request (e.g. 'chatterbox'). */
  provider: string;
  /** Resolved Chatterbox settings, or null when not a Chatterbox run. */
  chatterboxSettings: ChatterboxSettingsPayload | null;
}

/**
 * Map a generator id + choice to the concrete provider + settings for one run.
 * 'per_voice' (or unknown) defers to the voice's assigned provider with no
 * Chatterbox settings — the legacy behavior.
 */
export function resolveGeneratorChoice(
  generator: AudioGeneratorId | null | undefined,
  choice: ChatterboxSettingsChoice | null | undefined,
  voiceProvider: string,
): ResolvedGenerator {
  const id: AudioGeneratorId = generator ?? DEFAULT_AUDIO_GENERATOR;
  switch (id) {
    case 'chatterbox_base':
      return { provider: 'chatterbox', chatterboxSettings: resolveChatterboxSettings(choice, 'base') };
    case 'chatterbox_turbo':
      return { provider: 'chatterbox', chatterboxSettings: resolveChatterboxSettings(choice, 'turbo') };
    case 'vibevoice':
      return { provider: 'vibevoice_local', chatterboxSettings: null };
    case 'per_voice':
    default:
      return { provider: voiceProvider, chatterboxSettings: null };
  }
}

/** Advanced-overlay override: 'use_default' means "fall through to settings". */
export type GeneratorOverride =
  | { generator: 'use_default' | null; chatterboxChoice?: ChatterboxSettingsChoice }
  | { generator: AudioGeneratorId; chatterboxChoice?: ChatterboxSettingsChoice };

/**
 * Resolve the generator for a generation run. An explicit override (from the
 * advanced batch UI) wins; otherwise the settings-tab default applies,
 * falling back to 'chatterbox_base'.
 */
export function resolveEffectiveAudioGenerator(
  voiceProvider: string,
  override?: GeneratorOverride | null,
): ResolvedGenerator {
  const settings = get(audiobookSettings);
  const defaultGenerator = settings.audioGenerator ?? DEFAULT_AUDIO_GENERATOR;
  const defaultChoice = settings.chatterboxSettings ?? DEFAULT_CHATTERBOX_CHOICE;

  if (override && override.generator && override.generator !== 'use_default') {
    return resolveGeneratorChoice(
      override.generator,
      override.chatterboxChoice ?? defaultChoice,
      voiceProvider,
    );
  }
  return resolveGeneratorChoice(defaultGenerator, defaultChoice, voiceProvider);
}
