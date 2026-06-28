import { invoke } from '@tauri-apps/api/core';
import { get } from 'svelte/store';
import type { CharacterManifestStats } from '$lib/types';
import { chapters } from '$lib/stores/bookState';

interface ManifestLoadOptions {
  totalLines?: number;
  force?: boolean;
  manifestPathHint?: string;
}

interface ManifestSummary {
  stats: CharacterManifestStats;
  entries: any[];
  path: string;
}

const manifestCache = new Map<string, CharacterManifestStats>();

function cacheKey(root: string, character: string): string {
  return `${root}::${character}`;
}

function normalizeSegment(segment: string): string {
  return segment.replace(/^[/\\]+/, '').replace(/[/\\]+$/, '');
}

function buildManifestPath(root: string, character: string): string {
  if (!root) return '';
  const trimmedRoot = root.replace(/[/\\]+$/, '');
  const cleanedCharacter = normalizeSegment(character);
  const separator = /\\/.test(trimmedRoot) ? '\\' : '/';
  return [trimmedRoot, 'audio_lines', cleanedCharacter, 'manifest.json'].join(separator);
}

function buildChapterManifestPath(root: string, chapterTitle: string, character: string): string {
  if (!root) return '';
  const trimmedRoot = root.replace(/[/\\]+$/, '');
  const cleanedChapter = normalizeSegment(chapterTitle);
  const cleanedCharacter = normalizeSegment(character);
  const separator = /\\/.test(trimmedRoot) ? '\\' : '/';
  return [trimmedRoot, cleanedChapter, 'audio_lines', cleanedCharacter, 'manifest.json'].join(separator);
}

function computeVoiceCounts(entries: any[]): Record<string, number> {
  const acc = new Map<string, number>();
  for (const entry of entries) {
    const voiceId: string | undefined = entry?.voice_id ?? entry?.voiceId ?? entry?.voiceID;
    if (!voiceId) continue;
    acc.set(voiceId, (acc.get(voiceId) ?? 0) + 1);
  }
  return Object.fromEntries(acc);
}

function choosePrimaryVoice(voiceCounts: Record<string, number>): string | null {
  let bestId: string | null = null;
  let bestCount = -1;
  for (const [voiceId, count] of Object.entries(voiceCounts)) {
    if (count > bestCount) {
      bestId = voiceId;
      bestCount = count;
    }
  }
  return bestId;
}

function buildStats(entries: any[], totalLines: number | undefined): CharacterManifestStats {
  const clipCount = entries.length;
  const voiceIds = computeVoiceCounts(entries);
  const primaryVoiceId = choosePrimaryVoice(voiceIds);
  const total = totalLines ?? 0;
  const coverageRatio = total > 0 ? clipCount / total : undefined;

  return {
    clipCount,
    totalLines: total,
    voiceIds,
    coverageRatio,
    primaryVoiceId: primaryVoiceId ?? null,
    lastUpdated: new Date().toISOString(),
  };
}


async function resolveManifestPath(root: string, character: string, hint?: string): Promise<string> {
  if (hint) return hint;
  return buildManifestPath(root, character);
}

async function ensureManifestPathExists(path: string): Promise<void> {
  try {
    const exists = await invoke<boolean>('path_exists', { path });
    if (!exists) {
      throw new Error(`Manifest file not found at ${path}`);
    }
  } catch (error) {
    if (error instanceof Error && error.message.includes('not found')) {
      throw error;
    }
    throw new Error(`Unable to verify manifest path ${path}: ${error instanceof Error ? error.message : String(error)}`);
  }
}

async function pathExists(path: string): Promise<boolean> {
  try {
    return await invoke<boolean>('path_exists', { path });
  } catch (error) {
    throw new Error(`Unable to verify manifest path ${path}: ${error instanceof Error ? error.message : String(error)}`);
  }
}

async function readManifestFile(path: string): Promise<any[]> {
  const raw = await invoke<string>('read_json_file', { path });
  const parsed = JSON.parse(raw);

  // v2 format: { formatVersion: "2.0", clips: [...], metadata: {...} }
  if (parsed?.formatVersion === '2.0' && Array.isArray(parsed.clips)) {
    return parsed.clips;
  }

  // Legacy formats
  if (Array.isArray(parsed)) return parsed;
  if (Array.isArray((parsed as any)?.clips)) return (parsed as any).clips;
  if (parsed && typeof parsed === 'object') return Object.values(parsed);

  return [];
}

async function loadChapterScopedManifestEntries(root: string, character: string): Promise<{ entries: any[]; paths: string[] }> {
  const chapterList = get(chapters);
  const chapterPaths = chapterList
    .map((chapter) => String(chapter?.title || '').trim())
    .filter((title): title is string => title.length > 0)
    .map((title) => buildChapterManifestPath(root, title, character));

  const entries: any[] = [];
  const paths: string[] = [];

  for (const manifestPath of chapterPaths) {
    if (!(await pathExists(manifestPath))) continue;
    const manifestEntries = await readManifestFile(manifestPath);
    if (!manifestEntries.length) continue;
    entries.push(...manifestEntries);
    paths.push(manifestPath);
  }

  return { entries, paths };
}

export async function loadCharacterManifestSummary(
  root: string,
  character: string,
  options?: ManifestLoadOptions,
): Promise<ManifestSummary> {
  if (!root || !character) {
    throw new Error('Missing root or character when loading manifest summary.');
  }
  const key = cacheKey(root, character);
  if (!options?.force && manifestCache.has(key)) {
    const cached = manifestCache.get(key)!;
    return { stats: cached, entries: [], path: buildManifestPath(root, character) };
  }

  const manifestPath = await resolveManifestPath(root, character, options?.manifestPathHint);

  let entries: any[] = [];
  let resolvedPath = manifestPath;

  if (await pathExists(manifestPath)) {
    try {
      entries = await readManifestFile(manifestPath);
    } catch (error) {
      throw new Error(`Failed to read manifest at ${manifestPath}: ${error instanceof Error ? error.message : String(error)}`);
    }
  } else if (!options?.manifestPathHint) {
    const chapterScoped = await loadChapterScopedManifestEntries(root, character);
    entries = chapterScoped.entries;
    if (chapterScoped.paths.length > 0) {
      resolvedPath = chapterScoped.paths.join('; ');
    }
  } else {
    await ensureManifestPathExists(manifestPath);
  }

  if (!entries.length) {
    throw new Error(`Manifest @ ${manifestPath} is empty or missing entries.`);
  }

  const stats = buildStats(entries, options?.totalLines);
  manifestCache.set(key, stats);
  return { stats, entries, path: resolvedPath };
}

export function clearManifestCache(root?: string, character?: string): void {
  if (!root && !character) {
    manifestCache.clear();
    return;
  }
  for (const key of manifestCache.keys()) {
    const [r, c] = key.split('::');
    if (root && r !== root) continue;
    if (character && c !== character) continue;
    manifestCache.delete(key);
  }
}


